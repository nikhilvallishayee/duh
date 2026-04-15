"""Custom Textual widgets for the D.U.H. TUI (ADR-011 Tier 2).

Widgets
-------
MessageWidget      — renders a single conversation turn (user or assistant)
ToolCallWidget     — collapsible panel: tool name, input summary, output
ThinkingWidget     — dim/italic block for extended-thinking tokens
"""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Collapsible, Label, Markdown, Static


# ---------------------------------------------------------------------------
# MessageWidget
# ---------------------------------------------------------------------------


class MessageWidget(Widget):
    """Renders a single conversation message.

    Parameters
    ----------
    role:
        ``"user"`` or ``"assistant"``.
    text:
        Initial text content (may be empty; call :meth:`append` to stream).
    """

    DEFAULT_CSS = """
    MessageWidget {
        height: auto;
        margin: 0 0 1 0;
    }
    """

    # Reactive text so the widget re-renders when streamed text arrives.
    _content: reactive[str] = reactive("", layout=True)

    def __init__(
        self,
        role: str,
        text: str = "",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        role_class = "message-user" if role == "user" else "message-assistant"
        merged = f"{role_class} {classes}".strip() if classes else role_class
        super().__init__(name=name, id=id, classes=merged)
        self._role = role
        self._content = text
        self._body: Static | None = None

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        label_text = "You" if self._role == "user" else "Assistant"
        yield Label(label_text, classes="message-role-label")
        if self._role == "user":
            yield Static(self._content, classes="message-body", markup=False)
        else:
            yield Markdown(self._content, classes="message-body")

    def on_mount(self) -> None:
        if self._role == "user":
            self._body = self.query_one(".message-body", Static)
        else:
            self._md_body = self.query_one(".message-body", Markdown)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, delta: str) -> None:
        """Append streaming text delta to the message body."""
        self._content += delta
        if self._role == "user":
            if self._body is not None:
                self._body.update(self._content)
        else:
            if hasattr(self, "_md_body") and self._md_body is not None:
                self._md_body.update(self._content)

    def finish(self) -> None:
        """Called when streaming is complete — do a final markdown render."""
        if self._role != "user" and hasattr(self, "_md_body") and self._md_body is not None:
            self._md_body.update(self._content)


# ---------------------------------------------------------------------------
# ToolCallWidget
# ---------------------------------------------------------------------------

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class ToolCallWidget(Widget):
    """Collapsible panel showing tool name, input summary, and output.

    Usage::

        w = ToolCallWidget(name="Bash", input={"command": "ls /"})
        await app.mount(w)
        # … later …
        w.set_result("total 64\\n...", is_error=False)
    """

    DEFAULT_CSS = """
    ToolCallWidget {
        height: auto;
        margin: 0 0 0 2;
    }
    """

    def __init__(
        self,
        tool_name: str,
        input: dict[str, Any],
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        merged = f"tool-call-widget {classes}".strip() if classes else "tool-call-widget"
        super().__init__(name=name, id=id, classes=merged)
        self._tool_name = tool_name
        self._input = input
        self._result_label: Static | None = None
        self._running = True

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        summary = _summarise_input(self._input)
        title = f"Tool: {self._tool_name}({summary})"
        with Collapsible(title=title, collapsed=False):
            yield Label(f"Input: {summary}", classes="tool-call-label")
            yield Static("⠋ running…", classes="spinner-message", id="tool-result")

    def on_mount(self) -> None:
        self._result_label = self.query_one("#tool-result", Static)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_result(self, output: str, is_error: bool) -> None:
        """Update the widget with the tool result."""
        self._running = False
        if self._result_label is None:
            return
        if is_error:
            preview = output[:300] if output else "(empty)"
            self._result_label.update(f"[red]Error:[/red] {preview}")
            self._result_label.remove_class("spinner-message")
            self._result_label.add_class("tool-result-error")
        else:
            first_line = output.split("\n", 1)[0][:120] if output else "(empty)"
            self._result_label.update(f"[green]OK:[/green] {first_line}")
            self._result_label.remove_class("spinner-message")
            self._result_label.add_class("tool-result-ok")


def _summarise_input(inp: dict[str, Any]) -> str:
    """Return a compact one-line summary of a tool input dict."""
    if not inp:
        return ""
    parts = []
    for k, v in list(inp.items())[:2]:
        if isinstance(v, str) and len(v) > 40:
            v = v[:40] + "…"
        parts.append(f"{k}={v!r}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# ThinkingWidget
# ---------------------------------------------------------------------------


class ThinkingWidget(Widget):
    """Dim/italic block that accumulates extended-thinking tokens.

    Collapsed by default so it does not distract; users can expand it.
    """

    DEFAULT_CSS = """
    ThinkingWidget {
        height: auto;
        margin: 0 0 0 2;
    }
    """

    _content: reactive[str] = reactive("", layout=True)

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        merged = f"thinking-widget {classes}".strip() if classes else "thinking-widget"
        super().__init__(name=name, id=id, classes=merged)
        self._body: Static | None = None

    def compose(self) -> ComposeResult:
        with Collapsible(title="Thinking…", collapsed=True):
            yield Static("", classes="thinking-body", markup=False, id="thinking-body")

    def on_mount(self) -> None:
        self._body = self.query_one("#thinking-body", Static)

    def append(self, delta: str) -> None:
        """Append a thinking text delta."""
        self._content += delta
        if self._body is not None:
            self._body.update(self._content)
