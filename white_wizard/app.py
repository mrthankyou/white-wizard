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

# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
REV   = "\033[7m"
BLINK = "\033[5m"
WHITE = "\033[97m"
GOLD  = "\033[96m"
CYAN  = "\033[36m"
GRAY  = "\033[90m"

DEFAULT_MODEL = "claude-sonnet-4-6"
MCP_NAME      = "wizard-state-machine"

WIZARD = r"""
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
"""


def color(text, *codes):
    return "".join(codes) + text + RESET


def clear():
    print("\033[2J\033[H", end="")


def show_header():
    print(color(WIZARD, BOLD, WHITE))
    print(color("              The White Wizard", BOLD, GOLD))
    print(color("   Conjure multi-agent orchestration with a single button.", DIM, WHITE))
    print()


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
    m = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    try:
        return json.loads(response.strip())
    except Exception:
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
                  model=DEFAULT_MODEL, mcp=MCP_NAME)
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

DEFAULT_STREAM_QUESTIONS = [
    {
        "priority": 1,
        "id": "orch_drift",
        "prompt": (
            "Review the git diff and latest code. Have there been any changes to the "
            "codebase that require updating how the orchestration system works, or that "
            "expose missing components necessary to the success and evolution of the codebase?"
        ),
    },
    {
        "priority": 2,
        "id": "bug_scan",
        "prompt": (
            "Are there any bugs present in the codebase that should be surfaced "
            "as a proposed bug fix?"
        ),
    },
    {
        "priority": 3,
        "id": "stream_self_improve",
        "prompt": (
            "Review the questions currently saved in stream mode (wizard.yaml). "
            "Are any improvements, new questions, or refactorings needed to better "
            "identify issues, streamline codebase management, and simplify processes "
            "— without unnecessarily adding complexity? If so, return the revised "
            "question list as a JSON code block."
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


def save_wizard_yaml(plan, team_selection, model):
    """Append a completed orchestration to wizard.yaml."""
    data = load_wizard_yaml()
    data.setdefault("settings", {"default_model": model})
    data.setdefault("orchestrations", [])
    data.setdefault("stream", {"questions": DEFAULT_STREAM_QUESTIONS})

    team_label = team_selection.replace("Create ", "").title()
    entry = {
        "created_at": datetime.date.today().isoformat(),
        "team": team_label,
        "model": model,
        "agents": [
            {"name": a["name"], "description": a.get("description", ""), "model": model}
            for a in plan.get("agents", [])
        ],
        "transitions": plan.get("transitions", []),
    }
    data["orchestrations"].append(entry)
    save_wizard_yaml_data(data)


# ---------------------------------------------------------------------------
# wizard.yaml / handle existing
# ---------------------------------------------------------------------------

def handle_wizard_yaml(path):
    clear()
    show_header()

    spinner = itertools.cycle("|/-\\")
    end = time.time() + 1.4
    while time.time() < end:
        sys.stdout.write(
            "\r" + color(f"  {next(spinner)} ", GOLD)
            + color("Reading wizard.yaml...", WHITE)
        )
        sys.stdout.flush()
        time.sleep(0.08)
    sys.stdout.write("\r" + " " * 48 + "\r")

    with open(path) as f:
        yaml_contents = f.read()

    prompt_template = load_prompt("wizard_yaml.md")
    prompt = prompt_template + "\n\n" + yaml_contents

    response = conjure(ask, prompt, label="Conjuring magic...")
    return response


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

    spinner  = itertools.cycle("|/-\\")
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


def read_key():
    import termios
    import tty

    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\033":
            if sys.stdin.read(1) == "[":
                code = sys.stdin.read(1)
                return {"A": "up", "B": "down"}.get(code, "ignore")
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch in ("\x7f", "\x08"):
            return "backspace"
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def render_menu(options, selected, title, hint, custom_text):
    clear()
    show_header()
    print(color("  " + title, BOLD, CYAN))
    print(color("  " + hint, DIM, WHITE))
    print()
    for i, option in enumerate(options):
        chosen   = i == selected
        disabled = option in DISABLED_TYPES
        if option == CUSTOM_OPTION:
            if chosen and not disabled:
                body   = color(custom_text, WHITE) if custom_text else color(option, GRAY)
                cursor = color("_", GRAY, BLINK)
                print(color("   > ", BOLD, GOLD) + body + cursor)
            else:
                print("     " + color(option + "  (coming soon)", GRAY))
        elif disabled:
            print("     " + color(option + "  (coming soon)", GRAY))
        elif chosen:
            print(color(f"   > {option}  ", BOLD, GOLD, REV))
        else:
            print(color(f"     {option}", WHITE))
    print()
    print(color("  " + "─" * 40, DIM, WHITE))
    print(color("  s  ·  Stream mode", DIM, WHITE))
    print()


def select(options, title,
           hint="(arrows to move - type to fill in 'Something else' - Enter to choose - q to quit)"):
    if not sys.stdin.isatty():
        clear()
        show_header()
        print(color("  " + title, BOLD, CYAN))
        for i, option in enumerate(options, start=1):
            print(f"   {i}. {option}")
        raw = sys.stdin.readline().strip()
        if not (raw.isdigit() and 1 <= int(raw) <= len(options)):
            return None
        option = options[int(raw) - 1]
        if option == CUSTOM_OPTION:
            typed = sys.stdin.readline().strip()
            return typed or "a custom orchestration system"
        return option

    active   = [i for i, o in enumerate(options) if o not in DISABLED_TYPES]
    selected = active[0] if active else 0
    custom_text = ""
    while True:
        render_menu(options, selected, title, hint, custom_text)
        key      = read_key()
        on_custom = options[selected] == CUSTOM_OPTION
        if key == "up":
            idx      = active.index(selected) if selected in active else 0
            selected = active[(idx - 1) % len(active)]
        elif key == "down":
            idx      = active.index(selected) if selected in active else 0
            selected = active[(idx + 1) % len(active)]
        elif key == "enter":
            if on_custom:
                return custom_text.strip() or "a custom orchestration system"
            return options[selected]
        elif key == "esc":
            return None
        elif key in ("s", "S") and not on_custom:
            return "__stream__"
        elif on_custom:
            if key == "backspace":
                custom_text = custom_text[:-1]
            elif len(key) == 1 and key.isprintable():
                custom_text += key
        elif key in ("q", "Q"):
            return None


# ---------------------------------------------------------------------------
# Approval menu
# ---------------------------------------------------------------------------

APPROVAL_YES    = "Yes, proceed"
APPROVAL_NO     = "No, start over"
APPROVAL_DETAIL = "No, "


def render_approval(selected, feedback_text, content=""):
    clear()
    show_header()
    if content:
        print(content)
    print(color("  Does this look right?", BOLD, CYAN))
    print(color("  (arrows to move · type feedback on last option · Enter to choose)", DIM, WHITE))
    print()
    options = [APPROVAL_YES, APPROVAL_NO, APPROVAL_DETAIL]
    for i, opt in enumerate(options):
        chosen = i == selected
        if opt == APPROVAL_DETAIL:
            prefix = color("No, ", GRAY)
            typed  = color(feedback_text, WHITE) if feedback_text else ""
            cursor = color("_", GRAY, BLINK) if chosen else ""
            if chosen:
                print(color("   > ", BOLD, GOLD) + prefix + typed + cursor)
            else:
                print("     " + prefix + typed)
        elif chosen:
            print(color(f"   > {opt}  ", BOLD, GOLD, REV))
        else:
            print(color(f"     {opt}", WHITE))
    print()


def approval_select(content=""):
    if not sys.stdin.isatty():
        raw = sys.stdin.readline().strip()
        if raw == "1":
            return ("yes", "")
        if raw == "2":
            return ("no", "")
        detail = sys.stdin.readline().strip()
        return ("detail", detail or "no feedback given")

    selected      = 0
    feedback_text = ""
    while True:
        render_approval(selected, feedback_text, content=content)
        key       = read_key()
        on_detail = selected == 2
        if key == "up":
            selected = (selected - 1) % 3
        elif key == "down":
            selected = (selected + 1) % 3
        elif key == "enter":
            if selected == 0:
                return ("yes", "")
            elif selected == 1:
                return ("no", "")
            else:
                return ("detail", feedback_text.strip() or "no feedback given")
        elif key == "esc":
            return ("no", "")
        elif on_detail:
            if key == "backspace":
                feedback_text = feedback_text[:-1]
            elif len(key) == 1 and key.isprintable():
                feedback_text += key
        elif key in ("q", "Q"):
            return ("no", "")


# ---------------------------------------------------------------------------
# Simple yes/no prompt for offers
# ---------------------------------------------------------------------------

def yes_no(question):
    """Display a two-option yes/no menu. Returns True for yes, False for no."""
    options  = ["Yes", "No"]
    selected = 0
    while True:
        clear()
        show_header()
        print(color(f"  {question}", BOLD, CYAN))
        print(color("  (arrows · Enter to choose)", DIM, WHITE))
        print()
        for i, opt in enumerate(options):
            if i == selected:
                print(color(f"   > {opt}  ", BOLD, GOLD, REV))
            else:
                print(color(f"     {opt}", WHITE))
        print()
        key = read_key()
        if key == "up":
            selected = (selected - 1) % 2
        elif key == "down":
            selected = (selected + 1) % 2
        elif key == "enter":
            return selected == 0
        elif key in ("esc", "q", "Q"):
            return False


# ---------------------------------------------------------------------------
# Loading / spinner
# ---------------------------------------------------------------------------

def loading(label, seconds=3.0):
    spinner = itertools.cycle("|/-\\")
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

    spinner = itertools.cycle("|/-\\")
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
    CUSTOM_OPTION,
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


def offer_mcp_server(synopsis):
    """Offer to generate a project-specific MCP server for detected commands."""
    want = yes_no(
        "Would you like to generate a project MCP server?\n"
        "  Wizard can expose your project's commands (test, build, deploy)\n"
        "  as MCP tools that agents call directly."
    )
    if not want:
        return

    prompt = (
        "You are White Wizard generating a project-specific MCP server.\n\n"
        "Based on the project context below, identify the most important runnable "
        "commands (from README, Makefile, package.json, etc.) and generate a "
        "FastMCP server that exposes them as tools.\n\n"
        "Use the ### FILE: format:\n\n"
        "### FILE: mcp_project_tools.py\n```python\n<code>\n```\n\n"
        "Project context:\n" + synopsis
    )
    response = conjure(ask, prompt, label="Generating MCP server...", model=DEFAULT_MODEL)

    files = parse_generated_files(response)
    if not files:
        print(color("\n  Could not generate MCP server from response.\n", DIM, WHITE))
        return

    cwd = os.getcwd()
    for rel_path, code in files:
        full_path = os.path.join(cwd, rel_path)
        dir_path  = os.path.dirname(full_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(full_path, "w") as f:
            f.write(code + "\n")
        print(color(f"    {rel_path}", WHITE))


def generate_and_write_agents(plan, team_selection, synopsis):
    """Call Claude to generate agent code from the approved plan, write files to disk."""
    template = load_prompt("generate_agents.md")
    prompt   = (template.replace("{plan_json}", json.dumps(plan, indent=2))
                + "\n" + synopsis)

    response = conjure(ask, prompt, label="Generating agent code...", model=DEFAULT_MODEL)

    files = parse_generated_files(response)
    if not files:
        clear()
        show_header()
        print(color("  Could not parse generated files from Claude's response.\n", DIM, WHITE))
        for line in response.splitlines():
            print(color("  " + line, WHITE))
        return []

    cwd     = os.getcwd()
    written = []
    for rel_path, code in files:
        full_path = os.path.join(cwd, rel_path)
        dir_path  = os.path.dirname(full_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(full_path, "w") as f:
            f.write(code + "\n")
        written.append(rel_path)

    clear()
    show_header()
    team_label = team_selection.replace("Create ", "").title()
    print(color(f"  {team_label} — orchestration system generated\n", BOLD, CYAN))
    for rel_path in written:
        print(color(f"    {rel_path}", WHITE))
    print()
    print(color("  To run your orchestration:", DIM, WHITE))
    print(color("    python3 run_orch.py\n", BOLD, GOLD))

    # Offer project MCP server
    offer_mcp_server(synopsis)

    return written


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
    initial_prompt  = template + "\n" + synopsis

    history = []
    current = initial_prompt

    while True:
        response = conjure(ask_with_history, history, current,
                           label="Conjuring magic...", model=DEFAULT_MODEL)
        history.append(("user", current))
        history.append(("assistant", response))

        plan = parse_agent_plan(response)

        if plan:
            content = render_infographic(plan, team_name=team_selection)
        else:
            lines = [color("  " + "─" * 50, DIM, WHITE)]
            for line in response.splitlines():
                lines.append(color("  " + line, WHITE))
            lines.append(color("  " + "─" * 50, DIM, WHITE))
            lines.append("")
            content = "\n".join(lines)

        action, detail = approval_select(content=content)

        if action == "yes":
            if plan:
                save_wizard_yaml(plan, team_selection, DEFAULT_MODEL)
                generate_and_write_agents(plan, team_selection, synopsis)
                return "done"
            else:
                current = ("Please proceed and return the agent plan now "
                           "in the required JSON format.")
        elif action == "no":
            return None
        else:
            current = f"Please revise the plan with this feedback: {detail}"


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


def _stream_approval(question, response):
    """Three-option menu: Approve / Skip / Quit. Returns 'approve'|'skip'|'quit'."""
    options  = ["Approve finding", "Skip", "Quit stream"]
    selected = 0

    content_lines = [
        "",
        color(f"  Stream · {question['id']}  (priority {question['priority']})", BOLD, CYAN),
        color("  " + "─" * 50, DIM, WHITE),
        "",
    ]
    for line in response.splitlines():
        content_lines.append(color("  " + line, WHITE))
    content_lines.append("")
    content = "\n".join(content_lines)

    while True:
        clear()
        show_header()
        print(content)
        print(color("  What would you like to do with this finding?", BOLD, CYAN))
        print(color("  (arrows · Enter to choose)", DIM, WHITE))
        print()
        for i, opt in enumerate(options):
            if i == selected:
                print(color(f"   > {opt}  ", BOLD, GOLD, REV))
            else:
                print(color(f"     {opt}", WHITE))
        print()
        key = read_key()
        if key == "up":
            selected = (selected - 1) % 3
        elif key == "down":
            selected = (selected + 1) % 3
        elif key == "enter":
            return ["approve", "skip", "quit"][selected]
        elif key in ("esc", "q", "Q"):
            return "quit"


def _try_update_stream_questions(response, yaml_path, data):
    """If Claude returned a revised question list, write it back to wizard.yaml."""
    m = re.search(r'```json\s*(\[.*?\])\s*```', response, re.DOTALL)
    if not m:
        return
    try:
        new_questions = json.loads(m.group(1))
        if isinstance(new_questions, list) and new_questions:
            data["stream"]["questions"] = new_questions
            save_wizard_yaml_data(data)
            print(color("\n  Stream questions updated in wizard.yaml.\n", BOLD, GOLD))
    except Exception:
        pass


def run_stream_mode():
    """Execute the prioritized stream question checklist against the current codebase."""
    clear()
    show_header()

    yaml_path = find_wizard_yaml()
    if not yaml_path:
        print(color("  No wizard.yaml found.\n", BOLD, GOLD))
        print(color("  Run `wizard` first to set up an orchestration.\n", DIM, WHITE))
        return

    data      = load_wizard_yaml()
    questions = sorted(
        data.get("stream", {}).get("questions", DEFAULT_STREAM_QUESTIONS),
        key=lambda q: q["priority"],
    )
    orch_plan = json.dumps(data.get("orchestrations", []), indent=2)

    git_diff      = _git_diff()
    current_commit = _current_commit()
    last_commit    = wizard_db.get_last_scanned_commit()

    stats = wizard_db.get_stream_stats()
    print(color("  Stream mode\n", BOLD, CYAN))
    print(color(f"  Questions : {len(questions)}", WHITE))
    print(color(f"  Last run  : {stats['last_run_at'] or 'never'}", WHITE))
    print(color(f"  Last commit scanned: {stats['last_commit'] or 'none'}\n", WHITE))
    time.sleep(1.2)

    run_id = wizard_db.start_stream_run(current_commit, git_diff)
    template = load_prompt("stream_question.md")

    for q in questions:
        prompt = (template
                  .replace("{question_prompt}", q["prompt"])
                  .replace("{git_diff}", git_diff[:4000])
                  .replace("{orch_plan}", orch_plan[:2000])
                  .replace("{last_commit}", last_commit or "none")
                  .replace("{current_commit}", current_commit or "unknown"))

        response = conjure(ask, prompt,
                           label=f"Stream [{q['id']}]...", model=DEFAULT_MODEL)

        if "no issues found" in response.lower() or "no changes needed" in response.lower():
            wizard_db.save_finding(run_id, q["id"], q["priority"], response, approved=False)
            clear()
            show_header()
            print(color(f"  Stream · {q['id']}  (priority {q['priority']})", BOLD, CYAN))
            print(color("  " + "─" * 50, DIM, WHITE))
            print(color(f"\n  {response}\n", DIM, WHITE))
            print(color("  (no action needed — continuing)\n", GRAY))
            time.sleep(1.5)
            continue

        action = _stream_approval(q, response)

        if action == "approve":
            wizard_db.save_finding(run_id, q["id"], q["priority"], response, approved=True)
            if q["id"] == "stream_self_improve":
                _try_update_stream_questions(response, yaml_path, data)
        elif action == "skip":
            wizard_db.save_finding(run_id, q["id"], q["priority"], response, approved=False)
        elif action == "quit":
            wizard_db.finish_stream_run(run_id, current_commit)
            print(color("\n  Stream stopped.\n", DIM, WHITE))
            return

    wizard_db.finish_stream_run(run_id, current_commit)
    clear()
    show_header()
    print(color("  Stream complete.\n", BOLD, GOLD))
    approved = sum(
        1 for q in questions
        if wizard_db.get_stream_stats()["approved_findings"]
    )
    print(color(f"  All {len(questions)} questions processed.\n", DIM, WHITE))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    wizard_yaml = find_wizard_yaml()
    if wizard_yaml:
        response  = handle_wizard_yaml(wizard_yaml)
        selection = response
    else:
        files = scan_workspace()
        if files:
            selection = plan_with_ai(files)
        else:
            while True:
                selection = select(OPTIONS, "What shall we summon today?")
                if selection == "__stream__":
                    run_stream_mode()
                    continue
                break

    if selection is None:
        print(color("\n  The staff dims. Farewell.\n", DIM, WHITE))
        return

    if selection != "done":
        print(color("\n  The staff dims. Farewell.\n", DIM, WHITE))
