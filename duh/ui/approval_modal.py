"""Plan / snapshot approval modal for the D.U.H. TUI.

Presents the user with a titled panel showing the proposed change and
three buttons: Approve / Reject / Modify. Returns the user's choice as a
string (``"approve"`` / ``"reject"`` / ``"modify"``) via ``dismiss``.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class ApprovalModal(ModalScreen[str]):
    """Three-button approval modal. Returns ``"approve"`` / ``"reject"`` / ``"modify"``."""

    DEFAULT_CSS = """
    ApprovalModal {
        align: center middle;
        background: $background 60%;
    }

    #approval-dialog {
        width: 80;
        max-width: 95%;
        height: auto;
        max-height: 24;
        background: $surface;
        border: tall $primary;
        padding: 1 2;
    }

    #approval-title {
        color: $primary;
        text-style: bold;
        padding: 0 0 1 0;
    }

    #approval-body {
        height: auto;
        max-height: 16;
        padding: 0 0 1 0;
    }

    #approval-actions {
        height: auto;
        padding-top: 1;
        align: center middle;
    }

    #approval-actions Button {
        margin: 0 1;
    }

    #btn-approve {
        background: $success;
    }

    #btn-reject {
        background: $error;
    }

    #btn-modify {
        background: $warning;
    }
    """

    BINDINGS = [
        Binding("escape", "reject", "Reject", show=False),
        Binding("a", "approve", "Approve", show=False),
        Binding("r", "reject", "Reject", show=False),
        Binding("m", "modify", "Modify", show=False),
    ]

    def __init__(
        self,
        title: str,
        body: str,
        *,
        show_modify: bool = True,
    ) -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._show_modify = show_modify

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            yield Label(self._title, id="approval-title")
            yield Static(self._body, id="approval-body", markup=False)
            with Horizontal(id="approval-actions"):
                yield Button("Approve (a)", id="btn-approve", variant="success")
                yield Button("Reject (r)", id="btn-reject", variant="error")
                if self._show_modify:
                    yield Button("Modify (m)", id="btn-modify", variant="warning")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "btn-approve": "approve",
            "btn-reject": "reject",
            "btn-modify": "modify",
        }
        choice = mapping.get(event.button.id or "", "reject")
        self.dismiss(choice)

    def action_approve(self) -> None:
        self.dismiss("approve")

    def action_reject(self) -> None:
        self.dismiss("reject")

    def action_modify(self) -> None:
        if self._show_modify:
            self.dismiss("modify")
