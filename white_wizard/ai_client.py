"""AI client for White Wizard.

Backends:
  mock   — offline, returns a canned response (default)
  claude — shells out to `claude -p` for live responses

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


def ask_with_history(history, new_message, *, model=None, backend=None, timeout=120):
    """Send a message with prior conversation history serialized into the prompt."""
    if not history:
        return ask(new_message, model=model, backend=backend, timeout=timeout)
    parts = []
    for role, content in history:
        label = "User" if role == "user" else "Assistant"
        parts.append(f"{label}: {content}")
    parts.append(f"User: {new_message}")
    parts.append("Assistant:")
    return ask("\n\n".join(parts), model=model, backend=backend, timeout=timeout)


def ask(prompt, *, model=None, backend=None, timeout=120):
    """Send prompt to the AI and return its text response."""
    resolved = backend or _backend
    if resolved == "mock":
        return _mock(prompt, model=model)
    if resolved == "claude":
        return _claude(prompt, model=model, timeout=timeout)
    raise ValueError(f"Unknown AI backend: {resolved!r} (expected one of {BACKENDS})")


def _mock(prompt, *, model=None):
    name = model or "mock-model"
    first = next((ln for ln in prompt.strip().splitlines() if ln.strip()), "(empty prompt)")
    return (f"[mock:{name}] Simulated AI response. You asked: {first.strip()[:200]}")


def kill_current():
    """Kill the in-flight claude subprocess, if any. Safe to call from any thread."""
    global _current_proc
    proc = _current_proc
    if proc and proc.poll() is None:
        proc.kill()


def _claude(prompt, *, model=None, timeout=120):
    global _current_proc
    if shutil.which("claude") is None:
        raise RuntimeError(
            "`claude` CLI not found on PATH. "
            "Install it or run without --claude to use the mock."
        )
    cmd = ["claude", "-p", prompt]
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
