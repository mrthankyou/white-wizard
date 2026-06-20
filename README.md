# White Wizard

Conjure multi-agent orchestration systems with a single button.

White Wizard scans your codebase, proposes a state-machine agent team tailored to your project, generates all the scaffolding (agents, MCP state machine, orchestrator), and keeps the system healthy over time via stream mode.

---

## Install

**From PyPI (once published):**
```bash
pip install white-wizard
```

**Local development install (recommended for now):**
```bash
git clone https://github.com/mrthankyou/white-wizard.git
cd white-wizard
pip install -e .
```

This installs the `wizard` command on your PATH.

**Without installing:**
```bash
python3 white_wizard.py        # shim that runs the package directly
python3 -m white_wizard        # equivalent
```

---

## Usage

Run `wizard` from inside any project directory:

```bash
cd your-project/
wizard
```

White Wizard will:
1. Scan your codebase for code files
2. Call Claude to generate a project synopsis
3. Let you choose a team type (dev, devops, performance, security)
4. Propose an agent state machine — shown as an infographic
5. Let you approve, reject, or give feedback
6. On approval, generate all agent files + MCP state machine + orchestrator

### Flags

| Flag | Description |
|------|-------------|
| `--claude` | Use the real Claude CLI (`claude -p`) for AI calls |
| `--mock` | Use the offline mock backend (default) |
| `--stream` | Run stream mode against the current codebase |
| `--help` | Show help and exit |

---

## Requirements

- Python 3.9+
- For `--claude`: the [Claude CLI](https://github.com/anthropics/anthropic-tools) installed and on PATH
- For the generated orchestrator: `pip install mcp`

---

## What gets generated

When you approve an orchestration plan, Wizard writes to your project directory:

```
agents/
  __init__.py
  code_reviewer.py   # one file per agent in the plan
  test_runner.py
  ...
mcp_state_machine.py # FastMCP server — the state machine
run_orch.py          # orchestrator entry point
wizard.yaml          # persists your plan and stream questions
```

Run the orchestration:
```bash
pip install mcp      # one-time dep for the MCP server
python3 run_orch.py
```

---

## Stream mode

Stream mode runs a prioritized checklist against your live codebase and evolves its own questions over time.

```bash
wizard --claude --stream
```

Stream mode requires `wizard.yaml` (created when you approve an orchestration). It runs three questions by default:

1. **Orchestration drift** — has the codebase changed in a way that requires updating the agent plan?
2. **Bug scan** — are there any bugs that should be surfaced as a fix?
3. **Self-improvement** — should the stream questions themselves be refined?

For each finding, you can **Approve** (saved to `.wizard/state.db`), **Skip**, or **Quit**.

The self-improvement question can propose and write updated questions back to `wizard.yaml` — so stream mode gets smarter over time.

---

## Storage

| Location | Contents |
|---|---|
| `wizard.yaml` | Config: orchestration plan, stream questions, settings. Committed to git. |
| `.wizard/state.db` | Runtime: stream run history, findings, scan stats. Gitignored. |

---

## Architecture

White Wizard generates a **state machine** orchestration:

- Each agent is a state
- Agents transition on **pass** or **fail**
- A **MCP server** (`mcp_state_machine.py`) tracks current state and handles transitions
- The **orchestrator** (`run_orch.py`) drives the loop: read state → run agent → advance

```
run_orch.py
    │
    ├── reads .mcp.json (auto-configures MCP for claude -p)
    ├── calls claude -p for each agent
    │       └── agent calls report_result(status) → MCP advances state
    └── loops until state == "done"
```

---

## Development

Edit source in `white_wizard/app.py`, then run:
```bash
python3 build.py    # regenerates the white_wizard.py convenience shim
```

Source of truth is `white_wizard/app.py` — not `white_wizard.py`.
