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
import os
import shutil
import subprocess
import sys

DEFAULT_BACKEND = "mock"
BACKENDS = ("mock", "claude")

_backend = DEFAULT_BACKEND
_current_proc = None  # live claude subprocess, if any

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
  --help      Show this help message and exit.

Examples:
  wizard                   # run with mock AI (default)
  wizard --claude          # use real Claude CLI
  wizard --claude --stream # stream mode with real Claude
"""


def parse_args(argv=None):
    """Strip known flags from argv and configure the module backend.

    Returns a dict with keys: stream (bool).
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
    return {"stream": stream}


def ask_messages(messages, *, model=None, backend=None, timeout=120):
    """Send a structured list of {"role", "content"} messages to the backend.

    This is the canonical entry point. ``ask`` and ``ask_with_history`` are thin
    wrappers that build the message list. Backends consume messages directly — a
    future SDK backend would hand them straight to ``messages.create`` — while the
    CLI backend flattens them into a single ``claude -p`` prompt.
    """
    resolved = backend or _backend
    if resolved == "mock":
        return _mock(messages, model=model)
    if resolved == "claude":
        return _claude(messages, model=model, timeout=timeout)
    raise ValueError(f"Unknown AI backend: {resolved!r} (expected one of {BACKENDS})")


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
