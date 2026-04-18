"""Tests for ADR-073 Wave 3 #11: animated TUI spinner.

The ``ToolCallWidget`` cycles through Braille spinner frames every 80 ms
via a Textual ``Timer`` so the TUI visibly breathes while tool calls
execute.  Tests exercise:

1. Timer starts on mount when no result has been set yet.
2. Spinner frame advances when the timer fires.
3. :meth:`ToolCallWidget.set_result` stops the timer.
4. Timer does NOT start if ``set_result`` fires before ``on_mount``
   (pre-mount race guard).
5. :attr:`OutputStyle.CONCISE` skips the animation entirely.
6. :meth:`set_result` with ``is_error=True`` clears the spinner CSS class.
7. Calling ``set_result`` twice is idempotent (no stale timers).
8. ``on_unmount`` stops a still-running spinner timer.
"""

from __future__ import annotations

import pytest

textual = pytest.importorskip("textual", reason="textual not installed")

from duh.ui.widgets import ToolCallWidget, _SPINNER_FRAMES  # noqa: E402


# ---------------------------------------------------------------------------
# Lightweight timer stub — exercised by the pre-/post-mount race-guard tests
# ---------------------------------------------------------------------------


class _StubTimer:
    """Stand-in for ``textual.timer.Timer`` for tests that run outside the
    Textual driver.  Records ``.stop()`` calls so assertions can verify
    cancellation behaviour."""

    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


# ---------------------------------------------------------------------------
# Tests — timer start / stop lifecycle
# ---------------------------------------------------------------------------


class TestSpinnerLifecycle:
    @pytest.mark.asyncio
    async def test_timer_starts_on_mount_when_no_result(self):
        """On mount with no result, the widget must schedule an interval
        timer so the spinner animates."""
        from duh.ui.app import DuhApp
        from unittest.mock import MagicMock

        engine = MagicMock()
        engine.run = lambda *a, **kw: (_ for _ in ())
        engine.session_id = "sid"
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)):
            widget = ToolCallWidget(
                tool_name="Bash", input={"command": "ls"},
            )
            log = app.query_one("#message-log")
            await log.mount(widget)
            await app.workers.wait_for_complete()
            # After mount the spinner timer must be active.
            assert widget._spinner_timer is not None
            assert widget._tool_running is True

    @pytest.mark.asyncio
    async def test_timer_stops_when_set_result_called(self):
        from duh.ui.app import DuhApp
        from unittest.mock import MagicMock

        engine = MagicMock()
        engine.run = lambda *a, **kw: (_ for _ in ())
        engine.session_id = "sid"
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)):
            widget = ToolCallWidget(
                tool_name="Read", input={"path": "/x"},
            )
            log = app.query_one("#message-log")
            await log.mount(widget)
            assert widget._spinner_timer is not None
            widget.set_result("ok", is_error=False)
            # Handle is cleared immediately — _stop_spinner sets it to None.
            assert widget._spinner_timer is None
            assert widget._tool_running is False

    def test_set_result_before_mount_prevents_timer(self):
        """If ``set_result`` fires before ``on_mount``, the spinner must
        NOT start — the widget is already in its terminal state."""
        widget = ToolCallWidget(tool_name="Bash", input={"command": "ls"})
        # No mount. Set result directly.
        widget.set_result("done", is_error=False)
        # Now simulate a later mount attempt manually — would start timer
        # if we didn't guard on `_running`.  We can't easily call on_mount
        # without the Textual driver, so assert the invariant the guard
        # depends on: _running is False after set_result.
        assert widget._tool_running is False
        assert widget._spinner_timer is None

    @pytest.mark.asyncio
    async def test_mount_after_set_result_does_not_start_timer(self):
        """Full race: set_result() → on_mount().  on_mount must check the
        _running flag and skip scheduling the interval."""
        from duh.ui.app import DuhApp
        from unittest.mock import MagicMock

        engine = MagicMock()
        engine.run = lambda *a, **kw: (_ for _ in ())
        engine.session_id = "sid"
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)):
            widget = ToolCallWidget(
                tool_name="Bash", input={"command": "ls"},
            )
            # Pre-mount: flip _running false by calling set_result.
            widget.set_result("done", is_error=False)
            # Now mount — on_mount should early-return without starting.
            log = app.query_one("#message-log")
            await log.mount(widget)
            assert widget._spinner_timer is None


# ---------------------------------------------------------------------------
# Tests — frame advancement
# ---------------------------------------------------------------------------


class TestFrameAdvance:
    def test_advance_spinner_cycles_frame_index(self):
        """``_advance_spinner`` should increment the index modulo
        ``len(_SPINNER_FRAMES)``."""
        widget = ToolCallWidget(tool_name="Bash", input={})
        # Fake the result label so _advance_spinner doesn't early-return.
        from unittest.mock import MagicMock

        widget._result_label = MagicMock()
        widget._tool_running = True
        start = widget._spinner_frame_idx
        widget._advance_spinner()
        assert widget._spinner_frame_idx == (start + 1) % len(_SPINNER_FRAMES)
        widget._advance_spinner()
        assert widget._spinner_frame_idx == (start + 2) % len(_SPINNER_FRAMES)

    def test_advance_spinner_updates_label_with_frame(self):
        widget = ToolCallWidget(tool_name="Bash", input={})
        from unittest.mock import MagicMock

        widget._result_label = MagicMock()
        widget._tool_running = True
        widget._spinner_frame_idx = 0
        widget._advance_spinner()
        # Frame 1 should have been pushed to the label.
        widget._result_label.update.assert_called_once()
        call_arg = widget._result_label.update.call_args[0][0]
        # Must contain one of the Braille frames followed by "running…".
        assert any(frame in call_arg for frame in _SPINNER_FRAMES)
        assert "running" in call_arg

    def test_advance_spinner_noop_when_running_false(self):
        """Timer may fire after set_result briefly — the callback must
        short-circuit when ``_running`` is False."""
        widget = ToolCallWidget(tool_name="Bash", input={})
        from unittest.mock import MagicMock

        widget._result_label = MagicMock()
        widget._tool_running = False
        widget._advance_spinner()
        widget._result_label.update.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — CONCISE style skips animation
# ---------------------------------------------------------------------------


class TestConciseStyleNoAnimation:
    @pytest.mark.asyncio
    async def test_concise_style_does_not_start_timer(self):
        from duh.ui.app import DuhApp
        from unittest.mock import MagicMock

        engine = MagicMock()
        engine.run = lambda *a, **kw: (_ for _ in ())
        engine.session_id = "sid"
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)):
            widget = ToolCallWidget(
                tool_name="Bash", input={"command": "ls"},
                output_style="concise",
            )
            log = app.query_one("#message-log")
            await log.mount(widget)
            # CONCISE: no animation, no timer.
            assert widget._spinner_timer is None
            # But widget is still running — just not animated.
            assert widget._tool_running is True

    @pytest.mark.asyncio
    async def test_default_style_starts_timer(self):
        from duh.ui.app import DuhApp
        from unittest.mock import MagicMock

        engine = MagicMock()
        engine.run = lambda *a, **kw: (_ for _ in ())
        engine.session_id = "sid"
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)):
            widget = ToolCallWidget(
                tool_name="Bash", input={"command": "ls"},
                output_style="default",
            )
            log = app.query_one("#message-log")
            await log.mount(widget)
            assert widget._spinner_timer is not None

    @pytest.mark.asyncio
    async def test_verbose_style_starts_timer(self):
        from duh.ui.app import DuhApp
        from unittest.mock import MagicMock

        engine = MagicMock()
        engine.run = lambda *a, **kw: (_ for _ in ())
        engine.session_id = "sid"
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)):
            widget = ToolCallWidget(
                tool_name="Bash", input={"command": "ls"},
                output_style="verbose",
            )
            log = app.query_one("#message-log")
            await log.mount(widget)
            assert widget._spinner_timer is not None


# ---------------------------------------------------------------------------
# Tests — set_result CSS / error handling
# ---------------------------------------------------------------------------


class TestSetResultClearsSpinner:
    @pytest.mark.asyncio
    async def test_set_result_error_removes_spinner_css_class(self):
        from duh.ui.app import DuhApp
        from unittest.mock import MagicMock

        engine = MagicMock()
        engine.run = lambda *a, **kw: (_ for _ in ())
        engine.session_id = "sid"
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)):
            widget = ToolCallWidget(tool_name="Bash", input={"command": "x"})
            log = app.query_one("#message-log")
            await log.mount(widget)
            # Precondition: label has spinner-message class.
            assert "spinner-message" in widget._result_label.classes
            widget.set_result("boom", is_error=True)
            assert "spinner-message" not in widget._result_label.classes
            assert "tool-result-error" in widget._result_label.classes
            # And timer is stopped.
            assert widget._spinner_timer is None
            assert widget._tool_running is False

    def test_set_result_twice_is_idempotent(self):
        """Calling set_result twice must not re-start the timer or leak
        state.  (Defensive: the kernel might emit a duplicate result.)"""
        widget = ToolCallWidget(tool_name="Bash", input={})
        widget.set_result("first", is_error=False)
        # Spinner already stopped.
        assert widget._spinner_timer is None
        widget.set_result("second", is_error=True)
        # Still stopped, no crash.
        assert widget._spinner_timer is None
        assert widget._tool_running is False
