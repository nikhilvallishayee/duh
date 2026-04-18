"""Boot script: tool result — success, VERBOSE output style."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _showcase import ShowcaseApp  # noqa: E402
from _tool_result_helpers import _ResultToolCallWidget  # noqa: E402


VERBOSE_OUTPUT = (
    "line 1: first\n"
    "line 2: second\n"
    "line 3: third\n"
    "line 4: fourth\n"
    "line 5: fifth\n"
    "line 6: sixth"
)


widget = _ResultToolCallWidget(
    tool_name="Bash",
    input={"command": "seq 1 6"},
    output_style="verbose",
    result_output=VERBOSE_OUTPUT,
    result_is_error=False,
    result_elapsed_ms=900.0,
)

app = ShowcaseApp(widgets=[widget])


if __name__ == "__main__":  # pragma: no cover
    app.run()
