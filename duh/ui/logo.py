"""D.U.H. ASCII art logos — terminal, README, and website versions."""

from __future__ import annotations

import sys

# ─── ANSI color codes ───────────────────────────────────────────────

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_BLUE = "\033[34m"
_BRIGHT_CYAN = "\033[96m"
_BRIGHT_BLUE = "\033[94m"
_BRIGHT_WHITE = "\033[97m"
_WHITE = "\033[37m"
_GRAY = "\033[90m"
_MAGENTA = "\033[35m"
_BRIGHT_MAGENTA = "\033[95m"

# The accent color — used for D, U, H in the tagline to reveal the pun
_ACC = f"{_BRIGHT_MAGENTA}{_BOLD}"

# ─── Large hero logo (12 lines) ────────────────────────────────────

LOGO_LARGE = r"""
  ____        _   _       _   _
 |  _ \   _  | | | |  _  | | | |
 | | | | (_) | | | | (_) | |_| |
 | | | |     | | | |     |  _  |
 | |_| |  _  | |_| |  _  | | | |
 |____/  (_)  \___/  (_) |_| |_|

  D.U.H. is a Universal Harness
"""

# ─── Compact logo (6 lines) ────────────────────────────────────────

LOGO_COMPACT = r"""
  D . U . H .
  Duh is a Universal Harness
"""

# ─── Mini one-liner ─────────────────────────────────────────────────

LOGO_MINI = "◆ D.U.H. — Duh is a Universal Harness"

# ─── Colored versions ──────────────────────────────────────────────

_LOGO_LARGE_COLOR = f"""\
{_ACC}  ____        _   _       _   _ {_RESET}
{_ACC} |  _ \\   {_GRAY}_{_ACC}  | | | |  {_GRAY}_{_ACC}  | | | |{_RESET}
{_BRIGHT_MAGENTA}{_BOLD} | | | | {_GRAY}(_){_BRIGHT_MAGENTA} | | | | {_GRAY}(_){_BRIGHT_MAGENTA} | |_| |{_RESET}
{_BRIGHT_MAGENTA}{_BOLD} | | | |     | | | |     |  _  |{_RESET}
{_MAGENTA} | |_| |  {_GRAY}_{_MAGENTA}  | |_| |  {_GRAY}_{_MAGENTA}  | | | |{_RESET}
{_MAGENTA} |____/  {_GRAY}(_){_MAGENTA}  \\___/  {_GRAY}(_){_MAGENTA} |_| |_|{_RESET}

     {_ACC}D{_RESET}{_BRIGHT_WHITE}.{_ACC}U{_RESET}{_BRIGHT_WHITE}.{_ACC}H{_RESET}{_BRIGHT_WHITE}. is a {_ACC}U{_RESET}{_BRIGHT_WHITE}niversal {_ACC}H{_RESET}{_BRIGHT_WHITE}arness{_RESET}
"""

_LOGO_COMPACT_COLOR = f"""\
  {_ACC}D{_RESET}{_DIM} . {_ACC}U{_RESET}{_DIM} . {_ACC}H{_RESET}{_DIM} .{_RESET}
  {_ACC}D{_RESET}{_BRIGHT_WHITE}uh is a {_ACC}U{_RESET}{_BRIGHT_WHITE}niversal {_ACC}H{_RESET}{_BRIGHT_WHITE}arness{_RESET}
"""

_LOGO_MINI_COLOR = (
    f"{_BRIGHT_MAGENTA}◆{_RESET} "
    f"{_ACC}D{_RESET}{_BRIGHT_WHITE}.{_ACC}U{_RESET}{_BRIGHT_WHITE}.{_ACC}H{_RESET}{_BRIGHT_WHITE}.{_RESET} "
    f"{_DIM}—{_RESET} "
    f"{_ACC}U{_RESET}{_BRIGHT_WHITE}niversal {_ACC}H{_RESET}{_BRIGHT_WHITE}arness{_RESET}"
)


def print_logo(
    style: str = "compact",
    color: bool = True,
    file: object = None,
) -> None:
    """Print the D.U.H. logo.

    Args:
        style: "large", "compact", or "mini"
        color: Use ANSI color codes (auto-disabled if not a TTY)
        file: Output stream (default: sys.stderr)
    """
    out = file or sys.stderr
    use_color = color and hasattr(out, "isatty") and out.isatty()

    if style == "large":
        text = _LOGO_LARGE_COLOR if use_color else LOGO_LARGE
    elif style == "mini":
        text = _LOGO_MINI_COLOR if use_color else LOGO_MINI
    else:
        text = _LOGO_COMPACT_COLOR if use_color else LOGO_COMPACT

    out.write(text)
    if style == "mini":
        out.write("\n")
    out.flush()
