#!/usr/bin/env python3
"""AI client for White Wizard.

White Wizard talks to an AI model through `ask()`. Until a real API is wired up,
the default 'mock' backend returns a canned response so the rest of the tool can
be built and tested offline. A 'claude' backend shells out to the Claude CLI
(`claude -p`) for live responses.

The backend is chosen by passing --mock or --claude when launching white_wizard.py:

    python3 white_wizard.py           # defaults to mock
    python3 white_wizard.py --mock    # explicit mock
    python3 white_wizard.py --claude  # calls `claude -p <prompt>`

Callers outside white_wizard.py can pass `backend` directly to ask().

    $ python3 ai_client.py "Plan a web app orchestration system"
    $ python3 ai_client.py --claude "Say hi"
"""

import os
import shutil
import subprocess
import sys

DEFAULT_BACKEND = "mock"
BACKENDS = ("mock", "claude")

# Module-level backend, set once at startup by parse_args() or by the caller.
_backend = DEFAULT_BACKEND


HELP = """\
White Wizard - conjure multi-agent orchestration systems with a single button.

Usage:
  python3 white_wizard.py [options]

Options:
  --mock      Use the offline mock AI backend (default). No network or API
              key required; returns a canned response for every prompt.
  --claude    Use the Claude CLI backend. Requires `claude` on PATH.
              Calls `claude -p <prompt>` for each AI interaction.
  --help      Show this help message and exit.

Examples:
  python3 white_wizard.py              # run with mock AI (default)
  python3 white_wizard.py --mock       # explicit mock
  python3 white_wizard.py --claude     # use real Claude CLI
"""


def parse_args(argv=None):
    """Read --mock / --claude / --help from argv and configure the module backend.

    Returns the remaining argv with known flags stripped.
    Prints help and exits when --help is present.
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
    return argv


def ask_with_history(history, new_message, *, model=None, backend=None, timeout=120):
    """Send a message with prior conversation history serialized into the prompt.

    history is a list of ("user"|"assistant", text) tuples. Each call to
    claude -p is stateless, so we reconstruct the transcript in the prompt body
    so Claude has full context. We can upgrade this to a stateful API later.
    """
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
    """Send `prompt` to the AI and return its text response.

    backend: 'mock' or 'claude'. Defaults to the module-level backend set by
    parse_args() (itself defaulting to 'mock'). model is passed through to the
    backend when supported.
    """
    resolved = backend or _backend
    if resolved == "mock":
        return _mock(prompt, model=model)
    if resolved == "claude":
        return _claude(prompt, model=model, timeout=timeout)
    raise ValueError(f"Unknown AI backend: {resolved!r} (expected one of {BACKENDS})")


def _mock(prompt, *, model=None):
    name = model or "mock-model"
    first = next((ln for ln in prompt.strip().splitlines() if ln.strip()),
                 "(empty prompt)")
    return (f"[mock:{name}] Simulated AI response. "
            f"You asked: {first.strip()[:200]}")


def _claude(prompt, *, model=None, timeout=120):
    if shutil.which("claude") is None:
        raise RuntimeError(
            "`claude` CLI not found on PATH. "
            "Install it or run without --claude to use the mock."
        )
    cmd = ["claude", "-p", prompt]
    if model:
        cmd += ["--model", model]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"claude exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


if __name__ == "__main__":
    argv = parse_args()
    question = " ".join(argv) or "Hello, wizard."
    print(ask(question))
