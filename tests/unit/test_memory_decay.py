"""Tests for memory decay and garbage collection (ADR-069 P2).

Covers:
- score_facts with various age/access patterns
- gc_memories removing stale and excess facts
- Access tracking in FileMemoryStore.recall_facts
- /memory gc slash command
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.kernel.memory_decay import (
    _days_since,
    _parse_timestamp,
    gc_memories,
    score_facts,
)


# ---------------------------------------------------------------------------
# _parse_timestamp
# ---------------------------------------------------------------------------


class TestParseTimestamp:
    def test_valid_iso(self):
        dt = _parse_timestamp("2025-06-01T12:00:00+00:00")
        assert dt is not None
        assert dt.year == 2025

    def test_empty_string(self):
        assert _parse_timestamp("") is None

    def test_invalid_string(self):
        assert _parse_timestamp("not-a-date") is None

    def test_none(self):
        assert _parse_timestamp(None) is None


# ---------------------------------------------------------------------------
# _days_since
# ---------------------------------------------------------------------------


class TestDaysSince:
    def test_none_returns_sentinel(self):
        now = datetime.now(timezone.utc)
        assert _days_since(None, now) == 9999.0

    def test_same_time_returns_zero(self):
        now = datetime.now(timezone.utc)
        assert _days_since(now, now) == 0.0

    def test_one_day_ago(self):
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        result = _days_since(yesterday, now)
        assert 0.9 < result < 1.1

    def test_future_clamped_to_zero(self):
        now = datetime.now(timezone.utc)
        tomorrow = now + timedelta(days=1)
        assert _days_since(tomorrow, now) == 0.0


# ---------------------------------------------------------------------------
# score_facts
# ---------------------------------------------------------------------------

NOW = datetime(2025, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_fact(
    key: str,
    *,
    days_old: int = 0,
    last_accessed_days_ago: int | None = None,
    access_count: int = 0,
) -> dict:
    created = NOW - timedelta(days=days_old)
    fact: dict = {
        "key": key,
        "value": f"value for {key}",
        "timestamp": created.isoformat(),
        "access_count": access_count,
    }
    if last_accessed_days_ago is not None:
        fact["last_accessed"] = (NOW - timedelta(days=last_accessed_days_ago)).isoformat()
    return fact


class TestScoreFacts:
    def test_empty_list(self):
        assert score_facts([], now=NOW) == []

    def test_recent_scores_higher_than_old(self):
        recent = _make_fact("recent", days_old=1, last_accessed_days_ago=0)
        old = _make_fact("old", days_old=300, last_accessed_days_ago=300)
        scored = score_facts([recent, old], now=NOW)
        # scored is sorted by score descending
        assert scored[0][0]["key"] == "recent"
        assert scored[0][1] > scored[1][1]

    def test_high_access_count_boosts_score(self):
        popular = _make_fact("popular", days_old=100, last_accessed_days_ago=50, access_count=20)
        unpopular = _make_fact("unpopular", days_old=100, last_accessed_days_ago=50, access_count=0)
        scored = score_facts([popular, unpopular], now=NOW)
        assert scored[0][0]["key"] == "popular"
        assert scored[0][1] > scored[1][1]

    def test_no_last_accessed_falls_back_to_creation(self):
        fact = _make_fact("no-access", days_old=10)
        # No last_accessed field set
        scored = score_facts([fact], now=NOW)
        assert len(scored) == 1
        score = scored[0][1]
        assert score > 0

    def test_very_old_scores_near_zero(self):
        ancient = _make_fact("ancient", days_old=500, last_accessed_days_ago=500, access_count=0)
        scored = score_facts([ancient], now=NOW)
        assert scored[0][1] < 0.1

    def test_sorted_descending(self):
        facts = [
            _make_fact("low", days_old=200, last_accessed_days_ago=200),
            _make_fact("mid", days_old=50, last_accessed_days_ago=50, access_count=3),
            _make_fact("high", days_old=1, last_accessed_days_ago=0, access_count=10),
        ]
        scored = score_facts(facts, now=NOW)
        scores = [s for _, s in scored]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# gc_memories
# ---------------------------------------------------------------------------


class FakeStore:
    """Minimal fake store for testing gc_memories."""

    def __init__(self, facts: list[dict]):
        self._facts = list(facts)

    def list_facts(self) -> list[dict]:
        return list(self._facts)

    def delete_fact(self, key: str) -> bool:
        before = len(self._facts)
        self._facts = [f for f in self._facts if f.get("key") != key]
        return len(self._facts) < before


class TestGcMemories:
    def test_empty_store(self):
        store = FakeStore([])
        assert gc_memories(store, now=NOW) == 0

    def test_removes_low_score_facts(self):
        facts = [
            _make_fact("ancient", days_old=500, last_accessed_days_ago=500, access_count=0),
            _make_fact("recent", days_old=1, last_accessed_days_ago=0, access_count=5),
        ]
        store = FakeStore(facts)
        removed = gc_memories(store, min_score=0.1, now=NOW)
        assert removed == 1
        remaining = store.list_facts()
        assert len(remaining) == 1
        assert remaining[0]["key"] == "recent"

    def test_respects_max_facts_cap(self):
        # All relatively fresh, but cap is 2
        facts = [
            _make_fact(f"fact-{i}", days_old=i, last_accessed_days_ago=i, access_count=1)
            for i in range(5)
        ]
        store = FakeStore(facts)
        removed = gc_memories(store, max_facts=2, min_score=0.0, now=NOW)
        assert removed == 3
        remaining = store.list_facts()
        assert len(remaining) == 2

    def test_no_removal_when_all_healthy(self):
        facts = [
            _make_fact("healthy", days_old=1, last_accessed_days_ago=0, access_count=5),
        ]
        store = FakeStore(facts)
        removed = gc_memories(store, max_facts=200, min_score=0.1, now=NOW)
        assert removed == 0

    def test_combined_score_and_cap_pruning(self):
        facts = [
            _make_fact("stale1", days_old=400, last_accessed_days_ago=400),
            _make_fact("stale2", days_old=350, last_accessed_days_ago=350),
            _make_fact("ok1", days_old=30, last_accessed_days_ago=10, access_count=2),
            _make_fact("ok2", days_old=20, last_accessed_days_ago=5, access_count=3),
            _make_fact("fresh", days_old=1, last_accessed_days_ago=0, access_count=8),
        ]
        store = FakeStore(facts)
        # Stale ones will be below min_score; then cap at 2 means ok1 drops too
        removed = gc_memories(store, max_facts=2, min_score=0.1, now=NOW)
        remaining = store.list_facts()
        assert len(remaining) == 2
        remaining_keys = {f["key"] for f in remaining}
        assert "fresh" in remaining_keys


# ---------------------------------------------------------------------------
# Access tracking in FileMemoryStore
# ---------------------------------------------------------------------------


class TestAccessTracking:
    def test_recall_updates_access_fields(self, tmp_path):
        from duh.adapters.memory_store import FileMemoryStore

        # Create a store with a known facts dir
        store = FileMemoryStore.__new__(FileMemoryStore)
        store._cwd = str(tmp_path)
        store._memory_dir = tmp_path / "memory"
        store._facts_dir = tmp_path / "facts"
        store._facts_dir.mkdir(parents=True)

        # Write a fact
        store.store_fact("test-key", "test value", ["tag1"])

        # Recall it
        results = store.recall_facts("test")
        assert len(results) == 1

        # Read the fact back and check access tracking fields
        all_facts = store.list_facts()
        fact = all_facts[0]
        assert "last_accessed" in fact
        assert fact["access_count"] == 1

    def test_recall_increments_access_count(self, tmp_path):
        from duh.adapters.memory_store import FileMemoryStore

        store = FileMemoryStore.__new__(FileMemoryStore)
        store._cwd = str(tmp_path)
        store._memory_dir = tmp_path / "memory"
        store._facts_dir = tmp_path / "facts"
        store._facts_dir.mkdir(parents=True)

        store.store_fact("k", "findme value")

        # Multiple recalls
        store.recall_facts("findme")
        store.recall_facts("findme")
        store.recall_facts("findme")

        fact = store.list_facts()[0]
        assert fact["access_count"] == 3

    def test_recall_no_match_no_tracking(self, tmp_path):
        from duh.adapters.memory_store import FileMemoryStore

        store = FileMemoryStore.__new__(FileMemoryStore)
        store._cwd = str(tmp_path)
        store._memory_dir = tmp_path / "memory"
        store._facts_dir = tmp_path / "facts"
        store._facts_dir.mkdir(parents=True)

        store.store_fact("k", "something")

        # Recall with non-matching query
        results = store.recall_facts("zzzznotfound")
        assert results == []

        # access_count should still be 0
        fact = store.list_facts()[0]
        assert fact.get("access_count", 0) == 0


# ---------------------------------------------------------------------------
# /memory gc slash command (REPL)
# ---------------------------------------------------------------------------


class TestMemoryGcSlashCommand:
    def test_gc_command_runs(self, capsys):
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from unittest.mock import AsyncMock

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))

        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            mock_store = MockStore.return_value
            mock_store.list_facts.return_value = [
                {"key": "k1", "value": "v1", "timestamp": "2025-01-01T00:00:00+00:00"},
            ]
            mock_store.delete_fact.return_value = False

            with patch("duh.kernel.memory_decay.gc_memories", return_value=0) as mock_gc:
                keep, model = _handle_slash("/memory gc", engine, "test", deps)

            assert keep is True
            captured = capsys.readouterr()
            assert "Memory GC" in captured.out
            assert "removed" in captured.out

    def test_gc_command_with_max_facts(self, capsys):
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from unittest.mock import AsyncMock

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))

        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            mock_store = MockStore.return_value
            mock_store.list_facts.return_value = []

            with patch("duh.kernel.memory_decay.gc_memories", return_value=3) as mock_gc:
                keep, _ = _handle_slash("/memory gc 50", engine, "test", deps)

            mock_gc.assert_called_once()
            # Check max_facts was passed
            call_kwargs = mock_gc.call_args
            assert call_kwargs[1].get("max_facts") == 50 or (
                len(call_kwargs[0]) > 1 and call_kwargs[0][1] == 50
            ) or call_kwargs.kwargs.get("max_facts") == 50

    def test_gc_command_invalid_arg(self, capsys):
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from unittest.mock import AsyncMock

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))

        with patch("duh.adapters.memory_store.FileMemoryStore"):
            keep, _ = _handle_slash("/memory gc notanumber", engine, "test", deps)

        assert keep is True
        captured = capsys.readouterr()
        assert "Usage" in captured.out
