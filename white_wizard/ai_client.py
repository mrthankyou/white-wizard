"""AI client for White Wizard.

All AI access funnels through ``ask_messages(messages, ...)``, where ``messages``
is a list of ``{"role", "content"}`` dicts. ``ask`` and ``ask_with_history`` are
convenience wrappers that build that list. Backends consume messages directly, so
adding a new one (e.g. the Claude SDK) is a localized change here — no caller in
the app touches anything but ``ask`` / ``ask_with_history``.

Backends:
  mock   — offline, returns a canned response (default)
  claude — flattens messages into a single `claude -p` prompt

The backend is chosen by passing --mock or --claude when running wizard.
"""
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import threading

DEFAULT_BACKEND = "mock"
BACKENDS = ("mock", "claude")

_backend = DEFAULT_BACKEND
_current_procs = set()       # live claude subprocesses (may be >1 with concurrent workers)
_procs_lock = threading.Lock()
_logger = None        # AI request/response logger, set by enable_debug_logging
_log_path = None      # path to the active debug log
_seq = 0              # request counter, to pair prompts with responses


def enable_debug_logging():
    """Turn on the AI request/response trace log under .wizard/, return its path.

    Every prompt sent to the backend and every response (or error) is appended,
    so a failed run can be inspected after the fact. The log lives in .wizard/
    (gitignored) and is reused across runs. It runs always-on (enabled at
    startup), so the handler rotates at 5 MB (3 backups kept) to stay bounded.
    """
    global _logger, _log_path
    wizard_dir = os.path.join(os.getcwd(), ".wizard")
    os.makedirs(wizard_dir, exist_ok=True)
    _log_path = os.path.join(wizard_dir, "debug.log")
    logger = logging.getLogger("white_wizard.debug")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            _log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    _logger = logger
    logger.info("=== debug logging enabled (backend=%s) ===", _backend)
    return _log_path


HELP = """\
White Wizard - conjure multi-agent orchestration systems with a single button.

Usage:
  wizard [options]
  python3 -m white_wizard [options]

Options:
  --mock      Use the offline mock AI backend (default).
  --claude    Use the Claude CLI backend (`claude -p`).
  --help      Show this help message and exit.

Logging (always on, gitignored, size-rotated):
  .wizard/debug.log    every AI prompt and response
  .wizard/stream.log   task queue / stream-worker activity

Examples:
  wizard                   # run with mock AI (default)
  wizard --claude          # use real Claude CLI
"""


def parse_args(argv=None):
    """Strip known flags from argv and configure the module backend.

    Prints help and exits on --help. AI request/response logging is always on
    (the caller enables it at startup), so there is no flag to parse for it.
    """
    global _backend
    argv = list(argv if argv is not None else sys.argv[1:])
    if "--help" in argv or "-h" in argv:
        print(HELP)
        sys.exit(0)
    if "--claude" in argv:
        _backend = "claude"
        argv.remove("--claude")
    elif "--mock" in argv:
        _backend = "mock"
        argv.remove("--mock")
    if argv:  # anything left is unrecognized
        print(f"Warning: ignoring unrecognized option(s): {' '.join(argv)}",
              file=sys.stderr)


def ask_messages(messages, *, model=None, backend=None, timeout=120):
    """Send a structured list of {"role", "content"} messages to the backend.

    This is the canonical entry point. ``ask`` and ``ask_with_history`` are thin
    wrappers that build the message list. Backends consume messages directly — a
    future SDK backend would hand them straight to ``messages.create`` — while the
    CLI backend flattens them into a single ``claude -p`` prompt.
    """
    resolved = backend or _backend
    req_id = _log_request(resolved, model, messages)
    try:
        if resolved == "mock":
            response = _mock(messages, model=model)
        elif resolved == "claude":
            response = _claude(messages, model=model, timeout=timeout)
        else:
            raise ValueError(f"Unknown AI backend: {resolved!r} (expected one of {BACKENDS})")
    except Exception as exc:
        _log_error(req_id, exc)
        raise
    _log_response(req_id, response)
    return response


def _log_request(backend, model, messages):
    """Append the outgoing prompt to the debug log; return its request id."""
    global _seq
    if _logger is None:
        return None
    _seq += 1
    req_id = _seq
    _logger.info("--- REQUEST #%d (backend=%s, model=%s) ---", req_id, backend, model or "default")
    for m in messages:
        _logger.info("[%s]\n%s", m.get("role", "?"), m.get("content", ""))
    return req_id


def _log_response(req_id, response):
    if _logger is None:
        return
    _logger.info("--- RESPONSE #%s ---\n%s", req_id, response)


def _log_error(req_id, exc):
    if _logger is None:
        return
    _logger.error("--- ERROR #%s --- %r", req_id, exc)


def ask(prompt, *, model=None, backend=None, timeout=120):
    """Send a single-turn user prompt and return the text response."""
    return ask_messages(
        [{"role": "user", "content": prompt}],
        model=model, backend=backend, timeout=timeout,
    )


def ask_with_history(history, new_message, *, model=None, backend=None, timeout=120):
    """Send a new user message preceded by prior (role, content) turns."""
    messages = [{"role": role, "content": content} for role, content in history]
    messages.append({"role": "user", "content": new_message})
    return ask_messages(messages, model=model, backend=backend, timeout=timeout)


# Tools granted to the agentic task runner. Deliberately excludes Bash so the
# wizard edits files but doesn't run arbitrary shell commands on its own.
_TASK_TOOLS = ["Read", "Edit", "Write", "Grep", "Glob"]


def run_task(prompt, *, model=None, timeout=900):
    """Run an edit-enabled ``claude -p`` that actually performs work in the
    current directory — creating and editing files — rather than just answering.
    Returns the final text. The mock backend returns a canned string and makes
    no changes (so offline/test runs never touch the filesystem)."""
    resolved = _backend
    messages = [{"role": "user", "content": prompt}]
    req_id = _log_request(resolved, model, messages)
    try:
        if resolved == "mock":
            response = _mock(messages, model=model)
        else:
            response = _claude_agentic(prompt, model=model, timeout=timeout)
    except Exception as exc:
        _log_error(req_id, exc)
        raise
    _log_response(req_id, response)
    return response


# A valid agent plan the mock returns when a plan is requested, so running the
# wizard against the mock backend actually builds an orchestration (files on
# disk) instead of dead-ending. The planner prompt embeds a ``` fence on purpose
# to exercise the plan parser's nested-fence handling end to end.
_MOCK_PLAN = """\
Here is the dev team plan.

```json
{
  "agents": [
    {"name": "planner", "description": "Plans the work. Use first.",
     "tools": ["Read", "Grep"],
     "prompt": "You are the planner.\\n\\nWhen invoked:\\n1. Break the request into steps.\\n\\nOutput:\\n```\\nPLAN\\n1. step\\n```\\nReport the plan."},
    {"name": "implementer", "description": "Writes code. Use after planning.",
     "tools": ["Read", "Write", "Bash"],
     "prompt": "You are the implementer."},
    {"name": "reviewer", "description": "Reviews code. Use proactively after code is written.",
     "prompt": "You are the reviewer."}
  ],
  "transitions": [
    {"from": "planner", "to": "implementer", "condition": "always"},
    {"from": "implementer", "to": "reviewer", "condition": "pass"},
    {"from": "reviewer", "to": "planner", "condition": "fail"},
    {"from": "reviewer", "to": "done", "condition": "pass"}
  ]
}
```
"""


def _mock(messages, *, model=None):
    override = os.environ.get("WHITE_WIZARD_MOCK_REPLY")
    if override is not None:
        return override
    last = messages[-1]["content"] if messages else ""
    # When the wizard asks for an agent plan, return a real one so the build
    # path runs to completion against the mock backend.
    if "agent plan" in last.lower():
        return _MOCK_PLAN
    name = model or "mock-model"
    first = next((ln for ln in last.strip().splitlines() if ln.strip()), "(empty prompt)")
    return (f"[mock:{name}] Simulated AI response. You asked: {first.strip()[:200]}")


def kill_current():
    """Kill any in-flight claude subprocess(es). Safe to call from any thread."""
    with _procs_lock:
        procs = list(_current_procs)
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def _track_proc(proc):
    with _procs_lock:
        _current_procs.add(proc)


def _untrack_proc(proc):
    with _procs_lock:
        _current_procs.discard(proc)


def _flatten(messages):
    """Serialize messages into a single prompt string for the `claude -p` CLI.

    A lone user message passes through unchanged; a multi-turn exchange becomes
    labelled User:/Assistant: turns ending on an open Assistant turn.
    """
    if len(messages) == 1 and messages[0]["role"] == "user":
        return messages[0]["content"]
    parts = []
    for m in messages:
        label = "User" if m["role"] == "user" else "Assistant"
        parts.append(f"{label}: {m['content']}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


def _claude(messages, *, model=None, timeout=120):
    if shutil.which("claude") is None:
        raise RuntimeError(
            "`claude` CLI not found on PATH. "
            "Install it or run without --claude to use the mock."
        )
    cmd = ["claude", "-p", _flatten(messages)]
    if model:
        cmd += ["--model", model]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _track_proc(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError(f"claude timed out after {timeout}s")
    finally:
        _untrack_proc(proc)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {stderr.strip()}")
    return stdout.strip()


def _claude_agentic(prompt, *, model=None, timeout=900):
    """Like ``_claude``, but grants file-editing tools and a non-interactive
    permission mode (``acceptEdits``) so the model carries out the task and
    writes the changes instead of describing them."""
    if shutil.which("claude") is None:
        raise RuntimeError(
            "`claude` CLI not found on PATH. "
            "Install it or run without --claude to use the mock."
        )
    cmd = ["claude", "-p", prompt,
           "--permission-mode", "acceptEdits",
           "--allowedTools", *_TASK_TOOLS]
    if model:
        cmd += ["--model", model]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _track_proc(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError(f"claude timed out after {timeout}s")
    finally:
        _untrack_proc(proc)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {stderr.strip()}")
    return stdout.strip()


if __name__ == "__main__":
    flags = parse_args()
    print(ask("Hello, wizard."))
