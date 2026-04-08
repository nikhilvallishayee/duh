# tests/unit/test_post_compact_restore.py
"""Tests for post-compact context restoration."""

from __future__ import annotations

import pytest

from duh.adapters.simple_compactor import (
    POST_COMPACT_MAX_FILES,
    POST_COMPACT_TOKEN_BUDGET,
    restore_context,
)
from duh.kernel.file_tracker import FileTracker
from duh.kernel.messages import Message, TextBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(role: str = "user", content: str = "hello", **kw) -> Message:
    return Message(role=role, content=content, id=kw.get("id", "m"), timestamp="t")


def _sys(content: str = "system") -> Message:
    return Message(role="system", content=content, id="sys", timestamp="t0")


# ===========================================================================
# restore_context
# ===========================================================================

class TestRestoreContext:
    def test_no_tracker_no_change(self):
        """Without a file tracker, messages are returned unchanged."""
        msgs = [_msg(content="hello"), _msg(content="world")]
        result = restore_context(msgs, file_tracker=None, skill_context=None)
        assert len(result) == len(msgs)

    def test_recent_files_added(self):
        """Recently read files should be added as a system message."""
        tracker = FileTracker()
        tracker.track("/foo/bar.py", "read")
        tracker.track("/foo/baz.py", "read")

        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=tracker, skill_context=None)
        # Should have original message + restoration system message
        assert len(result) == len(msgs) + 1
        restore_msg = result[-1]
        assert restore_msg.role == "system"
        assert "/foo/bar.py" in restore_msg.content or "/foo/baz.py" in restore_msg.content

    def test_max_files_respected(self):
        """Only the most recent POST_COMPACT_MAX_FILES files are restored."""
        tracker = FileTracker()
        for i in range(POST_COMPACT_MAX_FILES + 5):
            tracker.track(f"/file_{i}.py", "read")

        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=tracker, skill_context=None)
        restore_msg = result[-1]
        # Should mention at most POST_COMPACT_MAX_FILES files
        file_mentions = [
            line for line in restore_msg.content.split("\n")
            if line.strip().startswith("/file_")
            or line.strip().startswith("- /file_")
        ]
        assert len(file_mentions) <= POST_COMPACT_MAX_FILES

    def test_skill_context_added(self):
        """Active skill context should be included in restoration."""
        skill_ctx = "Active skill: test-driven-development\nAlways write tests first."
        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=None, skill_context=skill_ctx)
        assert len(result) == len(msgs) + 1
        restore_msg = result[-1]
        assert "test-driven-development" in restore_msg.content

    def test_both_files_and_skills(self):
        """Both file tracker and skill context are combined."""
        tracker = FileTracker()
        tracker.track("/foo/bar.py", "read")
        skill_ctx = "Skill: debugging"
        msgs = [_msg(content="hello")]
        result = restore_context(
            msgs, file_tracker=tracker, skill_context=skill_ctx
        )
        assert len(result) == len(msgs) + 1
        restore_msg = result[-1]
        assert "/foo/bar.py" in restore_msg.content
        assert "debugging" in restore_msg.content

    def test_token_budget_respected(self):
        """Restoration content should not exceed POST_COMPACT_TOKEN_BUDGET."""
        tracker = FileTracker()
        # Track files with very long paths to test budget enforcement
        for i in range(10):
            tracker.track(f"/{'x' * 5000}/file_{i}.py", "read")

        msgs = [_msg(content="hello")]
        result = restore_context(
            msgs, file_tracker=tracker, skill_context=None,
            token_budget=100,  # very tight budget
        )
        if len(result) > len(msgs):
            restore_msg = result[-1]
            # Rough token estimate: len(content) / 4
            assert len(restore_msg.content) // 4 <= 200  # generous allowance

    def test_empty_tracker_no_restoration(self):
        """An empty file tracker should not add a restoration message."""
        tracker = FileTracker()
        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=tracker, skill_context=None)
        assert len(result) == len(msgs)

    def test_empty_skill_no_restoration(self):
        """Empty skill context should not add a restoration message."""
        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=None, skill_context="")
        assert len(result) == len(msgs)

    def test_deduplicates_files(self):
        """Same file read multiple times should appear only once."""
        tracker = FileTracker()
        tracker.track("/foo/bar.py", "read")
        tracker.track("/foo/bar.py", "read")
        tracker.track("/foo/bar.py", "edit")

        msgs = [_msg(content="hello")]
        result = restore_context(msgs, file_tracker=tracker, skill_context=None)
        restore_msg = result[-1]
        # /foo/bar.py should appear exactly once
        count = restore_msg.content.count("/foo/bar.py")
        assert count == 1

    def test_does_not_mutate_input(self):
        msgs = [_msg(content="hello")]
        original_len = len(msgs)
        restore_context(msgs, file_tracker=None, skill_context="some skill")
        assert len(msgs) == original_len


class TestConstants:
    def test_max_files_value(self):
        assert POST_COMPACT_MAX_FILES == 5

    def test_token_budget_value(self):
        assert POST_COMPACT_TOKEN_BUDGET == 50_000
