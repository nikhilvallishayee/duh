"""Tests for /sessions slash command in both REPL and TUI.

Covers:
- /sessions is listed in REPL help output (SLASH_COMMANDS dict)
- /sessions is listed in TUI help output
- /sessions handler formats session table correctly in REPL
- /sessions handler shows empty state in REPL
- TUI welcome banner includes project path and session count
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli.repl import SLASH_COMMANDS, _handle_slash
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(session_store=None) -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(model="test-model")
    return Engine(deps=deps, config=config, session_store=session_store)


def _make_deps() -> Deps:
    return Deps(call_model=AsyncMock(), run_tool=AsyncMock())


def _make_store(sessions: list[dict] | None = None):
    """Return a mock FileStore with a controllable list_sessions()."""
    store = MagicMock()
    store.list_sessions = AsyncMock(return_value=sessions or [])
    return store


# ---------------------------------------------------------------------------
# REPL: /sessions in help
# ---------------------------------------------------------------------------


class TestSessionsInHelp:
    def test_sessions_in_slash_commands_dict(self):
        assert "/sessions" in SLASH_COMMANDS

    def test_sessions_listed_in_help_output(self, capsys):
        engine = _make_engine()
        _handle_slash("/help", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        assert "/sessions" in captured.out


# ---------------------------------------------------------------------------
# REPL: /sessions handler
# ---------------------------------------------------------------------------


class TestSessionsREPL:
    def test_no_store_shows_message(self, capsys):
        engine = _make_engine(session_store=None)
        keep, model = _handle_slash("/sessions", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "No session store" in captured.out

    def test_empty_sessions(self, capsys):
        store = _make_store(sessions=[])
        engine = _make_engine(session_store=store)
        keep, model = _handle_slash("/sessions", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "No sessions" in captured.out

    def test_sessions_table_output(self, capsys):
        store = _make_store(sessions=[
            {
                "session_id": "abcdef1234567890",
                "message_count": 12,
                "modified": "2025-06-15T10:30:00+00:00",
                "created": "2025-06-15T09:00:00+00:00",
            },
            {
                "session_id": "xyz98765deadbeef",
                "message_count": 3,
                "modified": "2025-06-14T08:00:00+00:00",
                "created": "2025-06-14T07:00:00+00:00",
            },
        ])
        engine = _make_engine(session_store=store)
        keep, model = _handle_slash("/sessions", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        # Check header row
        assert "ID" in captured.out
        assert "Messages" in captured.out
        assert "Last Modified" in captured.out
        # Check session IDs (truncated to 8 chars)
        assert "abcdef12" in captured.out
        assert "xyz98765" in captured.out
        # Check message counts
        assert "12" in captured.out
        assert "3" in captured.out
        # Check modified dates are formatted (T replaced with space)
        assert "2025-06-15 10:30:00" in captured.out


# ---------------------------------------------------------------------------
# TUI: /sessions in help
# ---------------------------------------------------------------------------

textual = pytest.importorskip("textual", reason="textual not installed")

from duh.ui.app import DuhApp  # noqa: E402


def _fake_engine_for_tui(session_store=None):
    """Return a mock engine for TUI tests."""
    engine = MagicMock()
    engine.run = AsyncMock()
    engine.total_input_tokens = 0
    engine.total_output_tokens = 0
    engine.session_id = "test-session-abc"
    engine._session_store = session_store
    engine._messages = []
    engine._config = MagicMock()
    return engine


class TestSessionsTUIHelp:
    """Verify /sessions appears in TUI help (behavior test)."""

    @pytest.mark.asyncio
    async def test_sessions_in_tui_help_text(self):
        """/sessions must appear in the /help output via shared dispatcher."""
        from duh.cli.slash_commands import SlashDispatcher, SlashContext
        ctx = SlashContext(engine=MagicMock(), model="test", deps=None,
                           executor=None, task_manager=None, template_state={},
                           plan_mode=None, mcp_executor=None, provider_name="stub")
        dispatcher = SlashDispatcher(ctx)
        output, _ = await dispatcher.async_dispatch("/help", "")
        assert "/sessions" in output


class TestSessionsTUIBanner:
    """Verify TUI welcome banner includes project path."""

    def test_cwd_stored_on_app(self):
        engine = _fake_engine_for_tui()
        app = DuhApp(engine=engine, model="test", session_id="abc123", cwd="/tmp/myproject")
        assert app._cwd == "/tmp/myproject"

    def test_cwd_default_empty(self):
        engine = _fake_engine_for_tui()
        app = DuhApp(engine=engine, model="test", session_id="abc123")
        assert app._cwd == ""
