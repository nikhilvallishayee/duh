"""Minimal Textual app that mounts a single D.U.H. widget for snapshotting.

Several of the Phase 1 snapshots target individual widget states
(tool-call running, tool-result success/error, mid-stream assistant
message).  Driving those states through the full :class:`DuhApp`
worker loop is fragile — timing, theme initialisation, session-store
stubs and so on all conspire to make the output non-deterministic.

``ShowcaseApp`` sidesteps that by mounting the widget under test
inside a bare ``App`` with the same :data:`duh.ui.theme.APP_CSS`.  The
rendered output looks identical to how the widget appears inside
DuhApp, but with no moving parts around it.
"""

from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer

from duh.ui.theme import APP_CSS


class ShowcaseApp(App[int]):
    """Mount a single widget (or a list of widgets) for snapshotting."""

    CSS = APP_CSS

    def __init__(self, widgets: list[Any]) -> None:
        super().__init__()
        self._widgets = list(widgets)

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="message-log"):
            for w in self._widgets:
                yield w
