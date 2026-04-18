"""Boot script: command palette opened with filter "mem".

Subclasses :class:`DuhApp` to push :class:`CommandPalette` on mount,
then populates the palette's ``Input`` with "mem" so the OptionList is
filtered — exercising the palette's filter-and-render path.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _stub_engine import StubEngine  # noqa: E402

from textual.widgets import Input, OptionList  # noqa: E402

from duh.ui.app import DuhApp  # noqa: E402
from duh.ui.command_palette import CommandPalette  # noqa: E402


class _CommandPaletteDemoApp(DuhApp):
    async def on_mount(self) -> None:  # type: ignore[override]
        await super().on_mount()
        palette = CommandPalette()
        await self.push_screen(palette)
        # Fill the filter so the screen captures a filtered list.  The
        # Input.Changed handler on the palette triggers the list refresh.
        palette_input = palette.query_one("#palette-input", Input)
        palette_input.value = "mem"
        # Force the filter to run synchronously — set ``value`` posts
        # Input.Changed, but in a snapshot run we want the list filtered
        # before the frame is captured.
        palette._refresh_list("mem")
        # Highlight the first matching row so the chrome looks natural.
        try:
            option_list = palette.query_one("#palette-list", OptionList)
            if palette._filtered:
                option_list.highlighted = 0
        except Exception:
            pass


engine = StubEngine(events=[], model="snapshot-model")

app = _CommandPaletteDemoApp(
    engine=engine,
    model="snapshot-model",
    session_id="snapshot-00000000",
    debug=False,
    resumed_messages=[],
    cwd="/tmp/snapshot",
    approval_label="auto-approve",
    max_mounted_messages=0,
)


if __name__ == "__main__":  # pragma: no cover
    app.run()
