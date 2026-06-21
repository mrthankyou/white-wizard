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
import os
import shutil
import subprocess
import sys

DEFAULT_BACKEND = "mock"
BACKENDS = ("mock", "claude")

_backend = DEFAULT_BACKEND
_current_proc = None  # live claude subprocess, if any
_logger = None        # debug logger when --debug is on, else None
_log_path = None      # path to the active debug log, for display
_seq = 0              # request counter, to pair prompts with responses


def enable_debug_logging():
    """Turn on the AI request/response trace log under .wizard/, return its path.

    Every prompt sent to the backend and every response (or error) is appended,
    so a failed run can be inspected after the fact. The log lives in .wizard/
    (gitignored) and is reused across runs.
    """
    global _logger, _log_path
    wizard_dir = os.path.join(os.getcwd(), ".wizard")
    os.makedirs(wizard_dir, exist_ok=True)
    _log_path = os.path.join(wizard_dir, "debug.log")
    logger = logging.getLogger("white_wizard.debug")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.FileHandler(_log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    _logger = logger
    logger.info("=== debug logging enabled (backend=%s) ===", _backend)
    return _log_path


def debug_log_path():
    """Path of the active debug log, or None if --debug was not passed."""
    return _log_path

HELP = """\
White Wizard - conjure multi-agent orchestration systems with a single button.

Usage:
  wizard [options]
  python3 -m white_wizard [options]

Options:
  --mock      Use the offline mock AI backend (default).
  --claude    Use the Claude CLI backend (`claude -p`).
  --stream    Run stream mode: execute the prioritized review checklist
              against the current codebase (requires wizard.yaml).
  --debug     Log every AI prompt and response to .wizard/debug.log.
  --help      Show this help message and exit.

Examples:
  wizard                   # run with mock AI (default)
  wizard --claude          # use real Claude CLI
  wizard --claude --stream # stream mode with real Claude
  wizard --claude --debug  # trace AI prompts/responses to .wizard/debug.log
"""


def parse_args(argv=None):
    """Strip known flags from argv and configure the module backend.

    Returns a dict with keys: stream (bool), debug (bool).
    Prints help and exits on --help.
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
    stream = "--stream" in argv
    if stream:
        argv.remove("--stream")
    debug = "--debug" in argv
    if debug:
        argv.remove("--debug")
        enable_debug_logging()
    return {"stream": stream, "debug": debug}


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


def _mock(messages, *, model=None):
    override = os.environ.get("WHITE_WIZARD_MOCK_REPLY")
    if override is not None:
        return override
    name = model or "mock-model"
    last = messages[-1]["content"] if messages else ""
    first = next((ln for ln in last.strip().splitlines() if ln.strip()), "(empty prompt)")
    return (f"[mock:{name}] Simulated AI response. You asked: {first.strip()[:200]}")


def kill_current():
    """Kill the in-flight claude subprocess, if any. Safe to call from any thread."""
    global _current_proc
    proc = _current_proc
    if proc and proc.poll() is None:
        proc.kill()


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
    global _current_proc
    if shutil.which("claude") is None:
        raise RuntimeError(
            "`claude` CLI not found on PATH. "
            "Install it or run without --claude to use the mock."
        )
    cmd = ["claude", "-p", _flatten(messages)]
    if model:
        cmd += ["--model", model]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _current_proc = proc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError(f"claude timed out after {timeout}s")
    finally:
        _current_proc = None
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {stderr.strip()}")
    return stdout.strip()


if __name__ == "__main__":
    flags = parse_args()
    print(ask("Hello, wizard."))
