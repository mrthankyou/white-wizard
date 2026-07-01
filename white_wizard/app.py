#!/usr/bin/env python3
"""White Wizard — conjure multi-agent orchestration systems with a single button."""

import datetime
import importlib.resources
import itertools
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import time

from white_wizard.ai_client import ask, ask_with_history, kill_current, run_task
from white_wizard import db as wizard_db
from white_wizard.ui import (
    BOLD, DIM, WHITE, GOLD, CYAN, GRAY, DANGER, BRIGHT_BLUE,
    color, clear, show_header, show_header_compact, read_key, Option, menu, select_menu,
    SPINNER_FRAMES, run_split_pane,
)

# ---------------------------------------------------------------------------
# Constants (terminal primitives + the menu engine live in white_wizard.ui)
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-5"

# Keep the build/infographic view up at least this long so it can be read.
MIN_BUILD_DISPLAY_SECONDS = 15


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

def _is_agent_plan(obj):
    """True if obj is a dict with a non-empty list of named agent dicts."""
    return (isinstance(obj, dict)
            and isinstance(obj.get("agents"), list)
            and obj["agents"]
            and all(isinstance(a, dict) and a.get("name") for a in obj["agents"]))


def parse_agent_plan(response):
    """Parse a valid agent plan from Claude's response, or return None.

    Only a dict with a non-empty ``agents`` list of named agent dicts counts as
    a plan. A bare JSON array/string/number — or agents missing a name — returns
    None so the caller treats it as "not a plan yet" instead of crashing.

    Tries the full response as JSON first; then scans for a brace-delimited
    object using raw_decode, which handles nested ``` fences inside agent
    prompts, surrounding prose, and trailing text.
    """
    candidates = [response.strip()]
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except Exception:
            continue
        if _is_agent_plan(obj):
            return obj

    # Fallback: let the JSON decoder find an object starting at each '{'. This
    # parses the full object regardless of nested ``` or extra surrounding text.
    decoder = json.JSONDecoder()
    idx = response.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(response, idx)
        except ValueError:
            obj = None
        if _is_agent_plan(obj):
            return obj
        idx = response.find("{", idx + 1)
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
    """Make sure wizard.yaml holds the wizard's own settings config.

    Orchestration data is never written here — it lives in the AI's own store
    (.claude/ for Claude). Any legacy orchestrations are stripped out.
    """
    data = load_wizard_yaml()
    data.setdefault("settings", {"default_model": model})
    data.pop("orchestrations", None)
    save_wizard_yaml_data(data)


def _auto_commit_enabled():
    """Whether completed tasks auto-commit their changes (wizard.yaml setting,
    default on)."""
    return bool(load_wizard_yaml().get("settings", {}).get("auto_commit", True))


def _set_auto_commit(enabled):
    """Persist the auto-commit preference to wizard.yaml settings."""
    data = load_wizard_yaml()
    data.setdefault("settings", {})["auto_commit"] = bool(enabled)
    save_wizard_yaml_data(data)


def _auto_fix_enabled():
    """Whether the stream autonomously fixes scan findings (wizard.yaml setting,
    default OFF). When off, findings are 'suggested' and run only on demand."""
    return bool(load_wizard_yaml().get("settings", {}).get("auto_fix", False))


def _set_auto_fix(enabled):
    """Persist the autonomous auto-fix preference to wizard.yaml settings."""
    data = load_wizard_yaml()
    data.setdefault("settings", {})["auto_fix"] = bool(enabled)
    save_wizard_yaml_data(data)


# ---------------------------------------------------------------------------
# Alert queue — lets one action push a message that the next menu screen shows
# ---------------------------------------------------------------------------

_pending_alerts: list = []


def push_alert(msg: str) -> None:
    _pending_alerts.append(msg)


def pop_alerts() -> list:
    result = list(_pending_alerts)
    _pending_alerts.clear()
    return result


# ---------------------------------------------------------------------------
# Activity log — a persistent trace of the task queue / stream-worker lifecycle
# under .wizard/stream.log (gitignored), for debugging the background system.
# ---------------------------------------------------------------------------

_stream_logger = None


def _stream_log(msg: str) -> None:
    """Append a timestamped line to .wizard/stream.log. Thread-safe and
    best-effort — logging never interferes with the work it records. Always-on,
    so the handler rotates at 2 MB (3 backups kept) to stay bounded."""
    global _stream_logger
    try:
        if _stream_logger is None:
            wizard_dir = os.path.join(os.getcwd(), ".wizard")
            os.makedirs(wizard_dir, exist_ok=True)
            logger = logging.getLogger("white_wizard.stream")
            logger.setLevel(logging.INFO)
            logger.propagate = False
            if not logger.handlers:
                handler = logging.handlers.RotatingFileHandler(
                    os.path.join(wizard_dir, "stream.log"),
                    maxBytes=2_000_000, backupCount=3, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                logger.addHandler(handler)
            _stream_logger = logger
        _stream_logger.info(msg)
    except Exception:
        pass


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
    # Run the server with the same interpreter the wizard uses — that's where
    # `mcp` gets installed (_ensure_mcp), so the tool works without the user
    # having to fix their python3/PATH.
    _register_mcp_server(sm_name, [sys.executable, sm_rel])

    orch_name = base + "-orchestrator"
    _write_subagent(
        team_dir, orch_name,
        f"Coordinates the {team_label} workflow. Use to run the full {team_label} end to end.",
        [], model,
        _orchestrator_prompt(team_label, agents, transitions, sm_name),
    )

    orch = {
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
    _save_orchestration(orch)  # merge into the manifest and make it active

    ensure_wizard_config(model)
    return team_dir


def _read_manifest():
    """Read .claude/wizard-orch.json normalized to {"active", "orchestrations"}.

    Tolerates the legacy single-orchestration shape (a bare orch dict) and a
    missing/corrupt file, so older projects keep working.
    """
    path = _manifest_path()
    if not os.path.isfile(path):
        return {"active": None, "orchestrations": []}
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        return {"active": None, "orchestrations": []}
    if isinstance(data, dict) and isinstance(data.get("orchestrations"), list):
        orchs = [o for o in data["orchestrations"] if isinstance(o, dict) and o.get("agents")]
        return {"active": data.get("active"), "orchestrations": orchs}
    if isinstance(data, dict) and data.get("agents"):          # legacy single orch
        return {"active": data.get("team_folder"), "orchestrations": [data]}
    return {"active": None, "orchestrations": []}


def _write_manifest(manifest):
    with open(_manifest_path(), "w") as f:
        json.dump(manifest, f, indent=2)


def list_orchestrations():
    """Every orchestration the wizard has built, in creation order."""
    return _read_manifest()["orchestrations"]


def load_orchestration():
    """The active orchestration dict, or None.

    The returned shape matches the old manifest entry, so existing callers
    (stream mode, prompts, smoke test) stay unchanged — they just operate on
    whichever orchestration is currently active.
    """
    m = _read_manifest()
    orchs = m["orchestrations"]
    if not orchs:
        return None
    for o in orchs:
        if o.get("team_folder") == m["active"]:
            return o
    return orchs[0]


def _save_orchestration(orch):
    """Insert/replace orch in the manifest (keyed by team_folder) and activate it."""
    m = _read_manifest()
    folder = orch.get("team_folder")
    others = [o for o in m["orchestrations"] if o.get("team_folder") != folder]
    _write_manifest({"active": folder, "orchestrations": others + [orch]})


def set_active_orchestration(team_folder):
    """Mark team_folder as the active orchestration, if it exists."""
    m = _read_manifest()
    if any(o.get("team_folder") == team_folder for o in m["orchestrations"]):
        m["active"] = team_folder
        _write_manifest(m)


def _ordered_agent_names(orch):
    """Subagent slugs in order of operations, derived from the transitions."""
    names = [a.get("name") for a in orch.get("agents", []) if a.get("name")]
    return _flow_order(names, orch.get("transitions", []))


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


def wipe_orchestration(team_folder=None):
    """Delete one orchestration (the active one by default). DESTRUCTIVE, no undo.

    Removes that team's subagent folder under .claude/agents/<team>_team/, the
    MCP tools/servers it generated, and its manifest entry. Any remaining
    orchestration becomes active (the manifest file is removed only when the last
    one is wiped). The wizard's own config (wizard.yaml settings + stream
    thoughts) is preserved. Returns the list of removed paths/labels.
    """
    cwd   = os.getcwd()
    m     = _read_manifest()
    orchs = m["orchestrations"]
    if not orchs:
        return []

    want   = team_folder or m["active"]
    target = next((o for o in orchs if o.get("team_folder") == want), orchs[-1])

    # Guard against empty paths: a blank team_folder would resolve to the whole
    # .claude/agents dir (and a blank rel to cwd), which _remove_path would wipe.
    targets = []
    folder = target.get("team_folder")
    if folder:
        targets.append(os.path.join(_agents_dir(), folder))
    targets += [os.path.join(cwd, rel) for rel in target.get("mcp_tools", []) if rel]
    removed = [os.path.relpath(p, cwd) for p in dict.fromkeys(targets) if _remove_path(p)]

    # Clear this orchestration's operational state from the DB too, so its tasks
    # don't linger as orphans (and can't resurface if a team with the same folder
    # is rebuilt later).
    if folder:
        n_tasks = wizard_db.delete_tasks_for_orch(folder)
        wizard_db.delete_stream_config(folder)
        if n_tasks:
            removed.append(f"{n_tasks} queued/finished task(s)")

    # Unregister this orchestration's MCP servers without clobbering user ones.
    if _unregister_mcp_servers(target.get("mcp_servers", [])):
        removed.append(".mcp.json (entries removed)")

    remaining = [o for o in orchs if o is not target]
    if remaining:
        _write_manifest({"active": remaining[-1].get("team_folder"),
                         "orchestrations": remaining})
    else:
        _remove_path(_manifest_path())

    # Strip any legacy orchestrations from wizard.yaml (now lives in .claude/).
    data = load_wizard_yaml()
    if data.get("orchestrations"):
        data.pop("orchestrations", None)
        save_wizard_yaml_data(data)
        removed.append("wizard.yaml orchestrations")

    return removed


def _confirm_and_wipe():
    """Confirm (with a danger warning) and wipe the active orchestration.

    Returns (wiped: bool, name: str | None).
    """
    name = (load_orchestration() or {}).get("team", "this orchestration")

    def render_top():
        print(color(f"  Wipe '{name}'?\n", BOLD, WHITE))
        print(color("  DANGER: this permanently deletes the orchestration the wizard", BOLD, DANGER))
        print(color("  produced — its generated agent files and .claude/agents definitions.", DANGER))
        print(color("  This cannot be undone. Your wizard.yaml settings are kept.\n", DANGER))

    value, _ = menu(
        [Option(True, "Yes, wipe it"), Option(False, "No, keep it")],
        title="Are you sure?",
        hint="(arrows · Enter to choose)",
        render_top=render_top,
    )
    if value is not True:
        return False, None

    removed = wipe_orchestration()
    return bool(removed), name if removed else None


def _confirm_and_clear_tasks(orch):
    """Confirm, then delete all tasks (queue, scan findings, history) for this
    orchestration — keeping the orchestration itself. Returns the number of tasks
    removed (0 if cancelled or none)."""
    folder = orch.get("team_folder", "") if orch else ""
    if not folder:
        return 0
    team = orch.get("team", "this orchestration")

    def render_top():
        print(color(f"  Clear all tasks for '{team}'?\n", BOLD, WHITE))
        print(color("  Removes the task queue, scan findings, and history for this", DIM, WHITE))
        print(color("  orchestration. The orchestration itself is kept.\n", DIM, WHITE))

    value, _ = menu(
        [Option(True, "Yes, clear them"), Option(False, "No, keep them")],
        title="Are you sure?",
        hint="(arrows · Enter to choose)",
        render_top=render_top,
    )
    if value is not True:
        return 0
    return wizard_db.delete_tasks_for_orch(folder)


# ---------------------------------------------------------------------------
# wizard.yaml / handle existing
# ---------------------------------------------------------------------------

def _show_wizard_response(response):
    """Render a Claude response in the wizard frame and wait for a keypress."""
    clear()
    show_header_compact()
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


def _prompt_orchestration(user_prompt=None):
    """Call Claude with the orchestration context and display the response."""
    orch      = load_orchestration() or {}
    orch_json = json.dumps(orch, indent=2)

    if user_prompt:
        prompt = (
            "You are advising on a multi-agent orchestration built with White Wizard.\n\n"
            f"Orchestration plan:\n{orch_json}\n\n"
            f"User: {user_prompt}"
        )
    else:
        prompt = load_prompt("wizard_yaml.md").replace("{orch_json}", orch_json)

    response = conjure(ask, prompt, label="Consulting the wizard...", model=DEFAULT_MODEL)
    _show_wizard_response(response)


def _show_diagram():
    """Full-screen infographic of the active orchestration. Press any key to return."""
    orch = load_orchestration()
    clear()
    show_header_compact()
    if not orch or not orch.get("agents"):
        print(color("  No diagram available — orchestration has no agents.\n", DIM, WHITE))
    else:
        print(render_infographic(orch, team_name=orch.get("team", "")))
    print(color("  Press any key to return...", DIM, WHITE))
    read_key()


def handle_wizard_yaml():
    """The 'existing orchestrations' main menu — curses split-pane UI.

    Left pane: wizard hat, alerts, orch info, option list.
    Right pane: task list and stream summary for the current orch.
    Returns "__refresh__" when state changed (e.g. a wipe) so the caller
    re-evaluates routing, or None to quit.
    """
    # Shared mutable state between the three closures below.
    shared = {"mode": "main", "alerts": pop_alerts()}

    def get_state_fn(sel_orch_i):
        orchs  = list_orchestrations()
        sel    = min(sel_orch_i, len(orchs) - 1) if orchs else 0
        orch   = orchs[sel] if orchs else None
        folder = orch.get("team_folder") if orch else None
        # Keep the active orchestration in sync with the one on screen, so wipe
        # and task/adjust (which act on the active orch) target what the user
        # is looking at. Guarded so we only write the manifest on a real change.
        if folder and shared.get("active") != folder:
            set_active_orchestration(folder)
            shared["active"] = folder
        alerts = list(shared["alerts"])
        shared["alerts"].clear()  # show once per refresh cycle
        return {
            "orchs":          orchs,
            "current_orch":   orch,
            "agent_names":    _ordered_agent_names(orch) if orch else [],
            "alerts":         alerts,
            "mode":           shared["mode"],
            "stream_enabled": wizard_db.is_stream_enabled(folder) if folder else True,
            "auto_commit":    _auto_commit_enabled(),
            "auto_fix":       _auto_fix_enabled(),
            "tasks":          wizard_db.get_tasks_for_orch(folder) if folder else [],
        }

    def get_options_fn(state):
        orch = state.get("current_orch")
        team = orch.get("team", "team") if orch else "team"
        af_label = ("Disable auto-fix (findings)" if state.get("auto_fix", False)
                    else "Enable auto-fix (findings)")
        ac_label = ("Disable auto-commit" if state.get("auto_commit", True)
                    else "Enable auto-commit")
        if state.get("mode") == "manage":
            return [
                Option("adjust", "Adjust the orchestration", text_input=True,
                       placeholder="What should change?"),
                Option("clear_tasks",       "Clear all tasks & findings"),
                Option("diagram",           "View diagram"),
                Option("autofix_toggle",    af_label),
                Option("autocommit_toggle", ac_label),
                Option("wipe",              "Wipe the orchestration system"),
                Option("back",              "← Back"),
            ]
        task_label = f"What do you want the {team} to do?"
        stream_label = "Disable stream" if state.get("stream_enabled") else "Enable stream"
        return [
            Option("task",          task_label, text_input=True, placeholder=task_label),
            Option("manage",        "Manage"),
            Option("other",         "Something else", text_input=True,
                   placeholder="Something else"),
            Option("stream_toggle", stream_label),
            Option("build",         "Build a new team"),
            Option("quit",          "Quit (stop background AI)"),
        ]

    def handle_choice_fn(choice, text, state):
        orch = state.get("current_orch")
        if choice is None:
            # ESC / q
            if shared["mode"] == "manage":
                shared["mode"] = "main"
                return None  # stay in pane
            return "__quit__"

        if shared["mode"] == "manage":
            if choice == "back":
                shared["mode"] = "main"
            elif choice == "adjust" and text:
                _prompt_orchestration(text)
            elif choice == "clear_tasks":
                if not orch:
                    shared["alerts"] = ["No active orchestration — nothing to clear."]
                else:
                    n = _confirm_and_clear_tasks(orch)
                    if n:
                        folder = orch.get("team_folder", "")
                        _stream_log(f"[{folder}] cleared {n} task(s) by user")
                        shared["alerts"] = [f"Cleared {n} task(s)."]
            elif choice == "diagram":
                _show_diagram()
            elif choice == "autofix_toggle":
                now_on = _auto_fix_enabled()
                _set_auto_fix(not now_on)
                status = "disabled" if now_on else "enabled"
                _stream_log(f"auto-fix {status} by user")
                shared["alerts"] = [
                    "Auto-fix enabled — the stream will fix scan findings automatically."
                    if not now_on else
                    "Auto-fix disabled — findings stay as suggestions (run with Enter)."]
            elif choice == "autocommit_toggle":
                now_on = _auto_commit_enabled()
                _set_auto_commit(not now_on)
                status = "disabled" if now_on else "enabled"
                _stream_log(f"auto-commit {status} by user")
                shared["alerts"] = [f"Auto-commit {status}."]
            elif choice == "wipe":
                folder_before_wipe = orch.get("team_folder", "") if orch else ""
                wiped, name = _confirm_and_wipe()
                if wiped:
                    _stop_stream_worker(folder_before_wipe)
                    push_alert(f"'{name}' wiped successfully.")
                    return "__refresh__"
        else:
            if choice == "manage":
                shared["mode"] = "manage"
            elif choice == "build":
                _first_menu()
                return "__refresh__"
            elif choice == "task" and text and orch:
                # A team request runs as a background task in the right pane
                # (prioritised), not a separate blocking screen. If the stream is
                # on, the worker picks it up; if paused, kick a one-shot run so
                # the explicit request still runs.
                folder = orch.get("team_folder", "")
                tid = wizard_db.add_task(folder, text, "user", is_user_prompt=True)
                _stream_log(f"[{folder}] queued user task #{tid}: {text}")
                if wizard_db.is_stream_enabled(folder):
                    shared["alerts"] = [f"Queued for the team: {text[:50]}"]
                else:
                    _run_task_once(orch, {"id": tid, "description": text,
                                          "task_type": "user"})
                    shared["alerts"] = [f"Running: {text[:50]}"]
            elif choice == "other" and text:
                _prompt_orchestration(text)
            elif choice == "stream_toggle" and orch:
                folder = orch.get("team_folder", "")
                currently_enabled = wizard_db.is_stream_enabled(folder)
                wizard_db.set_stream_enabled(folder, not currently_enabled)
                if not currently_enabled:  # was off, now turning on
                    _start_stream_workers()
                status = "disabled" if currently_enabled else "enabled"
                _stream_log(f"[{folder}] stream {status} by user")
                shared["alerts"] = [f"Stream {status}."]
            elif choice == "quit":
                return "__quit__"
        return None  # stay in pane, redraw

    def handle_task_fn(task, state):
        """Act on the highlighted task from the right pane (Enter).

        - A 'done' task opens a detail view (commit + diff).
        - A 'suggested' scan finding is dispatched as real work.
        - A 'pending' task is run on demand while the stream is paused.
        """
        orch = state.get("current_orch")
        if not orch or not task:
            return None
        status = task.get("status")
        if status == "done":
            return {"task_detail": _build_task_detail(task)}
        if status == "suggested":
            _dispatch_finding(orch, task)
            shared["alerts"] = [f"Fixing: {task.get('description', '')[:50]}"]
            return None
        if status != "pending":
            return None
        folder = orch.get("team_folder", "")
        if wizard_db.is_stream_enabled(folder):
            shared["alerts"] = ["Stream is on — it runs queued tasks automatically."]
            return None
        _run_task_once(orch, task)
        shared["alerts"] = [f"Running: {task.get('description', '')[:50]}"]
        return None

    return run_split_pane(get_state_fn, get_options_fn, handle_choice_fn,
                          handle_task_fn)


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

    spinner  = itertools.cycle(SPINNER_FRAMES)
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
            "\r  " + next(spinner) + "  "
            + color(f"Scanning '{name}' for existing code... ", WHITE)
            + color(f"({count})", DIM, WHITE)
        )
        sys.stdout.flush()
        time.sleep(0.08)

    clear_w = 9 + len(f"Scanning '{name}' for existing code... ") + len(count) + 2
    sys.stdout.write("\r" + " " * clear_w + "\r")
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
           hint="(↑↓ move · type to fill in 'Something else' · ←→ edit text · Enter to choose · Esc to cancel)"):
    """Pick one of ``options`` (a list of label strings).

    Returns the chosen label, the typed text for the custom option,
    or None if cancelled.
    """
    opts = [
        Option(o, o,
               text_input=(o == CUSTOM_OPTION),
               disabled=(o in DISABLED_TYPES),
               placeholder=(o if o == CUSTOM_OPTION else ""))
        for o in options
    ]

    value, text = select_menu(opts, title, hint=hint)
    if value is None:
        return value
    if value == CUSTOM_OPTION:
        return text or "a custom orchestration system"
    return value


# ---------------------------------------------------------------------------
# Loading / spinner
# ---------------------------------------------------------------------------

def loading(label, seconds=3.0):
    spinner = itertools.cycle(SPINNER_FRAMES)
    end     = time.time() + seconds
    while time.time() < end:
        sys.stdout.write("\r  " + next(spinner) + "  " + color(label, WHITE))
        sys.stdout.flush()
        time.sleep(0.09)
    sys.stdout.write("\r" + " " * (len(label) + 12) + "\r")
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

    spinner = itertools.cycle(SPINNER_FRAMES)
    while not done.is_set():
        sys.stdout.write("\r  " + next(spinner) + "  " + color(label, WHITE))
        sys.stdout.flush()
        time.sleep(0.09)

    sys.stdout.write("\r" + " " * (len(label) + 12) + "\r")
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


def _ensure_mcp():
    """Make sure the `mcp` package (needed by the generated state machine) is
    installed, installing it with pip if missing.

    Returns (status, detail) where status is "ok" (already present), "installed"
    (just installed), or "fail" (with a short reason in detail). Never raises —
    a failed install degrades to a manual instruction in the summary.
    """
    import importlib.util
    if importlib.util.find_spec("mcp") is not None:
        return ("ok", "")

    def _pip(*args):
        return subprocess.run(
            [sys.executable, "-m", "pip", *args],
            capture_output=True, text=True,
        )

    try:
        proc = _pip("install", "mcp")
        if proc.returncode == 0:
            return ("installed", "")
        # Older pip versions can't resolve some packages — upgrade pip and retry.
        _pip("install", "--upgrade", "pip")
        proc = _pip("install", "mcp")
        if proc.returncode == 0:
            return ("installed", "")
    except Exception as exc:
        return ("fail", str(exc))

    # Last resort: try uv if it's on PATH.
    uv_proc = None
    try:
        uv_proc = subprocess.run(
            ["uv", "pip", "install", "mcp"],
            capture_output=True, text=True,
        )
        if uv_proc.returncode == 0:
            return ("installed", "")
    except Exception:
        pass

    last = uv_proc if uv_proc is not None else proc
    out  = (last.stderr or last.stdout).strip()
    return ("fail", out.splitlines()[-1] if out else f"install failed (exit {last.returncode})")


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
        with open(p) as f:
            head = f.read()
        if not (head.lstrip().startswith("---") and "name:" in head and "description:" in head):
            missing.append(name + " (frontmatter)")
    checks.append(("Subagent files and frontmatter", not missing, ", ".join(missing)))

    # 2. The state-machine MCP tool is syntactically valid.
    sm = os.path.join(team_dir, "state_machine.py")
    sm_ok = os.path.isfile(sm)
    sm_detail = "" if sm_ok else "state_machine.py missing"
    if sm_ok:
        try:
            with open(sm) as f:
                ast.parse(f.read())
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


def show_orchestration_created(team_selection, team_dir, smoke_ok=None, smoke_checks=None,
                               mcp_status=None, mcp_detail="", committed=None):
    """Show the Claude-native subagent files that were written, smoke-test results, and how to run."""
    clear()
    show_header_compact()
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
    if mcp_status == "installed":
        print(color("  ✓ MCP runtime installed (pip install mcp).", WHITE))
    elif mcp_status == "ok":
        print(color("  ✓ MCP runtime already present.", WHITE))
    elif mcp_status == "fail":
        print(color("  ! Could not auto-install the MCP runtime — install it manually:", GRAY))
        print(color(f"      pip install mcp   ({mcp_detail})" if mcp_detail
                    else "      pip install mcp", GRAY))
    print()
    if committed:
        print(color(f"  ✓ Committed to git ({committed}).", BOLD, GOLD))
        print()
    print(color(f"  Lead orchestrator: {orch.get('orchestrator', 'orchestrator')}  "
                "(delegates via the state-machine MCP tool)", DIM, WHITE))
    print(color("  Your team is ready.\n", BOLD, GOLD))


def run_team_conversation(team_selection, synopsis, custom_description=""):
    clear()
    show_header_compact()

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
        show_header_compact()
        print(color("  The wizard could not produce an orchestration plan.\n", BOLD, GOLD))
        for line in response.splitlines():
            print(color("  " + line, WHITE))
        print()
        print(color("  Press any key to continue...", DIM, WHITE))
        read_key()
        return None

    # Show the orchestration and build it for them, keeping the diagram on
    # screen for at least 15s so it can actually be read.
    clear()
    show_header_compact()
    print(render_infographic(plan, team_name=team_selection))
    print()
    build_start = time.time()

    loading("Building the AI orchestration system...", seconds=2.0)
    team_dir = write_orchestration(plan, team_selection, DEFAULT_MODEL)

    # Install the MCP runtime the generated state machine depends on.
    mcp_status, mcp_detail = conjure(
        _ensure_mcp, label="Installing the MCP runtime (pip install mcp)...")

    loading("Running a smoke test on the orchestration...", seconds=1.6)
    ok, checks = _smoke_test(team_dir, load_orchestration() or {})

    # Commit the built orchestration to git once the smoke test passes (honours
    # the same auto-commit setting as task commits; no-ops outside a git repo).
    committed = (_commit_built_orchestration(team_selection, team_dir)
                 if ok and _auto_commit_enabled() else None)

    remaining = MIN_BUILD_DISPLAY_SECONDS - (time.time() - build_start)
    if remaining > 0:
        loading("Finalising the orchestration...", seconds=remaining)

    show_orchestration_created(team_selection, team_dir, ok, checks, mcp_status, mcp_detail,
                               committed=committed)

    # Ask the AI for any project-specific stream tasks based on what it just built.
    extra_stream_tasks = []
    try:
        extra_prompt = (
            "Based on this project, propose up to 4 project-specific recurring "
            "stream tasks for a background code review agent. Each should be "
            "specific to what this project actually does.\n\n"
            f"Project synopsis:\n{synopsis[:800]}\n\n"
            "Return ONLY a JSON array:\n"
            '[{"task_type":"bug|test|refactor|doc","description":"short task (max 150 chars)"}]\n'
            "Return [] if no project-specific tasks needed. No other text."
        )
        raw = conjure(ask, extra_prompt, label="Generating stream tasks...",
                      model=DEFAULT_MODEL)
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            items = json.loads(m.group(0))
            if isinstance(items, list):
                extra_stream_tasks = [
                    (i.get("task_type", "bug"), i.get("description", "")[:150])
                    for i in items[:4] if i.get("description")
                ]
    except Exception:
        pass

    # Start the stream worker for this newly built orchestration.
    orch_built = load_orchestration()
    if orch_built:
        folder = orch_built.get("team_folder")
        if folder and folder not in _stream_workers:
            w = StreamWorker(orch_built, extra_tasks=extra_stream_tasks)
            w.start()
            _stream_workers[folder] = w

    return "done"


def plan_with_ai(files):
    prompt  = load_prompt("analyze_codebase.md") + "\n".join(files[:60])
    synopsis = conjure(ask, prompt, label="Analysing codebase...", model=DEFAULT_MODEL)

    while True:
        team = select(ORCH_TYPES, "Which team do you want to set up?")
        if team is None:
            return None
        custom_desc = team if team not in TEAM_PROMPTS and team != CUSTOM_OPTION else ""
        result = run_team_conversation(team, synopsis, custom_description=custom_desc)
        if result is not None:
            return result


# ---------------------------------------------------------------------------
# MCP server registration (.mcp.json)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Background stream workers (per-orchestration daemon threads)
# ---------------------------------------------------------------------------

STREAM_TASKS = {
    "dev": [
        ("bug",      "Scan for bugs and logic errors"),
        ("test",     "Identify missing test coverage"),
        ("refactor", "Find refactoring and code quality opportunities"),
        ("doc",      "Find missing or outdated documentation"),
    ],
}


def _git_snapshot():
    """Capture the working tree's change state for the cwd, or None if this isn't
    a git repo (or git is unavailable). Returns ``(status_lines, fingerprint)``:
    ``status_lines`` is the set of ``git status --porcelain`` lines (used to name
    changed files), and ``fingerprint`` folds in several signals so any real edit
    registers as a change:
      - ``git diff`` — content of modified tracked files (incl. re-edits of an
        already-dirty file, whose porcelain line alone wouldn't move);
      - each untracked file's (path, mtime, size) — ``git diff`` never shows
        untracked content, so editing an existing untracked file (common in a
        fresh/uncommitted repo) would otherwise look like "no change".
    ``-uall`` lists untracked files individually (not collapsed dirs) and
    ``core.quotePath=false`` keeps non-ASCII paths usable for a later commit."""
    import hashlib
    try:
        status = subprocess.run(
            ["git", "-c", "core.quotePath=false", "status", "--porcelain", "-uall"],
            capture_output=True, text=True, cwd=os.getcwd(), timeout=15,
        )
        if status.returncode != 0:
            return None
        diff = subprocess.run(
            ["git", "diff"],
            capture_output=True, text=True, cwd=os.getcwd(), timeout=15,
        )
    except Exception:
        return None
    lines = {ln for ln in status.stdout.splitlines() if ln.strip()}
    h = hashlib.sha1()
    h.update(status.stdout.encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update(diff.stdout.encode("utf-8", "replace"))
    for ln in sorted(lines):
        if ln.startswith("??") and len(ln) > 3:
            path = ln[3:]
            try:
                st = os.stat(os.path.join(os.getcwd(), path))
                h.update(f"\x00{path}\x00{st.st_mtime_ns}\x00{st.st_size}".encode("utf-8", "replace"))
            except OSError:
                pass
    return lines, h.hexdigest()


def _git_commit(paths, message):
    """Stage and commit exactly ``paths`` in the cwd; return the short commit hash
    (or None). Best-effort — never raises, so a commit problem can't break task
    completion. Skips when the index already has staged changes, so the user's
    in-progress staging is never swept into the wizard's commit."""
    try:
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"],
                                capture_output=True, cwd=os.getcwd(), timeout=15)
        if staged.returncode != 0:
            return None  # something already staged — leave it for the user
        add = subprocess.run(["git", "add", "--", *paths],
                             capture_output=True, text=True, cwd=os.getcwd(), timeout=30)
        if add.returncode != 0:
            return None
        commit = subprocess.run(["git", "commit", "-m", message],
                                capture_output=True, text=True, cwd=os.getcwd(), timeout=30)
        if commit.returncode != 0:
            return None
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, cwd=os.getcwd(), timeout=15)
        return rev.stdout.strip() or None
    except Exception:
        return None


def _git_show_commit(ref):
    """Return the text of `git show --no-color <ref>`, or empty string."""
    try:
        r = subprocess.run(
            ["git", "show", "--no-color", ref],
            capture_output=True, text=True, cwd=os.getcwd(), timeout=15,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _extract_commit_ref(summary):
    """Parse a short commit hash out of a task summary like 'Committed abc1234 — ...'"""
    m = re.search(r"Committed\s+([0-9a-f]{5,12})\b", summary or "")
    return m.group(1) if m else None


def _build_task_detail(task):
    """Build the dict shown in the right-pane detail view for a completed task."""
    summary = task.get("summary", "")
    ref     = _extract_commit_ref(summary)
    return {
        "description": task.get("description", ""),
        "summary":     summary,
        "commit":      ref,
        "diff_lines":  _git_show_commit(ref).splitlines() if ref else [],
    }


def _commit_built_orchestration(team_label, team_dir):
    """Commit a freshly built orchestration (after its smoke test passes): the
    generated subagents, the manifest, and the MCP config. Best-effort — reuses
    ``_git_commit``, which no-ops outside a git repo or when the index already
    has staged changes. Returns the short commit hash, or None."""
    cwd = os.getcwd()
    candidates = [team_dir, _manifest_path(),
                  os.path.join(cwd, ".mcp.json"),
                  os.path.join(cwd, "wizard.yaml")]
    paths = [os.path.relpath(p, cwd) for p in candidates if os.path.exists(p)]
    if not paths:
        return None
    label = team_label.replace("Create ", "").title()
    msg = (f"Add {label} orchestration\n\n"
           "Generated by White Wizard — subagents, state-machine MCP tool, and "
           "config. Committed after the build's smoke test passed.")
    return _git_commit(paths, msg)


# Only one agentic task edits the working tree at a time: concurrent edits — and
# the repo-wide git verification (and commit) wrapped around them — would corrupt
# each other.
_task_exec_lock = threading.Lock()


class StreamWorker(threading.Thread):
    def __init__(self, orch, extra_tasks=None):
        folder = orch.get("team_folder", "unknown")
        super().__init__(daemon=True, name=f"stream-{folder}")
        self.orch        = orch
        self.orch_folder = folder
        team_key         = next((k for k in STREAM_TASKS
                                 if k in orch.get("team", "").lower()), "dev")
        self._cycle = (list(STREAM_TASKS.get(team_key, STREAM_TASKS["dev"]))
                       + list(extra_tasks or []))
        self._idx   = 0
        self._stop  = threading.Event()

    def stop(self):
        self._stop.set()
        self._log("worker stop requested")

    def _log(self, msg):
        _stream_log(f"[{self.orch_folder}] {msg}")

    def run(self):
        self._log(f"worker started (cycle of {len(self._cycle)} task type(s))")
        while not self._stop.is_set():
            try:
                wizard_db.ensure_stream_config(self.orch_folder)
                if not wizard_db.is_stream_enabled(self.orch_folder):
                    time.sleep(2)
                    continue
                task = wizard_db.get_next_pending_task(self.orch_folder)
                if task:
                    self._log(f"picked queued task #{task['id']} "
                              f"[{task['task_type']}] {task['description']}")
                    self._execute(task["id"], task["description"], task["task_type"])
                elif _auto_fix_enabled() and (finding := wizard_db.get_next_suggested(self.orch_folder)):
                    self._run_finding(finding)
                else:
                    task_type, desc = self._cycle[self._idx % len(self._cycle)]
                    self._idx += 1
                    tid = wizard_db.add_task(self.orch_folder, desc, task_type)
                    self._log(f"started cycle task #{tid} [{task_type}] {desc}")
                    self._execute(tid, desc, task_type)
                time.sleep(0.5)
            except Exception as exc:
                self._log(f"loop error: {exc!r} — backing off")
                time.sleep(5)  # back off on DB errors, then retry
        self._log("worker stopped")

    def _execute(self, task_id, description, task_type):
        wizard_db.set_task_running(task_id)
        # A user-queued task is work for the team to do, not a code-review scan —
        # actually carry it out, and only mark it done if files really changed.
        if task_type == "user":
            ok, summary = self._run_user_task(description)
            if ok:
                wizard_db.complete_task(task_id, summary)
                self._log(f"completed user task #{task_id}: {summary[:120]}")
            else:
                wizard_db.fail_task(task_id, summary)
                self._log(f"user task #{task_id} failed/no-op: {summary[:120]}")
            return
        try:
            findings = self._analyse(task_type)
            for f in findings[:8]:
                d = f.get("description", "")[:200]
                if d:
                    # A finding is an actionable suggestion: 'suggested' so the
                    # user can run it on demand (or auto-fix can drain it), but it
                    # isn't auto-queued like a 'pending' task.
                    tid = wizard_db.add_task(self.orch_folder, d, task_type,
                                             status="suggested")
                    self._log(f"  finding #{tid} [{task_type}] {d}")
            summary = f"Found {len(findings)} item(s)" if findings else "Nothing to flag"
            wizard_db.complete_task(task_id, summary)
            self._log(f"completed task #{task_id} [{task_type}]: {summary}")
        except Exception as exc:
            wizard_db.complete_task(task_id, f"Error: {str(exc)[:80]}")
            self._log(f"task #{task_id} [{task_type}] failed: {exc!r}")

    def _run_user_task(self, description):
        """Carry out a user-queued task as real work: run an edit-enabled Claude
        in the project, verify (via git) that files actually changed, and commit
        the task's own changes. Returns ``(ok: bool, summary: str)``.

        Serialised on ``_task_exec_lock`` so two tasks can't edit the tree, read
        each other's git diff, or race on a commit."""
        prompt = (
            "You are an autonomous coding agent working in this software project.\n\n"
            f"Task: {description}\n\n"
            "Carry out this task now by directly creating and editing files in the "
            "project. Make the real changes — do not just describe a plan or ask "
            "for confirmation. Keep the changes focused on the task. When finished, "
            "reply with a one-line summary of what you changed."
        )
        with _task_exec_lock:
            before = _git_snapshot()
            try:
                result = run_task(prompt, model=self.orch.get("model"), timeout=900)
            except Exception as exc:
                return False, f"Error: {str(exc)[:150]}"
            after = _git_snapshot()

            if before is None or after is None:
                # Not a git repo (or git unavailable) — can't verify or commit.
                return True, (result.strip()[:300] or "Completed (changes not verified)")
            before_lines, before_fp = before
            after_lines, after_fp = after
            if after_fp == before_fp:
                return False, ("Ran but made no file changes — "
                               + (result.strip()[:160] or "no output"))
            new = sorted(ln[3:].strip() for ln in (after_lines - before_lines) if len(ln) > 3)
            if not new:
                # Only already-dirty files changed further — leave committing to
                # the user rather than risk bundling their in-progress edits.
                return True, "Modified existing file(s) — left uncommitted."
            auto = _auto_commit_enabled()
            ref = (_git_commit(new, self._commit_message(description, result))
                   if auto else None)

        listed = ", ".join(new[:6])
        if ref:
            return True, f"Committed {ref} — {len(new)} file(s): {listed}"
        if not auto:
            return True, f"Changed {len(new)} file(s): {listed} (auto-commit off)"
        return True, f"Changed {len(new)} file(s): {listed} (auto-commit skipped)"

    def _run_finding(self, task):
        """Carry out a scan finding as real work (agentic edit + commit, same path
        as a user task — the finding text is the work), then mark it done/failed."""
        self._log(f"fixing finding #{task['id']}: {task['description'][:80]}")
        wizard_db.set_task_running(task["id"])
        ok, summary = self._run_user_task(
            "Address this code-review finding: " + task["description"])
        (wizard_db.complete_task if ok else wizard_db.fail_task)(task["id"], summary)
        self._log(f"finding #{task['id']} {'fixed' if ok else 'unresolved'}: {summary[:100]}")

    def _commit_message(self, description, result=""):
        """Build a commit message. The subject is the agent's own one-line summary
        of what it changed — it already returns one (the task prompt asks for it),
        so no extra AI call is needed. Falls back to the task description if the
        summary is empty. The full task goes in the body for provenance."""
        d = description.strip()
        summary = next((ln.strip() for ln in (result or "").splitlines() if ln.strip()), "")
        if not summary:
            summary = (d.splitlines()[0] if d else "automated change")
        subject = summary[:72]
        return (f"{subject}\n\n"
                f"Automated change by White Wizard ({self.orch.get('team', 'orchestration')}).\n"
                f"Task: {d[:500]}")

    def _analyse(self, task_type):
        root       = os.getcwd()
        code_files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in IGNORE_DIRS and not d.startswith(".")]
            for fname in filenames:
                if os.path.splitext(fname)[1].lower() in CODE_EXTS:
                    code_files.append(
                        os.path.relpath(os.path.join(dirpath, fname), root))
        if not code_files:
            return []

        snippets = []
        for rel in code_files[:10]:
            try:
                with open(os.path.join(root, rel)) as fh:
                    snippets.append(f"### {rel}\n{fh.read(3000)}")
            except Exception:
                pass

        prompts_map = {
            "bug":      "Identify bugs and logic errors",
            "test":     "Identify missing test coverage",
            "refactor": "Find refactoring opportunities",
            "doc":      "Find missing or outdated documentation",
        }
        instruction = prompts_map.get(task_type, f"Analyze for: {task_type}")
        prompt = (
            f"You are a code review agent. Task: {instruction}.\n\n"
            f"Files ({len(code_files)} total, showing {len(snippets)}):\n\n"
            + "\n\n---\n\n".join(snippets)
            + "\n\nReturn ONLY a JSON array of specific findings:\n"
              '[{"description":"short issue (max 200 chars)","task_type":"'
              + task_type + '"}]\n'
              "Return [] if nothing notable. No prose."
        )
        try:
            raw = ask(prompt, timeout=90)
            m = re.search(r'\[.*\]', raw, re.DOTALL)
            if m:
                found = json.loads(m.group(0))
                if isinstance(found, list):
                    return found
        except Exception:
            pass
        return []


_stream_workers: dict = {}


def _start_stream_workers():
    """Start a StreamWorker daemon thread for every existing orchestration."""
    for orch in list_orchestrations():
        folder = orch.get("team_folder")
        if folder and folder not in _stream_workers:
            w = StreamWorker(orch)
            w.start()
            _stream_workers[folder] = w


def _stop_stream_worker(folder):
    """Signal and remove the worker for a wiped orchestration."""
    w = _stream_workers.pop(folder, None)
    if w:
        w.stop()


def _run_task_once(orch, task):
    """Run a single task in a one-shot background thread, regardless of whether
    the stream is enabled — lets the user run a hand-picked task while paused.

    Reuses StreamWorker._execute, which marks the task running, does the work,
    and completes it. set_task_running flips the status immediately, so an
    enabled worker won't also pick it up (it only takes 'pending' tasks)."""
    worker = StreamWorker(orch)
    threading.Thread(
        target=lambda: worker._execute(
            task["id"], task["description"], task["task_type"]),
        daemon=True,
    ).start()


def _dispatch_finding(orch, task):
    """Run a scan finding as real work on demand (agentic edit + commit), in a
    one-shot background thread — used when the user presses Enter on a suggested
    finding in the task pane.

    Atomically claims the finding first; bails silently if the stream's auto-fix
    already claimed it between the UI render and the user pressing Enter."""
    if not wizard_db.claim_finding(task["id"]):
        return
    worker = StreamWorker(orch)
    threading.Thread(target=lambda: worker._run_finding(task), daemon=True).start()


def _shutdown_stream_workers():
    """Gracefully stop every background stream worker and kill any in-flight AI
    call. Workers are daemon threads (they die on process exit regardless); this
    signals them to stop looping and releases the current `claude` subprocess so
    quitting — or a Ctrl+C — doesn't leave work running."""
    if _stream_workers:
        _stream_log(f"shutting down {len(_stream_workers)} worker(s)")
    for w in list(_stream_workers.values()):
        w.stop()
    _stream_workers.clear()
    kill_current()


def _ensure_wizard_gitignore():
    """Add .wizard/ to .gitignore if not already present."""
    gitignore = os.path.join(os.getcwd(), ".gitignore")
    entry     = ".wizard/"
    try:
        if os.path.isfile(gitignore):
            with open(gitignore) as f:
                content = f.read()
            if entry in content:
                return
            with open(gitignore, "a") as f:
                f.write(f"\n{entry}\n")
        else:
            with open(gitignore, "w") as f:
                f.write(f"{entry}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _first_menu():
    """The 'no orchestrations yet' main menu: pick what to build. Returns "done"
    after a successful build, or None if the user quit."""
    files = scan_workspace()
    if files:
        return plan_with_ai(files)

    while True:
        choice = select(OPTIONS, "What shall we summon today?")
        if choice is None:
            return None
        if choice not in OPTIONS:
            # Free-form "Something else" query — answer and keep browsing.
            _ask_and_show(choice)
            continue
        # A concrete build option in a fresh workspace: build it directly,
        # with no synopsis since there is no existing code to analyse.
        result = run_team_conversation(
            choice, "(fresh workspace — no existing code yet)",
            custom_description=choice,
        )
        if result is not None:
            return result


def main():
    # Two main menus: the existing-orchestrations menu when one or more have been
    # built (switch/task/wipe), else the build menu. Looping between them means a
    # wipe or a build lands back on the right menu instead of exiting.
    # wizard.yaml presence is intentionally excluded: it's preserved after wipe
    # (holds stream thoughts/settings), so it can't drive the routing decision.
    _ensure_wizard_gitignore()
    # Sweep operational state left behind by orchestrations wiped in older
    # sessions (before wipe cleaned the DB), so they don't linger or resurface.
    pruned = wizard_db.prune_orphaned_state(
        {o.get("team_folder") for o in list_orchestrations() if o.get("team_folder")})
    if pruned:
        _stream_log(f"pruned {pruned} orphaned task(s) from removed orchestration(s)")
    # Reset tasks left 'running' by a crashed/killed prior session before workers
    # start, so they don't show a perpetual flame or strand a claimed finding.
    interrupted = wizard_db.reset_running_tasks()
    if interrupted:
        _stream_log(f"reset {interrupted} interrupted task(s) from a previous session")
    _start_stream_workers()
    while True:
        if load_orchestration():
            if handle_wizard_yaml() == "__refresh__":
                continue            # wiped → re-evaluate (other orch, or build menu)
            break                   # user quit
        else:
            if _first_menu() is None:
                break               # user quit the build menu
            # built something → loop → existing-orchestrations menu

    _shutdown_stream_workers()
    print(color("\n  The staff dims. Farewell.\n", DIM, WHITE))
