"""Boot script: tool result — success, DEFAULT output style."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _showcase import ShowcaseApp  # noqa: E402
from _tool_result_helpers import _ResultToolCallWidget  # noqa: E402


widget = _ResultToolCallWidget(
    tool_name="Bash",
    input={"command": "ls -la /tmp/snapshot-dir"},
    output_style="default",
    result_output="total 8\ndrwxr-xr-x   4 user  wheel   128 Apr 16 12:00 .\ndrwxr-xr-x  20 user  wheel   640 Apr 16 12:00 ..\n-rw-r--r--   1 user  wheel   128 Apr 16 12:00 README.md",
    result_is_error=False,
    result_elapsed_ms=1200.0,
)

app = ShowcaseApp(widgets=[widget])


if __name__ == "__main__":  # pragma: no cover
    app.run()
