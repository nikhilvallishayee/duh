"""Boot script: D.U.H. welcome banner with 5 resumed messages.

Snapshot captures the initial screen after ``--resume`` hydrates a
session — the welcome banner plus the first five message widgets and
the "--- Restored N messages ---" divider.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _stub_engine import StubEngine, build_resumed_messages  # noqa: E402

from duh.ui.app import DuhApp  # noqa: E402


engine = StubEngine(events=[], model="snapshot-model")

app = DuhApp(
    engine=engine,
    model="snapshot-model",
    session_id="snapshot-00000000",
    debug=False,
    resumed_messages=build_resumed_messages(5),
    cwd="/tmp/snapshot",
    approval_label="auto-approve",
    max_mounted_messages=0,
)


if __name__ == "__main__":  # pragma: no cover
    app.run()
