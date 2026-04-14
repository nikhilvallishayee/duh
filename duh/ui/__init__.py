"""D.U.H. Textual TUI — Tier 2 of ADR-011.

Exports the Textual app and public widgets.  Import is optional: if
``textual`` is not installed the module silently degrades.
"""

from __future__ import annotations

__all__ = ["DuhApp", "run_tui"]

try:
    from duh.ui.app import DuhApp, run_tui  # noqa: F401
except ImportError:
    # textual not installed — degrade gracefully
    def run_tui(*_args, **_kwargs) -> int:  # type: ignore[misc]
        import sys

        sys.stderr.write(
            "Error: textual is not installed.  "
            "Install it with: pip install textual\n"
        )
        return 1
