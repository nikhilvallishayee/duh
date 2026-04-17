"""Tests for the multi-line TUI input widget (ADR-073 Wave 1 #4).

Covers the :class:`SubmittableTextArea` used in ``DuhApp``:

* Enter submits (posts ``Submitted`` message).
* Shift+Enter inserts a newline (does not submit).
* Ctrl+J inserts a newline (terminal fallback).
* Empty submissions are ignored by ``DuhApp._submit``.
* Slash commands still route through ``_handle_slash`` on submit.
* The widget grows with content up to the 6-line cap.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

textual = pytest.importorskip("textual", reason="textual not installed")

from textual.widgets import TextArea  # noqa: E402

from duh.ui.app import DuhApp, SubmittableTextArea  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_engine(events: list[dict] | None = None) -> MagicMock:
    """Mock engine whose ``run()`` yields the given events (or nothing)."""
    events = events or []

    async def _run(_prompt: str):
        for ev in events:
            yield ev

    engine = MagicMock()
    engine.run = _run
    engine.total_input_tokens = 0
    engine.total_output_tokens = 0
    engine.session_id = "sid-test"
    return engine


# ---------------------------------------------------------------------------
# Static: class identity + subclassing
# ---------------------------------------------------------------------------


class TestSubmittableTextAreaStatic:
    def test_is_subclass_of_textarea(self):
        assert issubclass(SubmittableTextArea, TextArea)

    def test_has_submitted_message_class(self):
        assert hasattr(SubmittableTextArea, "Submitted")
        # Must expose a ``value`` attribute like Input.Submitted does.
        fields = {f for f in SubmittableTextArea.Submitted.__dataclass_fields__}
        assert "value" in fields
        assert "text_area" in fields


# ---------------------------------------------------------------------------
# Behavioural tests — require a running app for event/key pipelines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMultilineInput:
    async def test_widget_is_submittable_textarea(self):
        """compose() must yield a SubmittableTextArea, not a plain Input."""
        app = DuhApp(engine=_fake_engine())
        async with app.run_test(size=(120, 40)) as _pilot:
            widget = app.query_one("#prompt-input")
            assert isinstance(widget, SubmittableTextArea)

    async def test_enter_submits_text(self):
        """Pressing Enter triggers _submit (and clears the TextArea)."""
        called: list[str] = []
        app = DuhApp(engine=_fake_engine())

        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", SubmittableTextArea)

            # Patch _submit to record the submission without kicking off the
            # worker/engine path (which isn't what we're testing here).
            original_submit = app._submit

            async def _fake_submit() -> None:
                called.append(inp.text)
                inp.clear()

            app._submit = _fake_submit  # type: ignore[assignment]
            try:
                inp.focus()
                await pilot.pause()
                inp.load_text("hello world")
                await pilot.press("enter")
                await pilot.pause()
                assert called == ["hello world"]
                # TextArea was cleared by our fake submit.
                assert inp.text == ""
            finally:
                app._submit = original_submit  # type: ignore[assignment]

    async def test_shift_enter_inserts_newline(self):
        """Shift+Enter inserts \\n at the cursor without submitting."""
        submitted: list[str] = []
        app = DuhApp(engine=_fake_engine())

        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", SubmittableTextArea)

            async def _fake_submit() -> None:
                submitted.append(inp.text)

            app._submit = _fake_submit  # type: ignore[assignment]
            inp.focus()
            await pilot.pause()
            inp.load_text("line1")
            # Cursor is at end of line1.
            await pilot.press("shift+enter")
            await pilot.pause()
            # No submission occurred.
            assert submitted == []
            # Newline was inserted.
            assert "\n" in inp.text
            assert inp.document.line_count >= 2

    async def test_ctrl_j_inserts_newline(self):
        """Ctrl+J acts as a newline fallback for terminals that can't
        distinguish shift+enter."""
        submitted: list[str] = []
        app = DuhApp(engine=_fake_engine())

        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", SubmittableTextArea)

            async def _fake_submit() -> None:
                submitted.append(inp.text)

            app._submit = _fake_submit  # type: ignore[assignment]
            inp.focus()
            await pilot.pause()
            inp.load_text("first")
            await pilot.press("ctrl+j")
            await pilot.pause()
            # No submission.
            assert submitted == []
            # Newline inserted.
            assert inp.document.line_count >= 2
            assert "\n" in inp.text

    async def test_empty_submission_is_ignored(self):
        """An empty (or whitespace-only) TextArea should not advance the turn
        counter or mount a new user message."""
        app = DuhApp(engine=_fake_engine())
        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", SubmittableTextArea)
            log = app.query_one("#message-log")
            initial = len(list(log.children))

            # Blank.
            inp.clear()
            await pilot.click("#send-button")
            await pilot.pause(0.2)
            assert app._turn == 0
            assert len(list(log.children)) == initial

            # Whitespace only.
            inp.load_text("   \n  ")
            await pilot.click("#send-button")
            await pilot.pause(0.2)
            assert app._turn == 0
            assert len(list(log.children)) == initial

    async def test_slash_command_still_routes(self):
        """Slash commands must still be handled by ``_handle_slash`` rather
        than being sent to the engine."""
        handled: list[str] = []
        app = DuhApp(engine=_fake_engine())

        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", SubmittableTextArea)

            async def _fake_handle_slash(text: str) -> bool:
                handled.append(text)
                return True

            app._handle_slash = _fake_handle_slash  # type: ignore[assignment]
            inp.load_text("/help")
            await pilot.click("#send-button")
            await pilot.pause(0.2)

            assert handled == ["/help"]
            # Engine was not run for a slash command.
            assert app._turn == 0

    async def test_textarea_grows_with_content(self):
        """The ``document.line_count`` reflects the number of lines the user
        has typed.  CSS caps the rendered height at 6 rows."""
        app = DuhApp(engine=_fake_engine())
        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", SubmittableTextArea)

            # 1 line.
            inp.load_text("single line")
            await pilot.pause()
            assert inp.document.line_count == 1

            # 3 lines.
            inp.load_text("one\ntwo\nthree")
            await pilot.pause()
            assert inp.document.line_count == 3

            # 10 lines of content — document keeps them all, but the widget
            # scrolls internally once the CSS max-height is reached.
            inp.load_text("\n".join(f"line {i}" for i in range(10)))
            await pilot.pause()
            assert inp.document.line_count == 10

    async def test_focus_restored_to_textarea_on_mount(self):
        """The TextArea should have focus as soon as the app mounts."""
        app = DuhApp(engine=_fake_engine())
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            inp = app.query_one("#prompt-input", SubmittableTextArea)
            assert inp.has_focus
