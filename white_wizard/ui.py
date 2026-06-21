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
           /  *    *  \
          /     **    \
         /____________ \
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
