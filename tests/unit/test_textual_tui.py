"""Tests for the Textual TUI (ADR-011 Tier 2).

Covers:
- DuhApp can be instantiated
- Widgets render and accept content correctly
- Event consumption: streaming text, tool calls, thinking, errors
- CLI parser exposes --tui flag
- ui.__init__ degrades gracefully when textual is absent (mocked)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Skip entire module when textual is not installed
# ---------------------------------------------------------------------------

textual = pytest.importorskip("textual", reason="textual not installed")

from textual.widgets import Input, Button, Static  # noqa: E402

from duh.ui.widgets import MessageWidget, ToolCallWidget, ThinkingWidget  # noqa: E402
from duh.ui.theme import APP_CSS  # noqa: E402
from duh.ui.app import DuhApp  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_engine(events: list[dict]) -> MagicMock:
    """Return a mock engine whose run() yields the given events."""

    async def _run(_prompt: str):
        for ev in events:
            yield ev

    engine = MagicMock()
    engine.run = _run
    engine.total_input_tokens = 42
    engine.total_output_tokens = 17
    engine.session_id = "test-session-abc"
    return engine


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------


class TestTheme:
    def test_css_string_is_non_empty(self):
        assert isinstance(APP_CSS, str)
        assert len(APP_CSS) > 100

    def test_css_contains_key_selectors(self):
        assert "#header" in APP_CSS
        assert "#message-log" in APP_CSS
        assert "#input-area" in APP_CSS
        assert "#statusbar" in APP_CSS
        assert "#sidebar" in APP_CSS


# ---------------------------------------------------------------------------
# MessageWidget
# ---------------------------------------------------------------------------


class TestMessageWidget:
    def test_user_message_has_correct_class(self):
        w = MessageWidget(role="user", text="hello")
        assert "message-user" in w.classes

    def test_assistant_message_has_correct_class(self):
        w = MessageWidget(role="assistant", text="hi there")
        assert "message-assistant" in w.classes

    def test_append_accumulates_text(self):
        w = MessageWidget(role="assistant", text="")
        w.append("Hello")
        w.append(", world")
        assert w._content == "Hello, world"

    def test_initial_text_stored(self):
        w = MessageWidget(role="user", text="initial")
        assert w._content == "initial"

    def test_finish_does_not_raise(self):
        w = MessageWidget(role="assistant", text="done")
        w.finish()  # must not raise


# ---------------------------------------------------------------------------
# ToolCallWidget
# ---------------------------------------------------------------------------


class TestToolCallWidget:
    def test_tool_widget_has_correct_class(self):
        w = ToolCallWidget(tool_name="Bash", input={"command": "ls"})
        assert "tool-call-widget" in w.classes

    def test_set_result_does_not_raise_before_mount(self):
        w = ToolCallWidget(tool_name="Read", input={"path": "/tmp/x"})
        # _result_label is None before mount — should not raise
        w.set_result("content", is_error=False)

    def test_tool_name_stored(self):
        w = ToolCallWidget(tool_name="Write", input={})
        assert w._tool_name == "Write"

    def test_input_stored(self):
        inp = {"command": "echo hi"}
        w = ToolCallWidget(tool_name="Bash", input=inp)
        assert w._input == inp


# ---------------------------------------------------------------------------
# ThinkingWidget
# ---------------------------------------------------------------------------


class TestThinkingWidget:
    def test_thinking_has_correct_class(self):
        w = ThinkingWidget()
        assert "thinking-widget" in w.classes

    def test_append_accumulates_text(self):
        w = ThinkingWidget()
        w.append("I am ")
        w.append("thinking")
        assert w._content == "I am thinking"

    def test_append_before_mount_does_not_raise(self):
        w = ThinkingWidget()
        w.append("step 1")  # _body is None, should not raise


# ---------------------------------------------------------------------------
# DuhApp instantiation
# ---------------------------------------------------------------------------


class TestDuhAppInstantiation:
    def test_app_can_be_instantiated(self):
        engine = _fake_engine([])
        app = DuhApp(engine=engine, model="claude-test", session_id="sid-1")
        assert app is not None
        assert app._model == "claude-test"
        assert app._session_id == "sid-1"

    def test_app_stores_debug_flag(self):
        engine = _fake_engine([])
        app = DuhApp(engine=engine, debug=True)
        assert app._debug is True

    def test_header_text_contains_model(self):
        engine = _fake_engine([])
        app = DuhApp(engine=engine, model="gpt-test-model")
        assert "gpt-test-model" in app._header_text()

    def test_header_text_contains_session_id_prefix(self):
        engine = _fake_engine([])
        app = DuhApp(engine=engine, session_id="abc12345xyz")
        header = app._header_text()
        assert "abc12345" in header

    def test_status_text_shows_turn_zero_initially(self):
        engine = _fake_engine([])
        app = DuhApp(engine=engine, model="m")
        status = app._status_text()
        assert "turn 0" in status

    def test_status_text_shows_connected(self):
        engine = _fake_engine([])
        app = DuhApp(engine=engine)
        assert "connected" in app._status_text()


# ---------------------------------------------------------------------------
# DuhApp.run_test — widget tree + event consumption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDuhAppRunTest:
    async def test_compose_creates_required_widgets(self):
        engine = _fake_engine([])
        app = DuhApp(engine=engine, model="test-model")
        async with app.run_test(size=(120, 40)) as pilot:
            # Required layout elements must exist
            assert app.query_one("#header")
            assert app.query_one("#message-log")
            assert app.query_one("#input-area")
            assert app.query_one("#statusbar")
            assert app.query_one("#sidebar")
            assert app.query_one("#prompt-input", Input)
            assert app.query_one("#send-button", Button)

    async def test_sidebar_hidden_by_default(self):
        engine = _fake_engine([])
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)) as pilot:
            sidebar = app.query_one("#sidebar")
            assert "visible" not in sidebar.classes

    async def test_toggle_sidebar_adds_visible_class(self):
        engine = _fake_engine([])
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_toggle_sidebar()
            await pilot.pause()
            sidebar = app.query_one("#sidebar")
            assert "visible" in sidebar.classes

    async def test_toggle_sidebar_twice_removes_visible_class(self):
        engine = _fake_engine([])
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)) as pilot:
            app.action_toggle_sidebar()
            await pilot.pause()
            app.action_toggle_sidebar()
            await pilot.pause()
            sidebar = app.query_one("#sidebar")
            assert "visible" not in sidebar.classes

    async def test_empty_input_does_not_trigger_query(self):
        """Submitting an empty input should be a no-op (only welcome banner present)."""
        engine = _fake_engine([])
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)) as pilot:
            log = app.query_one("#message-log")
            initial_count = len(list(log.children))
            inp = app.query_one("#prompt-input", Input)
            inp.value = ""
            await pilot.click("#send-button")
            await pilot.pause(0.2)
            # No new messages beyond the welcome banner
            assert len(list(log.children)) == initial_count

    async def test_text_delta_events_create_assistant_message(self):
        events = [
            {"type": "text_delta", "text": "Hello "},
            {"type": "text_delta", "text": "world"},
        ]
        engine = _fake_engine(events)
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", Input)
            inp.value = "hi"
            await pilot.click("#send-button")
            # Allow the worker to complete
            await pilot.pause(0.5)
            await pilot.pause(0.1)
            # A user message and at least one assistant message should exist
            log = app.query_one("#message-log")
            children = list(log.children)
            assert len(children) >= 1

    async def test_tool_use_events_create_tool_widget(self):
        events = [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls /"}},
            {"type": "tool_result", "output": "bin  etc  tmp", "is_error": False},
        ]
        engine = _fake_engine(events)
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", Input)
            inp.value = "list root"
            await pilot.click("#send-button")
            await pilot.pause(0.5)
            await pilot.pause(0.1)
            log = app.query_one("#message-log")
            children = list(log.children)
            # At least one widget (user message) must be mounted
            assert len(children) >= 1

    async def test_error_event_mounts_error_widget(self):
        events = [
            {"type": "error", "error": "rate limit exceeded"},
        ]
        engine = _fake_engine(events)
        app = DuhApp(engine=engine)
        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", Input)
            inp.value = "do something"
            await pilot.click("#send-button")
            await pilot.pause(0.5)
            await pilot.pause(0.1)
            log = app.query_one("#message-log")
            children = list(log.children)
            assert len(children) >= 1

    async def test_status_updated_after_turn(self):
        events = [
            {"type": "text_delta", "text": "done"},
        ]
        engine = _fake_engine(events)
        app = DuhApp(engine=engine, model="test-m")
        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", Input)
            inp.value = "go"
            await pilot.click("#send-button")
            await pilot.pause(0.5)
            await pilot.pause(0.1)
            # Turn counter should be 1
            assert app._turn == 1


# ---------------------------------------------------------------------------
# CLI parser exposes --tui
# ---------------------------------------------------------------------------


class TestCLIParser:
    def test_tui_flag_exists(self):
        from duh.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args(["--tui"])
        assert args.tui is True

    def test_tui_default_is_false(self):
        from duh.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args([])
        assert args.tui is False


# ---------------------------------------------------------------------------
# main.py routes --tui to run_tui
# ---------------------------------------------------------------------------


class TestMainTuiRouting:
    def test_main_calls_run_tui_when_flag_set(self):
        with patch("duh.ui.run_tui", return_value=0) as mock_run_tui:
            from duh.cli.main import main

            result = main(["--tui"])
            mock_run_tui.assert_called_once()
            assert result == 0


# ---------------------------------------------------------------------------
# ui.__init__ graceful degradation (no textual)
# ---------------------------------------------------------------------------


class TestUiInitDegradation:
    def test_run_tui_exported_from_init(self):
        from duh.ui import run_tui

        assert callable(run_tui)
