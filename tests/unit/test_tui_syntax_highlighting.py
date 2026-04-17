"""Tests for TUI syntax highlighting (ADR-073 Wave 2 #6).

Covers the new :class:`HighlightedMarkdown` widget and its integration with
:class:`MessageWidget`.  Verifies that:

* Fenced code blocks with a language tag produce highlighted output
  (ANSI color codes in the rendered stream).
* Different languages (python, bash, json) all get highlighted.
* Code blocks without a language tag render without crashing.
* Unknown languages fall back gracefully (no exception; Rich's Markdown
  delegates to Pygments' ``TextLexer``).
* ``MessageWidget`` uses ``HighlightedMarkdown`` for assistant messages but
  keeps plain ``Static`` for user messages (ensuring assistant output is
  visually richer than user input).
* Streaming via ``append()`` continues to work incrementally.
* Non-code markdown constructs (headers, bold, lists) still render.
"""

from __future__ import annotations

import io

import pytest

# Skip the module entirely when Textual is not installed — matches the
# pattern used in test_textual_tui.py.
textual = pytest.importorskip("textual", reason="textual not installed")

from rich.console import Console  # noqa: E402
from rich.markdown import Markdown as RichMarkdown  # noqa: E402

from textual.widgets import Static  # noqa: E402

from duh.ui.widgets import (  # noqa: E402
    DEFAULT_CODE_THEME,
    HighlightedMarkdown,
    MessageWidget,
    _HAS_RICH,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_to_ansi(widget: HighlightedMarkdown, width: int = 80) -> str:
    """Force-render the widget's Rich renderable through a truecolor
    Console and return the raw ANSI-laden string.

    This lets tests assert on the syntax-highlighted bytes without having
    to spin up a full Textual app (which would require an event loop).
    """
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        width=width,
        record=False,
    )
    # Reconstruct the renderable exactly as the widget does.
    renderable = RichMarkdown(
        widget.markdown_source,
        code_theme=widget.code_theme,
    )
    console.print(renderable)
    return buf.getvalue()


# Sentinel escape-sequence prefix emitted by Rich for any colorized output.
_ANSI_PREFIX = "\x1b["


# ---------------------------------------------------------------------------
# HighlightedMarkdown basics
# ---------------------------------------------------------------------------


class TestHighlightedMarkdownBasics:
    def test_widget_is_a_static(self):
        """HighlightedMarkdown should subclass Static so Textual knows
        how to embed it in the widget tree."""
        w = HighlightedMarkdown("hello")
        assert isinstance(w, Static)

    def test_default_theme_is_monokai(self):
        """Default Pygments theme matches the REPL's RichRenderer for
        visual consistency across frontends."""
        w = HighlightedMarkdown("hi")
        assert w.code_theme == "monokai"
        assert DEFAULT_CODE_THEME == "monokai"

    def test_source_is_stored_verbatim(self):
        src = "# Title\n\npara"
        w = HighlightedMarkdown(src)
        assert w.markdown_source == src

    def test_update_markdown_replaces_source(self):
        w = HighlightedMarkdown("old")
        w.update_markdown("new content")
        assert w.markdown_source == "new content"

    def test_rich_is_available(self):
        """Rich is a hard dep of duh-cli — confirm the import succeeded
        (if this fails the gated-import fallback is being exercised,
        which is fine for production but means these tests skip the
        highlighting path)."""
        assert _HAS_RICH is True


# ---------------------------------------------------------------------------
# Syntax highlighting: language-aware fences
# ---------------------------------------------------------------------------


class TestSyntaxHighlighting:
    def test_python_code_block_gets_highlighted(self):
        """A fenced ``python`` block should produce ANSI color codes and
        the Monokai background color in the rendered output."""
        src = "```python\ndef foo():\n    return 42\n```\n"
        w = HighlightedMarkdown(src)
        out = _render_to_ansi(w)
        # Pygments emitted ANSI escape sequences ...
        assert _ANSI_PREFIX in out
        # ... and specifically the Monokai background (39, 40, 34 in 8-bit).
        assert "48;2;39;40;34" in out
        # The literal source tokens must survive (order may differ post-reflow).
        assert "def" in out
        assert "foo" in out
        assert "42" in out

    def test_bash_code_block_gets_highlighted(self):
        """Bash fences should also be highlighted (different lexer than
        python, but the same Monokai theme applies)."""
        src = "```bash\nls -la /tmp\necho hello\n```\n"
        w = HighlightedMarkdown(src)
        out = _render_to_ansi(w)
        assert _ANSI_PREFIX in out
        assert "48;2;39;40;34" in out
        assert "ls" in out
        assert "hello" in out

    def test_json_code_block_gets_highlighted(self):
        """JSON fences get a dedicated lexer — confirm colorization happens."""
        src = '```json\n{"key": "value", "n": 42}\n```\n'
        w = HighlightedMarkdown(src)
        out = _render_to_ansi(w)
        assert _ANSI_PREFIX in out
        assert "key" in out
        assert "value" in out

    def test_code_block_without_language_renders_without_error(self):
        """A fence with no language tag must still render (plain text,
        no Pygments tokenization) without raising."""
        src = "```\nsome plain text in a fence\n```\n"
        w = HighlightedMarkdown(src)
        # Must not raise
        out = _render_to_ansi(w)
        # Content survives
        assert "some plain text in a fence" in out

    def test_unknown_language_falls_back_gracefully(self):
        """An unknown fence language (e.g. ``foobarbaz``) must not crash
        the renderer — Rich's Markdown delegates to Pygments'
        ``guess_lexer`` which defaults to ``TextLexer`` on failure."""
        src = "```foobarbazzzz\nhello world\n```\n"
        w = HighlightedMarkdown(src)
        # Must not raise
        out = _render_to_ansi(w)
        # The content survives even though no lexer matched.
        assert "hello world" in out


# ---------------------------------------------------------------------------
# Non-code markdown still renders
# ---------------------------------------------------------------------------


class TestNonCodeMarkdown:
    def test_headers_render(self):
        w = HighlightedMarkdown("# Header One\n\nSome paragraph text.\n")
        out = _render_to_ansi(w)
        # The header text is present; Rich styles it (bold + underline)
        # which emits ANSI codes.
        assert "Header One" in out
        assert "Some paragraph text." in out
        assert _ANSI_PREFIX in out

    def test_bold_emphasis_renders(self):
        """Bold markdown should render the inner text with ANSI styling."""
        w = HighlightedMarkdown("This is **very bold** text.\n")
        out = _render_to_ansi(w)
        assert "very bold" in out
        assert _ANSI_PREFIX in out


# ---------------------------------------------------------------------------
# MessageWidget integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMessageWidgetIntegration:
    async def test_assistant_message_uses_highlighted_markdown(self):
        """Assistant messages must compose a HighlightedMarkdown body
        so they benefit from code-fence syntax highlighting."""
        from duh.ui.app import DuhApp

        from unittest.mock import MagicMock

        async def _run(_p):
            if False:
                yield {}

        engine = MagicMock()
        engine.run = _run
        engine.total_input_tokens = 0
        engine.total_output_tokens = 0
        engine.session_id = "sid"

        app = DuhApp(engine=engine, model="test")
        async with app.run_test(size=(120, 40)):
            mw = MessageWidget(role="assistant", text="# hi\n\n```python\nprint(1)\n```")
            await app.query_one("#message-log").mount(mw)
            # After mount, `_md_body` must be a HighlightedMarkdown instance.
            assert hasattr(mw, "_md_body")
            assert isinstance(mw._md_body, HighlightedMarkdown)
            assert mw._md_body.markdown_source == "# hi\n\n```python\nprint(1)\n```"

    async def test_user_message_does_not_use_highlighted_markdown(self):
        """User messages are plain text — no markdown parsing, no
        syntax highlighting.  This preserves the visual distinction
        between roles (assistant looks richer than user)."""
        from duh.ui.app import DuhApp
        from unittest.mock import MagicMock

        async def _run(_p):
            if False:
                yield {}

        engine = MagicMock()
        engine.run = _run
        engine.total_input_tokens = 0
        engine.total_output_tokens = 0
        engine.session_id = "sid"

        app = DuhApp(engine=engine, model="test")
        async with app.run_test(size=(120, 40)):
            uw = MessageWidget(role="user", text="hello world")
            await app.query_one("#message-log").mount(uw)
            # User widget uses plain Static, not HighlightedMarkdown.
            assert uw._body is not None
            assert not isinstance(uw._body, HighlightedMarkdown)
            assert isinstance(uw._body, Static)

    async def test_streaming_append_updates_highlighted_body(self):
        """Incremental ``append()`` calls must re-render the Rich
        markdown so syntax highlighting appears as the assistant streams."""
        from duh.ui.app import DuhApp
        from unittest.mock import MagicMock

        async def _run(_p):
            if False:
                yield {}

        engine = MagicMock()
        engine.run = _run
        engine.total_input_tokens = 0
        engine.total_output_tokens = 0
        engine.session_id = "sid"

        app = DuhApp(engine=engine, model="test")
        async with app.run_test(size=(120, 40)):
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            # Stream three deltas; final source must be the concatenation.
            mw.append("```python\n")
            mw.append("def hi():\n")
            mw.append("    pass\n```\n")
            assert mw._content == "```python\ndef hi():\n    pass\n```\n"
            assert mw._md_body.markdown_source == mw._content
