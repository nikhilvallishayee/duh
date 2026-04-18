"""Boot script: assistant message frozen mid-stream.

Renders a :class:`MessageWidget` with ~50 % of a multi-paragraph
response already streamed in.  Captures what the user sees while the
model is still generating tokens.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _showcase import ShowcaseApp  # noqa: E402

from duh.ui.widgets import MessageWidget  # noqa: E402


# A paragraph long enough to look "mid-stream" — no trailing period so
# the screen visibly reads as unfinished prose.
MID_STREAM_TEXT = (
    "Here is the first half of a response that is still streaming "
    "from the model. Markdown rendering continues to work incremen"
)


assistant = MessageWidget(role="assistant", text=MID_STREAM_TEXT)

app = ShowcaseApp(widgets=[assistant])


if __name__ == "__main__":  # pragma: no cover
    app.run()
