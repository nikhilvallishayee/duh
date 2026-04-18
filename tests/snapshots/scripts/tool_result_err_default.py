"""Boot script: tool result — error, DEFAULT output style."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _showcase import ShowcaseApp  # noqa: E402
from _tool_result_helpers import _ResultToolCallWidget  # noqa: E402


widget = _ResultToolCallWidget(
    tool_name="Bash",
    input={"command": "cat /nonexistent/path"},
    output_style="default",
    result_output="cat: /nonexistent/path: No such file or directory",
    result_is_error=True,
    result_elapsed_ms=300.0,
)

app = ShowcaseApp(widgets=[widget])


if __name__ == "__main__":  # pragma: no cover
    app.run()
