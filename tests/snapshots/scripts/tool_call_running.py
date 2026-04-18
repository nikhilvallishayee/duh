"""Boot script: tool call in the "running" state with a frozen spinner.

Uses CONCISE output style so the spinner animation is *not* started —
the Braille glyph that ``compose()`` sets ("⠋") remains stable across
runs, which is exactly what we want for deterministic snapshots.

See :class:`duh.ui.widgets.ToolCallWidget` for the output-style gating.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _showcase import ShowcaseApp  # noqa: E402

from duh.ui.widgets import ToolCallWidget  # noqa: E402


widget = ToolCallWidget(
    tool_name="Bash",
    input={"command": "ls -la /tmp/snapshot-dir"},
    output_style="concise",  # ← disables spinner animation (deterministic)
)

app = ShowcaseApp(widgets=[widget])


if __name__ == "__main__":  # pragma: no cover
    app.run()
