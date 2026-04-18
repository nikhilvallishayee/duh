"""Empirical tests for the "TUI streaming is not visible" bug report.

The user reports that deltas don't render progressively in the TUI; either
the whole response appears at once, or nothing appears until scroll.

Recent suspects:
  1. PR #45: deferred-markdown — ``MessageWidget`` streams into a plain
     ``Static`` and swaps to ``HighlightedMarkdown`` only on ``finish()``.
     If the plain Static never refreshes visibly, streaming is invisible.
  2. ``TextDeltaCoalescer`` 8 ms window — if the timer never fires,
     the buffer is stuck until ``finish()`` triggers an explicit flush.
  3. ``Static.update()`` must actually drive a layout refresh.

These tests do NOT mock rendering — they drive a live ``Textual`` app via
``run_test`` and assert on what the widget claims to render.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

textual = pytest.importorskip("textual", reason="textual not installed")

from textual.widgets import Static  # noqa: E402

from duh.ui.app import DuhApp, TextDeltaCoalescer  # noqa: E402
from duh.ui.widgets import MessageWidget  # noqa: E402


def _make_app() -> DuhApp:
    """Minimal DuhApp with a no-op engine."""

    async def _run(_p):
        if False:
            yield {}

    engine = MagicMock()
    engine.run = _run
    engine.total_input_tokens = 0
    engine.total_output_tokens = 0
    engine.session_id = "sid"
    return DuhApp(engine=engine, model="test")


def _body_text(mw: MessageWidget) -> str:
    """Return the string currently held inside the MessageWidget's body.

    The key invariant: whatever the Static renders visually should match
    the ``_content`` attribute — if the Static's stored content diverges
    from ``_content``, we have a streaming-visibility bug.
    """
    body = mw._body
    if body is None:
        return ""
    return str(body.content)


# ---------------------------------------------------------------------------
# Direct widget-level test — does append() visibly drive Static content?
# ---------------------------------------------------------------------------


class TestMessageWidgetStreaming:
    @pytest.mark.asyncio
    async def test_streaming_content_visible_before_finish(self):
        """During streaming, the body's ``content`` must reflect the
        accumulated text — not be stuck at empty until ``finish()``."""
        app = _make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            await pilot.pause(0.01)

            mw.append("Hello ")
            await pilot.pause(0.01)
            assert "Hello" in _body_text(mw), (
                f"after first append, body content = {_body_text(mw)!r}"
            )

            mw.append("world!")
            await pilot.pause(0.01)
            assert "Hello world!" == _body_text(mw), (
                f"after second append, body content = {_body_text(mw)!r}"
            )

    @pytest.mark.asyncio
    async def test_body_refresh_called_on_append(self):
        """Each ``append()`` must call ``Static.refresh()`` (via
        ``Static.update()``).  If refresh is never triggered, the terminal
        cell buffer is never repainted and the user sees nothing."""
        app = _make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            await pilot.pause(0.01)

            refresh_count = {"n": 0}
            original = mw._body.refresh  # type: ignore[union-attr]

            def _counting_refresh(*args, **kwargs):
                refresh_count["n"] += 1
                return original(*args, **kwargs)

            mw._body.refresh = _counting_refresh  # type: ignore[union-attr,method-assign]

            mw.append("a")
            mw.append("b")
            mw.append("c")
            await pilot.pause(0.01)
            assert refresh_count["n"] >= 3, (
                f"expected ≥ 3 refresh calls, got {refresh_count['n']}"
            )

    @pytest.mark.asyncio
    async def test_visual_render_contains_streamed_text(self):
        """The Static's actual visual (the thing Textual paints to the
        terminal) must contain the streamed characters as plain text."""
        app = _make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            await pilot.pause(0.01)

            mw.append("Streaming text visible?")
            await pilot.pause(0.02)

            # Access the Static's visual — the actual rendered object.
            body = mw._body
            assert body is not None
            visual_str = str(body.visual)
            assert "Streaming text visible" in visual_str, (
                f"visual did not contain streamed text: {visual_str!r}"
            )


# ---------------------------------------------------------------------------
# Coalescer + active widget pipeline (simulates _flush_delta_to_active)
# ---------------------------------------------------------------------------


class TestCoalescerFlushesToWidget:
    @pytest.mark.asyncio
    async def test_coalescer_timer_delivers_to_widget(self):
        """End-to-end: deltas go through the coalescer's timer tick and
        reach the MessageWidget body.  If the timer never fires, the body
        stays empty and the user sees nothing until finish()."""
        app = _make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            await pilot.pause(0.01)

            # Wire a coalescer directly to the widget's append.
            coalescer = TextDeltaCoalescer(
                interval_s=0.008, flush_cb=mw.append,
            )
            coalescer.add("Hello ")
            coalescer.add("streaming ")
            coalescer.add("world")
            # Must wait > interval_s for the timer to fire.
            await pilot.pause(0.05)

            assert _body_text(mw) == "Hello streaming world", (
                f"coalescer failed to deliver during streaming; "
                f"body={_body_text(mw)!r}, coalescer.flush_count="
                f"{coalescer.flush_count}"
            )

    @pytest.mark.asyncio
    async def test_app_active_assistant_updates_during_stream(self):
        """Drive the app's real coalescer via its flush callback and
        verify the widget body visibly updates before any finish()."""
        app = _make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            mw = MessageWidget(role="assistant", text="")
            await app.query_one("#message-log").mount(mw)
            await pilot.pause(0.01)

            # Pretend the worker just created this as the active assistant.
            app._active_assistant = mw

            # Push deltas via the REAL coalescer — the same one the
            # streaming pipeline uses.
            app._delta_coalescer.add("Part 1. ")
            app._delta_coalescer.add("Part 2. ")
            app._delta_coalescer.add("Part 3.")
            await pilot.pause(0.05)

            assert "Part 1. Part 2. Part 3." in _body_text(mw), (
                f"real coalescer did not deliver mid-stream; "
                f"body={_body_text(mw)!r}"
            )


# ---------------------------------------------------------------------------
# End-to-end: drive _run_query with a stub engine that yields real deltas
# ---------------------------------------------------------------------------


class TestEndToEndStreamingVisible:
    @pytest.mark.asyncio
    async def test_deltas_visible_before_done_event(self):
        """This is the core user bug report: stream many text_delta events
        with gaps, and the widget body must progressively fill — not wait
        for the ``done`` event before anything appears."""

        # Stub engine yielding 10 deltas with 15 ms gaps.  15 ms > 8 ms
        # coalescer window, so each delta should drive a timer flush.
        async def _run(_p):
            for i in range(10):
                yield {"type": "text_delta", "text": f"[{i}]"}
                await asyncio.sleep(0.015)
            yield {"type": "assistant"}
            yield {"type": "done", "stop_reason": "end_turn", "turns": 1}

        engine = MagicMock()
        engine.run = _run
        engine.total_input_tokens = 0
        engine.total_output_tokens = 0
        engine.session_id = "sid"

        app = DuhApp(engine=engine, model="test")
        async with app.run_test(size=(120, 40)) as pilot:
            # Kick off the worker path directly — mimics what _submit()
            # does after the user hits Enter.
            app._run_query("test prompt")
            # After 60 ms (4 deltas at ~15 ms each), we should already
            # see the first few tokens visibly in the body.
            await pilot.pause(0.06)
            active = app._active_assistant
            assert active is not None, (
                "no active assistant widget was created during streaming"
            )
            mid_stream = _body_text(active)
            assert "[0]" in mid_stream, (
                f"user would see NOTHING at mid-stream; body={mid_stream!r}"
            )
            # Wait for the rest to complete.
            await pilot.pause(0.3)
            # After done, the widget body is promoted to HighlightedMarkdown.
            # Either way the full text must be visible somewhere.
            if active._md_body is not None:
                final = active._md_body.markdown_source
            else:
                final = _body_text(active)
            for i in range(10):
                assert f"[{i}]" in final, (
                    f"final rendering missing [{i}]; got {final!r}"
                )

    @pytest.mark.asyncio
    async def test_progressive_snapshots_show_increasing_content(self):
        """Take samples at 50 ms, 100 ms, 200 ms. Each snapshot must
        have strictly more content than the prior one."""

        async def _run(_p):
            for i in range(20):
                yield {"type": "text_delta", "text": f"chunk{i:02d} "}
                await asyncio.sleep(0.02)
            yield {"type": "assistant"}
            yield {"type": "done", "stop_reason": "end_turn", "turns": 1}

        engine = MagicMock()
        engine.run = _run
        engine.total_input_tokens = 0
        engine.total_output_tokens = 0
        engine.session_id = "sid"

        app = DuhApp(engine=engine, model="test")
        async with app.run_test(size=(120, 40)) as pilot:
            app._run_query("test prompt")

            await pilot.pause(0.05)
            active = app._active_assistant
            assert active is not None
            snap1 = _body_text(active)

            await pilot.pause(0.1)
            snap2 = _body_text(active)

            await pilot.pause(0.15)
            snap3 = _body_text(active)

            # Ensure worker completes before teardown to avoid #header
            # lookup in the worker's finally block racing app shutdown.
            await pilot.pause(0.5)

            # Streaming visibility claim: length(snap1) < length(snap2) < length(snap3).
            assert len(snap1) < len(snap2), (
                f"body did not grow between 50 ms and 150 ms; "
                f"snap1={snap1!r}, snap2={snap2!r}"
            )
            assert len(snap2) < len(snap3) or len(snap3) >= len(
                "chunk00 chunk01 chunk02 chunk03 chunk04 chunk05 "
                "chunk06 chunk07 chunk08 chunk09 chunk10 chunk11 "
                "chunk12 chunk13 chunk14 chunk15 chunk16 chunk17 "
                "chunk18 chunk19 "
            ), (
                f"body did not grow between 150 ms and 300 ms; "
                f"snap2={snap2!r}, snap3={snap3!r}"
            )

    @pytest.mark.asyncio
    async def test_streaming_still_visible_with_usage_delta_events(self):
        """The real engine interleaves ``usage_delta`` with ``text_delta``
        events (every ~40 chars).  Each ``usage_delta`` triggers
        ``_refresh_status`` which calls Static.update() on #header and
        #statusbar — layout=True by default.  Verify that the frequent
        layout passes don't HIDE the streaming text."""

        async def _run(_p):
            # Mimic engine's pattern: text_delta then usage_delta every
            # few deltas.  This is the actual production event sequence.
            for i in range(10):
                yield {"type": "text_delta", "text": f"word{i} "}
                await asyncio.sleep(0.01)
                yield {
                    "type": "usage_delta",
                    "input_tokens": 100,
                    "output_tokens": 10 + i * 5,
                    "estimated": True,
                    "model": "test",
                }
            yield {"type": "assistant"}
            yield {"type": "done", "stop_reason": "end_turn", "turns": 1}

        engine = MagicMock()
        engine.run = _run
        engine.total_input_tokens = 0
        engine.total_output_tokens = 0
        engine.session_id = "sid"

        app = DuhApp(engine=engine, model="test")
        async with app.run_test(size=(120, 40)) as pilot:
            app._run_query("x")
            await pilot.pause(0.08)

            active = app._active_assistant
            assert active is not None
            mid = _body_text(active)
            assert "word0" in mid, (
                f"usage_delta layout passes hid the streaming text; "
                f"body={mid!r}"
            )

            # Let the stream finish cleanly.
            await pilot.pause(0.5)

    @pytest.mark.asyncio
    async def test_scroll_end_uses_immediate_and_force(self):
        """Regression lock: ``_flush_delta_to_active`` must call
        ``scroll_end(immediate=True, force=True)``.

        Without ``immediate=True`` the scroll is deferred until the next
        screen refresh, so rapid streaming content falls below the
        viewport and the user sees nothing until they scroll manually
        (the reported bug).  ``force=True`` overrides user scrollback so
        the viewport stays pinned to the growing bottom.
        """
        import inspect

        source = inspect.getsource(DuhApp._flush_delta_to_active)
        assert "scroll_end" in source
        assert "immediate=True" in source, (
            "_flush_delta_to_active must call scroll_end(immediate=True); "
            "without it, deferred scrolls lag the append() rate and "
            "streamed text is invisible until the user scrolls."
        )
        assert "force=True" in source, (
            "_flush_delta_to_active must call scroll_end(force=True); "
            "without it, a user who scrolled up earlier will never see "
            "new streamed content reach the viewport."
        )

    @pytest.mark.asyncio
    async def test_compositor_output_contains_streamed_text_mid_stream(self):
        """Verify the final compositor output — the text that would be
        painted onto the terminal — actually contains the streamed deltas
        mid-stream.  If this fails, the coalescer/widget path works but
        something in the rendering layer is dropping bytes.
        """

        async def _run(_p):
            for i in range(5):
                yield {"type": "text_delta", "text": f"TOK{i}_"}
                await asyncio.sleep(0.03)
            yield {"type": "assistant"}
            yield {"type": "done", "stop_reason": "end_turn", "turns": 1}

        engine = MagicMock()
        engine.run = _run
        engine.total_input_tokens = 0
        engine.total_output_tokens = 0
        engine.session_id = "sid"

        app = DuhApp(engine=engine, model="test")
        async with app.run_test(size=(120, 40)) as pilot:
            app._run_query("x")
            await pilot.pause(0.1)

            # Export the compositor screenshot mid-stream.
            svg = app.export_screenshot(title="mid-stream")
            # At least the first token should have reached the compositor.
            assert "TOK0_" in svg, (
                "first streamed token did not reach the terminal "
                "compositor mid-stream — streaming IS invisible to the user"
            )

            # Wait for completion, then teardown cleanly.
            await pilot.pause(0.5)
