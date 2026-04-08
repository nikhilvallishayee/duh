"""Tests for ghost snapshot mode."""

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from duh.kernel.messages import Message
from duh.kernel.snapshot import ReadOnlyExecutor, SnapshotSession


class TestReadOnlyExecutor:
    async def test_allows_read(self):
        inner = AsyncMock(return_value="file contents")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("Read", {"file_path": "/tmp/x"})
        assert result == "file contents"
        inner.assert_called_once()

    async def test_allows_glob(self):
        inner = AsyncMock(return_value="a.py\nb.py")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("Glob", {"pattern": "*.py"})
        assert result == "a.py\nb.py"

    async def test_allows_grep(self):
        inner = AsyncMock(return_value="match")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("Grep", {"pattern": "foo"})
        assert result == "match"

    async def test_blocks_write(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("Write", {"file_path": "/tmp/x", "content": "y"})
        inner.assert_not_called()

    async def test_blocks_edit(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("Edit", {"file_path": "/tmp/x"})
        inner.assert_not_called()

    async def test_blocks_bash(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("Bash", {"command": "rm -rf /"})
        inner.assert_not_called()

    async def test_blocks_multi_edit(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("MultiEdit", {"file_path": "/tmp/x"})

    async def test_allows_tool_search(self):
        inner = AsyncMock(return_value="results")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("ToolSearch", {"query": "test"})
        assert result == "results"

    async def test_allows_web_search(self):
        inner = AsyncMock(return_value="results")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("WebSearch", {"query": "test"})
        assert result == "results"


class TestSnapshotSession:
    def test_creates_forked_state(self):
        messages = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        snapshot = SnapshotSession(messages)
        assert len(snapshot.messages) == 2
        # Verify deep copy: modifying original doesn't affect snapshot
        messages.append(Message(role="user", content="new"))
        assert len(snapshot.messages) == 2

    def test_messages_are_independent_copies(self):
        messages = [Message(role="user", content="hello")]
        snapshot = SnapshotSession(messages)
        # Modify the snapshot's messages
        snapshot.messages.append(Message(role="user", content="extra"))
        # Original should be unaffected
        assert len(messages) == 1

    def test_add_message(self):
        snapshot = SnapshotSession([])
        snapshot.add_message(Message(role="user", content="test"))
        assert len(snapshot.messages) == 1

    def test_discard(self):
        snapshot = SnapshotSession([Message(role="user", content="hello")])
        snapshot.add_message(Message(role="assistant", content="reply"))
        snapshot.discard()
        assert len(snapshot.messages) == 0
        assert snapshot.is_discarded is True

    def test_is_discarded_default(self):
        snapshot = SnapshotSession([])
        assert snapshot.is_discarded is False

    def test_merge_returns_new_messages(self):
        original = [Message(role="user", content="hello")]
        snapshot = SnapshotSession(original)
        snapshot.add_message(Message(role="assistant", content="hi from snapshot"))
        snapshot.add_message(Message(role="user", content="more"))
        new_msgs = snapshot.get_new_messages()
        assert len(new_msgs) == 2
        assert new_msgs[0].content == "hi from snapshot"
        assert new_msgs[1].content == "more"

    def test_merge_after_discard_returns_empty(self):
        snapshot = SnapshotSession([Message(role="user", content="hello")])
        snapshot.add_message(Message(role="assistant", content="reply"))
        snapshot.discard()
        assert snapshot.get_new_messages() == []

    def test_str_representation(self):
        snapshot = SnapshotSession([Message(role="user", content="hello")])
        s = str(snapshot)
        assert "Snapshot" in s or "snapshot" in s
