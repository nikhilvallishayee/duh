"""Boot script: permission modal overlay on top of the welcome screen.

Subclasses :class:`DuhApp` so we can push :class:`PermissionModal`
immediately after the base mount finishes.  The snapshot captures the
modal (Bash command pending approval) overlaid on an otherwise-fresh
TUI.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _stub_engine import StubEngine  # noqa: E402

from duh.ui.app import DuhApp  # noqa: E402
from duh.ui.permission_modal import PermissionModal  # noqa: E402


class _PermissionDemoApp(DuhApp):
    async def on_mount(self) -> None:  # type: ignore[override]
        await super().on_mount()
        # Push the modal once the base screen is composed.
        await self.push_screen(
            PermissionModal(
                tool_name="Bash",
                tool_input={"command": "rm -rf /tmp/snapshot-demo"},
            )
        )


engine = StubEngine(events=[], model="snapshot-model")

app = _PermissionDemoApp(
    engine=engine,
    model="snapshot-model",
    session_id="snapshot-00000000",
    debug=False,
    resumed_messages=[],
    cwd="/tmp/snapshot",
    approval_label="interactive",
    max_mounted_messages=0,
)


if __name__ == "__main__":  # pragma: no cover
    app.run()
