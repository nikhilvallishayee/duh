"""Tests for the deferred-markdown streaming optimization in
:class:`duh.ui.widgets.MessageWidget`.

Background
----------
Before this optimization, every coalesced ``text_delta`` flush triggered
:meth:`HighlightedMarkdown.update_markdown`, which re-parsed the ENTIRE
accumulated buffer via ``rich.markdown.Markdown``.  On a ~20 KB response
streamed as ~125 flushes, that's ~125 full-buffer parses — O(n²) work.

After the optimization, assistant messages stream into a plain
:class:`textual.widgets.Static` during :meth:`append`; the full markdown
parse runs *exactly once* when the ``assistant`` event triggers
:meth:`finish`.

These tests lock down the new invariants:

1. During streaming the body is a plain Static (NOT HighlightedMarkdown).
2. After ``finish()`` the body is a HighlightedMarkdown with the full source.
3. 100 ``append()`` calls trigger at MOST 1 RichMarkdown construction
   (the one fired by ``finish``).
4. ``finish()`` without any prior ``append()`` still works (empty-message
   edge case).
5. ``finish()`` is idempotent — calling it twice does not double-parse.
6. User messages keep using plain Static always (no new behaviour).
7. Markdown content (headers, code fences) renders correctly at finish.
8. Streaming plain text preserves whitespace and newlines exactly.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

textual = pytest.importorskip("textual", reason="textual not installed")

from textual.widgets import Static  # noqa: E402

from duh.ui.widgets import (  # noqa: E402
    HighlightedMarkdown,
    MessageWidget,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app():
    """Return a minimal DuhApp wired with a no-op engine so we can
    ``run_test`` it and mount widgets into ``#message-log``."""
    from duh.ui.app import DuhApp

    async def _run(_p):
        if False:
            yield {}

    engine = MagicMock()
    engine.run = _run
    engine.total_input_tokens = 0
    engine.total_output_tokens = 0
    engine.session_id = "sid"
    return DuhApp(engine=engine, model="test")


# ---------------------------------------------------------------------------
# Streaming-state invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestStreamingBody:
    async def test_during_streaming_body_is_plain_static(self):
        """While the widget is in the streaming state, the body must be a
        plain :class:`Static` and NOT a :class:`HighlightedMarkdown`."""
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            mw.append("# Header\n\nSome text")
            # Body is a Static but not the markdown subclass.
            assert mw._body is not None
            assert isinstance(mw._body, Static)
            assert not isinstance(mw._body, HighlightedMarkdown)
            # No HighlightedMarkdown has been constructed yet.
            assert mw._md_body is None
            # _streaming flag is still True.
            assert mw._streaming is True

    async def test_after_finish_body_is_highlighted_markdown(self):
        """After ``finish()`` the widget promotes to HighlightedMarkdown
        with the full accumulated source."""
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            mw.append("# Title\n\n")
            mw.append("Paragraph one.\n")
            mw.finish()
            assert mw._md_body is not None
            assert isinstance(mw._md_body, HighlightedMarkdown)
            assert mw._md_body.markdown_source == "# Title\n\nParagraph one.\n"
            # Streaming flag flipped.
            assert mw._streaming is False
            # Plain Static body cleared so we don't leak a reference
            # to a now-removed widget.
            assert mw._body is None


# ---------------------------------------------------------------------------
# Parse-count invariant (the actual performance claim)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestParseCount:
    async def test_many_appends_trigger_at_most_one_markdown_parse(self):
        """100 ``append()`` calls during streaming must NOT construct 100
        ``rich.markdown.Markdown`` objects — the whole point of the
        optimization is that the parse happens only at ``finish()``."""
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            # Patch RichMarkdown at the widgets-module level so we count
            # every construction that happens inside widget code.
            with patch("duh.ui.widgets.RichMarkdown") as mock_md:
                mock_md.return_value = "STUB"
                for i in range(100):
                    mw.append(f"line {i}\n")
                # No RichMarkdown constructed during streaming.
                assert mock_md.call_count == 0
                # Now finish — exactly ONE construction.
                mw.finish()
                assert mock_md.call_count == 1

    async def test_finish_called_without_appends_still_renders(self):
        """Edge case: ``finish()`` on a widget that never received any
        ``append()`` (empty assistant message).  Must not raise, and
        the widget must still expose a HighlightedMarkdown with empty
        source."""
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            mw.finish()  # Must not raise
            assert mw._md_body is not None
            assert mw._md_body.markdown_source == ""
            assert mw._streaming is False

    async def test_finish_is_idempotent(self):
        """Calling ``finish()`` twice must not trigger a second parse or
        mount a second HighlightedMarkdown."""
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            mw.append("hello")
            with patch("duh.ui.widgets.RichMarkdown") as mock_md:
                mock_md.return_value = "STUB"
                mw.finish()
                first_md_body = mw._md_body
                assert mock_md.call_count == 1
                # Second finish — no-op.
                mw.finish()
                assert mock_md.call_count == 1
                # _md_body reference is unchanged (no replacement widget).
                assert mw._md_body is first_md_body


# ---------------------------------------------------------------------------
# User messages are unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUserMessageUnaffected:
    async def test_user_message_always_plain_static(self):
        """User messages must ALWAYS use plain Static — there is no
        'streaming' markdown promotion for user turns."""
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            uw = MessageWidget(role="user", text="hi there")
            await app.query_one("#message-log").mount(uw)
            assert isinstance(uw._body, Static)
            assert not isinstance(uw._body, HighlightedMarkdown)
            assert uw._md_body is None
            # append() on a user message updates Static without any
            # markdown construction path.
            with patch("duh.ui.widgets.RichMarkdown") as mock_md:
                mock_md.return_value = "STUB"
                uw.append(" — and more")
                uw.finish()  # no-op for user role
                assert mock_md.call_count == 0
            assert uw._content == "hi there — and more"
            # Still plain Static; _md_body never created.
            assert isinstance(uw._body, Static)
            assert uw._md_body is None


# ---------------------------------------------------------------------------
# Correctness: final markdown render produces expected content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFinalMarkdownRender:
    async def test_code_block_renders_with_syntax_highlighting_at_finish(self):
        """After ``finish()``, a fenced code block in the final source
        must survive into the HighlightedMarkdown's source verbatim so
        Pygments can syntax-highlight it on render."""
        import io

        from rich.console import Console
        from rich.markdown import Markdown as RichMarkdown

        app = _make_app()
        async with app.run_test(size=(120, 40)):
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            mw.append("Here is code:\n\n")
            mw.append("```python\n")
            mw.append("def answer():\n    return 42\n")
            mw.append("```\n")
            mw.finish()
            # HighlightedMarkdown holds the exact accumulated source.
            src = mw._md_body.markdown_source
            assert "```python" in src
            assert "def answer():" in src
            assert "return 42" in src
            # Render it the same way the widget does and confirm
            # Pygments emitted ANSI color codes for the code fence
            # (i.e. syntax highlighting is actually happening).
            buf = io.StringIO()
            console = Console(
                file=buf, force_terminal=True,
                color_system="truecolor", width=80,
            )
            console.print(RichMarkdown(src, code_theme=mw._md_body.code_theme))
            out = buf.getvalue()
            assert "\x1b[" in out  # ANSI escape prefix present
            assert "def" in out
            assert "answer" in out

    async def test_streaming_preserves_whitespace_and_newlines_exactly(self):
        """During streaming the plain Static must preserve whitespace and
        newlines byte-for-byte — no markdown-driven collapsing of
        consecutive spaces, no reflow.

        We verify by spying on ``Static.update`` to capture the exact
        argument passed on each append and asserting the final delivery
        matches the raw accumulated bytes.
        """
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            # Nasty mix: tabs, multiple spaces, blank lines.
            tricky = "line one\n\n  \tindented (tab+spaces)\n\n\nfinal"
            # Spy on the Static's update method so we can capture the
            # exact string delivered to the rendering backend.
            captured: list[str] = []
            original_update = mw._body.update

            def _capture(payload=""):
                captured.append(payload)
                return original_update(payload)

            mw._body.update = _capture  # type: ignore[method-assign]
            for ch in tricky:
                mw.append(ch)
            # _content is the byte-exact accumulation.
            assert mw._content == tricky
            # The final update() call delivered the full raw string,
            # with every whitespace character preserved.
            assert captured, "expected at least one Static.update call"
            assert captured[-1] == tricky
            # No markdown interpretation happened during streaming —
            # the markdown-promotion sentinel must still be None.
            assert mw._md_body is None


# ---------------------------------------------------------------------------
# Performance sanity check (micro-benchmark assertion)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPerformanceSanity:
    async def test_simulated_125_flushes_yield_one_parse(self):
        """Simulates the real-world case described in the optimization
        note: ~125 coalesced flushes on a 20 KB response.  Before: 125
        RichMarkdown constructions.  After: 1.

        This test asserts the new behaviour concretely so regressions
        that reintroduce per-flush parsing are caught by CI.
        """
        app = _make_app()
        async with app.run_test(size=(120, 40)):
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            chunk = "x" * 160  # 160 chars × 125 = 20 000 chars ≈ 20 KB
            with patch("duh.ui.widgets.RichMarkdown") as mock_md:
                mock_md.return_value = "STUB"
                for _ in range(125):
                    mw.append(chunk)
                # Zero parses during the flush storm.
                assert mock_md.call_count == 0
                mw.finish()
                # Exactly one parse at the very end.
                assert mock_md.call_count == 1
            # Content integrity: no bytes lost.
            assert len(mw._content) == 125 * 160
            assert mw._content == chunk * 125
