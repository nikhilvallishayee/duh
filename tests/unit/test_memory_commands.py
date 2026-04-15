"""Tests for /memory slash command in REPL and TUI.

Covers ADR-069 P0:
- /memory listed in /help output
- /memory (bare) and /memory list show stored facts
- /memory search filters by keyword
- /memory show displays a specific fact
- /memory delete removes a fact
- Edge cases: empty store, unknown subcommand, missing args
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from duh.cli.repl import SLASH_COMMANDS, _handle_slash
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine() -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(model="test-model")
    return Engine(deps=deps, config=config)


def _make_deps() -> Deps:
    return Deps(call_model=AsyncMock(), run_tool=AsyncMock())


SAMPLE_FACTS = [
    {"key": "auth-pattern", "value": "JWT with refresh tokens", "tags": ["auth", "security"], "timestamp": "2025-01-01T00:00:00+00:00"},
    {"key": "db-schema", "value": "PostgreSQL 16", "tags": ["infra"], "timestamp": "2025-01-02T00:00:00+00:00"},
    {"key": "test-framework", "value": "pytest with asyncio", "tags": ["test"], "timestamp": "2025-01-03T00:00:00+00:00"},
]


# ---------------------------------------------------------------------------
# /memory in SLASH_COMMANDS dict
# ---------------------------------------------------------------------------


class TestMemoryInSlashCommands:
    def test_memory_in_slash_commands(self):
        assert "/memory" in SLASH_COMMANDS

    def test_help_lists_memory(self, capsys):
        engine = _make_engine()
        _handle_slash("/help", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        assert "/memory" in captured.out


# ---------------------------------------------------------------------------
# /memory list
# ---------------------------------------------------------------------------


class TestMemoryList:
    def test_list_shows_facts(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            MockStore.return_value.list_facts.return_value = SAMPLE_FACTS
            keep, model = _handle_slash("/memory list", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "auth-pattern" in captured.out
        assert "JWT with refresh tokens" in captured.out
        assert "db-schema" in captured.out
        assert "3 fact(s)" in captured.out

    def test_bare_memory_defaults_to_list(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            MockStore.return_value.list_facts.return_value = SAMPLE_FACTS
            keep, _ = _handle_slash("/memory", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "auth-pattern" in captured.out

    def test_list_empty_store(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            MockStore.return_value.list_facts.return_value = []
            _handle_slash("/memory list", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        assert "No memory facts" in captured.out


# ---------------------------------------------------------------------------
# /memory search
# ---------------------------------------------------------------------------


class TestMemorySearch:
    def test_search_finds_matches(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            MockStore.return_value.recall_facts.return_value = [SAMPLE_FACTS[0]]
            keep, _ = _handle_slash("/memory search auth", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "auth-pattern" in captured.out
        assert "1 match" in captured.out

    def test_search_no_matches(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            MockStore.return_value.recall_facts.return_value = []
            _handle_slash("/memory search nonexistent", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        assert "No facts matching" in captured.out

    def test_search_no_query_shows_usage(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            _handle_slash("/memory search", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        assert "Usage" in captured.out


# ---------------------------------------------------------------------------
# /memory show
# ---------------------------------------------------------------------------


class TestMemoryShow:
    def test_show_existing_key(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            MockStore.return_value.list_facts.return_value = SAMPLE_FACTS
            keep, _ = _handle_slash("/memory show auth-pattern", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "auth-pattern" in captured.out
        assert "JWT with refresh tokens" in captured.out

    def test_show_nonexistent_key(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            MockStore.return_value.list_facts.return_value = SAMPLE_FACTS
            _handle_slash("/memory show ghost-key", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        assert "No fact with key" in captured.out

    def test_show_no_key_shows_usage(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            _handle_slash("/memory show", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        assert "Usage" in captured.out


# ---------------------------------------------------------------------------
# /memory delete
# ---------------------------------------------------------------------------


class TestMemoryDelete:
    def test_delete_existing_key(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            MockStore.return_value.delete_fact.return_value = True
            keep, _ = _handle_slash("/memory delete auth-pattern", engine, "test-model", _make_deps())
        assert keep is True
        captured = capsys.readouterr()
        assert "Deleted fact" in captured.out

    def test_delete_nonexistent_key(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            MockStore.return_value.delete_fact.return_value = False
            _handle_slash("/memory delete ghost", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        assert "No fact with key" in captured.out

    def test_delete_no_key_shows_usage(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            _handle_slash("/memory delete", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        assert "Usage" in captured.out


# ---------------------------------------------------------------------------
# Unknown subcommand
# ---------------------------------------------------------------------------


class TestMemoryUnknownSubcommand:
    def test_unknown_subcommand_shows_usage(self, capsys):
        engine = _make_engine()
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            _handle_slash("/memory foobar", engine, "test-model", _make_deps())
        captured = capsys.readouterr()
        assert "Usage" in captured.out
        assert "/memory list" in captured.out
        assert "/memory search" in captured.out
        assert "/memory show" in captured.out
        assert "/memory delete" in captured.out
