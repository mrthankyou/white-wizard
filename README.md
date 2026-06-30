# The White Wizard

```
                /\
               /  \
              / ** \
             /  *   \
            /   **   \
           /  *    *  \
          /     **    \
         /____________ \
    ____/______________\____
   (________________________)

          The White Wizard
```

**Build, run, and manage multiple AI agents at once — with one command.**

---

## Quick start

Pull the repo, install once, then run `wizard` inside any project you want to manage:

```bash
git clone https://github.com/mrthankyou/white-wizard.git
cd white-wizard
pip install -e .          # installs the `wizard` command

cd ../your-project/       # go to the project you want to manage
wizard --claude           # summon the wizard (real Claude CLI)
```

That's it. The first run walks you through everything.

> **Just exploring?** Drop `--claude` and run plain `wizard` to use the offline
> **mock AI** — you can click through the whole experience with zero setup. The
> `--claude` flag uses the real [Claude CLI](https://github.com/anthropics/anthropic-tools)
> for real work (it must be installed and on your PATH).

---

## The idea in 30 seconds

You run `wizard` inside your project. It reads your code, then summons a **team of
specialized AI agents** tailored to it — a code reviewer, a test runner, a
deployer, whatever your project needs. From then on, the wizard is your control
panel for working at a higher level than individual files:

- 🧙 **Manage the team** — spin up, inspect, or tear down your agent orchestration.
- 🛠️ **Ship features** — tell the team *what* you want; it figures out *which*
  agents do *what*, and in what order.
- 🔄 **Let it run on its own** — stream mode works in the background, scanning
  your live codebase for bugs, missing tests, and cleanup, and can fix what it
  finds (with changes committed for you).

No config files to hand-write. No agent framework to learn. One button.

---

## What happens on your first run

```
1. Scan      →  Wizard finds the code files in your project.
2. Summarize →  Claude writes a short synopsis of what your project is.
3. Choose    →  Pick a team type (dev team, or describe your own).
4. Preview   →  See the proposed agent flow as an infographic.
5. Decide    →  Approve it, start over, or type feedback to refine it.
6. Conjure   →  On approval, every agent + the orchestrator is generated.
```

The agent flow is shown as a live diagram so you can *see* the team before you
commit to it — each box is an agent, arrows show how work passes from one to the
next (and where it loops back on failure).

> Today the **Dev team** is the ready-to-use template; DevOps, Performance, and
> Security teams are on the way. You can also pick **"Something else"** and
> describe a custom team in your own words.

---

## Your control panel

Once a team exists, running `wizard` again opens the management hub — a split pane
with the **menu** on the left and the **live task feed** on the right.

**Main menu:**

| Choose | What it does |
|---|---|
| **What do you want the team to do?** | Describe a feature or change; the wizard plans which agents handle it and how. |
| **Manage** | Open the management submenu (below). |
| **Something else** | Free-form question to the wizard. |
| **Enable / Disable stream** | Toggle stream mode (see below). |
| **Build a new team** | Generate another orchestration. |
| **Quit** | Stop the background AI and exit. |

**Manage submenu:**

| Choose | What it does |
|---|---|
| **Adjust the orchestration** | Refine the team in your own words. |
| **Clear all tasks & findings** | Empty the task queue, findings, and history (keeps the team). |
| **View diagram** | Show the agent-flow infographic. |
| **Enable / Disable auto-fix** | When on, the stream fixes scan findings automatically. |
| **Enable / Disable auto-commit** | When on, completed task changes are committed to git. |
| **Wipe the orchestration system** | Delete the generated orchestration. (Your settings are kept.) |

**Task feed** — press `→` to focus it, `↑↓` to select, then `Enter`:

| On a… | Enter does |
|---|---|
| **Suggested finding** | Dispatches the fix as real work (agentic edit + commit). |
| **Pending task** (stream paused) | Runs that one task in the background. |
| **Completed task** | Opens its detail view — description, commit, and the diff with line numbers (`↑↓` to scroll, `←` to go back). |

---

## Stream mode — the background worker 🔄

Stream mode is where White Wizard works on its own. Toggle it from the main menu
with **Enable / Disable stream** (use `--claude` so it does real work):

```bash
wizard --claude
```

When enabled, a background worker loops over your project and fills the **task
feed** in the right pane. Each pass it does one thing, in priority order:

1. **Your tasks first** — anything you queued via *"What do you want the team to
   do?"* runs ahead of everything else.
2. **Auto-fix a finding** — if **auto-fix** is on, it takes the oldest open
   finding and fixes it (agentic edit + commit).
3. **Scan** — otherwise it runs the next check in its rotation and records what it
   finds. For a dev team the rotation is: bugs → missing tests → refactors → docs.

Each scan produces **findings**, saved as `Suggested fix` items in the feed. A
finding is a suggestion, not an automatic change: highlight it and press `Enter`
to fix it on demand, or turn on **auto-fix** to let the worker drain them itself.
Only one task edits your working tree at a time (serialized behind a lock), so
concurrent runs never corrupt each other's changes or commits.

Two settings (in the **Manage** submenu, persisted to `wizard.yaml`) shape this:

- **Auto-commit** *(on by default)* — commit each completed task's changes to git
  with a generated message. Turn it off to review changes yourself.
- **Auto-fix** *(off by default)* — let the worker act on findings without you.

Every task and finding is saved to `.wizard/state.db`, so the feed survives across
runs and you keep a history of what the wizard has done. A task interrupted by a
crash or quit is marked failed on the next start, so nothing shows a stuck spinner.

---

## What gets generated

When you approve a plan, Wizard writes into your project:

```
.claude/
  wizard-orch.json          # the orchestration manifest — source of truth
  agents/
    dev_team/
      code-reviewer.md       # one Claude-native subagent per agent
      test-runner.md
      dev-orchestrator.md    # a lead agent that coordinates the flow
      state_machine.py       # FastMCP state-machine tool (drives delegation order)
      ...
.mcp.json                   # registers the state-machine tool for Claude
wizard.yaml                 # the wizard's own config (model, auto-commit, auto-fix)
```

> With **auto-commit** on (the default), the wizard also commits the generated
> orchestration to git for you once its smoke test passes.

The orchestration lives in the AI's **own** store — `.claude/` for Claude — which
the wizard treats as the source of truth. `wizard.yaml` holds only the wizard's
configuration, never orchestration data.

**To run your team:** open Claude in the project and invoke the orchestrator
subagent (e.g. ask Claude to use the `dev-orchestrator` subagent). It runs the
workflow in order, hands each agent's output to the next, and follows failure
paths to remediate.

---

## Where things live

| Location | Contents |
|---|---|
| `.claude/` | The orchestration itself (manifest + Claude-native subagents). Source of truth. |
| `wizard.yaml` | The wizard's own config: model, auto-commit, auto-fix. Commit it. |
| `.wizard/state.db` | Runtime: the task queue, findings, and stream state. Gitignored. |
| `.wizard/stream.log`, `.wizard/debug.log` | Background-worker activity and AI prompt/response traces. Gitignored. |

---

## How the orchestration works

White Wizard generates a **state machine** as a small **MCP tool**
(`state_machine.py`, registered in `.mcp.json`):

- Each agent is a state; the transition table is embedded in the tool.
- Agents transition on **pass** or **fail**.
- The lead **orchestrator** subagent drives the loop by *calling the tool* —
  `get_current_state` to see whose turn it is, delegate to that subagent,
  `report_result(agent, status)` to advance — until the state reaches `done`.

```
orchestrator
    │
    ├── get_current_state()  → who's up?
    │       └── delegate to that subagent
    │       └── report_result(agent, pass|fail)  → next state
    └── loops until state == "done"
```

> The state-machine tool needs the `mcp` package: `pip install mcp`.

---

## Command reference

| Flag | Description |
|------|-------------|
| `--mock` | Use the offline mock AI backend (**default** — no setup needed). |
| `--claude` | Use the real Claude CLI (`claude -p`) for AI calls. |
| `--help`, `-h` | Show help and exit. |

> Stream mode is no longer a flag — toggle it from the main menu once a team exists.

Run without installing:

```bash
python3 -m white_wizard          # equivalent to the `wizard` command
python3 white_wizard.py          # convenience shim that runs the package
```

---

## Requirements

- Python 3.9+
- For `--claude`: the [Claude CLI](https://github.com/anthropics/anthropic-tools)
  installed and on your PATH.

---

## Development

Source of truth is `white_wizard/app.py` — not the root `white_wizard.py` shim.
After editing the source, regenerate the shim:

```bash
python3 build.py
```
