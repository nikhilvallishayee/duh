"""Tests for persistent cross-session memory.

Covers:
- FileMemoryStore.store_fact / recall_facts / list_facts / delete_fact
- facts.jsonl persistence (read/write/prune)
- Project hash stability
- MemoryStoreTool and MemoryRecallTool (tool protocol + call)
- build_memory_prompt with persistent facts
- Edge cases: empty query, missing file, duplicate keys, malformed JSONL
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from duh.adapters.memory_store import (
    FACTS_FILENAME,
    FACTS_LINE_CAP,
    FileMemoryStore,
    _project_hash,
)
from duh.kernel.memory import build_memory_prompt
from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.memory_tool import MemoryRecallTool, MemoryStoreTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path, cwd: str = "/Users/alice/Code/proj") -> FileMemoryStore:
    """Create a FileMemoryStore with both dirs redirected to tmp_path."""
    store = FileMemoryStore(cwd=cwd)
    store._memory_dir = tmp_path / "memory"
    store._facts_dir = tmp_path / "facts"
    return store


def ctx(cwd: str = "/tmp/test") -> ToolContext:
    return ToolContext(cwd=cwd)


# ---------------------------------------------------------------------------
# _project_hash
# ---------------------------------------------------------------------------

class TestProjectHash:
    def test_deterministic(self):
        h1 = _project_hash("/Users/alice/Code/proj")
        h2 = _project_hash("/Users/alice/Code/proj")
        assert h1 == h2

    def test_different_paths_different_hashes(self):
        h1 = _project_hash("/Users/alice/Code/proj-a")
        h2 = _project_hash("/Users/alice/Code/proj-b")
        assert h1 != h2

    def test_returns_12_hex_chars(self):
        h = _project_hash("/tmp/test")
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)

    def test_uses_git_root_when_available(self):
        with patch("duh.config._find_git_root", return_value=Path("/git/root")):
            h1 = _project_hash("/git/root/subdir/deep")
            h2 = _project_hash("/git/root/other")
            # Both should hash to the same value since git root is the same
            assert h1 == h2

    def test_uses_cwd_when_no_git(self):
        with patch("duh.config._find_git_root", return_value=None):
            h1 = _project_hash("/no/git/here")
            h2 = _project_hash("/no/git/elsewhere")
            assert h1 != h2


# ---------------------------------------------------------------------------
# FileMemoryStore -- store_fact
# ---------------------------------------------------------------------------

class TestStoreFact:
    def test_store_and_retrieve(self, tmp_path: Path):
        store = _make_store(tmp_path)
        entry = store.store_fact("auth-pattern", "Uses JWT", ["auth", "security"])
        assert entry["key"] == "auth-pattern"
        assert entry["value"] == "Uses JWT"
        assert entry["tags"] == ["auth", "security"]
        assert "timestamp" in entry

    def test_creates_facts_dir(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert not store.get_facts_dir().exists()
        store.store_fact("test", "value")
        assert store.get_facts_dir().exists()

    def test_persists_to_jsonl(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("key1", "value1", ["tag1"])
        store.store_fact("key2", "value2")

        facts_file = store.get_facts_dir() / FACTS_FILENAME
        assert facts_file.exists()
        lines = facts_file.read_text().strip().splitlines()
        assert len(lines) == 2
        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["key"] == "key1"
        assert parsed[1]["key"] == "key2"

    def test_deduplicates_by_key(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("k", "old value")
        store.store_fact("k", "new value")

        facts = store.list_facts()
        assert len(facts) == 1
        assert facts[0]["value"] == "new value"

    def test_default_tags_empty_list(self, tmp_path: Path):
        store = _make_store(tmp_path)
        entry = store.store_fact("k", "v")
        assert entry["tags"] == []

    def test_prunes_oldest_over_cap(self, tmp_path: Path):
        store = _make_store(tmp_path)
        # Write FACTS_LINE_CAP + 10 entries
        for i in range(FACTS_LINE_CAP + 10):
            store.store_fact(f"key-{i}", f"value-{i}")

        facts = store.list_facts()
        assert len(facts) == FACTS_LINE_CAP
        # Oldest entries should be gone
        keys = {f["key"] for f in facts}
        assert "key-0" not in keys
        assert "key-9" not in keys
        # Newest should remain
        assert f"key-{FACTS_LINE_CAP + 9}" in keys


# ---------------------------------------------------------------------------
# FileMemoryStore -- recall_facts
# ---------------------------------------------------------------------------

class TestRecallFacts:
    def test_recall_by_key(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("auth-pattern", "JWT with refresh tokens")
        store.store_fact("db-schema", "PostgreSQL v14")

        results = store.recall_facts("auth")
        assert len(results) == 1
        assert results[0]["key"] == "auth-pattern"

    def test_recall_by_value(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("k", "Uses PostgreSQL database")

        results = store.recall_facts("PostgreSQL")
        assert len(results) == 1

    def test_recall_by_tag(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("k", "something", ["security", "auth"])

        results = store.recall_facts("security")
        assert len(results) == 1

    def test_recall_case_insensitive(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("Auth-Pattern", "JWT Auth")

        results = store.recall_facts("auth")
        assert len(results) == 1

    def test_recall_empty_returns_empty(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("k", "v")
        results = store.recall_facts("nonexistent")
        assert results == []

    def test_recall_no_facts_file(self, tmp_path: Path):
        store = _make_store(tmp_path)
        results = store.recall_facts("anything")
        assert results == []

    def test_recall_respects_limit(self, tmp_path: Path):
        store = _make_store(tmp_path)
        for i in range(10):
            store.store_fact(f"match-{i}", "common keyword")

        results = store.recall_facts("common", limit=3)
        assert len(results) == 3

    def test_recall_newest_first(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("old", "pattern X")
        store.store_fact("new", "pattern X")

        results = store.recall_facts("pattern")
        assert results[0]["key"] == "new"
        assert results[1]["key"] == "old"


# ---------------------------------------------------------------------------
# FileMemoryStore -- delete_fact
# ---------------------------------------------------------------------------

class TestDeleteFact:
    def test_delete_existing(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("k", "v")
        assert store.delete_fact("k") is True
        assert store.list_facts() == []

    def test_delete_nonexistent(self, tmp_path: Path):
        store = _make_store(tmp_path)
        assert store.delete_fact("ghost") is False

    def test_delete_preserves_others(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("a", "1")
        store.store_fact("b", "2")
        store.store_fact("c", "3")
        store.delete_fact("b")
        facts = store.list_facts()
        assert len(facts) == 2
        keys = [f["key"] for f in facts]
        assert "b" not in keys
        assert "a" in keys
        assert "c" in keys


# ---------------------------------------------------------------------------
# FileMemoryStore -- malformed JSONL handling
# ---------------------------------------------------------------------------

class TestMalformedFacts:
    def test_skips_malformed_lines(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store._ensure_facts_dir()
        facts_file = store.get_facts_dir() / FACTS_FILENAME
        facts_file.write_text(
            '{"key":"good","value":"ok","timestamp":"t","tags":[]}\n'
            'NOT JSON\n'
            '{"key":"also-good","value":"fine","timestamp":"t","tags":[]}\n'
        )
        facts = store.list_facts()
        assert len(facts) == 2
        assert facts[0]["key"] == "good"
        assert facts[1]["key"] == "also-good"

    def test_empty_file_returns_empty(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store._ensure_facts_dir()
        facts_file = store.get_facts_dir() / FACTS_FILENAME
        facts_file.write_text("")
        assert store.list_facts() == []


# ---------------------------------------------------------------------------
# MemoryStoreTool
# ---------------------------------------------------------------------------

class TestMemoryStoreTool:
    tool = MemoryStoreTool()

    def test_satisfies_protocol(self):
        assert isinstance(self.tool, Tool)

    def test_has_required_attrs(self):
        assert self.tool.name == "MemoryStore"
        assert self.tool.input_schema["type"] == "object"
        assert "key" in self.tool.input_schema["properties"]
        assert "value" in self.tool.input_schema["properties"]

    async def test_store_fact(self, tmp_path: Path):
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            instance = MockStore.return_value
            instance.store_fact.return_value = {
                "key": "k", "value": "v", "timestamp": "t", "tags": [],
            }
            result = await self.tool.call(
                {"key": "k", "value": "v"}, ctx(cwd=str(tmp_path))
            )
            assert result.is_error is False
            assert "Stored fact" in result.output
            instance.store_fact.assert_called_once_with(key="k", value="v", tags=[])

    async def test_store_with_tags(self, tmp_path: Path):
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            instance = MockStore.return_value
            instance.store_fact.return_value = {
                "key": "k", "value": "v", "timestamp": "t", "tags": ["a", "b"],
            }
            result = await self.tool.call(
                {"key": "k", "value": "v", "tags": ["a", "b"]}, ctx(cwd=str(tmp_path))
            )
            assert result.is_error is False
            instance.store_fact.assert_called_once_with(key="k", value="v", tags=["a", "b"])

    async def test_empty_key_errors(self):
        result = await self.tool.call({"key": "", "value": "v"}, ctx())
        assert result.is_error is True
        assert "required" in result.output.lower()

    async def test_empty_value_errors(self):
        result = await self.tool.call({"key": "k", "value": ""}, ctx())
        assert result.is_error is True
        assert "required" in result.output.lower()

    def test_is_not_read_only(self):
        assert self.tool.is_read_only is False

    def test_is_not_destructive(self):
        assert self.tool.is_destructive is False


# ---------------------------------------------------------------------------
# MemoryRecallTool
# ---------------------------------------------------------------------------

class TestMemoryRecallTool:
    tool = MemoryRecallTool()

    def test_satisfies_protocol(self):
        assert isinstance(self.tool, Tool)

    def test_has_required_attrs(self):
        assert self.tool.name == "MemoryRecall"
        assert self.tool.input_schema["type"] == "object"
        assert "query" in self.tool.input_schema["properties"]

    async def test_recall_with_results(self, tmp_path: Path):
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            instance = MockStore.return_value
            instance.recall_facts.return_value = [
                {"key": "auth", "value": "JWT", "tags": ["sec"], "timestamp": "2024-01-01"},
            ]
            result = await self.tool.call(
                {"query": "auth"}, ctx(cwd=str(tmp_path))
            )
            assert result.is_error is False
            assert "auth" in result.output
            assert "JWT" in result.output
            assert result.metadata["match_count"] == 1

    async def test_recall_no_results(self, tmp_path: Path):
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            instance = MockStore.return_value
            instance.recall_facts.return_value = []
            result = await self.tool.call(
                {"query": "nothing"}, ctx(cwd=str(tmp_path))
            )
            assert result.is_error is False
            assert "No saved facts" in result.output
            assert result.metadata["match_count"] == 0

    async def test_recall_empty_query_errors(self):
        result = await self.tool.call({"query": ""}, ctx())
        assert result.is_error is True
        assert "required" in result.output.lower()

    def test_is_read_only(self):
        assert self.tool.is_read_only is True

    def test_is_not_destructive(self):
        assert self.tool.is_destructive is False

    async def test_recall_with_limit(self, tmp_path: Path):
        with patch("duh.adapters.memory_store.FileMemoryStore") as MockStore:
            instance = MockStore.return_value
            instance.recall_facts.return_value = [
                {"key": "k1", "value": "v1", "tags": [], "timestamp": "t"},
            ]
            await self.tool.call(
                {"query": "search", "limit": 5}, ctx(cwd=str(tmp_path))
            )
            instance.recall_facts.assert_called_once_with(query="search", limit=5)


# ---------------------------------------------------------------------------
# build_memory_prompt with facts
# ---------------------------------------------------------------------------

class TestBuildMemoryPromptWithFacts:
    def test_includes_facts_section(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write_index("# Memory\n- entry\n")
        store.store_fact("auth", "JWT pattern", ["security"])

        prompt = build_memory_prompt(store)
        assert "<memory>" in prompt
        assert "</memory>" in prompt
        assert "<persistent-facts>" in prompt
        assert "</persistent-facts>" in prompt
        assert "auth: JWT pattern [security]" in prompt

    def test_facts_only_no_index(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.store_fact("k", "v")

        prompt = build_memory_prompt(store)
        assert "<memory>" in prompt
        assert "<persistent-facts>" in prompt
        assert "k: v" in prompt

    def test_no_facts_no_index(self, tmp_path: Path):
        store = _make_store(tmp_path)
        prompt = build_memory_prompt(store)
        assert prompt == ""

    def test_index_only_no_facts(self, tmp_path: Path):
        store = _make_store(tmp_path)
        store.write_index("# Memory\n- entry\n")

        prompt = build_memory_prompt(store)
        assert "<memory>" in prompt
        assert "# Memory" in prompt
        assert "<persistent-facts>" not in prompt

    def test_facts_capped_at_20_in_prompt(self, tmp_path: Path):
        store = _make_store(tmp_path)
        for i in range(30):
            store.store_fact(f"fact-{i}", f"value-{i}")

        prompt = build_memory_prompt(store)
        # Should only show the 20 most recent
        assert "fact-29" in prompt
        assert "fact-10" in prompt
        assert "fact-9" not in prompt


# ---------------------------------------------------------------------------
# Integration: full round-trip
# ---------------------------------------------------------------------------

class TestIntegrationRoundTrip:
    def test_store_recall_delete_cycle(self, tmp_path: Path):
        store = _make_store(tmp_path)

        # Store multiple facts
        store.store_fact("arch", "Hexagonal architecture", ["design"])
        store.store_fact("db", "PostgreSQL 14", ["infra"])
        store.store_fact("test-framework", "pytest with asyncio", ["test"])

        # Recall
        assert len(store.recall_facts("arch")) == 1
        assert len(store.recall_facts("infra")) == 1
        assert len(store.recall_facts("test")) == 1

        # Update a fact (same key)
        store.store_fact("db", "PostgreSQL 16 (upgraded)", ["infra", "migration"])
        db_facts = store.recall_facts("PostgreSQL")
        assert len(db_facts) == 1
        assert "16" in db_facts[0]["value"]

        # Delete
        store.delete_fact("arch")
        assert store.recall_facts("Hexagonal") == []

        # Remaining facts intact
        assert len(store.list_facts()) == 2

    def test_persistence_across_store_instances(self, tmp_path: Path):
        """Facts survive creating a new FileMemoryStore instance."""
        store1 = _make_store(tmp_path)
        store1.store_fact("persist-test", "I should survive")

        # Create a new store pointing at the same dirs
        store2 = _make_store(tmp_path)
        facts = store2.recall_facts("persist")
        assert len(facts) == 1
        assert facts[0]["value"] == "I should survive"
