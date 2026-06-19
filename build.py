#!/usr/bin/env python3
"""Build-time assembler: write the self-contained white_wizard.py.

Run `python3 build.py` after editing the WIZARD art or app code in the template
below. Produces a dependency-free white_wizard.py.
"""
APP = r'''#!/usr/bin/env python3
"""White Wizard - conjure multi-agent orchestration systems with a single button.

Iteration 1: scans the folder first. If it finds existing code it consults the AI
and offers orchestration options tailored to the project; an empty folder goes
straight to the starting menu. A loading sequence plays, then a placeholder marks
where future magic will land.

The wizard-hat art below is static ASCII; edit it directly and re-run build.py.
"""

import datetime
import itertools
import json
import os
import re
import sys
import time

from ai_client import ask, ask_with_history

# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
REV = "\033[7m"
BLINK = "\033[5m"
WHITE = "\033[97m"
GOLD = "\033[96m"
CYAN = "\033[36m"
GRAY = "\033[90m"

DEFAULT_MODEL = "claude-sonnet-4-6"

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
    """Wrap text in ANSI codes; harmless if the terminal ignores them."""
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_prompt(name):
    """Load a prompt template from the prompts/ folder next to this script."""
    path = os.path.join(SCRIPT_DIR, "prompts", name)
    with open(path) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Agent plan parsing and infographic
# ---------------------------------------------------------------------------

def parse_agent_plan(response):
    """Extract the JSON agent plan from Claude's response. Returns dict or None."""
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
    """Word-wrap text to fit within width chars. Returns list of lines."""
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


def _make_box(name, desc_lines, box_w, box_h, model=None):
    """Render an agent box as a list of exactly box_h strings (no color)."""
    rows = []
    rows.append("┌" + "─" * box_w + "┐")
    rows.append("│" + name.upper()[:box_w].center(box_w) + "│")
    rows.append("├" + "─" * box_w + "┤")
    # 4 fixed rows (top, name, sep, bottom) + 2 more if model footer (sep + model)
    content_rows = box_h - (6 if model else 4)
    for i in range(content_rows):
        if i < len(desc_lines):
            cell = (" " + desc_lines[i])[:box_w].ljust(box_w)
        else:
            cell = " " * box_w
        rows.append("│" + cell + "│")
    if model:
        rows.append("├" + "─" * box_w + "┤")
        rows.append("│" + (" Model: " + model)[:box_w].ljust(box_w) + "│")
    rows.append("└" + "─" * box_w + "┘")
    return rows


def _fail_arrow_rows(src_col, tgt_col, line_w):
    """Return two strings: the │/▲ row and the └──┘ row for one fail arc."""
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

    # Build ordered main path
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

    # Layout dimensions — fill the terminal width
    ARROW  = " ──▶ "       # 5 chars between boxes
    DONE   = " ──▶ ✓ DONE" # appended after last box on mid row
    try:
        term_w = os.get_terminal_size().columns
    except OSError:
        term_w = 80
    # 2 margin + N*(box_w+2) + (N-1)*5 arrows + 11 DONE suffix <= term_w
    box_w  = max(14, (term_w - 8 - 7 * n) // n)
    stride = box_w + 2 + len(ARROW)  # box (with borders) + arrow

    # Wrap descriptions; compute uniform box height
    box_data = []
    for name in order:
        a    = agent_map.get(name, {"name": name})
        desc = a.get("description", a.get("responsibility", ""))
        box_data.append({"name": name, "lines": _wrap(desc, box_w - 1)})

    max_desc = max(len(d["lines"]) for d in box_data)
    box_h    = max(max_desc + 6, 8)   # +6: top/name/sep/model_sep/model/bottom
    rendered = [_make_box(d["name"], d["lines"], box_w, box_h, model=DEFAULT_MODEL)
                for d in box_data]

    # Agent center columns (from left edge of margin "  ")
    centers = [i * stride + (box_w + 2) // 2 for i in range(n)]
    line_w  = centers[-1] + (box_w + 2) // 2 + len(DONE) + 4
    mid_row = box_h // 2

    out = [
        "",
        color(f"  {team_name.upper()}  ·  {team_name.replace('Create ', '').title()} Flow", BOLD, CYAN),
        color("  " + "─" * min(70, n * stride + len(DONE)), DIM, WHITE),
        "",
    ]

    # Main horizontal row of boxes
    for row_idx in range(box_h):
        parts = ["  "]
        for i, box_lines in enumerate(rendered):
            parts.append(color(box_lines[row_idx], WHITE))
            if i < n - 1:
                parts.append(color(ARROW, GOLD) if row_idx == mid_row
                              else " " * len(ARROW))
            elif row_idx == mid_row:
                parts.append(color(DONE, BOLD, GOLD))
        out.append("".join(parts))

    # Fail transition arcs drawn below the boxes
    fail_pairs = []
    for i, name in enumerate(order):
        for target in fails.get(name, []):
            if target in order:
                j = order.index(target)
                if j < i:
                    fail_pairs.append((i, j))

    # Sort so shorter arcs (closer fails) render on top, longer ones below
    fail_pairs.sort(key=lambda p: p[0] - p[1])
    for src_i, tgt_i in fail_pairs:
        r1, r2 = _fail_arrow_rows(centers[src_i], centers[tgt_i], line_w)
        out.append(color("  " + r1, GOLD))
        out.append(color("  " + r2, GOLD))

    out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# wizard.yaml detection
# ---------------------------------------------------------------------------

def find_wizard_yaml():
    """Return the path to wizard.yaml in the cwd, or None if absent."""
    path = os.path.join(os.getcwd(), "wizard.yaml")
    return path if os.path.isfile(path) else None


def handle_wizard_yaml(path):
    """wizard.yaml exists: call the AI with the configured prompt + file contents.

    Returns whatever the AI replies (mocked for now), or None if the user quit.
    """
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
               "dist", "build", ".next", "target"}


def scan_workspace():
    """Walk the current directory for code, showing a scanning loading screen.

    Returns the list of code files found. The tool may be launched in an empty
    folder or one already containing a project, so this runs before anything
    else to learn what it's working with.
    """
    root = os.getcwd()
    name = os.path.basename(root.rstrip("/")) or root
    clear()
    show_header()

    spinner = itertools.cycle("|/-\\")
    files = []
    walker = os.walk(root)
    finished = False
    end = time.time() + 1.6                       # minimum visible scan time
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
        print(color(f"  + Found {len(files)} code file{plural} in this workspace.",
                    BOLD, GOLD))
    else:
        print(color("  + No code found - this looks like a fresh workspace.",
                    BOLD, GOLD))
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
    """Read a keypress.

    Returns 'up'/'down'/'enter'/'backspace'/'esc'/'ctrl-c', 'ignore' for
    unhandled escape sequences, or the raw character typed (case preserved).
    """
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\033":                       # escape sequence
            if sys.stdin.read(1) == "[":
                code = sys.stdin.read(1)
                return {"A": "up", "B": "down"}.get(code, "ignore")
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch in ("\x7f", "\x08"):             # Backspace / Delete
            return "backspace"
        if ch == "\x03":                       # Ctrl-C — raise so finally restores terminal
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
        chosen = i == selected
        disabled = option in DISABLED_TYPES
        if option == CUSTOM_OPTION:
            body = color(custom_text, WHITE) if custom_text else color(option, GRAY)
            if chosen and not disabled:
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


def select(options, title,
           hint="(arrows to move - type to fill in 'Something else' - "
                "Enter to choose - q to quit)"):
    """Return the chosen option's text, or None if the user quit.

    The 'Something else' option is editable inline: when it is highlighted the
    user types a custom description, shown in gray with a blinking cursor.

    Falls back to numeric line-entry when stdin is not an interactive TTY (e.g.
    piped input); there the next input line fills in the custom option.
    """
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

    # Start on the first non-disabled option
    active = [i for i, o in enumerate(options) if o not in DISABLED_TYPES]
    selected = active[0] if active else 0
    custom_text = ""
    while True:
        render_menu(options, selected, title, hint, custom_text)
        key = read_key()
        on_custom = options[selected] == CUSTOM_OPTION
        if key == "up":
            idx = active.index(selected) if selected in active else 0
            selected = active[(idx - 1) % len(active)]
        elif key == "down":
            idx = active.index(selected) if selected in active else 0
            selected = active[(idx + 1) % len(active)]
        elif key == "enter":
            if on_custom:
                return custom_text.strip() or "a custom orchestration system"
            return options[selected]
        elif key == "esc":
            return None
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
APPROVAL_DETAIL = "No, "   # editable — user types their feedback after the prefix


def render_approval(selected, feedback_text, content=""):
    clear()
    show_header()
    if content:
        print(content)
    print(color("  Does this look right?", BOLD, CYAN))
    print(color("  (arrows to move · type feedback on last option · Enter to choose)",
                DIM, WHITE))
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
    """Return ('yes'|'no'|'detail', feedback_text).

    content is redrawn above the menu on every keypress so it stays visible.
    Falls back to numeric input when stdin is not a TTY.
    """
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
        key = read_key()
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
# Loading
# ---------------------------------------------------------------------------

def loading(label, seconds=3.0):
    spinner = itertools.cycle("|/-\\")
    end = time.time() + seconds
    while time.time() < end:
        sys.stdout.write("\r" + color(f"  {next(spinner)} ", GOLD) + color(label, WHITE))
        sys.stdout.flush()
        time.sleep(0.09)
    sys.stdout.write("\r" + " " * (len(label) + 6) + "\r")
    sys.stdout.flush()


def conjure(fn, *args, label="Conjuring magic...", **kwargs):
    """Run fn(*args, **kwargs) in a background thread while showing a spinner.

    The spinner stays visible for the full duration of the AI call rather than
    disappearing the moment the prompt is sent.
    """
    import threading

    result = [None]
    error = [None]
    done = threading.Event()

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
        sys.stdout.write(
            "\r" + color(f"  {next(spinner)} ", GOLD) + color(label, WHITE)
        )
        sys.stdout.flush()
        time.sleep(0.09)

    sys.stdout.write("\r" + " " * (len(label) + 6) + "\r")
    sys.stdout.flush()

    if error[0]:
        raise error[0]
    return result[0]


# ---------------------------------------------------------------------------
# AI-assisted planning (runs when the workspace already contains code)
# ---------------------------------------------------------------------------

ORCH_TYPES = [
    "Create dev team",
    "Create devops team",
    "Create performance team",
    "Create security team",
    CUSTOM_OPTION,
]

# Options grayed out and skipped in navigation until implemented
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
    """Extract ### FILE: path / ```code``` blocks from a Claude response.

    Returns a list of (relative_path, code) tuples in the order they appear.
    """
    pattern = r'### FILE: ([^\n]+)\n```[a-zA-Z]*\n(.*?)```'
    return [(p.strip(), c.strip()) for p, c in re.findall(pattern, response, re.DOTALL)]


def generate_and_write_agents(plan, team_selection, synopsis):
    """Call Claude to generate agent code from the approved plan, write files to disk."""
    template = load_prompt("generate_agents.md")
    prompt = (template
              .replace("{plan_json}", json.dumps(plan, indent=2))
              + "\n" + synopsis)

    response = conjure(ask, prompt, label="Generating agent code...", model=DEFAULT_MODEL)

    files = parse_generated_files(response)
    if not files:
        clear()
        show_header()
        print(color("  Could not parse generated files from Claude's response.\n", DIM, WHITE))
        print(color("  Raw response:\n", DIM, WHITE))
        for line in response.splitlines():
            print(color("  " + line, WHITE))
        return []

    cwd = os.getcwd()
    written = []
    for rel_path, code in files:
        full_path = os.path.join(cwd, rel_path)
        dir_path = os.path.dirname(full_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        with open(full_path, "w") as f:
            f.write(code + "\n")
        written.append(rel_path)

    # Success screen
    clear()
    show_header()
    team_label = team_selection.replace("Create ", "").title()
    print(color(f"  {team_label} — orchestration system generated\n", BOLD, CYAN))
    for rel_path in written:
        print(color(f"    {rel_path}", WHITE))
    print()
    print(color("  To run your orchestration:", DIM, WHITE))
    print(color("    python3 run_orch.py\n", BOLD, GOLD))

    return written


def display_synopsis(synopsis):
    """Print the AI's project synopsis between the scan and the team menu."""
    clear()
    show_header()
    print(color("  Project synopsis", BOLD, CYAN))
    print(color("  " + "-" * 50, DIM, WHITE))
    for line in synopsis.splitlines():
        print(color("  " + line, WHITE))
    print()


def save_wizard_yaml(plan, team_selection, model):
    """Persist a completed orchestration to wizard.yaml in the current directory.

    Uses JSON format (valid YAML superset) so no third-party deps are needed.
    Appends to existing data if the file already exists and is parseable.
    """
    path = os.path.join(os.getcwd(), "wizard.yaml")
    data = {"settings": {"default_model": model}, "orchestrations": []}
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            pass  # start fresh if the file is corrupt or non-JSON

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
    data.setdefault("orchestrations", []).append(entry)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def run_team_conversation(team_selection, synopsis, custom_description=""):
    """Send the team prompt to the AI and run a conversation loop.

    claude -p is stateless, so the full conversation history is serialized into
    each subsequent call. Claude either returns a JSON agent plan (rendered as
    an infographic) or plain text (usually follow-up questions). The approval
    menu gives the user three explicit choices after each round.
    """
    prompt_file = TEAM_PROMPTS.get(team_selection, "team_custom.md")
    template = load_prompt(prompt_file)
    if "{team_description}" in template:
        template = template.replace("{team_description}", custom_description)
    initial_prompt = template + "\n" + synopsis

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
                # Claude asked questions; user says proceed → ask Claude to return the plan
                current = ("Please proceed and return the agent plan now "
                           "in the required JSON format.")
        elif action == "no":
            return None   # caller loops back to team selection
        else:   # detail
            current = f"Please revise the plan with this feedback: {detail}"


def plan_with_ai(files):
    """Found code in the folder: analyse it, show synopsis, pick a team, converse.

    Returns the final AI response, or None if the user quit.
    """
    prompt = load_prompt("analyze_codebase.md") + "\n".join(files[:60])

    synopsis = conjure(ask, prompt, label="Analysing codebase...", model=DEFAULT_MODEL)

    display_synopsis(synopsis)
    time.sleep(0.6)

    while True:
        team = select(ORCH_TYPES, "Which team do you want to set up?")
        if team is None:
            return None
        custom_desc = team if team not in TEAM_PROMPTS and team != CUSTOM_OPTION else ""
        result = run_team_conversation(team, synopsis, custom_description=custom_desc)
        if result is not None:
            return result
        # None means user pressed No — loop back to team selection


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    wizard_yaml = find_wizard_yaml()
    if wizard_yaml:
        response = handle_wizard_yaml(wizard_yaml)
        # TODO: use AI response to drive next steps (coming next iteration)
        selection = response
    else:
        files = scan_workspace()
        if files:
            selection = plan_with_ai(files)
        else:
            selection = select(OPTIONS, "What shall we summon today?")

    if selection is None:
        print(color("\n  The staff dims. Farewell.\n", DIM, WHITE))
        return

    if selection != "done":
        # Fallback for paths that don't generate (e.g. wizard.yaml branch)
        print(color("\n  The staff dims. Farewell.\n", DIM, WHITE))


if __name__ == "__main__":
    from ai_client import parse_args
    parse_args()
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print(color("\n\n  The staff dims. Farewell.\n", DIM, WHITE))
'''

with open("white_wizard.py", "w") as fh:
    fh.write(APP)

print("Wrote white_wizard.py")
