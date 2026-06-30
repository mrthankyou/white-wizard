"""Terminal UI primitives and the reusable menu engine for White Wizard.

Every option screen in the app is one ``menu()`` call: pass a list of
``Option``s plus an optional top-of-page renderer, and the engine owns arrow
navigation, the selection highlight, text-input options, cancel keys, and the
non-TTY fallback. Adding a new page is a declaration, not another copy of the
render/read loop.
"""
import sys

# ANSI styling ---------------------------------------------------------------

RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
REV   = "\033[7m"
BLINK = "\033[5m"
WHITE = "\033[97m"
GOLD  = "\033[96m"
CYAN  = "\033[36m"
GRAY  = "\033[90m"
BLUE        = "\033[34m"
BRIGHT_BLUE = "\033[94m"
# Amber/orange — the complement of the blue theme, used for danger/warnings.
DANGER      = "\033[38;5;208m"

WIZARD = r"""
             /\
            /  \
           / ** \
          /  *   \
         /   **   \
        /____________\
   ____/______________\____
  (________________________)
"""


def color(text, *codes):
    return "".join(codes) + text + RESET


# Blue-flame spinner -----------------------------------------------------------
# Three diamonds that flicker through a blue→white gradient, out of phase, so the
# loading indicator reads like a small blue fire instead of one spinning diamond.

_FLAME_RAMP  = (BLUE, BRIGHT_BLUE, CYAN, GOLD, WHITE)  # cool → hot
_FLAME_GLYPH = ("◇", "◇", "◈", "◆", "◆")              # dim → bright


def _flame_frames():
    span   = len(_FLAME_RAMP)
    period = 2 * (span - 1)               # triangle wave: cool → hot → cool
    frames = []
    for t in range(period):
        cells = []
        for pos in range(3):
            phase = (t + pos * 3) % period   # each diamond offset for a lively shimmer
            level = phase if phase < span else period - phase
            cells.append(_FLAME_RAMP[level] + _FLAME_GLYPH[level] + RESET)
        frames.append(" ".join(cells))
    return frames


# Pre-rendered, fully coloured frames; cycle with itertools.cycle(SPINNER_FRAMES).
SPINNER_FRAMES = _flame_frames()


def clear():
    print("\033[2J\033[H", end="")


def show_header():
    print(color(WIZARD, BOLD, WHITE))
    print(color("              The White Wizard", BOLD, GOLD))
    print(color("   Conjure multi-agent orchestration with a single button.", DIM, WHITE))
    print()


def show_header_compact():
    """Hat only — no title/subtitle. Used in build and result screens."""
    print(color(WIZARD, BOLD, WHITE))


def read_key():
    import termios
    import tty

    # Without a real terminal (piped input, some CI/IDE shells) raw mode and
    # termios calls fail; degrade to a line read so "press any key" screens and
    # cancels don't crash with an opaque termios error.
    if not sys.stdin.isatty():
        ch = sys.stdin.read(1)
        return "enter" if ch else "esc"

    fd  = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\033":
            if sys.stdin.read(1) == "[":
                code = sys.stdin.read(1)
                return {"A": "up", "B": "down", "Z": "shift_tab"}.get(code, "ignore")
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


# Menu engine ----------------------------------------------------------------

class Option:
    """One row in a menu.

    value        what ``menu()`` returns when this row is chosen.
    label        text shown for a normal row.
    text_input   if True, the user types into this row; the typed text is
                 returned alongside the value.
    disabled     shown greyed-out as "(coming soon)" and skipped by navigation.
    prefix       text shown before the typed text on an input row (already
                 coloured, e.g. a grey "No, ").
    placeholder  greyed hint shown on an input row before anything is typed.
    """

    def __init__(self, value, label="", *, text_input=False, disabled=False,
                 prefix="", placeholder=""):
        self.value       = value
        self.label       = label
        self.text_input  = text_input
        self.disabled    = disabled
        self.prefix      = prefix
        self.placeholder = placeholder


def _render_option(opt, chosen, text):
    if opt.disabled:
        print("     " + color(opt.label + "  (coming soon)", GRAY))
        return
    if opt.text_input:
        if chosen:
            shown = color(text, WHITE) if text else (
                color(opt.placeholder, GRAY) if opt.placeholder else "")
            print(color("   > ", BOLD, GOLD) + opt.prefix + shown
                  + color("_", GRAY, BLINK))
        elif opt.prefix:
            print("     " + opt.prefix + (color(text, WHITE) if text else ""))
        else:
            print("     " + (color(text, WHITE) if text else color(opt.label, WHITE)))
        return
    if chosen:
        print(color(f"   > {opt.label}  ", BOLD, GOLD, REV))
    else:
        print(color(f"     {opt.label}", WHITE))


def _menu_no_tty(options, title):
    """Fallback for piped input: print a numbered list and read a choice."""
    clear()
    show_header()
    if title:
        print(color("  " + title, BOLD, CYAN))
    active = [o for o in options if not o.disabled]
    for i, o in enumerate(active, start=1):
        print(f"   {i}. {o.label}")
    raw = sys.stdin.readline().strip()
    if not (raw.isdigit() and 1 <= int(raw) <= len(active)):
        return None, ""
    chosen = active[int(raw) - 1]
    if chosen.text_input:
        return chosen.value, sys.stdin.readline().strip()
    return chosen.value, ""


def menu(options, title="", hint="", render_top=None, footer=None, extra_keys=None):
    """Run an arrow-key menu page. Returns ``(value, text)``.

    value : the chosen ``Option.value``, a value from ``extra_keys``, or None if
            the user cancelled (esc, or q/Q while not editing a text field).
    text  : stripped text typed into the chosen text-input option, else "".

    render_top  optional callable that prints content above the options.
    footer      optional callable that prints below the options.
    extra_keys  map of single keypress -> value to return (ignored while the
                selected row is a text field).
    """
    extra_keys = extra_keys or {}
    if not sys.stdin.isatty():
        return _menu_no_tty(options, title)

    active = [i for i, o in enumerate(options) if not o.disabled]
    if not active:
        active = list(range(len(options)))
    selected = active[0]
    texts    = {}

    while True:
        clear()
        show_header()
        if render_top:
            render_top()
        if title:
            print(color("  " + title, BOLD, CYAN))
        if hint:
            print(color("  " + hint, DIM, WHITE))
        print()
        for i, opt in enumerate(options):
            _render_option(opt, i == selected, texts.get(i, ""))
        print()
        if footer:
            footer()

        key     = read_key()
        cur     = options[selected]
        on_text = cur.text_input

        if key == "up":
            idx = active.index(selected) if selected in active else 0
            selected = active[(idx - 1) % len(active)]
        elif key == "down":
            idx = active.index(selected) if selected in active else 0
            selected = active[(idx + 1) % len(active)]
        elif key == "enter":
            return cur.value, texts.get(selected, "").strip()
        elif key == "esc":
            return None, ""
        elif key in extra_keys and (not on_text or len(key) > 1):
            # Single-char extra keys are typed into a text row; multi-char
            # control keys (e.g. shift+tab) always fire so they work everywhere.
            return extra_keys[key], ""
        elif key in ("q", "Q") and not on_text:
            return None, ""
        elif on_text:
            if key == "backspace":
                texts[selected] = texts.get(selected, "")[:-1]
            elif len(key) == 1 and key.isprintable():
                texts[selected] = texts.get(selected, "") + key


# ---------------------------------------------------------------------------
# Curses split-pane live UI
# ---------------------------------------------------------------------------

import time as _time

_CP_GOLD   = 1
_CP_CYAN   = 2
_CP_GREEN  = 3
_CP_DIM    = 4
_CP_SELECT = 5
_CP_WHITE  = 6
_CP_BLUE   = 7
_CP_RED    = 8

# Three "magic diamond" flame shown beside each running task — the same blue→
# white shimmer as the loading spinner, rebuilt for curses (the SPINNER_FRAMES
# strings embed ANSI codes curses can't render). Each diamond is phase-shifted
# so the three flicker out of step; the frame is chosen by wall-clock time, so
# it animates as the split pane redraws.
_FLAME_GLYPH  = ("◇", "◇", "◈", "◆", "◆")   # cool/dim → hot/bright
_FLAME_SPAN   = len(_FLAME_GLYPH)
_FLAME_PERIOD = 2 * (_FLAME_SPAN - 1)        # triangle wave: cool → hot → cool


def _flame_attr(level):
    import curses
    ramp = (
        curses.color_pair(_CP_BLUE),                    # blue
        curses.color_pair(_CP_BLUE)  | curses.A_BOLD,   # bright blue
        curses.color_pair(_CP_CYAN),                    # cyan
        curses.color_pair(_CP_GOLD),                    # gold
        curses.color_pair(_CP_WHITE) | curses.A_BOLD,   # white
    )
    return ramp[level]


def _sp_draw_flame(win, y, x):
    """Draw the animated three-diamond magic flame at columns x..x+2."""
    t = int(_time.time() * 8)
    for pos in range(3):
        phase = (t + pos * 3) % _FLAME_PERIOD
        level = phase if phase < _FLAME_SPAN else _FLAME_PERIOD - phase
        _sp_addstr(win, y, x + pos, _FLAME_GLYPH[level], _flame_attr(level))


def _sp_draw_shimmer(win, y, x, text):
    """Draw text with an animated per-character colour wave (the flame palette),
    so the word itself flickers blue→white as the pane redraws."""
    import curses
    t = int(_time.time() * 8)
    for i, ch in enumerate(text):
        phase = (t + i) % _FLAME_PERIOD
        level = phase if phase < _FLAME_SPAN else _FLAME_PERIOD - phase
        _sp_addstr(win, y, x + i, ch, _flame_attr(level) | curses.A_BOLD)


# Gently rotating progress phrases for the running-task bar — cosmetic liveness
# (the flame + elapsed timer carry the real signal). Cycled by wall-clock time.
_STATUS_PHRASES = (
    "Conjuring results…",
    "Weaving it together…",
    "Working the magic…",
)


def _status_label(status):
    """Human-readable label + curses color-pair id for a task status, shown above
    the highlighted task's description in the right pane."""
    return {
        "done":      ("✓ Completed",     _CP_GREEN),
        "pending":   ("○ To do",         _CP_GOLD),
        "running":   ("● In progress",   _CP_CYAN),
        "failed":    ("✗ Cancelled",     _CP_RED),
        "suggested": ("● Suggested fix", _CP_BLUE),
    }.get(status, (f"• {status or 'unknown'}", _CP_DIM))


def _format_task_time(ts):
    """Format a SQLite UTC datetime string to local 'Mon D HH:MM' ('' if bad)."""
    import datetime
    try:
        dt_utc = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=datetime.timezone.utc)
        dt_local = dt_utc.astimezone()
        return dt_local.strftime("%b %-d %H:%M")
    except (ValueError, TypeError):
        return ""


def _elapsed_str(updated_at):
    """Human elapsed time since a SQLite UTC 'updated_at' timestamp ('' if bad)."""
    import datetime
    try:
        started = datetime.datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
        started = started.replace(tzinfo=datetime.timezone.utc)
    except (ValueError, TypeError):
        return ""
    secs = max(0, int((datetime.datetime.now(datetime.timezone.utc) - started)
                      .total_seconds()))
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    return f"{m}m {s}s"


def _wrap_hard(text, width):
    """Wrap text to width, hard-splitting tokens longer than width so nothing is
    clipped. Returns a list of lines."""
    width = max(1, width)
    words = []
    for w in text.split():
        while len(w) > width:
            words.append(w[:width]); w = w[width:]
        words.append(w)
    lines, line = [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            lines.append(line); line = word
        else:
            line = (line + " " + word).strip()
    if line:
        lines.append(line)
    return lines


def _wrap_chars(text, width):
    """Char-level wrap into fixed-width visual lines, giving an exact
    index↔(line, col) mapping (line = i // width, col = i % width) for cursor
    math. A trailing empty line is added when the text is empty or ends exactly
    on a line boundary, so the cursor always has a cell at the end."""
    width = max(1, width)
    lines = [text[k:k + width] for k in range(0, len(text), width)]
    if not lines or len(text) % width == 0:
        lines.append("")
    return lines


# Shared multi-line text-field editor — used by the main split pane and the
# build/selection menu so the prompt experience (soft-wrap, blinking block
# caret, gray placeholder, cursor nav) is identical everywhere.

def _te_render(win, y, x, field_w, display, attr, caret):
    """Draw ``display`` soft-wrapped from (y, x) within ``field_w`` columns, with
    an optional reverse-video block caret at index ``caret`` (None = no caret).
    Returns the number of rows drawn."""
    import curses
    lines = _wrap_chars(display, field_w)
    for li, ln in enumerate(lines):
        _sp_addstr(win, y + li, x, ln, attr)
    if caret is not None:
        c_line, c_col = caret // field_w, caret % field_w
        if 0 <= c_line < len(lines):
            ln = lines[c_line]
            cell = ln[c_col] if c_col < len(ln) else " "
            _sp_addstr(win, y + c_line, x + c_col, cell or " ",
                       curses.color_pair(_CP_WHITE) | curses.A_REVERSE | curses.A_BOLD)
    return len(lines)


def _te_edit(ed, key, field_w):
    """Apply an editing keypress to ``ed`` = {"text", "cursor", "goal_col"} in
    place. Returns:
      "edit"                       — consumed as text editing or a cursor move;
      "top"/"bottom"/"left"/"right" — the cursor is already at that edge, so the
                                      caller decides what crossing it means;
      None                          — ``key`` isn't an editing key at all.
    """
    import curses
    text, cur = ed["text"], ed["cursor"]
    if key == curses.KEY_LEFT:
        if cur > 0:
            ed["cursor"] = cur - 1; ed["goal_col"] = ed["cursor"] % field_w
            return "edit"
        return "left"
    if key == curses.KEY_RIGHT:
        if cur < len(text):
            ed["cursor"] = cur + 1; ed["goal_col"] = ed["cursor"] % field_w
            return "edit"
        return "right"
    if key == curses.KEY_UP:
        lines, c_line = _wrap_chars(text, field_w), cur // field_w
        if c_line > 0:
            prev = lines[c_line - 1]
            ed["cursor"] = min((c_line - 1) * field_w + min(ed["goal_col"], len(prev)), len(text))
            return "edit"
        return "top"
    if key == curses.KEY_DOWN:
        lines, c_line = _wrap_chars(text, field_w), cur // field_w
        if c_line < len(lines) - 1:
            nxt = lines[c_line + 1]
            ed["cursor"] = min((c_line + 1) * field_w + min(ed["goal_col"], len(nxt)), len(text))
            return "edit"
        return "bottom"
    if key in (curses.KEY_BACKSPACE, 127, 8):
        if cur > 0:
            ed["text"] = text[:cur - 1] + text[cur:]
            ed["cursor"] = cur - 1; ed["goal_col"] = ed["cursor"] % field_w
        return "edit"
    if 32 <= key < 127:
        ed["text"] = text[:cur] + chr(key) + text[cur:]
        ed["cursor"] = cur + 1; ed["goal_col"] = ed["cursor"] % field_w
        return "edit"
    return None


WIZARD_COMPACT = [
    "       /\\",
    "      /  \\",
    "     / ** \\",
    "    /______\\",
    "___/________\\___",
    "(_______________)",
]


def _sp_setup_colors():
    import curses
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_CP_GOLD,   curses.COLOR_YELLOW, -1)
    curses.init_pair(_CP_CYAN,   curses.COLOR_CYAN,   -1)
    curses.init_pair(_CP_GREEN,  curses.COLOR_GREEN,  -1)
    curses.init_pair(_CP_DIM,    -1, -1)
    curses.init_pair(_CP_SELECT, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(_CP_WHITE,  curses.COLOR_WHITE,  -1)
    curses.init_pair(_CP_BLUE,   curses.COLOR_BLUE,   -1)
    curses.init_pair(_CP_RED,    curses.COLOR_RED,    -1)


def _sp_addstr(win, y, x, text, attr=0):
    """curses addstr that silently clips and ignores out-of-bounds."""
    import curses
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        avail = w - x - 1
        if avail <= 0:
            return
        win.addstr(y, x, text[:avail], attr)
    except curses.error:
        pass


def _sp_draw_left(win, state):
    import curses
    win.erase()
    h, left_w = win.getmaxyx()
    y = 0

    # Wizard hat
    hat_attr = curses.color_pair(_CP_WHITE) | curses.A_BOLD
    for line in WIZARD_COMPACT:
        _sp_addstr(win, y, 1, line, hat_attr)
        y += 1
    y += 1

    # Alerts (shown once — caller clears after providing)
    for alert in state.get("alerts", []):
        _sp_addstr(win, y, 1, f"✓ {alert}"[:left_w - 2],
                   curses.color_pair(_CP_GOLD) | curses.A_BOLD)
        y += 1
    if state.get("alerts"):
        y += 1

    # Orch info
    orch = state.get("current_orch")
    orchs = state.get("orchs", [])
    if orch:
        team = orch.get("team", "Orchestration")
        pos = f" [{state.get('sel_orch_i', 0) + 1}/{len(orchs)}]" if len(orchs) > 1 else ""
        _sp_addstr(win, y, 1, f"{team}{pos}"[:left_w - 2],
                   curses.color_pair(_CP_GOLD) | curses.A_BOLD)
        y += 1
        if state.get("mode") == "manage":
            _sp_addstr(win, y, 1, "› Manage",
                       curses.color_pair(_CP_DIM) | curses.A_DIM)
            y += 1
        orch_name = orch.get("orchestrator", "")
        _sp_addstr(win, y, 1, f"orch: {orch_name}"[:left_w - 2],
                   curses.color_pair(_CP_DIM) | curses.A_DIM)
        y += 1
        for n, name in enumerate(state.get("agent_names", [])[:6], 1):
            _sp_addstr(win, y, 2, f"{n}. {name}"[:left_w - 3],
                       curses.color_pair(_CP_WHITE))
            y += 1
        y += 1

    # Options
    opts   = state.get("opts", [])
    opt_i  = state.get("opt_i", 0)
    text   = state.get("text", "")
    cursor = state.get("cursor", 0)
    focus  = state.get("focus", "left")

    for i, opt in enumerate(opts):
        if y >= h - 1:
            break
        selected = (i == opt_i)
        if opt.disabled:
            _sp_addstr(win, y, 1, f"  {opt.label}  (coming soon)"[:left_w - 2],
                       curses.color_pair(_CP_DIM) | curses.A_DIM)
        elif selected and opt.text_input:
            # Multi-line editable field: soft-wrap the text so all of it shows,
            # with a blinking block caret at the cursor. The first line carries
            # the "> " prompt; wrapped lines align under it.
            _sp_addstr(win, y, 1, "> ", curses.color_pair(_CP_GOLD) | curses.A_BOLD)
            field_w = max(1, left_w - 4)
            if text:
                display, base_attr, cur = text, curses.color_pair(_CP_WHITE), cursor
            else:
                # Grayed placeholder reads as a hint, not a value — signals the
                # field is empty and typable.
                display = opt.placeholder or opt.label
                base_attr, cur = curses.color_pair(_CP_DIM) | curses.A_DIM, 0
            # Block caret only while the left pane has focus (that's where typing
            # lands) and on the blink-on half-second.
            caret = cur if (focus == "left" and int(_time.time() * 2) % 2 == 0) else None
            y += _te_render(win, y, 3, field_w, display, base_attr, caret) - 1
            # (trailing y += 1 below adds the first line)
        elif selected:
            # Bright highlight only while the left pane has focus; a calmer gold
            # when focus is on the task pane, so it's clear where input goes.
            attr = (curses.color_pair(_CP_SELECT) | curses.A_BOLD
                    if state.get("focus", "left") == "left"
                    else curses.color_pair(_CP_GOLD) | curses.A_BOLD)
            _sp_addstr(win, y, 1, f"> {opt.label}"[:left_w - 2], attr)
        else:
            # A text-input option with a kept draft shows that draft (collapsed
            # to its row) instead of its label/placeholder, so typed text isn't
            # hidden once the option is no longer selected.
            saved = state.get("texts", {}).get(opt.value, "") if opt.text_input else ""
            _sp_addstr(win, y, 3, (saved or opt.label)[:left_w - 4],
                       curses.color_pair(_CP_WHITE))
        y += 1

    win.noutrefresh()


def _sp_draw_right(win, state):
    import curses
    win.erase()
    h, right_w = win.getmaxyx()

    if state.get("task_detail"):
        _sp_draw_task_detail(win, state)
        return

    orch = state.get("current_orch")
    if not orch:
        _sp_addstr(win, h // 2, 2, "No orchestration yet.",
                   curses.color_pair(_CP_DIM) | curses.A_DIM)
        win.noutrefresh()
        return

    stream_enabled = state.get("stream_enabled", True)
    focus_right    = state.get("focus", "left") == "right"
    tasks          = state.get("tasks", [])
    task_i         = state.get("task_i", 0)
    team           = orch.get("team", "Orchestration")

    # Header
    y = 0
    _sp_addstr(win, y, 2, team[:right_w // 2],
               curses.color_pair(_CP_GOLD) | curses.A_BOLD)
    status_str  = "● streaming" if stream_enabled else "‖ paused"
    status_attr = (curses.color_pair(_CP_GREEN) if stream_enabled
                   else curses.color_pair(_CP_GOLD) | curses.A_BOLD)
    _sp_addstr(win, y, max(2, right_w - len(status_str) - 2), status_str, status_attr)
    y += 1
    _sp_addstr(win, y, 2, "─" * max(0, right_w - 4),
               curses.color_pair(_CP_DIM) | curses.A_DIM)
    y += 2  # divider + blank

    # Live status bar — shown only while a task is actually in progress. The
    # headline is the running task's own description (clean, already readable)
    # plus an elapsed timer; the raw streamed tail is demoted to a dim detail.
    running_task = next((t for t in tasks if t.get("status") == "running"), None)
    if running_task:
        desc    = (running_task.get("description") or "").strip()
        elapsed = _elapsed_str(running_task.get("updated_at"))
        bar_dim   = curses.color_pair(_CP_DIM) | curses.A_DIM
        bar_white = curses.color_pair(_CP_WHITE)

        _sp_draw_shimmer(win, y, 2, "Working")
        if elapsed:
            _sp_addstr(win, y, 2 + len("Working"), f" · {elapsed}",
                       curses.color_pair(_CP_GOLD) | curses.A_BOLD)
        y += 1
        # Cap the headline to 2 lines so a long task description can't crowd out
        # the task list below (or push the footer off-screen).
        desc_lines = _wrap_hard(desc, right_w - 6)
        if len(desc_lines) > 2:
            desc_lines = desc_lines[:2]
            desc_lines[-1] = desc_lines[-1][:max(0, right_w - 7)] + "…"
        for ln in desc_lines:
            _sp_addstr(win, y, 4, ln, bar_white)
            y += 1
        # A gently rotating progress phrase (cosmetic — the flame + timer already
        # signal liveness; the description above is the real "what").
        phrase = _STATUS_PHRASES[int(_time.time() / 3.5) % len(_STATUS_PHRASES)]
        _sp_addstr(win, y, 4, phrase, bar_dim)
        y += 1
        _sp_addstr(win, y, 2, "─" * max(0, right_w - 4), bar_dim)
        y += 2

    # Pre-compute selected task for footer status + description.
    sel = tasks[task_i] if (focus_right and 0 <= task_i < len(tasks)) else None
    sel_status_label = sel_status_cp = None
    desc_footer_lines = []
    if sel:
        sel_status_label, sel_status_cp = _status_label(sel.get("status", "pending"))
        d = (sel.get("description") or "").strip()
        if d:
            wrapped = _wrap_hard(d, right_w - 4)
            desc_footer_lines = wrapped[:3]
            if len(wrapped) > 3:
                desc_footer_lines[-1] = desc_footer_lines[-1][:max(0, right_w - 5)] + "…"
    status_lines = 1 if sel_status_label else 0

    # Task list — scrolls to keep the highlighted task in view when navigating.
    task_area_h = max(0, h - 6 - len(desc_footer_lines) - status_lines)
    scroll_info = ""

    if not tasks:
        empty_msg = ("No tasks yet — stream is scanning..." if stream_enabled
                     else "No tasks yet — stream is paused.")
        _sp_addstr(win, y, 2, empty_msg,
                   curses.color_pair(_CP_DIM) | curses.A_DIM)
    else:
        n     = len(tasks)
        avail = max(1, task_area_h - y)
        # Centre the window on the cursor while navigating the pane; otherwise
        # show the newest tasks from the top.
        if focus_right and n > avail:
            offset = max(0, min(task_i - avail // 2, n - avail))
        else:
            offset = 0
        end = min(n, offset + avail)
        if n > avail:
            scroll_info = f"{offset + 1}–{end}/{n}"

        for i in range(offset, end):
            task        = tasks[i]
            status      = task.get("status", "pending")
            task_type   = (task.get("task_type") or "")[:8]
            desc        = task.get("description", "")
            # Anything that isn't a user-submitted task is a recurring scan; mark
            # it with a ↻ glyph so the two kinds are distinguishable at a glance.
            is_scan     = bool(task_type) and task_type != "user"
            label_w     = len(task_type) + (2 if is_scan else 0)  # room for "↻ "
            type_col    = max(2, right_w - label_w - 3)
            highlighted = focus_right and i == task_i

            if status == "running":
                _sp_draw_flame(win, y, 1)   # three magic diamonds at cols 1-3
                desc_x, desc_attr = 5, curses.color_pair(_CP_WHITE) | curses.A_BOLD
            else:
                if status == "done":
                    icon, icon_attr = "✓", curses.color_pair(_CP_GREEN)
                    desc_attr = curses.color_pair(_CP_DIM) | curses.A_DIM
                elif status == "failed":
                    icon, icon_attr = "✗", curses.color_pair(_CP_RED)
                    desc_attr = curses.color_pair(_CP_DIM) | curses.A_DIM
                elif status == "suggested":  # actionable scan finding — Enter to fix
                    # A blue dot (not a ✓, which would imply it's already done).
                    icon, icon_attr = "●", curses.color_pair(_CP_BLUE) | curses.A_BOLD
                    desc_attr = curses.color_pair(_CP_WHITE)
                elif not stream_enabled:    # pending but the stream is paused
                    icon, icon_attr = "‖", curses.color_pair(_CP_GOLD) | curses.A_BOLD
                    desc_attr = curses.color_pair(_CP_WHITE)
                else:                        # pending, queued to run
                    icon, icon_attr = "·", curses.color_pair(_CP_DIM) | curses.A_DIM
                    desc_attr = curses.color_pair(_CP_WHITE)
                desc_x = 4
                _sp_addstr(win, y, 2, icon, icon_attr)

            if highlighted:
                desc_attr = curses.color_pair(_CP_SELECT) | curses.A_BOLD

            max_desc = max(0, type_col - desc_x - 1)
            _sp_addstr(win, y, desc_x, desc[:max_desc], desc_attr)
            if is_scan:   # recurring-scan marker, then the type label
                _sp_addstr(win, y, type_col, "↻", curses.color_pair(_CP_CYAN))
                _sp_addstr(win, y, type_col + 2, task_type,
                           curses.color_pair(_CP_DIM) | curses.A_DIM)
            else:
                _sp_addstr(win, y, type_col, task_type,
                           curses.color_pair(_CP_DIM) | curses.A_DIM)
            y += 1

    # Footer — context-sensitive hints / status
    y = max(y + 1, h - 5 - len(desc_footer_lines) - status_lines)
    _sp_addstr(win, y, 2, "─" * max(0, right_w - 4),
               curses.color_pair(_CP_DIM) | curses.A_DIM)
    ts = _format_task_time(sel.get("updated_at") or sel.get("created_at", "")) if sel else ""
    if ts:
        _sp_addstr(win, y, 2, f" {ts} ", curses.color_pair(_CP_DIM) | curses.A_DIM)
    if scroll_info:
        scroll_tag = f" {scroll_info} "
        _sp_addstr(win, y, max(2, right_w - len(scroll_tag) - 2), scroll_tag,
                   curses.color_pair(_CP_GOLD))
    y += 1

    if sel_status_label:
        _sp_addstr(win, y, 2, sel_status_label,
                   curses.color_pair(sel_status_cp) | curses.A_BOLD)
        y += 1
    for ln in desc_footer_lines:
        _sp_addstr(win, y, 2, ln, curses.color_pair(_CP_WHITE))
        y += 1

    gold = curses.color_pair(_CP_GOLD) | curses.A_BOLD
    dim  = curses.color_pair(_CP_DIM) | curses.A_DIM

    if focus_right:
        if sel and sel.get("status") == "suggested":
            _sp_addstr(win, y, 2, "press Enter to fix this finding (runs in background)", gold)
            y += 1
            _sp_addstr(win, y, 2, "↑↓ select   ← back", dim)
        elif sel and sel.get("status") == "done":
            _sp_addstr(win, y, 2, "press Enter to view commit & diff", gold)
            y += 1
            _sp_addstr(win, y, 2, "↑↓ select   ← back", dim)
        elif sel and sel.get("status") == "pending" and not stream_enabled:
            _sp_addstr(win, y, 2, "press Enter to run this task (in background)", gold)
            y += 1
            _sp_addstr(win, y, 2, "↑↓ select   ← back", dim)
        elif sel and sel.get("status") == "pending":
            _sp_addstr(win, y, 2, "stream on — runs automatically", dim)
            y += 1
            _sp_addstr(win, y, 2, "↑↓ select   ← back", dim)
        else:
            _sp_addstr(win, y, 2, "↑↓ select   ← back", dim)
    else:
        # Not in the task pane — point the user at actionable items and how to
        # fire them (→ to enter the pane, then Enter to run the highlighted one).
        if any(t.get("status") == "suggested" for t in tasks):
            _sp_addstr(win, y, 2, "→ pick a finding, then Enter to fix it", gold)
        elif not stream_enabled and any(t.get("status") == "pending" for t in tasks):
            _sp_addstr(win, y, 2, "‖ paused — → pick a task, then Enter to run it", gold)
        elif not stream_enabled:
            _sp_addstr(win, y, 2, "‖ stream paused", dim)
        else:
            _sp_addstr(win, y, 2, "● streaming", curses.color_pair(_CP_GREEN))

    win.noutrefresh()


def _sp_draw_task_detail(win, state):
    """Render the task detail view in the right pane: description, commit, diff."""
    import curses
    win.erase()
    h, w = win.getmaxyx()
    detail = state.get("task_detail", {})
    scroll = state.get("detail_scroll", 0)

    gold  = curses.color_pair(_CP_GOLD)  | curses.A_BOLD
    dim   = curses.color_pair(_CP_DIM)   | curses.A_DIM
    green = curses.color_pair(_CP_GREEN)
    red   = curses.color_pair(_CP_RED)
    cyan  = curses.color_pair(_CP_CYAN)

    # Fixed header: task description (up to 2 lines)
    y = 0
    desc_lines = _wrap_hard((detail.get("description") or "").strip(), w - 4)[:2]
    for ln in desc_lines:
        _sp_addstr(win, y, 2, ln[:w - 4], gold)
        y += 1
    _sp_addstr(win, y, 2, "─" * max(0, w - 4), dim)
    y += 1
    header_h = y

    # Fixed footer height (separator + hint)
    footer_h = 2
    body_h   = max(1, h - header_h - footer_h)

    # Build body lines: commit summary first, then the diff with line numbers.
    # Each entry is (lnum_str, content_str, lnum_attr, content_attr).
    # lnum_str is 5 chars wide ("NNNN " or "     " for non-code lines).
    import re as _re
    _hunk_re = _re.compile(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')
    NO_LNUM = "     "

    body_lines = []  # (lnum, content, lnum_attr, content_attr)
    commit  = detail.get("commit")
    summary = detail.get("summary", "")
    if commit:
        body_lines.append((NO_LNUM, f"commit {commit}", dim, dim))
    if summary:
        body_lines.append((NO_LNUM, summary, dim, dim))
    if body_lines:
        body_lines.append((NO_LNUM, "", dim, dim))

    old_line = new_line = 0
    for raw in detail.get("diff_lines", []):
        m = _hunk_re.match(raw)
        if m:
            old_line = int(m.group(1))
            new_line = int(m.group(2))
            body_lines.append((NO_LNUM, raw, cyan, cyan))
        elif raw.startswith("+") and not raw.startswith("+++"):
            lnum = f"{new_line:4d} "
            new_line += 1
            body_lines.append((lnum, raw, green, green))
        elif raw.startswith("-") and not raw.startswith("---"):
            lnum = f"{old_line:4d} "
            old_line += 1
            body_lines.append((lnum, raw, red, red))
        elif raw.startswith(" "):  # context line
            lnum = f"{new_line:4d} "
            old_line += 1
            new_line += 1
            body_lines.append((lnum, raw, dim, dim))
        else:
            body_lines.append((NO_LNUM, raw, dim, dim))

    # Publish the max valid scroll back to the shared task_detail dict so the
    # key handler can clamp KEY_DOWN (this dict is the same object held in ui).
    max_scroll = max(0, len(body_lines) - body_h)
    detail["_max_scroll"] = max_scroll
    scroll = min(scroll, max_scroll)

    # Scroll and render body; line number and content colored independently
    lnum_w   = len(NO_LNUM)
    content_x = 2 + lnum_w
    visible  = body_lines[scroll: scroll + body_h]
    for lnum, content, lnum_attr, content_attr in visible:
        _sp_addstr(win, y, 2, lnum, lnum_attr | curses.A_DIM)
        _sp_addstr(win, y, content_x, content[:max(0, w - content_x - 2)], content_attr)
        y += 1

    # Fixed footer
    y = h - footer_h
    _sp_addstr(win, y, 2, "─" * max(0, w - 4), dim)
    y += 1
    has_more = (scroll + body_h) < len(body_lines)
    hint = "↑↓ scroll" if body_lines else ""
    if has_more:
        hint += "  ▼ more"
    hint += "   ← back"
    _sp_addstr(win, y, 2, hint.strip(), dim)

    win.noutrefresh()


def run_split_pane(get_state_fn, get_options_fn, handle_choice_fn,
                   handle_task_fn=None):
    """Run a curses split-pane UI.

    get_state_fn(sel_orch_i) -> dict   fresh data each second
    get_options_fn(state)    -> list   Option objects for left pane
    handle_choice_fn(choice, text, state) -> None | "__refresh__" | "__quit__"
      Called with curses already suspended (endwin called by run_split_pane).
    handle_task_fn(task, state) -> None | "__refresh__" | "__quit__"
      Optional. Invoked when the user presses Enter on a highlighted task in the
      right pane. Called WITHOUT suspending curses (it must not print).

    Left/Right arrows move focus between the option pane and the task pane.
    """
    import curses

    def _inner(stdscr):
        _sp_setup_colors()
        curses.curs_set(0)
        stdscr.nodelay(True)

        ui = {
            "opt_i":        0,
            "text":         "",
            "cursor":       0,        # insertion point within the text field
            "goal_col":     0,        # preferred column for vertical cursor moves
            "texts":        {},       # per-option text, kept when switching options
            "cursors":      {},       # per-option cursor position
            "sel_orch_i":   0,
            "last_refresh": 0.0,
            "focus":        "left",   # "left" (options) or "right" (tasks)
            "task_i":       0,        # highlighted task in the right pane (index)
            "task_id":      None,     # ...anchored to the task's id, so the live
                                      # list churning (new tasks prepended) doesn't
                                      # drag the selection onto a different task
            "task_detail":  None,     # dict from _build_task_detail when viewing a done task
            "detail_scroll": 0,       # scroll offset within the detail diff view
        }
        data = {}
        left_win = right_win = None
        prev_h = prev_w = 0

        def _switch_option(new_idx, opts, field_w):
            """Move selection to new_idx, preserving each option's typed text:
            stash the current field, then restore the destination's buffer."""
            old = opts[ui["opt_i"]] if opts and 0 <= ui["opt_i"] < len(opts) else None
            if old is not None:
                ui["texts"][old.value]   = ui["text"]
                ui["cursors"][old.value] = ui["cursor"]
            ui["opt_i"] = new_idx
            new = opts[new_idx] if opts and 0 <= new_idx < len(opts) else None
            val = new.value if new is not None else None
            ui["text"]     = ui["texts"].get(val, "")
            ui["cursor"]   = ui["cursors"].get(val, 0)
            ui["goal_col"] = ui["cursor"] % field_w
            ui["last_refresh"] = 0

        while True:
            now = _time.time()
            if now - ui["last_refresh"] >= 1.0:
                data = get_state_fn(ui["sel_orch_i"])
                # Rebuild option list and clamp opt_i to active options
                merged = {**data, **ui}
                opts   = get_options_fn(merged)
                active = [i for i, o in enumerate(opts) if not o.disabled]
                if active and ui["opt_i"] not in active:
                    ui["opt_i"] = active[0]
                data["_opts"] = opts
                # Keep the right-pane selection on the same task by id, so newly
                # added tasks (prepended newest-first) don't shift it underneath.
                if ui["focus"] == "right" and ui["task_id"] is not None:
                    ids = [t.get("id") for t in data.get("tasks", [])]
                    if ui["task_id"] in ids:
                        ui["task_i"] = ids.index(ui["task_id"])
                    elif ids:
                        ui["task_i"] = min(ui["task_i"], len(ids) - 1)
                ui["last_refresh"] = now

            h, w = stdscr.getmaxyx()

            if w < 60 or h < 20:
                stdscr.erase()
                msg = "Terminal too small — resize to continue"
                _sp_addstr(stdscr, h // 2, max(0, (w - len(msg)) // 2), msg)
                stdscr.refresh()
                stdscr.getch()
                _time.sleep(0.1)
                continue

            left_w  = min(42, w // 3)
            right_w = w - left_w - 1

            # Recreate sub-windows only when the terminal has been resized.
            if h != prev_h or w != prev_w:
                left_win  = curses.newwin(h, left_w, 0, 0)
                right_win = curses.newwin(h, right_w, 0, left_w + 1)
                prev_h, prev_w = h, w

            opts   = data.get("_opts") or get_options_fn({**data, **ui})
            merged = {**data, **ui, "opts": opts}

            # Draw
            stdscr.erase()
            # Divider
            dim_attr = curses.color_pair(_CP_DIM) | curses.A_DIM
            for dy in range(h - 1):
                _sp_addstr(stdscr, dy, left_w, "│", dim_attr)
            stdscr.noutrefresh()

            _sp_draw_left(left_win, merged)
            _sp_draw_right(right_win, merged)
            curses.doupdate()

            key = stdscr.getch()
            if key == -1:
                _time.sleep(0.05)
                continue

            tasks   = data.get("tasks", [])
            active  = [i for i, o in enumerate(opts) if not o.disabled]
            cur_opt = opts[ui["opt_i"]] if opts and ui["opt_i"] < len(opts) else None
            on_text = bool(cur_opt and cur_opt.text_input)
            field_w = max(1, left_w - 4)

            # --- focus-right (task pane / detail view) navigation ---
            if ui["focus"] == "right" and key in (curses.KEY_LEFT, 27):  # ← / Esc
                if ui["task_detail"] is not None:
                    ui["task_detail"]  = None
                    ui["detail_scroll"] = 0
                else:
                    ui["focus"] = "left"
                ui["last_refresh"] = 0
                continue
            if ui["focus"] == "right":
                if ui["task_detail"] is not None:
                    # Detail view: ↑↓ scroll the diff (clamped to the rendered
                    # range the draw published in _max_scroll for this frame).
                    if key == curses.KEY_UP:
                        ui["detail_scroll"] = max(0, ui["detail_scroll"] - 1)
                    elif key == curses.KEY_DOWN:
                        max_scroll = ui["task_detail"].get("_max_scroll", 0)
                        ui["detail_scroll"] = min(ui["detail_scroll"] + 1, max_scroll)
                    elif key in (ord("q"), ord("Q")):
                        return None
                elif not tasks:
                    ui["focus"] = "left"
                elif key == curses.KEY_UP:
                    ui["task_i"] = (ui["task_i"] - 1) % len(tasks)
                    ui["task_id"] = tasks[ui["task_i"]].get("id")
                    ui["last_refresh"] = 0
                elif key == curses.KEY_DOWN:
                    ui["task_i"] = (ui["task_i"] + 1) % len(tasks)
                    ui["task_id"] = tasks[ui["task_i"]].get("id")
                    ui["last_refresh"] = 0
                elif key in (10, 13):  # Enter — act on the highlighted task
                    if handle_task_fn and 0 <= ui["task_i"] < len(tasks):
                        result = handle_task_fn(tasks[ui["task_i"]], merged)
                        if isinstance(result, dict) and "task_detail" in result:
                            ui["task_detail"]  = result["task_detail"]
                            ui["detail_scroll"] = 0
                        elif result == "__quit__":
                            return None
                        elif result == "__refresh__":
                            return "__refresh__"
                        ui["last_refresh"] = 0
                elif key in (ord("q"), ord("Q")):
                    return None
                continue

            # --- focus-left: the shared text editor on a selected text field,
            # with the option pane / task pane handling its edges ---
            if on_text:
                ed  = {"text": ui["text"], "cursor": ui["cursor"], "goal_col": ui["goal_col"]}
                act = _te_edit(ed, key, field_w)
                if act == "edit":
                    ui["text"], ui["cursor"], ui["goal_col"] = ed["text"], ed["cursor"], ed["goal_col"]
                    continue
                if act in ("left", "right"):
                    if act == "right" and tasks:           # end of text → task pane
                        ui["focus"]  = "right"
                        ui["task_i"] = min(ui["task_i"], len(tasks) - 1)
                        ui["task_id"] = tasks[ui["task_i"]].get("id")
                        ui["last_refresh"] = 0
                    continue                                # start of text → nothing
                if act in ("top", "bottom"):
                    if active:                              # cross a line edge → switch option
                        idx  = active.index(ui["opt_i"]) if ui["opt_i"] in active else 0
                        step = 1 if act == "bottom" else -1
                        _switch_option(active[(idx + step) % len(active)], opts, field_w)
                    continue
                # act is None (Enter / Esc / Shift-Tab) → fall through to shared keys

            # --- non-text option navigation + shared keys ---
            if key == curses.KEY_RIGHT:                     # a non-text option → task pane
                if tasks:
                    ui["focus"]  = "right"
                    ui["task_i"] = min(ui["task_i"], len(tasks) - 1)
                    ui["task_id"] = tasks[ui["task_i"]].get("id")
                    ui["last_refresh"] = 0
            elif key == curses.KEY_LEFT:
                pass                                        # no pane to the left
            elif key in (curses.KEY_UP, curses.KEY_DOWN):
                if active:
                    idx  = active.index(ui["opt_i"]) if ui["opt_i"] in active else 0
                    step = 1 if key == curses.KEY_DOWN else -1
                    _switch_option(active[(idx + step) % len(active)], opts, field_w)
            elif key == curses.KEY_BTAB:
                orchs = data.get("orchs", [])
                if len(orchs) > 1:
                    ui["sel_orch_i"]   = (ui["sel_orch_i"] + 1) % len(orchs)
                    ui["last_refresh"] = 0
            elif key in (10, 13):  # Enter
                if cur_opt is not None:
                    choice_val  = cur_opt.value
                    choice_text = ui["text"].strip()
                    # The prompt was submitted — drop its kept buffer so it
                    # doesn't reappear when the option is revisited.
                    ui["texts"].pop(choice_val, None)
                    ui["cursors"].pop(choice_val, None)
                    ui["text"]  = ""
                    ui["cursor"] = 0
                    ui["goal_col"] = 0
                    curses.endwin()
                    result = handle_choice_fn(choice_val, choice_text, merged)
                    stdscr.touchwin()
                    stdscr.refresh()
                    if result == "__refresh__":
                        ui["last_refresh"] = 0
                        return "__refresh__"
                    if result == "__quit__":
                        return None
                    ui["last_refresh"] = 0  # re-draw after any action
            elif key == 27 and not on_text:  # ESC
                curses.endwin()
                result = handle_choice_fn(None, "", merged)
                stdscr.touchwin()
                stdscr.refresh()
                if result in ("__refresh__", "__quit__"):
                    return None
            elif key in (ord("q"), ord("Q")) and not on_text:
                return None

            _time.sleep(0.02)

    return curses.wrapper(_inner)


def select_menu(options, title="", hint=""):
    """Single-pane selection menu that uses the same multi-line text editor as
    the main split pane (soft-wrap, blinking block caret, gray placeholder,
    cursor navigation, per-option drafts). Returns ``(value, text)`` like
    :func:`menu`; Esc / q cancel to ``(None, "")``. Falls back to the numbered
    prompt on a non-TTY stdin."""
    if not sys.stdin.isatty():
        return _menu_no_tty(options, title)

    import curses

    def _inner(stdscr):
        _sp_setup_colors()
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)
        active = [i for i, o in enumerate(options) if not o.disabled] or list(range(len(options)))
        sel = active[0]
        texts, cursors = {}, {}                 # per-option drafts, kept across switches
        ed = {"text": "", "cursor": 0, "goal_col": 0}
        field_w = 1

        def _select(new_sel):
            texts[sel] = ed["text"]; cursors[sel] = ed["cursor"]
            ed["text"]     = texts.get(new_sel, "")
            ed["cursor"]   = cursors.get(new_sel, 0)
            ed["goal_col"] = ed["cursor"] % field_w
            return new_sel

        while True:
            h, w = stdscr.getmaxyx()
            field_w = max(1, w - 6)
            stdscr.erase()
            y = 0
            for line in WIZARD_COMPACT:
                _sp_addstr(stdscr, y, 2, line, curses.color_pair(_CP_WHITE) | curses.A_BOLD)
                y += 1
            y += 1
            if title:
                _sp_addstr(stdscr, y, 2, title, curses.color_pair(_CP_CYAN) | curses.A_BOLD)
                y += 1
            if hint:
                _sp_addstr(stdscr, y, 2, hint, curses.color_pair(_CP_DIM) | curses.A_DIM)
                y += 1
            y += 1
            for i, opt in enumerate(options):
                if y >= h - 1:
                    break
                selected = (i == sel)
                if opt.disabled:
                    _sp_addstr(stdscr, y, 2, f"  {opt.label}  (coming soon)",
                               curses.color_pair(_CP_DIM) | curses.A_DIM)
                elif selected and opt.text_input:
                    _sp_addstr(stdscr, y, 2, "> ", curses.color_pair(_CP_GOLD) | curses.A_BOLD)
                    if ed["text"]:
                        display, attr, cur = ed["text"], curses.color_pair(_CP_WHITE), ed["cursor"]
                    else:
                        display = opt.placeholder or opt.label
                        attr, cur = curses.color_pair(_CP_DIM) | curses.A_DIM, 0
                    caret = cur if int(_time.time() * 2) % 2 == 0 else None
                    y += _te_render(stdscr, y, 4, field_w, display, attr, caret) - 1
                elif selected:
                    _sp_addstr(stdscr, y, 2, f"> {opt.label}",
                               curses.color_pair(_CP_SELECT) | curses.A_BOLD)
                else:
                    # show a kept draft (collapsed) instead of the placeholder/label
                    _sp_addstr(stdscr, y, 4, (texts.get(i) or opt.label),
                               curses.color_pair(_CP_WHITE))
                y += 1
            stdscr.noutrefresh()
            curses.doupdate()

            key = stdscr.getch()
            if key == -1:
                _time.sleep(0.05)
                continue

            cur_opt = options[sel]
            on_text = cur_opt.text_input
            if on_text:
                act = _te_edit(ed, key, field_w)
                if act == "edit":
                    continue
                if act in ("left", "right"):
                    continue                            # no side panes here
                if act in ("top", "bottom"):
                    idx  = active.index(sel) if sel in active else 0
                    step = 1 if act == "bottom" else -1
                    sel  = _select(active[(idx + step) % len(active)])
                    continue
                # act None (Enter / Esc) → fall through

            if key in (curses.KEY_UP, curses.KEY_DOWN):
                idx  = active.index(sel) if sel in active else 0
                step = 1 if key == curses.KEY_DOWN else -1
                sel  = _select(active[(idx + step) % len(active)])
            elif key in (10, 13):  # Enter
                return cur_opt.value, ed["text"].strip()
            elif key == 27:  # Esc cancels from anywhere (incl. the text field) —
                return None, ""   # the build menu has no other "back out" key
            elif key in (ord("q"), ord("Q")) and not on_text:
                return None, ""

    return curses.wrapper(_inner)
