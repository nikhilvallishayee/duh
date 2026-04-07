"""Tests for the /search REPL command."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from duh.cli.repl import _handle_slash, _search_messages, SLASH_COMMANDS
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message


def _make_engine() -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(model="test-model")
    return Engine(deps=deps, config=config)


def _make_deps() -> Deps:
    return Deps(call_model=AsyncMock(), run_tool=AsyncMock())


def _populate(engine: Engine, pairs: list[tuple[str, str]]) -> None:
    """Add (role, content) pairs to engine._messages."""
    for role, content in pairs:
        engine._messages.append(Message(role=role, content=content))


class TestSearchRegistered:
    """Verify /search appears in command listing."""

    def test_search_in_slash_commands(self):
        assert "/search" in SLASH_COMMANDS

    def test_help_lists_search(self, capsys):
        engine = _make_engine()
        _handle_slash("/help", engine, "m", _make_deps())
        captured = capsys.readouterr()
        assert "/search" in captured.out


class TestSearchNoQuery:
    """Calling /search without a query shows usage."""

    def test_no_arg_shows_usage(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash("/search", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "Usage" in captured.out


class TestSearchNoMatches:
    """Searching for something absent reports zero matches."""

    def test_no_matches(self, capsys):
        engine = _make_engine()
        _populate(engine, [
            ("user", "hello world"),
            ("assistant", "hi there"),
        ])
        keep, _ = _handle_slash("/search nonexistent", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "No matches" in captured.out


class TestSearchCaseInsensitive:
    """Search must be case-insensitive."""

    def test_case_insensitive_match(self, capsys):
        engine = _make_engine()
        _populate(engine, [
            ("user", "Fix the BUG in parser"),
            ("assistant", "I found a bug in line 42"),
        ])
        keep, _ = _handle_slash("/search bug", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        # Both messages should match (BUG and bug)
        assert captured.out.count("[user]") == 1
        assert captured.out.count("[assistant]") == 1
        assert "2 matches" in captured.out


class TestSearchTurnNumbering:
    """Turn numbers increment per user message."""

    def test_turn_numbers(self, capsys):
        engine = _make_engine()
        _populate(engine, [
            ("user", "first question about alpha"),
            ("assistant", "alpha response"),
            ("user", "second question about alpha"),
            ("assistant", "alpha again"),
        ])
        keep, _ = _handle_slash("/search alpha", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        lines = [l for l in captured.out.strip().split("\n") if "[turn" in l]
        # 4 messages mention alpha
        assert len(lines) == 4
        assert "[turn 1]" in lines[0]
        assert "[turn 1]" in lines[1]
        assert "[turn 2]" in lines[2]
        assert "[turn 2]" in lines[3]


class TestSearchHighlight:
    """Matched text should be highlighted with ANSI bold yellow."""

    def test_highlight_ansi(self, capsys):
        engine = _make_engine()
        _populate(engine, [
            ("user", "deploy the service"),
        ])
        _handle_slash("/search deploy", engine, "m", _make_deps())
        captured = capsys.readouterr()
        # \033[1;33m = bold yellow, \033[0m = reset
        assert "\033[1;33m" in captured.out
        assert "deploy" in captured.out


class TestSearchSingleMatch:
    """Single match should say '1 match' (not '1 matches')."""

    def test_single_match_grammar(self, capsys):
        engine = _make_engine()
        _populate(engine, [
            ("user", "hello world"),
        ])
        _handle_slash("/search hello", engine, "m", _make_deps())
        captured = capsys.readouterr()
        assert "1 match)" in captured.out
        assert "matches" not in captured.out


class TestSearchEmptyMessages:
    """Searching with no messages reports no matches."""

    def test_empty_session(self, capsys):
        engine = _make_engine()
        keep, _ = _handle_slash("/search anything", engine, "m", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "No matches" in captured.out


class TestSearchMultiWordQuery:
    """Query can contain multiple words (the full arg after /search)."""

    def test_multi_word(self, capsys):
        engine = _make_engine()
        _populate(engine, [
            ("user", "please fix the broken parser"),
            ("assistant", "the broken parser is now fixed"),
        ])
        _handle_slash("/search broken parser", engine, "m", _make_deps())
        captured = capsys.readouterr()
        assert "[user]" in captured.out
        assert "[assistant]" in captured.out
        assert "2 matches" in captured.out
