"""Boot script: D.U.H. welcome banner, fresh start.

Mounts :class:`duh.ui.app.DuhApp` with an empty stub engine and no
resumed messages.  The snapshot captures the initial screen: logo,
session banner, empty message log, focused input.
"""

from __future__ import annotations

import os
import sys

# Make the script's directory importable so ``_stub_engine`` resolves
# whether the file is launched by ``snap_compare`` (``runpy.run_path``)
# or by ``python <script>`` directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _stub_engine import StubEngine  # noqa: E402

from duh.ui.app import DuhApp  # noqa: E402


engine = StubEngine(events=[], model="snapshot-model")

app = DuhApp(
    engine=engine,
    model="snapshot-model",
    session_id="snapshot-00000000",
    debug=False,
    resumed_messages=[],
    cwd="/tmp/snapshot",
    approval_label="auto-approve",
    # Disable virtualization so nothing unexpected evicts on first paint.
    max_mounted_messages=0,
)


if __name__ == "__main__":  # pragma: no cover — manual inspection entry point
    app.run()
