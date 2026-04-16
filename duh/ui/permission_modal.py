"""TUI permission modal — asks the user to approve a tool call.

Part of ADR-066 P1: replaces AutoApprover in the Textual TUI with an
interactive modal dialog that supports y/a/n/N (same vocabulary as
InteractiveApprover and SessionPermissionCache).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class PermissionModal(ModalScreen[str]):
    """Modal dialog asking the user to approve a tool call.

    Returns one of: ``"y"`` (yes), ``"a"`` (always), ``"n"`` (no), ``"N"`` (never).
    """

    DEFAULT_CSS = """
    PermissionModal {
        align: center middle;
    }

    #permission-dialog {
        width: 70;
        max-width: 90%;
        height: auto;
        background: $surface;
        border: tall $warning;
        padding: 1 2;
    }

    #permission-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }

    #permission-tool {
        color: $text;
        margin-bottom: 0;
    }

    #permission-input {
        color: $text-muted;
        margin-bottom: 1;
    }

    #permission-prompt {
        color: $text;
        text-style: bold;
        margin-bottom: 1;
    }

    #permission-buttons {
        height: auto;
        align: center middle;
    }

    #permission-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("y", "respond_yes", "Yes", show=False),
        Binding("a", "respond_always", "Always", show=False),
        Binding("n", "respond_no", "No", show=False),
        Binding("N", "respond_never", "Never", show=False, key_display="shift+n"),
        Binding("escape", "respond_no", "Cancel", show=False),
    ]

    def __init__(self, tool_name: str, tool_input: dict) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._input_preview = str(tool_input)[:200]

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="permission-dialog"):
            yield Static("Permission Request", id="permission-title")
            yield Static(f"Tool: [bold]{self._tool_name}[/bold]", id="permission-tool")
            yield Static(f"Input: [dim]{self._input_preview}[/dim]", id="permission-input")
            yield Static("Allow this tool call?", id="permission-prompt")
            with Horizontal(id="permission-buttons"):
                yield Button("[y]es", id="yes", variant="success")
                yield Button("[a]lways", id="always", variant="primary")
                yield Button("[n]o", id="no", variant="error")
                yield Button("[N]ever", id="never", variant="warning")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {"yes": "y", "always": "a", "no": "n", "never": "N"}
        self.dismiss(mapping.get(event.button.id, "n"))

    def action_respond_yes(self) -> None:
        self.dismiss("y")

    def action_respond_always(self) -> None:
        self.dismiss("a")

    def action_respond_no(self) -> None:
        self.dismiss("n")

    def action_respond_never(self) -> None:
        self.dismiss("N")
