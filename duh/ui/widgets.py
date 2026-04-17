"""Custom Textual widgets for the D.U.H. TUI (ADR-011 Tier 2).

Widgets
-------
MessageWidget        — renders a single conversation turn (user or assistant)
HighlightedMarkdown  — Rich-backed Markdown Static with syntax highlighting
ToolCallWidget       — collapsible panel: tool name, input summary, output
ThinkingWidget       — dim/italic block for extended-thinking tokens
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.markup import escape as escape_markup
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Collapsible, Label, Static

from duh.ui.styles import OutputStyle, OutputTruncationPolicy

# Rich is a hard dependency of duh-cli (see pyproject.toml: "rich>=13.0,<16.0"),
# but we gate the import defensively so this module never hard-fails if a
# downstream consumer strips it out.  When Rich is unavailable we degrade
# `HighlightedMarkdown` to a plain Textual `Markdown` widget.
try:
    from rich.markdown import Markdown as RichMarkdown
    from rich.syntax import Syntax as RichSyntax  # noqa: F401  (re-exported for tests)

    _HAS_RICH = True
except ImportError:  # pragma: no cover — Rich is a hard dep in practice
    RichMarkdown = None  # type: ignore[assignment]
    RichSyntax = None  # type: ignore[assignment]
    _HAS_RICH = False


# ---------------------------------------------------------------------------
# HighlightedMarkdown — Rich-backed Markdown with syntax-highlighted code blocks
# ---------------------------------------------------------------------------
#
# Textual ships a `MarkdownFence` widget that renders fenced code blocks but
# *without* language-aware syntax highlighting — the rendered text uses the
# theme's default color for every token.  The REPL's RichRenderer gets
# highlighting for free because it pipes content through `rich.markdown.Markdown`
# with `code_theme="monokai"`, which internally drives each fence through
# `rich.syntax.Syntax` (Pygments-backed).
#
# This widget closes that gap for the TUI (ADR-073 Wave 2 #6).  It composes a
# single `Static` whose renderable is a `rich.markdown.Markdown` instance.
# Textual's `Static.update()` accepts arbitrary Rich renderables, so the full
# Rich Markdown output — headers, bold, lists, tables, AND syntax-highlighted
# code fences — is rendered natively inside the Textual widget tree.
#
# Streaming semantics: `update(content)` re-renders the entire markdown string.
# Rich's parser is fast enough that doing this on every `text_delta` is fine
# (measured <2ms for 4 KB messages).


# Rich markdown themes (Pygments themes for code blocks).
#
# "monokai" — dark, high-contrast; matches RichRenderer in `repl_renderers.py`.
# "default" — light, Pygments' default; used when the user picks a light UI.
#
# The theme is passed to `rich.markdown.Markdown(code_theme=...)`, which uses
# it only for fenced code blocks.  Everything else (headers, bold, lists) is
# styled by Rich's own markdown styles + the Textual CSS theme.
DEFAULT_CODE_THEME = "monokai"


class HighlightedMarkdown(Static):
    """Markdown renderer with language-aware syntax highlighting.

    Parameters
    ----------
    content:
        Markdown source.  May contain fenced code blocks with or without a
        language tag; unknown languages fall back to Pygments' ``TextLexer``
        (Rich handles this automatically — no exception is raised).
    code_theme:
        Pygments theme name used by ``rich.syntax.Syntax`` for code fences.
        Defaults to ``"monokai"``.

    Notes
    -----
    When Rich is unavailable at import time, the widget degrades to rendering
    the raw markdown text via Textual's default Static markup so that the TUI
    never crashes on a malformed environment.
    """

    DEFAULT_CSS = """
    HighlightedMarkdown {
        height: auto;
        background: transparent;
        color: $text;
    }
    """

    def __init__(
        self,
        content: str = "",
        *,
        code_theme: str = DEFAULT_CODE_THEME,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, markup=False)
        self._markdown_source = content
        self._code_theme = code_theme
        # Populate the initial renderable.  Do this *after* super().__init__()
        # so the widget is fully constructed.
        self._refresh_renderable()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_markdown(self, content: str) -> None:
        """Replace the markdown source and re-render.

        This is the streaming-friendly entry point: MessageWidget.append()
        calls it on every `text_delta`.  Rich's markdown parser is fast
        enough that re-parsing on every delta is acceptable.
        """
        self._markdown_source = content
        self._refresh_renderable()

    @property
    def markdown_source(self) -> str:
        """Return the raw markdown source (for tests / debugging)."""
        return self._markdown_source

    @property
    def code_theme(self) -> str:
        """Return the configured Pygments theme name."""
        return self._code_theme

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _refresh_renderable(self) -> None:
        """Build a Rich Markdown renderable from the current source and
        hand it to ``Static.update()``.

        Falls back to plain-text rendering when Rich is not importable.
        """
        if _HAS_RICH and RichMarkdown is not None:
            try:
                renderable = RichMarkdown(
                    self._markdown_source,
                    code_theme=self._code_theme,
                )
                self.update(renderable)
                return
            except Exception:
                # Defensive: if Rich raises on malformed markdown, fall back
                # to plain text so the TUI never crashes.
                pass
        # Plain-text fallback (Rich unavailable or raised).
        self.update(self._markdown_source)


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
            # User messages are plain text: no markdown parsing, no
            # syntax highlighting.  Keeps visual distinction between roles
            # (ADR-073 Wave 2 #6: assistant output must look richer than
            # user input).
            yield Static(self._content, classes="message-body", markup=False)
        else:
            # Assistant messages use HighlightedMarkdown (Rich-backed Static)
            # to get language-aware syntax highlighting on fenced code blocks.
            yield HighlightedMarkdown(self._content, classes="message-body")

    def on_mount(self) -> None:
        if self._role == "user":
            self._body = self.query_one(".message-body", Static)
        else:
            self._md_body = self.query_one(".message-body", HighlightedMarkdown)

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
                # HighlightedMarkdown.update_markdown() re-parses the full
                # source and re-renders via Rich.  Fast enough for streaming
                # (Rich's markdown parser is ~O(n) and measured <2ms/4KB).
                self._md_body.update_markdown(self._content)

    def finish(self) -> None:
        """Called when streaming is complete — do a final markdown render."""
        if self._role != "user" and hasattr(self, "_md_body") and self._md_body is not None:
            self._md_body.update_markdown(self._content)


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
        # Escape Rich markup chars in tool input to prevent MarkupError
        safe_summary = escape_markup(summary)
        safe_name = escape_markup(self._tool_name)
        title = f"Tool: {safe_name}({safe_summary})"
        with Collapsible(title=title, collapsed=False):
            yield Label(f"Input: {safe_summary}", classes="tool-call-label")
            yield Static("⠋ running…", classes="spinner-message", id="tool-result")

    def on_mount(self) -> None:
        self._result_label = self.query_one("#tool-result", Static)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_result(
        self,
        output: str,
        is_error: bool,
        style: str = "default",
        tool_name: str | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        """Update the widget with the tool result.

        Parameters
        ----------
        output:
            Raw tool output text.
        is_error:
            Whether the tool returned an error.
        style:
            One of ``"default"``, ``"concise"``, ``"verbose"``.  Mapped to an
            :class:`OutputTruncationPolicy` (ADR-073) so the REPL and TUI
            render the same number of characters for the same style.
        tool_name:
            Name of the tool that produced this result.  When the tool is
            ``"Edit"`` or ``"Write"`` and the output looks like a diff,
            lines are rendered with green/red coloring.
        elapsed_ms:
            Wall-clock milliseconds the tool call took.  When provided the
            elapsed time is shown next to the status indicator, e.g.
            ``"OK (1.2s)"`` or ``"Error (0.3s)"``.
        """
        self._running = False
        if self._result_label is None:
            return

        effective_name = tool_name or self._tool_name
        time_suffix = _format_elapsed(elapsed_ms)

        # Resolve the user-facing style string into an OutputStyle enum so we
        # can look up the shared truncation policy.  Unknown values degrade to
        # DEFAULT — matches the TUI's existing "fall through to default" logic.
        try:
            style_enum = OutputStyle(style)
        except ValueError:
            style_enum = OutputStyle.DEFAULT
        policy = OutputTruncationPolicy.for_style(style_enum, is_error=is_error)

        if is_error:
            if policy.max_chars <= 0 or policy.max_lines <= 0:
                # Concise-like policy that suppresses preview text entirely.
                self._result_label.update(f"[red]ERR[/red]{time_suffix}")
            else:
                truncated, was_truncated = policy.apply(output or "")
                preview = escape_markup(truncated) if truncated else "(empty)"
                suffix = " [dim](truncated)[/dim]" if was_truncated else ""
                self._result_label.update(
                    f"[red]Error[/red]{time_suffix}: {preview}{suffix}"
                )
            self._result_label.remove_class("spinner-message")
            self._result_label.add_class("tool-result-error")
        else:
            # Check for diff-like output from Edit/Write tools.
            # Diff rendering has its own line cap (_render_diff max_lines=60)
            # that matches VERBOSE's max_lines — intentionally independent of
            # OutputTruncationPolicy because diffs need their own line-oriented
            # truncation semantics (whole hunks, not arbitrary cuts).
            if effective_name in ("Edit", "Write") and _looks_like_diff(output):
                rendered = _render_diff(output, time_suffix=time_suffix)
                self._result_label.update(rendered)
            elif policy.max_chars <= 0 or policy.max_lines <= 0:
                # Concise success: status only, no preview.
                self._result_label.update(f"[green]OK[/green]{time_suffix}")
            elif style_enum is OutputStyle.DEFAULT and output:
                # Preserve the historical "first line only" summary for the
                # DEFAULT style, then apply the char cap from the policy.
                first_line = output.split("\n", 1)[0]
                truncated, was_truncated = policy.apply(first_line)
                preview = escape_markup(truncated) if truncated else "(empty)"
                suffix = " [dim](truncated)[/dim]" if was_truncated else ""
                self._result_label.update(
                    f"[green]OK[/green]{time_suffix}: {preview}{suffix}"
                )
            else:
                truncated, was_truncated = policy.apply(output or "")
                preview = escape_markup(truncated) if truncated else "(empty)"
                suffix = " [dim](truncated)[/dim]" if was_truncated else ""
                self._result_label.update(
                    f"[green]OK[/green]{time_suffix}: {preview}{suffix}"
                )
            self._result_label.remove_class("spinner-message")
            self._result_label.add_class("tool-result-ok")


def _format_elapsed(elapsed_ms: float | None) -> str:
    """Return a human-readable elapsed-time suffix like `` (1.2s)``."""
    if elapsed_ms is None:
        return ""
    secs = elapsed_ms / 1000.0
    if secs < 0.1:
        return f" ({elapsed_ms:.0f}ms)"
    return f" ({secs:.1f}s)"


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
# Diff rendering helpers
# ---------------------------------------------------------------------------


def _looks_like_diff(text: str) -> bool:
    """Return True if *text* appears to contain unified-diff markers."""
    if not text:
        return False
    for line in text.split("\n")[:20]:
        stripped = line.lstrip()
        if stripped.startswith("@@") or stripped.startswith("---") or stripped.startswith("+++"):
            return True
    return False


def _render_diff(text: str, max_lines: int = 60, *, time_suffix: str = "") -> str:
    """Render a diff with Rich markup: green for additions, red for removals.

    Lines starting with ``+`` (but not ``+++``) are green.
    Lines starting with ``-`` (but not ``---``) are red.
    Lines starting with ``@@`` are cyan (hunk headers).
    Everything else is left as-is.
    """
    lines = text.split("\n")[:max_lines]
    rendered: list[str] = [f"[green]OK[/green]{time_suffix}: diff preview"]
    for line in lines:
        safe = escape_markup(line)
        stripped = line.lstrip()
        if stripped.startswith("@@"):
            rendered.append(f"[cyan]{safe}[/cyan]")
        elif stripped.startswith("+++") or stripped.startswith("---"):
            rendered.append(f"[bold]{safe}[/bold]")
        elif stripped.startswith("+"):
            rendered.append(f"[green]{safe}[/green]")
        elif stripped.startswith("-"):
            rendered.append(f"[red]{safe}[/red]")
        else:
            rendered.append(safe)
    if len(text.split("\n")) > max_lines:
        rendered.append(f"[dim]… ({len(text.split(chr(10))) - max_lines} more lines)[/dim]")
    return "\n".join(rendered)


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
        collapsed: bool = True,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        merged = f"thinking-widget {classes}".strip() if classes else "thinking-widget"
        super().__init__(name=name, id=id, classes=merged)
        self._collapsed = collapsed
        self._body: Static | None = None
        self._collapsible: Collapsible | None = None

    def compose(self) -> ComposeResult:
        with Collapsible(title="Thinking…", collapsed=self._collapsed) as c:
            self._collapsible = c
            yield Static("", classes="thinking-body", markup=False, id="thinking-body")

    def on_mount(self) -> None:
        self._body = self.query_one("#thinking-body", Static)
        try:
            self._collapsible = self.query_one(Collapsible)
        except Exception:
            pass

    def append(self, delta: str) -> None:
        """Append a thinking text delta."""
        self._content += delta
        if self._body is not None:
            self._body.update(self._content)
        # Update collapsible title with character count
        if self._collapsible is not None:
            char_count = len(self._content)
            self._collapsible.title = f"Thinking… ({char_count:,} chars)"
