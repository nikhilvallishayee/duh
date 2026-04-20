"""Transcript-search modal for the D.U.H. TUI (Ctrl+F).

A minimal single-field modal: user types a query, presses Enter → the
query is returned to the caller (the app) which handles the actual
highlighting + scroll. Escape cancels and returns an empty string.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class SearchModal(ModalScreen[str]):
    """Modal input for transcript search — returns the query string."""

    DEFAULT_CSS = """
    SearchModal {
        align: center middle;
        background: $background 60%;
    }

    #search-dialog {
        width: 60;
        max-width: 90%;
        height: auto;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }

    #search-title {
        color: $primary;
        text-style: bold;
        padding: 0 0 1 0;
    }

    #search-input {
        background: $background;
        border: solid $primary-darken-2;
    }

    #search-input:focus {
        border: solid $primary;
    }

    #search-hint {
        color: $text-muted;
        padding: 1 0 0 0;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="search-dialog"):
            yield Label("Find in transcript", id="search-title")
            yield Input(placeholder="query…", id="search-input")
            yield Label("Enter to search · Esc to cancel", id="search-hint")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss("")
