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

# ─── Large hero logo (12 lines) ────────────────────────────────────

LOGO_LARGE = r"""
    ██████╗        ██╗   ██╗       ██╗  ██╗
    ██╔══██╗       ██║   ██║       ██║  ██║
    ██║  ██║       ██║   ██║       ██████╔╝
    ██║  ██║  ██╗  ██║   ██║  ██╗  ██╔══██╗
    ██████╔╝  ╚═╝  ╚██████╔╝  ╚═╝  ██║  ██║
    ╚═════╝        ╚═════╝        ╚═╝  ╚═╝
    ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
     is  a  Universal  Harness
"""

# ─── Compact logo (6 lines) ────────────────────────────────────────

LOGO_COMPACT = r"""
  ╔═══╗  ╔╗ ╔╗  ╔╗  ╔╗
  ║╔═╗║  ║║ ║║  ║╚══╝║
  ║║ ║║  ║║ ║║  ║╔══╗║
  ║╚═╝║  ║╚═╝║  ║║  ║║
  ╚═══╝  ╚═══╝  ╚╝  ╚╝
  Universal Harness
"""

# ─── Mini one-liner ─────────────────────────────────────────────────

LOGO_MINI = "◆ D.U.H. — Universal Harness"

# ─── Colored versions ──────────────────────────────────────────────

_LOGO_LARGE_COLOR = f"""\
{_BRIGHT_CYAN}{_BOLD}    ██████╗        ██╗   ██╗       ██╗  ██╗{_RESET}
{_BRIGHT_CYAN}{_BOLD}    ██╔══██╗       ██║   ██║       ██║  ██║{_RESET}
{_BRIGHT_BLUE}{_BOLD}    ██║  ██║       ██║   ██║       ██████╔╝{_RESET}
{_BRIGHT_BLUE}{_BOLD}    ██║  ██║  {_GRAY}██╗{_BRIGHT_BLUE}  ██║   ██║  {_GRAY}██╗{_BRIGHT_BLUE}  ██╔══██╗{_RESET}
{_CYAN}    ██████╔╝  {_GRAY}╚═╝{_CYAN}  ╚██████╔╝  {_GRAY}╚═╝{_CYAN}  ██║  ██║{_RESET}
{_BLUE}    ╚═════╝        ╚═════╝        ╚═╝  ╚═╝{_RESET}
{_DIM}    ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄{_RESET}
{_BRIGHT_WHITE}     is  a  {_BRIGHT_CYAN}Universal{_BRIGHT_WHITE}  Harness{_RESET}
"""

_LOGO_COMPACT_COLOR = f"""\
{_BRIGHT_CYAN}{_BOLD}  ╔═══╗  ╔╗ ╔╗  ╔╗  ╔╗{_RESET}
{_BRIGHT_BLUE}{_BOLD}  ║╔═╗║  ║║ ║║  ║╚══╝║{_RESET}
{_BRIGHT_BLUE}{_BOLD}  ║║ ║║  ║║ ║║  ║╔══╗║{_RESET}
{_CYAN}  ║╚═╝║  ║╚═╝║  ║║  ║║{_RESET}
{_BLUE}  ╚═══╝  ╚═══╝  ╚╝  ╚╝{_RESET}
{_BRIGHT_WHITE}  Universal Harness{_RESET}
"""

_LOGO_MINI_COLOR = (
    f"{_BRIGHT_CYAN}◆{_RESET} "
    f"{_BRIGHT_WHITE}{_BOLD}D.U.H.{_RESET} "
    f"{_DIM}—{_RESET} "
    f"{_BRIGHT_CYAN}Universal Harness{_RESET}"
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
