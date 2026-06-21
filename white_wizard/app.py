#!/usr/bin/env python3
"""White Wizard — conjure multi-agent orchestration systems with a single button."""

import datetime
import importlib.resources
import itertools
import json
import os
import re
import subprocess
import sys
import time

from white_wizard.ai_client import ask, ask_with_history
from white_wizard import db as wizard_db
from white_wizard.ui import (
    BOLD, DIM, WHITE, GOLD, CYAN, GRAY,
    color, clear, show_header, read_key, Option, menu,
)

# ---------------------------------------------------------------------------
# Constants (terminal primitives + the menu engine live in white_wizard.ui)
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def load_prompt(name):
    """Load a prompt file from the white_wizard/prompts/ package directory."""
    ref = importlib.resources.files("white_wizard.prompts").joinpath(name)
    return ref.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Agent plan parsing and infographic
# ---------------------------------------------------------------------------

def parse_agent_plan(response):
    """Parse a valid agent plan from Claude's response, or return None.

    Only a dict with a non-empty ``agents`` list of named agent dicts counts as
    a plan. A bare JSON array/string/number — or agents missing a name — returns
    None so the caller treats it as "not a plan yet" instead of crashing.
    """
    m = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
    candidates = [m.group(1)] if m else []
    candidates.append(response.strip())
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if (isinstance(obj, dict)
                and isinstance(obj.get("agents"), list)
                and obj["agents"]
                and all(isinstance(a, dict) and a.get("name") for a in obj["agents"])):
            return obj
    return None


def _wrap(text, width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = (current + " " + word).strip() if current else word
    if current:
        lines.append(current)
    return lines or [""]


def _make_box(name, desc_lines, box_w, box_h, model=None, mcp=None):
    """Render an agent box as a list of exactly box_h strings (no color)."""
    rows = []
    rows.append("┌" + "─" * box_w + "┐")
    rows.append("│" + name.upper()[:box_w].center(box_w) + "│")
    rows.append("├" + "─" * box_w + "┤")
    footer_rows = (2 if model else 0) + (1 if mcp else 0)  # sep + model [+ mcp]
    content_rows = box_h - 4 - footer_rows
    for i in range(content_rows):
        cell = (" " + desc_lines[i])[:box_w].ljust(box_w) if i < len(desc_lines) else " " * box_w
        rows.append("│" + cell + "│")
    if model:
        rows.append("├" + "─" * box_w + "┤")
        rows.append("│" + (" Model: " + model)[:box_w].ljust(box_w) + "│")
    if mcp:
        rows.append("│" + (" MCP:   " + mcp)[:box_w].ljust(box_w) + "│")
    rows.append("└" + "─" * box_w + "┘")
    return rows


def _fail_arrow_rows(src_col, tgt_col, line_w):
    r1 = [" "] * line_w
    r2 = [" "] * line_w
    if tgt_col < line_w: r1[tgt_col] = "▲"
    if src_col < line_w: r1[src_col] = "│"
    label = " FAIL"
    for k, ch in enumerate(label):
        idx = src_col + 1 + k
        if idx < line_w: r1[idx] = ch
    if tgt_col < line_w: r2[tgt_col] = "└"
    if src_col < line_w: r2[src_col] = "┘"
    for col in range(tgt_col + 1, src_col):
        if col < line_w: r2[col] = "─"
    return "".join(r1).rstrip(), "".join(r2).rstrip()


def render_infographic(plan, team_name=""):
    agents      = plan.get("agents", [])
    transitions = plan.get("transitions", [])

    fwd   = {}
    fails = {}
    for t in transitions:
        frm  = t.get("from", "")
        to   = t.get("to", "")
        cond = t.get("condition", "always")
        if cond == "fail":
            fails.setdefault(frm, []).append(to)
        elif frm not in fwd:
            fwd[frm] = (to, cond)

    if not agents:
        return color("  (no agents returned)", DIM, WHITE)

    start = agents[0]["name"]
    order, seen, cur = [start], {start}, start
    for _ in range(len(agents) + 1):
        nxt = fwd.get(cur)
        if not nxt or nxt[0].lower() in ("done", "end") or nxt[0] in seen:
            break
        order.append(nxt[0]); seen.add(nxt[0]); cur = nxt[0]
    for a in agents:
        if a["name"] not in seen:
            order.append(a["name"])

    agent_map = {a["name"]: a for a in agents}
    n = len(order)

    ARROW = " ──▶ "
    DONE  = " ──▶ ✓ DONE"
    try:
        term_w = os.get_terminal_size().columns
    except OSError:
        term_w = 80
    box_w  = max(14, (term_w - 8 - 7 * n) // n)
    stride = box_w + 2 + len(ARROW)

    box_data = []
    for name in order:
        a    = agent_map.get(name, {"name": name})
        desc = a.get("description", a.get("responsibility", ""))
        box_data.append({"name": name, "lines": _wrap(desc, box_w - 1)})

    max_desc = max(len(d["lines"]) for d in box_data)
    # +7: top/name/sep/footer_sep/model/mcp/bottom
    box_h    = max(max_desc + 7, 9)
    rendered = [
        _make_box(d["name"], d["lines"], box_w, box_h,
                  model=DEFAULT_MODEL, mcp=None)
        for d in box_data
    ]

    centers = [i * stride + (box_w + 2) // 2 for i in range(n)]
    line_w  = centers[-1] + (box_w + 2) // 2 + len(DONE) + 4
    mid_row = box_h // 2

    out = [
        "",
        color(f"  {team_name.upper()}  ·  {team_name.replace('Create ', '').title()} Flow",
              BOLD, CYAN),
        color("  " + "─" * min(70, n * stride + len(DONE)), DIM, WHITE),
        "",
    ]

    for row_idx in range(box_h):
        parts = ["  "]
        for i, box_lines in enumerate(rendered):
            parts.append(color(box_lines[row_idx], WHITE))
            if i < n - 1:
                parts.append(color(ARROW, GOLD) if row_idx == mid_row else " " * len(ARROW))
            elif row_idx == mid_row:
                parts.append(color(DONE, BOLD, GOLD))
        out.append("".join(parts))

    fail_pairs = []
    for i, name in enumerate(order):
        for target in fails.get(name, []):
            if target in order:
                j = order.index(target)
                if j < i:
                    fail_pairs.append((i, j))

    fail_pairs.sort(key=lambda p: p[0] - p[1])
    for src_i, tgt_i in fail_pairs:
        r1, r2 = _fail_arrow_rows(centers[src_i], centers[tgt_i], line_w)
        out.append(color("  " + r1, GOLD))
        out.append(color("  " + r2, GOLD))

    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# wizard.yaml helpers
# ---------------------------------------------------------------------------

# Stream mode maintains; it does not build. Every thought serves one of two
# axes: PRIMARY — keep the AI orchestration system healthy; SECONDARY — maintain
# the codebase via refactors and bug fixes (never new features).
DEFAULT_STREAM_THOUGHTS = [
    # Primary axis — the AI orchestration system.
    {
        "priority": 1,
        "id": "orch_drift",
        "prompt": (
            "PRIMARY FOCUS — the AI orchestration system. Review the latest code "
            "and git diff against the current orchestration. Has the codebase "
            "changed in a way that the agent team should change to stay aligned? "
            "Are any subagents now missing, overlapping, or mis-scoped? Propose "
            "orchestration updates only — do not propose new product features."
        ),
    },
    {
        "priority": 2,
        "id": "mcp_optimization",
        "prompt": (
            "PRIMARY FOCUS — the AI orchestration system. Look across the "
            "orchestration's subagents. Do several of them run the same command "
            "(build, test, lint, deploy, a query) or repeat the same multi-step "
            "procedure? If so, that command is a candidate to become a dedicated "
            "MCP tool the subagents call directly. If you find a good one, build "
            "the MCP tool and update the subagents that used the raw command to "
            "use it instead."
        ),
    },
    # Secondary axis — codebase maintenance (refactor + fix, never new features).
    {
        "priority": 3,
        "id": "bug_scan",
        "prompt": (
            "SECONDARY FOCUS — codebase maintenance. Are there any bugs in the "
            "codebase that should be surfaced as a proposed fix? Maintenance only "
            "— do not propose new features."
        ),
    },
    {
        "priority": 4,
        "id": "code_health",
        "prompt": (
            "SECONDARY FOCUS — codebase maintenance. Is there old, duplicated, "
            "dead, or overly complex code worth refactoring for maintainability "
            "and simplicity? Propose maintenance refactors only — never new "
            "features, and do not add complexity."
        ),
    },
    # Meta — let stream mode improve its own thoughts.
    {
        "priority": 5,
        "id": "stream_self_improve",
        "prompt": (
            "Review the thoughts currently saved in stream mode (wizard.yaml). "
            "Are any improvements, new thoughts, or refactorings needed to better "
            "keep the orchestration healthy and the codebase maintained — without "
            "unnecessarily adding complexity? If so, return the revised thought "
            "list as a JSON code block."
        ),
    },
]


def find_wizard_yaml():
    path = os.path.join(os.getcwd(), "wizard.yaml")
    return path if os.path.isfile(path) else None


def load_wizard_yaml():
    path = find_wizard_yaml()
    if not path:
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save_wizard_yaml_data(data):
    path = os.path.join(os.getcwd(), "wizard.yaml")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def ensure_wizard_config(model):
    """Make sure wizard.yaml holds the wizard's own config (settings + stream).

    Orchestration data is never written here — it lives in the AI's own store
    (.claude/ for Claude). Any legacy orchestrations are stripped out.
    """
    data = load_wizard_yaml()
    data.setdefault("settings", {"default_model": model})
    data.setdefault("stream", {"thoughts": DEFAULT_STREAM_THOUGHTS})
    data.pop("orchestrations", None)
    save_wizard_yaml_data(data)


# ---------------------------------------------------------------------------
# Orchestration store — Claude-native (.claude/), the source of truth
# ---------------------------------------------------------------------------

def _claude_dir():
    return os.path.join(os.getcwd(), ".claude")


def _agents_dir():
    return os.path.join(_claude_dir(), "agents")


def _manifest_path():
    return os.path.join(_claude_dir(), "wizard-orch.json")


def _slug(name):
    """Turn an agent name into a Claude subagent slug (lowercase, hyphens)."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "agent"


def _team_folder(team_label):
    """Folder grouping a team's subagents, e.g. 'Dev Team' -> 'dev_team'."""
    base = re.sub(r"[^a-z0-9]+", "_", team_label.lower()).strip("_") or "team"
    return base if base.endswith("_team") else base + "_team"


def _norm_transitions(transitions):
    """Normalize transition endpoints to agent slugs (or 'done')."""
    out = []
    for t in transitions:
        to = str(t.get("to", ""))
        out.append({
            "from": _slug(t.get("from", "")),
            "to": "done" if to.lower() in ("done", "end") else _slug(to),
            "condition": t.get("condition", "always"),
        })
    return out


def _flow_order(names, transitions):
    """Best-effort linear order of agent slugs from the (normalized) transitions."""
    if not names:
        return []
    fwd = {}
    for t in transitions:
        if t["condition"] != "fail" and t["from"] not in fwd:
            fwd[t["from"]] = t["to"]
    start = names[0]
    order, seen, cur = [start], {start}, start
    for _ in range(len(names)):
        nxt = fwd.get(cur)
        if not nxt or nxt == "done" or nxt in seen:
            break
        order.append(nxt); seen.add(nxt); cur = nxt
    for n in names:
        if n not in seen:
            order.append(n); seen.add(n)
    return order


def _state_machine_server(server_name, team_label, start, transitions):
    """Generate a FastMCP state-machine server that drives delegation order.

    The transition table is embedded; runtime progress is persisted to a
    state.json beside the server. The orchestrator subagent calls its tools
    (get_current_state / report_result) so delegation follows the plan instead
    of being guessed.
    """
    table = {}
    for t in transitions:
        table.setdefault(t["from"], {})[t["condition"]] = t["to"]
    header = (
        "#!/usr/bin/env python3\n"
        f'"""Generated by White Wizard — state machine for the {team_label}.\n\n'
        "The orchestrator subagent calls these tools to delegate in the right\n"
        "order. Requires the `mcp` package (pip install mcp).\n"
        '"""\n'
        "import json, os\n"
        "from mcp.server.fastmcp import FastMCP\n\n"
        f"START = {start!r}\n"
        f"TABLE = {table!r}\n"
        f"mcp = FastMCP({server_name!r})\n"
    )
    body = '''
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")


def _load():
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"current": START, "history": []}


def _save(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


@mcp.tool()
def get_current_state() -> dict:
    """Return {"current": <subagent or 'done'>, "history": [...]} — whose turn it is."""
    return _load()


@mcp.tool()
def report_result(agent: str, status: str) -> str:
    """Record a subagent's result ('pass' or 'fail') and advance to the next state."""
    state = _load()
    edges = TABLE.get(agent, {})
    nxt = edges.get(status) or edges.get("always") or "done"
    state["history"].append({"agent": agent, "status": status, "next": nxt})
    state["current"] = nxt
    _save(state)
    return "Orchestration complete." if nxt == "done" else "Next state: " + nxt


@mcp.tool()
def reset() -> str:
    """Reset the machine to the start state."""
    _save({"current": START, "history": []})
    return "Reset to " + START


if __name__ == "__main__":
    mcp.run()
'''
    return header + body


def _orchestrator_prompt(team_label, agents, transitions, server_name):
    """Build the lead orchestrator subagent's system prompt from the flow."""
    by_name = {a["name"]: a for a in agents}
    order   = _flow_order([a["name"] for a in agents], transitions)
    lines = [
        f"You are the orchestrator for the {team_label}: a team of Claude "
        "subagents. You coordinate the team and pass results between members; "
        "you do not do their work yourself.",
        "",
        "The team members and their jobs:",
        "",
    ]
    for i, name in enumerate(order, start=1):
        lines.append(f"{i}. **{name}** — {by_name.get(name, {}).get('description', '')}")
    lines += [
        "",
        f"Use the `{server_name}` MCP state-machine tool to drive delegation in "
        "the correct order — do not guess the sequence:",
        "",
        "1. Call `get_current_state` to see which subagent is up (the `current` field).",
        "2. Delegate that step to that subagent and collect its result.",
        "3. Call `report_result(agent, status)` with status `pass` or `fail`; it "
        "returns the next state.",
        "4. Repeat until the state machine reports `done`.",
        "",
        "Transitions (also embedded in the state machine):",
    ]
    for t in transitions:
        lines.append(f"- {t['from']} → {t['to']} on {t['condition']}")
    lines += ["", "Keep it simple — follow the state machine, add no extra steps."]
    return "\n".join(lines)


def _write_subagent(dir_path, name, description, tools, model, body):
    """Write one .claude/agents subagent Markdown file (frontmatter + prompt)."""
    front = ["---", f"name: {name}", f"description: {description}"]
    if tools:
        front.append("tools: " + ", ".join(tools))
    front += [f"model: {model}", "---"]
    with open(os.path.join(dir_path, name + ".md"), "w") as f:
        f.write("\n".join(front) + "\n\n" + body.rstrip() + "\n")


def write_orchestration(plan, team_selection, model):
    """Write the orchestration to its Claude-native store under .claude/.

    For Claude, the team's subagents are written as Markdown files under
    .claude/agents/<team>_team/ — one specialist per agent plus a lead
    orchestrator that coordinates the flow — and a manifest
    (.claude/wizard-orch.json) records the agents, transitions, and provenance.
    This store, not wizard.yaml, is the source of truth. Returns the team dir.
    """
    team_label  = team_selection.replace("Create ", "").title()
    team_folder = _team_folder(team_label)
    transitions = _norm_transitions(plan.get("transitions", []))

    agents = [
        {
            "name": _slug(a["name"]),
            "description": a.get("description", a.get("responsibility", "")),
            "tools": a.get("tools") or [],
            "prompt": (a.get("prompt") or "").strip(),
        }
        for a in plan.get("agents", [])
    ]

    team_dir = os.path.join(_agents_dir(), team_folder)
    os.makedirs(team_dir, exist_ok=True)

    for a in agents:
        body = a["prompt"] or f"You are the {a['name']} subagent.\n\n{a['description']}\n"
        _write_subagent(team_dir, a["name"], a["description"], a["tools"], model, body)

    # State-machine MCP tool: drives delegation order for the orchestrator.
    base    = team_folder.replace("_", "-").removesuffix("-team")
    sm_name = base + "-state-machine"
    order   = _flow_order([a["name"] for a in agents], transitions)
    start   = order[0] if order else "done"
    sm_path = os.path.join(team_dir, "state_machine.py")
    with open(sm_path, "w") as f:
        f.write(_state_machine_server(sm_name, team_label, start, transitions))
    sm_rel = os.path.relpath(sm_path, os.getcwd())
    _register_mcp_server(sm_name, ["python3", sm_rel])

    orch_name = base + "-orchestrator"
    _write_subagent(
        team_dir, orch_name,
        f"Coordinates the {team_label} workflow. Use to run the full {team_label} end to end.",
        [], model,
        _orchestrator_prompt(team_label, agents, transitions, sm_name),
    )

    manifest = {
        "created_at": datetime.date.today().isoformat(),
        "team": team_label,
        "team_folder": team_folder,
        "model": model,
        "orchestrator": orch_name,
        "agents": [
            {"name": a["name"], "description": a["description"], "model": model}
            for a in agents
        ],
        "transitions": transitions,
        "mcp_tools": [sm_rel],
        "mcp_servers": [sm_name],
    }
    with open(_manifest_path(), "w") as f:
        json.dump(manifest, f, indent=2)

    ensure_wizard_config(model)
    return team_dir


def load_orchestration():
    """Load the current orchestration from .claude/, or None.

    .claude/wizard-orch.json is the source of truth. Returns a dict shaped like
    the old wizard.yaml orchestration entry, so callers stay unchanged.
    """
    path = _manifest_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            orch = json.load(f)
    except Exception:
        return None
    if not isinstance(orch, dict) or not orch.get("agents"):
        return None
    return orch


# ---------------------------------------------------------------------------
# Wipe the orchestration system
# ---------------------------------------------------------------------------

def _remove_path(path):
    """Delete a file or directory tree. Returns True if something was removed."""
    import shutil
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
        return True
    if os.path.exists(path) or os.path.islink(path):
        os.remove(path)
        return True
    return False


def wipe_orchestration():
    """Delete the orchestration the wizard produced. DESTRUCTIVE, no undo.

    For Claude the orchestration is the team's subagent folder(s) under
    .claude/agents/<team>_team/ plus the manifest (.claude/wizard-orch.json) and
    any MCP tools the wizard generated. The wizard's own config (wizard.yaml
    settings + stream thoughts) is preserved.
    """
    import glob
    cwd  = os.getcwd()
    orch = load_orchestration() or {}

    targets = list(glob.glob(os.path.join(_agents_dir(), "*_team")))
    targets.append(_manifest_path())
    for rel in orch.get("mcp_tools", []):
        targets.append(os.path.join(cwd, rel))

    removed = [os.path.relpath(p, cwd) for p in dict.fromkeys(targets) if _remove_path(p)]

    # Unregister the wizard's MCP servers without clobbering any the user added.
    if _unregister_mcp_servers(orch.get("mcp_servers", [])):
        removed.append(".mcp.json (entries removed)")

    # Strip any legacy orchestrations from wizard.yaml (now lives in .claude/).
    data = load_wizard_yaml()
    if data.get("orchestrations"):
        data.pop("orchestrations", None)
        save_wizard_yaml_data(data)
        removed.append("wizard.yaml orchestrations")

    return removed


def _confirm_and_wipe():
    """Confirm with a danger warning, then wipe. Returns True if anything was wiped."""
    confirmed = yes_no(
        "Wipe the orchestration system?\n\n"
        "  DANGER: this permanently deletes the orchestration the wizard\n"
        "  produced — the generated agent files and .claude/agents definitions.\n"
        "  This cannot be undone. Your wizard.yaml settings are kept.\n\n"
        "  Are you sure?"
    )
    if not confirmed:
        return False

    removed = wipe_orchestration()
    clear()
    show_header()
    if removed:
        print(color("  Orchestration system wiped.\n", BOLD, GOLD))
        for item in removed:
            print(color(f"    - {item}", DIM, WHITE))
    else:
        print(color("  Nothing to wipe — no orchestration system found.\n", DIM, WHITE))
    print()
    print(color("  Press any key to continue...", DIM, WHITE))
    read_key()
    return bool(removed)


# ---------------------------------------------------------------------------
# wizard.yaml / handle existing
# ---------------------------------------------------------------------------

def _show_wizard_response(response):
    """Render a Claude response in the wizard frame and wait for a keypress."""
    clear()
    show_header()
    print(color("  Wizard\n", BOLD, CYAN))
    print(color("  " + "─" * 50, DIM, WHITE))
    for line in response.splitlines():
        print(color("  " + line, WHITE))
    print()
    print(color("  Press any key to continue...", DIM, WHITE))
    read_key()


def _ask_and_show(query):
    """Send a free-form user query to Claude and display the reply."""
    response = conjure(ask, query, label="Consulting the wizard...", model=DEFAULT_MODEL)
    _show_wizard_response(response)


def _prompt_orchestration(user_prompt=None, task_mode=False):
    """Call Claude with the orchestration context and display the response."""
    orch      = load_orchestration() or {}
    orch_json = json.dumps(orch, indent=2)
    team      = orch.get("team", "dev")

    if task_mode and user_prompt:
        prompt = (
            f"You are the orchestrator for a multi-agent {team} team.\n\n"
            f"The user wants the team to: {user_prompt}\n\n"
            f"Based on the plan below, outline which agents should handle this task, "
            f"in what order, and what each agent should specifically do.\n\n"
            f"Orchestration plan:\n{orch_json}"
        )
    elif user_prompt:
        prompt = (
            "You are advising on a multi-agent orchestration built with White Wizard.\n\n"
            f"Orchestration plan:\n{orch_json}\n\n"
            f"User: {user_prompt}"
        )
    else:
        prompt = load_prompt("wizard_yaml.md").replace("{orch_json}", orch_json)

    response = conjure(ask, prompt, label="Consulting the wizard...", model=DEFAULT_MODEL)
    _show_wizard_response(response)


def handle_wizard_yaml(path):
    last = load_orchestration()
    team = last.get("team", "Dev") if last else "Dev"

    task_label = f"What do you want the {team} team to do?"
    options = [
        Option("task",   task_label, text_input=True, placeholder=task_label),
        Option("manage", "Manage the system"),
        Option("other",  "Something else", text_input=True,
               placeholder="Something else"),
        Option("stream", "Start streaming"),
        Option("wipe",   "Wipe the orchestration system"),
    ]

    def render_top():
        if last:
            agents  = last.get("agents", [])
            created = last.get("created_at", "")
            print(color(f"  {team} orchestration  ·  {created}\n", BOLD, GOLD))
            for a in agents:
                print(color(f"    · {a['name']}", DIM, WHITE))
        else:
            print(color("  No orchestration in .claude/ yet.", DIM, WHITE))
        print()

    while True:
        choice, text = menu(
            options,
            hint="(arrows · Enter to choose · q to quit)",
            render_top=render_top,
        )
        if choice is None:
            return None
        if choice == "stream":
            run_stream_mode()
        elif choice == "wipe":
            if _confirm_and_wipe():
                return None
        elif choice == "manage":
            _prompt_orchestration()
        elif choice == "task" and text:
            _prompt_orchestration(text, task_mode=True)
        elif choice == "other" and text:
            _prompt_orchestration(text)


# ---------------------------------------------------------------------------
# Workspace scan
# ---------------------------------------------------------------------------

CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
    ".html", ".css", ".scss", ".vue", ".svelte",
}
IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
               "dist", "build", ".next", "target", ".wizard"}


def scan_workspace():
    root   = os.getcwd()
    name   = os.path.basename(root.rstrip("/")) or root
    clear()
    show_header()

    spinner  = itertools.cycle("◆◈◇◈")
    files    = []
    walker   = os.walk(root)
    finished = False
    end      = time.time() + 1.6
    while time.time() < end or not finished:
        if not finished:
            try:
                dirpath, dirnames, filenames = next(walker)
                dirnames[:] = [d for d in dirnames
                               if d not in IGNORE_DIRS and not d.startswith(".")]
                files.extend(
                    os.path.relpath(os.path.join(dirpath, f), root)
                    for f in filenames
                    if os.path.splitext(f)[1].lower() in CODE_EXTS
                )
            except StopIteration:
                finished = True
        count = f"{len(files)} file{'s' if len(files) != 1 else ''}" if files else "0 files"
        sys.stdout.write(
            "\r" + color(f"  {next(spinner)} ", GOLD)
            + color(f"Scanning '{name}' for existing code... ", WHITE)
            + color(f"({count})", DIM, WHITE)
        )
        sys.stdout.flush()
        time.sleep(0.08)

    sys.stdout.write("\r" + " " * 64 + "\r")
    if files:
        plural = "s" if len(files) != 1 else ""
        print(color(f"  + Found {len(files)} code file{plural} in this workspace.", BOLD, GOLD))
    else:
        print(color("  + No code found - this looks like a fresh workspace.", BOLD, GOLD))
    time.sleep(0.9)
    return files


# ---------------------------------------------------------------------------
# Menu (arrow-key navigation)
# ---------------------------------------------------------------------------

CUSTOM_OPTION = "Something else"

OPTIONS = [
    "Create a web app orchestration system",
    "Create a UI/UX design mockup",
    CUSTOM_OPTION,
]


def select(options, title,
           hint="(arrows to move - type to fill in 'Something else' - Enter to choose - q to quit)"):
    """Pick one of ``options`` (a list of label strings).

    Returns the chosen label, the typed text for the custom option,
    "__stream__" for the stream shortcut, or None if cancelled.
    """
    opts = [
        Option(o, o,
               text_input=(o == CUSTOM_OPTION),
               disabled=(o in DISABLED_TYPES),
               placeholder=(o if o == CUSTOM_OPTION else ""))
        for o in options
    ]

    def footer():
        print(color("  " + "─" * 40, DIM, WHITE))
        print(color("  s  ·  Stream mode", DIM, WHITE))
        print()

    value, text = menu(opts, title, hint=hint, footer=footer,
                       extra_keys={"s": "__stream__", "S": "__stream__"})
    if value is None or value == "__stream__":
        return value
    if value == CUSTOM_OPTION:
        return text or "a custom orchestration system"
    return value


# ---------------------------------------------------------------------------
# Simple yes/no prompt for offers
# ---------------------------------------------------------------------------

def yes_no(question):
    """Display a two-option yes/no menu. Returns True for yes, False for no."""
    value, _ = menu(
        [Option(True, "Yes"), Option(False, "No")],
        title=question,
        hint="(arrows · Enter to choose)",
    )
    return value is True


# ---------------------------------------------------------------------------
# Loading / spinner
# ---------------------------------------------------------------------------

def loading(label, seconds=3.0):
    spinner = itertools.cycle("◆◈◇◈")
    end     = time.time() + seconds
    while time.time() < end:
        sys.stdout.write("\r" + color(f"  {next(spinner)} ", GOLD) + color(label, WHITE))
        sys.stdout.flush()
        time.sleep(0.09)
    sys.stdout.write("\r" + " " * (len(label) + 6) + "\r")
    sys.stdout.flush()


def conjure(fn, *args, label="Conjuring magic...", **kwargs):
    """Run fn(*args, **kwargs) in a background thread while showing a spinner."""
    import threading

    result = [None]
    error  = [None]
    done   = threading.Event()

    def worker():
        try:
            result[0] = fn(*args, **kwargs)
        except Exception as exc:
            error[0] = exc
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True).start()

    spinner = itertools.cycle("◆◈◇◈")
    while not done.is_set():
        sys.stdout.write("\r" + color(f"  {next(spinner)} ", GOLD) + color(label, WHITE))
        sys.stdout.flush()
        time.sleep(0.09)

    sys.stdout.write("\r" + " " * (len(label) + 6) + "\r")
    sys.stdout.flush()

    if error[0]:
        raise error[0]
    return result[0]


# ---------------------------------------------------------------------------
# AI-assisted planning
# ---------------------------------------------------------------------------

ORCH_TYPES = [
    "Create dev team",
    "Create devops team",
    "Create performance team",
    "Create security team",
    CUSTOM_OPTION,
]

DISABLED_TYPES = {
    "Create devops team",
    "Create performance team",
    "Create security team",
}

TEAM_PROMPTS = {
    "Create dev team":         "team_dev.md",
    "Create devops team":      "team_devops.md",
    "Create performance team": "team_performance.md",
    "Create security team":    "team_security.md",
}


def parse_generated_files(response):
    pattern = r'### FILE: ([^\n]+)\n```[a-zA-Z]*\n(.*?)```'
    return [(p.strip(), c.strip()) for p, c in re.findall(pattern, response, re.DOTALL)]


def _smoke_test(team_dir, orch):
    """Validate the freshly built orchestration. Returns (ok, [(label, passed, detail)])."""
    import ast
    checks = []
    agents = [a["name"] for a in orch.get("agents", [])]

    # 1. Every subagent (specialists + orchestrator) has a file with frontmatter.
    missing = []
    for name in agents + [orch.get("orchestrator", "")]:
        if not name:
            continue
        p = os.path.join(team_dir, name + ".md")
        if not os.path.isfile(p):
            missing.append(name)
            continue
        head = open(p).read()
        if not (head.lstrip().startswith("---") and "name:" in head and "description:" in head):
            missing.append(name + " (frontmatter)")
    checks.append(("Subagent files and frontmatter", not missing, ", ".join(missing)))

    # 2. The state-machine MCP tool is syntactically valid.
    sm = os.path.join(team_dir, "state_machine.py")
    sm_ok = os.path.isfile(sm)
    sm_detail = "" if sm_ok else "state_machine.py missing"
    if sm_ok:
        try:
            ast.parse(open(sm).read())
        except SyntaxError as exc:
            sm_ok, sm_detail = False, str(exc)
    checks.append(("State-machine tool valid", sm_ok, sm_detail))

    # 3. Transitions reference real agents (or 'done').
    known = set(agents) | {"done"}
    trans = orch.get("transitions", [])
    bad = [f"{t.get('from')}→{t.get('to')}"
           for t in trans
           if t.get("to") not in known or t.get("from") not in set(agents)]
    checks.append(("Transitions reference real agents", not bad, ", ".join(bad)))

    # 4. Following pass/always edges from the start reaches 'done'.
    fwd = {}
    for t in trans:
        if t.get("condition") in ("pass", "always") and t.get("from") not in fwd:
            fwd[t["from"]] = t.get("to")
    cur, seen, reached = (agents[0] if agents else "done"), set(), False
    for _ in range(len(agents) + 2):
        if cur == "done":
            reached = True
            break
        if cur in seen:
            break
        seen.add(cur)
        cur = fwd.get(cur)
        if cur is None:
            break
    checks.append(("Flow reaches 'done'", reached, "" if reached else f"stuck at {cur}"))

    # 5. The state-machine server is registered in .mcp.json.
    reg = False
    mp = os.path.join(os.getcwd(), ".mcp.json")
    if os.path.isfile(mp):
        try:
            with open(mp) as f:
                servers = json.load(f).get("mcpServers", {})
            reg = all(s in servers for s in orch.get("mcp_servers", []))
        except Exception:
            reg = False
    checks.append(("MCP server registered", reg, ""))

    return all(passed for _, passed, _ in checks), checks


def show_orchestration_created(team_selection, team_dir, smoke_ok=None, smoke_checks=None):
    """Show the Claude-native subagent files that were written, smoke-test results, and how to run."""
    clear()
    show_header()
    team_label = team_selection.replace("Create ", "").title()
    orch = load_orchestration() or {}
    rel  = os.path.relpath(team_dir, os.getcwd())

    print(color(f"  {team_label} — subagents created\n", BOLD, CYAN))
    print(color(f"  {rel}/", DIM, WHITE))
    for name in sorted(os.listdir(team_dir)):
        print(color(f"    {name}", WHITE))
    print()
    if smoke_checks is not None:
        print(color(f"  Smoke test {'passed' if smoke_ok else 'found issues'}:",
                    BOLD, GOLD if smoke_ok else GRAY))
        for label, passed, detail in smoke_checks:
            line = f"    {'✓' if passed else '✗'} {label}"
            if detail and not passed:
                line += f" — {detail}"
            print(color(line, WHITE if passed else GRAY))
        print()
    print(color("  To run your orchestration:", DIM, WHITE))
    print(color("    pip install mcp   # the state-machine tool needs it", DIM, WHITE))
    print(color("    open Claude in this project and invoke the orchestrator —", DIM, WHITE))
    print(color(f"    e.g. ask Claude to use the '{orch.get('orchestrator', 'orchestrator')}' subagent.\n",
                BOLD, GOLD))
    print(color("  The orchestrator delegates via the generated state-machine MCP tool,", DIM, WHITE))
    print(color("  so each subagent runs in the right order.\n", DIM, WHITE))
    print(color("  Press any key to continue...", DIM, WHITE))
    read_key()


def display_synopsis(synopsis):
    clear()
    show_header()
    print(color("  Project synopsis", BOLD, CYAN))
    print(color("  " + "-" * 50, DIM, WHITE))
    for line in synopsis.splitlines():
        print(color("  " + line, WHITE))
    print()


def run_team_conversation(team_selection, synopsis, custom_description=""):
    prompt_file     = TEAM_PROMPTS.get(team_selection, "team_custom.md")
    template        = load_prompt(prompt_file)
    if "{team_description}" in template:
        template = template.replace("{team_description}", custom_description)
    guidelines      = load_prompt("subagent_guidelines.md")
    initial_prompt  = (template + "\n" + guidelines
                       + "\n\n## Project context\n" + synopsis)

    # Generate the plan — auto-retry until it parses; the wizard builds it for
    # the user rather than asking for approval.
    history, current, plan, response = [], initial_prompt, None, ""
    for _ in range(4):
        response = conjure(ask_with_history, history, current,
                           label="Conjuring the orchestration...", model=DEFAULT_MODEL)
        history.append(("user", current))
        history.append(("assistant", response))
        plan = parse_agent_plan(response)
        if plan:
            break
        current = ("Please return the agent plan now as a single JSON code block "
                   "in the required format, with no other text.")

    if not plan:
        clear()
        show_header()
        print(color("  The wizard could not produce an orchestration plan.\n", BOLD, GOLD))
        for line in response.splitlines():
            print(color("  " + line, WHITE))
        print()
        print(color("  Press any key to continue...", DIM, WHITE))
        read_key()
        return None

    # Show the orchestration, then build it for them.
    clear()
    show_header()
    print(render_infographic(plan, team_name=team_selection))
    print()
    loading("Building the AI orchestration system...", seconds=2.0)
    team_dir = write_orchestration(plan, team_selection, DEFAULT_MODEL)

    # Smoke-test the freshly built orchestration.
    clear()
    show_header()
    print(color(f"  {team_selection.replace('Create ', '').title()} — orchestration built\n",
                BOLD, CYAN))
    loading("Running a smoke test on the orchestration...", seconds=1.6)
    ok, checks = _smoke_test(team_dir, load_orchestration() or {})

    show_orchestration_created(team_selection, team_dir, ok, checks)
    return "done"


def plan_with_ai(files):
    prompt  = load_prompt("analyze_codebase.md") + "\n".join(files[:60])
    synopsis = conjure(ask, prompt, label="Analysing codebase...", model=DEFAULT_MODEL)

    display_synopsis(synopsis)
    time.sleep(0.6)

    while True:
        team = select(ORCH_TYPES, "Which team do you want to set up?")
        if team is None:
            return None
        if team == "__stream__":
            run_stream_mode()
            continue
        custom_desc = team if team not in TEAM_PROMPTS and team != CUSTOM_OPTION else ""
        result = run_team_conversation(team, synopsis, custom_description=custom_desc)
        if result is not None:
            return result


# ---------------------------------------------------------------------------
# Stream mode
# ---------------------------------------------------------------------------

def _git_diff():
    try:
        r = subprocess.run(["git", "diff"], capture_output=True, text=True, cwd=os.getcwd())
        return r.stdout.strip() or "(no unstaged changes)"
    except Exception:
        return "(git not available)"


def _current_commit():
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                           cwd=os.getcwd())
        return r.stdout.strip() or None
    except Exception:
        return None


def _stream_approval(thought, response):
    """Three-option menu: Approve / Skip / Quit. Returns 'approve'|'skip'|'quit'."""
    content_lines = [
        "",
        color(f"  Stream · {thought['id']}  (priority {thought['priority']})", BOLD, CYAN),
        color("  " + "─" * 50, DIM, WHITE),
        "",
    ]
    for line in response.splitlines():
        content_lines.append(color("  " + line, WHITE))
    content_lines.append("")
    content = "\n".join(content_lines)

    value, _ = menu(
        [Option("approve", "Approve finding"),
         Option("skip",    "Skip"),
         Option("quit",    "Quit stream")],
        title="What would you like to do with this finding?",
        hint="(arrows · Enter to choose)",
        render_top=lambda: print(content),
    )
    return value or "quit"


def _valid_thought(q):
    """A stream thought must have a non-empty id/prompt and an int priority."""
    return (
        isinstance(q, dict)
        and isinstance(q.get("id"), str) and q["id"].strip()
        and isinstance(q.get("prompt"), str) and q["prompt"].strip()
        and isinstance(q.get("priority"), int) and not isinstance(q["priority"], bool)
    )


def _try_update_stream_thoughts(response, data):
    """If Claude returned a valid revised thought list, write it back to wizard.yaml.

    The whole list is rejected if any item is malformed, so a bad AI response
    can never leave the persisted thoughts in a half-broken state.
    """
    m = re.search(r'```json\s*(\[.*?\])\s*```', response, re.DOTALL)
    if not m:
        return
    try:
        new_thoughts = json.loads(m.group(1))
    except Exception:
        return
    if not (isinstance(new_thoughts, list) and new_thoughts):
        return
    if not all(_valid_thought(q) for q in new_thoughts):
        return
    cleaned = [
        {"priority": q["priority"], "id": q["id"], "prompt": q["prompt"]}
        for q in new_thoughts
    ]
    data.setdefault("stream", {}).pop("questions", None)
    data.setdefault("stream", {})["thoughts"] = cleaned
    save_wizard_yaml_data(data)
    print(color("\n  Stream thoughts updated in wizard.yaml.\n", BOLD, GOLD))


def _register_mcp_server(server_name, command):
    """Add an MCP server to the project's .mcp.json (merge, don't clobber)."""
    path   = os.path.join(os.getcwd(), ".mcp.json")
    config = {}
    if os.path.isfile(path):
        try:
            with open(path) as f:
                config = json.load(f)
        except Exception:
            config = {}
    config.setdefault("mcpServers", {})[server_name] = {
        "command": command[0], "args": command[1:],
    }
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def _unregister_mcp_servers(names):
    """Remove named servers from .mcp.json; delete the file if it ends up empty.

    Returns True if .mcp.json changed.
    """
    path = os.path.join(os.getcwd(), ".mcp.json")
    if not names or not os.path.isfile(path):
        return False
    try:
        with open(path) as f:
            config = json.load(f)
    except Exception:
        return False
    servers = config.get("mcpServers", {})
    changed = False
    for n in names:
        if n in servers:
            del servers[n]
            changed = True
    if not changed:
        return False
    if servers:
        with open(path, "w") as f:
            json.dump(config, f, indent=2)
    else:
        _remove_path(path)
    return True


def _append_to_subagent(team_folder, agent_slug, note):
    """Append an MCP-tool usage note to a subagent file. Returns True if updated."""
    path = os.path.join(_agents_dir(), team_folder, agent_slug + ".md")
    if not os.path.isfile(path):
        return False
    with open(path, "a") as f:
        f.write(f"\n## MCP tools\n- {note}\n")
    return True


def _try_build_mcp_tool(answer, orch):
    """Build an MCP tool the stream proposed and wire the subagents to it.

    Expects generated ### FILE: blocks (the MCP server) plus a JSON object with
    server_name, command (argv list), agents (subagent names to update), and an
    optional tool_note. Writes the file(s), registers the server in .mcp.json,
    appends the note to each named subagent, and records the artifacts in the
    manifest so wipe cleans them up. No-ops on an incomplete proposal.
    """
    files = parse_generated_files(answer)
    m     = re.search(r'```json\s*(\{.*?\})\s*```', answer, re.DOTALL)
    if not files or not m:
        return
    try:
        spec = json.loads(m.group(1))
    except Exception:
        return
    server_name = spec.get("server_name")
    command     = spec.get("command")
    if not (server_name and isinstance(command, list) and command):
        return

    cwd, written = os.getcwd(), []
    for rel_path, code in files:
        full = os.path.join(cwd, rel_path)
        if os.path.dirname(full):
            os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(code + "\n")
        written.append(rel_path)

    _register_mcp_server(server_name, command)

    note        = spec.get("tool_note") or f"Use the {server_name} MCP tool."
    team_folder = orch.get("team_folder", "")
    updated = [a for a in spec.get("agents", [])
               if _append_to_subagent(team_folder, _slug(a), note)]

    # Record generated artifacts in the manifest so wipe removes them.
    path = _manifest_path()
    if os.path.isfile(path):
        try:
            with open(path) as f:
                manifest = json.load(f)
            manifest.setdefault("mcp_tools", []).extend(written)
            manifest.setdefault("mcp_servers", []).append(server_name)
            with open(path, "w") as f:
                json.dump(manifest, f, indent=2)
        except Exception:
            pass

    print(color(f"\n  Built MCP tool '{server_name}' ({len(written)} file(s)); "
                f"updated {len(updated)} subagent(s).\n", BOLD, GOLD))


def _build_thought_prompt(template, q, git_diff, orch_plan, last_commit, current_commit):
    return (template
            .replace("{thought_prompt}", q.get("prompt", ""))
            .replace("{git_diff}", git_diff[:4000])
            .replace("{orch_plan}", orch_plan[:2000])
            .replace("{last_commit}", last_commit or "none")
            .replace("{current_commit}", current_commit or "unknown"))


def _wizard_route(answer, current_q, remaining):
    """ROUTE state: the White Wizard reads an agent's answer and decides
    (a) whether it is an actionable finding worth surfacing to the user, and
    (b) which remaining thought to visit next ('done' to finish).

    Returns (actionable: bool, next_id: str). Defaults to surfacing the finding
    when the wizard's reply can't be parsed, so nothing is silently hidden.
    """
    default_next = remaining[0]["id"] if remaining else "done"
    menu = "\n".join(f'  - {q["id"]}: {q.get("prompt", "")}' for q in remaining) or "  (none)"
    prompt = (
        "You are the White Wizard, the router in a two-state stream-mode state "
        "machine. An agent has just answered a review thought. Do two things:\n"
        "1. Decide whether the answer is an ACTIONABLE finding (a real issue, a "
        "needed change, or a concrete recommendation) or a NO-OP (no issues, "
        "nothing to do).\n"
        "2. Choose which remaining thought to visit next, or finish.\n\n"
        f"Thought answered ({current_q.get('id')}): {current_q.get('prompt', '')}\n\n"
        f"Agent's answer:\n{answer}\n\n"
        f"Remaining thoughts:\n{menu}\n\n"
        "Reply with ONLY a JSON object, no prose:\n"
        '{"actionable": true or false, "next": "<thought id, or done>"}'
    )
    decision = conjure(ask, prompt, label="Wizard routing...", model=DEFAULT_MODEL)

    actionable, nxt = True, default_next
    m = re.search(r"\{.*\}", decision, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            actionable = bool(obj.get("actionable", True))
            nxt = re.sub(r"[^a-z0-9_]", "", str(obj.get("next", default_next)).lower()) or "done"
        except Exception:
            pass
    return actionable, nxt


def _load_stream_thoughts(data):
    """Read stream thoughts from wizard.yaml, accepting the legacy 'questions' key."""
    stream = data.get("stream", {})
    raw = stream.get("thoughts")
    if raw is None:
        raw = stream.get("questions", DEFAULT_STREAM_THOUGHTS)
    thoughts = [q for q in raw if _valid_thought(q)]
    return thoughts or list(DEFAULT_STREAM_THOUGHTS)


def run_stream_mode():
    """Run stream mode as a two-state machine: an agent ANSWERs the current
    thought, then the White Wizard ROUTEs to the next thought (or finishes)."""
    clear()
    show_header()

    yaml_path = find_wizard_yaml()
    if not yaml_path:
        print(color("  No wizard.yaml found.\n", BOLD, GOLD))
        print(color("  Run `wizard` first to set up an orchestration.\n", DIM, WHITE))
        return

    data     = load_wizard_yaml()
    thoughts = _load_stream_thoughts(data)
    thoughts.sort(key=lambda q: q["priority"])
    by_id     = {q["id"]: q for q in thoughts}
    orch_plan = json.dumps(load_orchestration() or {}, indent=2)

    git_diff      = _git_diff()
    current_commit = _current_commit()
    last_commit    = wizard_db.get_last_scanned_commit()

    stats = wizard_db.get_stream_stats()
    print(color("  Stream mode\n", BOLD, CYAN))
    print(color(f"  Thoughts  : {len(thoughts)}", WHITE))
    print(color(f"  Last run  : {stats['last_run_at'] or 'never'}", WHITE))
    print(color(f"  Last commit scanned: {stats['last_commit'] or 'none'}\n", WHITE))
    time.sleep(1.2)

    run_id   = wizard_db.start_stream_run(current_commit, git_diff)
    template = load_prompt("stream_thought.md")

    current = thoughts[0]
    visited = set()

    while current and current["id"] not in visited:
        # STATE 1 — ANSWER: an agent answers the current thought.
        prompt = _build_thought_prompt(template, current, git_diff, orch_plan,
                                       last_commit, current_commit)
        answer = conjure(ask, prompt,
                         label=f"Stream [{current['id']}]...", model=DEFAULT_MODEL)

        # STATE 2 — ROUTE: the White Wizard judges the answer and picks next.
        remaining = [q for q in thoughts
                     if q["id"] not in visited and q["id"] != current["id"]]
        actionable, nxt = _wizard_route(answer, current, remaining)

        if actionable:
            action = _stream_approval(current, answer)
            if action == "approve":
                wizard_db.save_finding(run_id, current["id"], current["priority"], answer, approved=True)
                if current["id"] == "stream_self_improve":
                    _try_update_stream_thoughts(answer, data)
                elif current["id"] == "mcp_optimization":
                    _try_build_mcp_tool(answer, load_orchestration() or {})
            elif action == "skip":
                wizard_db.save_finding(run_id, current["id"], current["priority"], answer, approved=False)
            elif action == "quit":
                wizard_db.finish_stream_run(run_id, current_commit)
                print(color("\n  Stream stopped.\n", DIM, WHITE))
                return
        else:
            # NO-OP: the wizard auto-dismisses the finding without prompting.
            wizard_db.save_finding(run_id, current["id"], current["priority"], answer, approved=False)
            clear()
            show_header()
            print(color(f"  Stream · {current['id']}  (priority {current['priority']})", BOLD, CYAN))
            print(color("  " + "─" * 50, DIM, WHITE))
            print(color(f"\n  {answer}\n", DIM, WHITE))
            print(color("  (no action needed — continuing)\n", GRAY))
            time.sleep(1.5)

        visited.add(current["id"])

        if nxt == "done" or not remaining:
            break
        candidate = by_id.get(nxt)
        current   = candidate if (candidate and nxt not in visited) else remaining[0]

    wizard_db.finish_stream_run(run_id, current_commit)
    clear()
    show_header()
    print(color("  Stream complete.\n", BOLD, GOLD))
    print(color(f"  {len(visited)} thought(s) processed.\n", DIM, WHITE))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    wizard_yaml = find_wizard_yaml()
    if wizard_yaml:
        result = handle_wizard_yaml(wizard_yaml)
        if result is None:
            print(color("\n  The staff dims. Farewell.\n", DIM, WHITE))
        return

    files = scan_workspace()
    if files:
        selection = plan_with_ai(files)
    else:
        while True:
            selection = select(OPTIONS, "What shall we summon today?")
            if selection == "__stream__":
                run_stream_mode()
                continue
            if selection is not None and selection not in OPTIONS:
                _ask_and_show(selection)
                continue
            break

    if selection is None:
        print(color("\n  The staff dims. Farewell.\n", DIM, WHITE))
        return

    if selection == "done":
        wizard_yaml = find_wizard_yaml()
        if wizard_yaml:
            result = handle_wizard_yaml(wizard_yaml)
            if result is None:
                print(color("\n  The staff dims. Farewell.\n", DIM, WHITE))
    else:
        print(color("\n  The staff dims. Farewell.\n", DIM, WHITE))
