"""Tests for ADR-058 compact features:

1. Compact boundary marker in SummarizeCompactor
2. Post-compact file state rebuild (rebuild_post_compact_context)
3. Compact analytics (CompactStats)
4. Snip boundary marker verification
5. Engine wiring of analytics
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock

import pytest

from duh.kernel.messages import Message
from duh.kernel.compact_analytics import CompactStats
from duh.kernel.post_compact import rebuild_post_compact_context
from duh.kernel.file_tracker import FileTracker
from duh.adapters.compact.summarize import SummarizeCompactor
from duh.adapters.compact.snip import SnipCompactor, _SNIP_MARKER_PREFIX


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(role: str = "user", content: str = "hello", **kw) -> Message:
    return Message(role=role, content=content, id=kw.get("id", "m"), timestamp="t")


def _make_conversation(num_rounds: int) -> list[Message]:
    """Build: [user, assistant, user, assistant, ...] for N rounds."""
    messages: list[Message] = [
        Message(role="user", content="Please implement the feature."),
    ]
    for i in range(num_rounds):
        messages.append(
            Message(role="assistant", content=f"Working on step {i}... " * 50)
        )
        messages.append(
            Message(role="user", content=f"Tool result for step {i}: " + "x" * 200)
        )
    return messages


# ===========================================================================
# 1. Compact Boundary Marker — SummarizeCompactor
# ===========================================================================


class TestSummarizeBoundaryMarker:
    """SummarizeCompactor inserts a boundary marker when compaction drops messages."""

    @pytest.mark.asyncio
    async def test_boundary_marker_inserted(self):
        """When messages are dropped, a boundary marker precedes the summary."""
        sc = SummarizeCompactor(bytes_per_token=1, min_keep=1)
        messages = [
            _msg(content="a" * 500),
            _msg(role="assistant", content="b" * 500),
            _msg(content="c" * 500),
        ]
        result = await sc.compact(messages, token_limit=700)

        # Find boundary marker
        boundaries = [
            m for m in result
            if isinstance(m, Message)
            and m.metadata.get("subtype") == "compact_boundary"
        ]
        assert len(boundaries) == 1

    @pytest.mark.asyncio
    async def test_boundary_marker_content(self):
        """Boundary marker has the expected content text."""
        sc = SummarizeCompactor(bytes_per_token=1, min_keep=1)
        messages = [
            _msg(content="a" * 500),
            _msg(role="assistant", content="b" * 500),
            _msg(content="c" * 500),
        ]
        result = await sc.compact(messages, token_limit=700)

        boundaries = [
            m for m in result
            if isinstance(m, Message)
            and m.metadata.get("subtype") == "compact_boundary"
        ]
        assert "Conversation compacted" in boundaries[0].content

    @pytest.mark.asyncio
    async def test_boundary_marker_metadata(self):
        """Boundary marker metadata contains pre_compact_count and tokens_freed."""
        sc = SummarizeCompactor(bytes_per_token=1, min_keep=1)
        messages = [
            _msg(content="a" * 500),
            _msg(role="assistant", content="b" * 500),
            _msg(content="c" * 200),
        ]
        result = await sc.compact(messages, token_limit=400)

        boundaries = [
            m for m in result
            if isinstance(m, Message)
            and m.metadata.get("subtype") == "compact_boundary"
        ]
        assert len(boundaries) == 1
        meta = boundaries[0].metadata
        assert "pre_compact_count" in meta
        assert meta["pre_compact_count"] == len(messages)
        assert "tokens_freed" in meta
        assert meta["tokens_freed"] > 0

    @pytest.mark.asyncio
    async def test_boundary_marker_precedes_summary(self):
        """The boundary marker appears before the summary message in output."""
        sc = SummarizeCompactor(bytes_per_token=1, min_keep=1)
        messages = [
            _msg(content="a" * 500),
            _msg(role="assistant", content="b" * 500),
            _msg(content="c" * 200),
        ]
        result = await sc.compact(messages, token_limit=400)

        # Find indices
        boundary_idx = None
        summary_idx = None
        for i, m in enumerate(result):
            if isinstance(m, Message):
                if m.metadata.get("subtype") == "compact_boundary":
                    boundary_idx = i
                if isinstance(m.content, str) and "Previous conversation summary" in m.content:
                    summary_idx = i

        assert boundary_idx is not None
        assert summary_idx is not None
        assert boundary_idx < summary_idx

    @pytest.mark.asyncio
    async def test_no_boundary_when_nothing_dropped(self):
        """No boundary marker when all messages fit."""
        sc = SummarizeCompactor(bytes_per_token=1, min_keep=2)
        messages = [
            _msg(content="short"),
            _msg(role="assistant", content="also short"),
        ]
        result = await sc.compact(messages, token_limit=100_000)

        boundaries = [
            m for m in result
            if isinstance(m, Message)
            and m.metadata.get("subtype") == "compact_boundary"
        ]
        assert len(boundaries) == 0

    @pytest.mark.asyncio
    async def test_boundary_role_is_user(self):
        """The boundary marker has role='user' to maintain alternation context."""
        sc = SummarizeCompactor(bytes_per_token=1, min_keep=1)
        messages = [
            _msg(content="a" * 500),
            _msg(role="assistant", content="b" * 500),
            _msg(content="c" * 200),
        ]
        result = await sc.compact(messages, token_limit=400)

        boundaries = [
            m for m in result
            if isinstance(m, Message)
            and m.metadata.get("subtype") == "compact_boundary"
        ]
        assert boundaries[0].role == "user"


# ===========================================================================
# 2. Post-Compact File State Rebuild
# ===========================================================================


class TestRebuildPostCompactContext:
    """rebuild_post_compact_context re-injects file content after compaction."""

    @pytest.mark.asyncio
    async def test_no_tracker_returns_as_is(self):
        """When file_tracker is None, messages are returned unchanged."""
        messages = [_msg(), _msg(role="assistant")]
        result = await rebuild_post_compact_context(messages, file_tracker=None)
        assert len(result) == len(messages)

    @pytest.mark.asyncio
    async def test_empty_tracker_returns_as_is(self):
        """When tracker has no ops, messages are returned unchanged."""
        tracker = FileTracker()
        messages = [_msg(), _msg(role="assistant")]
        result = await rebuild_post_compact_context(messages, file_tracker=tracker)
        assert len(result) == len(messages)

    @pytest.mark.asyncio
    async def test_existing_files_injected(self):
        """Files that exist on disk are injected as system messages."""
        tracker = FileTracker()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("def hello():\n    return 'world'\n")
            f.flush()
            path = f.name

        try:
            tracker.track(path, "read")
            messages = [_msg()]
            result = await rebuild_post_compact_context(
                messages, file_tracker=tracker
            )
            # Should append a system message with file content
            assert len(result) == 2
            restore_msg = result[-1]
            assert isinstance(restore_msg, Message)
            assert restore_msg.role == "system"
            assert path in restore_msg.content
            assert "def hello" in restore_msg.content
            assert restore_msg.metadata.get("subtype") == "post_compact_file_restore"
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_nonexistent_files_skipped(self):
        """Files that don't exist are silently skipped."""
        tracker = FileTracker()
        tracker.track("/nonexistent/path/foo.py", "read")
        messages = [_msg()]
        result = await rebuild_post_compact_context(
            messages, file_tracker=tracker
        )
        assert len(result) == len(messages)

    @pytest.mark.asyncio
    async def test_max_files_respected(self):
        """At most max_files files are injected."""
        tracker = FileTracker()
        paths = []
        for i in range(5):
            f = tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False
            )
            f.write(f"# file {i}\n")
            f.close()
            paths.append(f.name)
            tracker.track(f.name, "read")

        try:
            messages = [_msg()]
            result = await rebuild_post_compact_context(
                messages, file_tracker=tracker, max_files=2,
            )
            # Original message + at most 2 file messages
            restore_msgs = [
                m for m in result
                if isinstance(m, Message)
                and m.metadata.get("subtype") == "post_compact_file_restore"
            ]
            assert len(restore_msgs) == 2
        finally:
            for p in paths:
                os.unlink(p)

    @pytest.mark.asyncio
    async def test_deduplicates_paths(self):
        """The same file tracked multiple times is only injected once."""
        tracker = FileTracker()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("content\n")
            f.flush()
            path = f.name

        try:
            tracker.track(path, "read")
            tracker.track(path, "edit")
            tracker.track(path, "read")
            messages = [_msg()]
            result = await rebuild_post_compact_context(
                messages, file_tracker=tracker
            )
            restore_msgs = [
                m for m in result
                if isinstance(m, Message)
                and m.metadata.get("subtype") == "post_compact_file_restore"
            ]
            assert len(restore_msgs) == 1
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_truncates_large_files(self):
        """Files exceeding the token budget are truncated."""
        tracker = FileTracker()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            # Write 100K chars -- way over default budget
            f.write("x" * 100_000)
            f.flush()
            path = f.name

        try:
            tracker.track(path, "read")
            messages = [_msg()]
            result = await rebuild_post_compact_context(
                messages, file_tracker=tracker, max_tokens_per_file=100,
            )
            restore_msg = result[-1]
            # 100 tokens * 4 chars = 400 max chars
            assert len(restore_msg.content) < 1000
            assert "..." in restore_msg.content
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_does_not_mutate_input(self):
        """Input message list is not mutated."""
        tracker = FileTracker()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write("data\n")
            f.flush()
            path = f.name

        try:
            tracker.track(path, "read")
            messages = [_msg()]
            original_len = len(messages)
            await rebuild_post_compact_context(messages, file_tracker=tracker)
            assert len(messages) == original_len
        finally:
            os.unlink(path)


# ===========================================================================
# 3. Compact Analytics
# ===========================================================================


class TestCompactStats:
    """CompactStats tracks and reports compaction events."""

    def test_initial_state(self):
        stats = CompactStats()
        assert stats.total_compactions == 0
        assert stats.total_tokens_freed == 0
        assert stats.snip_count == 0
        assert stats.summary_count == 0
        assert stats.microcompact_count == 0
        assert stats.dedup_count == 0

    def test_record_snip(self):
        stats = CompactStats()
        stats.record("snip", tokens_freed=4200)
        assert stats.total_compactions == 1
        assert stats.total_tokens_freed == 4200
        assert stats.snip_count == 1
        assert stats.summary_count == 0

    def test_record_summary(self):
        stats = CompactStats()
        stats.record("summary", tokens_freed=18000)
        assert stats.summary_count == 1
        assert stats.total_tokens_freed == 18000

    def test_record_summarize_alias(self):
        stats = CompactStats()
        stats.record("summarize", tokens_freed=5000)
        assert stats.summary_count == 1

    def test_record_microcompact(self):
        stats = CompactStats()
        stats.record("microcompact", tokens_freed=500)
        assert stats.microcompact_count == 1

    def test_record_micro_alias(self):
        stats = CompactStats()
        stats.record("micro", tokens_freed=300)
        assert stats.microcompact_count == 1

    def test_record_dedup(self):
        stats = CompactStats()
        stats.record("dedup", tokens_freed=1000)
        assert stats.dedup_count == 1

    def test_record_unknown_tier(self):
        """Unknown tiers increment total but not any specific counter."""
        stats = CompactStats()
        stats.record("auto", tokens_freed=5000)
        assert stats.total_compactions == 1
        assert stats.total_tokens_freed == 5000
        assert stats.snip_count == 0
        assert stats.summary_count == 0

    def test_multiple_records(self):
        stats = CompactStats()
        stats.record("snip", tokens_freed=1000)
        stats.record("summary", tokens_freed=5000)
        stats.record("snip", tokens_freed=2000)
        assert stats.total_compactions == 3
        assert stats.total_tokens_freed == 8000
        assert stats.snip_count == 2
        assert stats.summary_count == 1

    def test_summary_no_compactions(self):
        stats = CompactStats()
        text = stats.summary()
        assert "No compactions" in text

    def test_summary_with_data(self):
        stats = CompactStats()
        stats.record("snip", tokens_freed=4200)
        stats.record("summary", tokens_freed=18000)
        text = stats.summary()
        assert "Compaction statistics" in text
        assert "Total compactions" in text
        assert "22,200" in text  # total freed
        assert "Snip" in text
        assert "Summary" in text

    def test_history_tracks_events(self):
        stats = CompactStats()
        stats.record("snip", tokens_freed=100)
        stats.record("summary", tokens_freed=200)
        assert len(stats._history) == 2
        assert stats._history[0] == ("snip", 100)
        assert stats._history[1] == ("summary", 200)

    def test_summary_includes_history(self):
        stats = CompactStats()
        stats.record("snip", tokens_freed=100)
        text = stats.summary()
        assert "History" in text
        assert "snip" in text


# ===========================================================================
# 4. Snip Boundary Marker Verification
# ===========================================================================


class TestSnipBoundaryMarkerWorking:
    """Verify the snip boundary marker is functional (existing implementation)."""

    def test_snip_marker_present_after_snip(self):
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)
        result, freed = sc.snip(messages)

        assert freed > 0
        first = result[0]
        text = first.content if isinstance(first.content, str) else first.text
        assert _SNIP_MARKER_PREFIX in text

    def test_snip_marker_metadata_subtype(self):
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)
        result, freed = sc.snip(messages)

        assert result[0].metadata.get("subtype") == "snip_boundary"
        assert result[0].metadata.get("snipped_count") > 0

    def test_snip_marker_tokens_freed_in_text(self):
        """The marker text includes the tokens freed count."""
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)
        result, freed = sc.snip(messages)

        text = result[0].content if isinstance(result[0].content, str) else result[0].text
        assert "tokens freed" in text

    def test_snip_marker_preserves_original_content(self):
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)
        result, freed = sc.snip(messages)

        text = result[0].content if isinstance(result[0].content, str) else result[0].text
        assert "implement the feature" in text


# ===========================================================================
# 5. Engine Wiring — CompactStats accessible
# ===========================================================================


class TestEngineCompactStatsWiring:
    """Engine exposes compact_stats and it starts empty."""

    def test_engine_has_compact_stats(self):
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.deps import Deps

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        assert hasattr(engine, "compact_stats")
        assert isinstance(engine.compact_stats, CompactStats)

    def test_engine_compact_stats_initially_empty(self):
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.deps import Deps

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        assert engine.compact_stats.total_compactions == 0
        assert engine.compact_stats.total_tokens_freed == 0

    def test_engine_compact_stats_record_works(self):
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.deps import Deps

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        engine.compact_stats.record("snip", tokens_freed=5000)
        assert engine.compact_stats.total_compactions == 1
        assert engine.compact_stats.snip_count == 1


# ===========================================================================
# 6. REPL /compact-stats command
# ===========================================================================


class TestCompactStatsSlashCommand:
    """The /compact-stats command is registered and produces output."""

    def test_slash_command_registered(self):
        from duh.cli.repl import SLASH_COMMANDS
        assert "/compact-stats" in SLASH_COMMANDS

    def test_context_breakdown_includes_stats(self):
        """When compactions have occurred, /context output includes stats."""
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.deps import Deps
        from duh.cli.repl import context_breakdown

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        config = EngineConfig(model="claude-sonnet-4-6")
        engine = Engine(deps=deps, config=config)

        # No compactions — should not appear
        text = context_breakdown(engine, "claude-sonnet-4-6")
        assert "Compaction statistics" not in text

        # Record a compaction
        engine.compact_stats.record("snip", tokens_freed=5000)
        text = context_breakdown(engine, "claude-sonnet-4-6")
        assert "Compaction statistics" in text
        assert "5,000" in text
