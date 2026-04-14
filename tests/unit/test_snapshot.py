"""Tests for ghost snapshot mode."""

import copy
import time
import warnings
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from duh.kernel.messages import Message
from duh.kernel.snapshot import (
    GHOST_MAX_BYTES,
    GHOST_MAX_FILES,
    GHOST_MAX_TURNS,
    GHOST_WARN_TURNS,
    GhostExecutor,
    GhostSnapshot,
    ReadOnlyExecutor,
    SnapshotSession,
)


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
        messages.append(Message(role="user", content="new"))
        assert len(snapshot.messages) == 2

    def test_messages_are_independent_copies(self):
        messages = [Message(role="user", content="hello")]
        snapshot = SnapshotSession(messages)
        snapshot.messages.append(Message(role="user", content="extra"))
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


# ---------------------------------------------------------------------------
# GhostExecutor tests
# ---------------------------------------------------------------------------


def _make_real_executor(read_return: str = "real content") -> AsyncMock:
    real = AsyncMock()
    real.run = AsyncMock(return_value=read_return)
    return real


class TestGhostExecutorWriteCapture:
    async def test_write_goes_to_overlay(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        output = await executor.execute_write("/tmp/ghost_test.py", "print('hello')")
        assert "/tmp/ghost_test.py" in overlay
        assert overlay["/tmp/ghost_test.py"] == "print('hello')"
        assert "ghost" in output["output"].lower() or "write" in output["output"].lower()

    async def test_write_does_not_touch_disk(self, tmp_path):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        target = str(tmp_path / "should_not_exist.txt")
        await executor.execute_write(target, "nope")
        assert not Path(target).exists()

    async def test_write_via_run_interface(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        result = await executor.run("Write", {"file_path": "/tmp/run_write.py", "content": "x=1"})
        assert "/tmp/run_write.py" in overlay
        assert "[ghost]" in result or "ghost" in result.lower()

    async def test_edit_via_run_interface(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        await executor.run("Edit", {"file_path": "/tmp/edit.py", "new_string": "y=2"})
        assert "/tmp/edit.py" in overlay

    async def test_multi_edit_via_run_interface(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        await executor.run("MultiEdit", {"file_path": "/tmp/me.py", "new_string": "z=3"})
        assert "/tmp/me.py" in overlay

    async def test_overlay_replaces_previous_write(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        await executor.execute_write("/tmp/f.py", "version 1")
        await executor.execute_write("/tmp/f.py", "version 2")
        assert overlay["/tmp/f.py"] == "version 2"
        assert len(overlay) == 1


class TestGhostExecutorReadFallthrough:
    async def test_read_from_overlay_when_present(self):
        overlay = {"/tmp/ghost_file.py": "ghost content"}
        executor = GhostExecutor(_make_real_executor("real content"), overlay)
        result = await executor.execute_read("/tmp/ghost_file.py")
        assert result["output"] == "ghost content"
        assert result["ghost"] is True

    async def test_read_falls_back_to_real(self):
        overlay = {}
        real = _make_real_executor("real content")
        executor = GhostExecutor(real, overlay)
        result = await executor.execute_read("/tmp/real_file.py")
        assert result["output"] == "real content"
        assert result["ghost"] is False
        real.run.assert_called_once()

    async def test_read_via_run_returns_overlay_content(self):
        overlay = {"/tmp/ovr.py": "overlay data"}
        executor = GhostExecutor(_make_real_executor(), overlay)
        result = await executor.run("Read", {"file_path": "/tmp/ovr.py"})
        assert result == "overlay data"

    async def test_non_write_tools_forwarded_to_real(self):
        overlay = {}
        real = _make_real_executor("grep match")
        executor = GhostExecutor(real, overlay)
        result = await executor.run("Grep", {"pattern": "foo"})
        assert result == "grep match"
        real.run.assert_called_once_with("Grep", {"pattern": "foo"})


class TestGhostExecutorBashBlocking:
    async def test_bash_blocked(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        with pytest.raises(PermissionError, match="[Gg]host"):
            await executor.run("Bash", {"command": "echo hi"})

    async def test_bash_blocked_message_is_informative(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        with pytest.raises(PermissionError) as exc_info:
            await executor.run("Bash", {"command": "ls"})
        assert "ghost" in str(exc_info.value).lower()
        assert len(str(exc_info.value)) > 30


class TestGhostExecutorOverlayCaps:
    async def test_file_cap_raises_overflow(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        for i in range(GHOST_MAX_FILES):
            await executor.execute_write(f"/tmp/file_{i}.py", "x")
        assert len(overlay) == GHOST_MAX_FILES
        with pytest.raises(OverflowError, match="cap"):
            await executor.execute_write("/tmp/file_overflow.py", "y")

    async def test_replacing_existing_file_does_not_breach_file_cap(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        for i in range(GHOST_MAX_FILES):
            await executor.execute_write(f"/tmp/file_{i}.py", "x")
        await executor.execute_write("/tmp/file_0.py", "updated")
        assert overlay["/tmp/file_0.py"] == "updated"

    async def test_byte_cap_raises_overflow(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        big_content = "a" * (GHOST_MAX_BYTES - 10)
        await executor.execute_write("/tmp/big.txt", big_content)
        with pytest.raises(OverflowError, match="cap"):
            await executor.execute_write("/tmp/big2.txt", "a" * 100)

    async def test_replacing_content_reduces_byte_count(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        big_content = "a" * (GHOST_MAX_BYTES - 100)
        await executor.execute_write("/tmp/shrink.txt", big_content)
        await executor.execute_write("/tmp/shrink.txt", "tiny")
        assert overlay["/tmp/shrink.txt"] == "tiny"

    async def test_error_message_mentions_max_files(self):
        overlay = {f"/tmp/f_{i}.py": "x" for i in range(GHOST_MAX_FILES)}
        executor = GhostExecutor(_make_real_executor(), overlay)
        with pytest.raises(OverflowError) as exc_info:
            await executor.execute_write("/tmp/overflow.py", "y")
        assert str(GHOST_MAX_FILES) in str(exc_info.value)


class TestGhostExecutorMergeToDisk:
    def test_merge_writes_files(self, tmp_path):
        overlay = {
            str(tmp_path / "a.py"): "print('a')",
            str(tmp_path / "b.py"): "print('b')",
        }
        executor = GhostExecutor(_make_real_executor(), overlay)
        written = executor.merge_to_disk()
        assert set(written) == {str(tmp_path / "a.py"), str(tmp_path / "b.py")}
        assert (tmp_path / "a.py").read_text() == "print('a')"
        assert (tmp_path / "b.py").read_text() == "print('b')"

    def test_merge_clears_overlay(self, tmp_path):
        overlay = {str(tmp_path / "c.py"): "x=1"}
        executor = GhostExecutor(_make_real_executor(), overlay)
        executor.merge_to_disk()
        assert overlay == {}

    def test_merge_creates_parent_directories(self, tmp_path):
        target = str(tmp_path / "nested" / "dir" / "file.py")
        overlay = {target: "content"}
        executor = GhostExecutor(_make_real_executor(), overlay)
        executor.merge_to_disk()
        assert Path(target).exists()
        assert Path(target).read_text() == "content"

    def test_merge_returns_empty_list_for_empty_overlay(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        result = executor.merge_to_disk()
        assert result == []

    def test_overlay_empty_after_merge(self, tmp_path):
        overlay = {str(tmp_path / "d.py"): "y=2"}
        executor = GhostExecutor(_make_real_executor(), overlay)
        executor.merge_to_disk()
        assert len(overlay) == 0


class TestGhostSnapshot:
    def test_default_id_is_unique(self):
        s1 = GhostSnapshot()
        s2 = GhostSnapshot()
        assert s1.id != s2.id

    def test_created_at_is_recent(self):
        before = time.time()
        snap = GhostSnapshot()
        after = time.time()
        assert before <= snap.created_at <= after

    def test_initial_turn_count_is_zero(self):
        snap = GhostSnapshot()
        assert snap.turn_count == 0

    def test_increment_turn(self):
        snap = GhostSnapshot()
        snap.increment_turn()
        snap.increment_turn()
        assert snap.turn_count == 2

    def test_warn_at_warn_turns(self):
        snap = GhostSnapshot()
        for _ in range(GHOST_WARN_TURNS - 1):
            snap.increment_turn()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            snap.increment_turn()
            assert snap.turn_count == GHOST_WARN_TURNS
            ghost_warnings = [
                x for x in w
                if "ghost" in str(x.message).lower() or "turn" in str(x.message).lower()
            ]
            assert len(ghost_warnings) >= 1

    def test_raises_after_max_turns(self):
        snap = GhostSnapshot()
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            for _ in range(GHOST_MAX_TURNS):
                snap.increment_turn()
        with pytest.raises(RuntimeError, match="[Gg]host"):
            snap.increment_turn()

    def test_discard_clears_overlay_and_messages(self):
        overlay = {"/tmp/x.py": "content"}
        messages = [Message(role="user", content="hi")]
        snap = GhostSnapshot(parent_messages=messages, fs_overlay=overlay)
        snap.discard()
        assert overlay == {}
        assert snap.parent_messages == []

    def test_label_stored(self):
        snap = GhostSnapshot(label="refactor exploration")
        assert snap.label == "refactor exploration"

    def test_get_new_messages(self):
        original = [Message(role="user", content="hello")]
        snap = GhostSnapshot(parent_messages=list(original))
        snap.parent_messages.append(Message(role="assistant", content="ghost reply"))
        new_msgs = snap.get_new_messages(original_count=1)
        assert len(new_msgs) == 1
        assert new_msgs[0].content == "ghost reply"


class TestGhostModeIntegration:
    async def test_write_then_merge(self, tmp_path):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        snap = GhostSnapshot(fs_overlay=overlay)
        target = str(tmp_path / "merged.py")
        await executor.execute_write(target, "print('merged!')")
        assert not Path(target).exists()
        executor.merge_to_disk()
        assert Path(target).read_text() == "print('merged!')"
        assert overlay == {}

    async def test_write_then_discard(self, tmp_path):
        overlay = {}
        executor = GhostExecutor(_make_real_executor(), overlay)
        snap = GhostSnapshot(fs_overlay=overlay)
        target = str(tmp_path / "discarded.py")
        await executor.execute_write(target, "print('discarded!')")
        snap.discard()
        assert not Path(target).exists()
        assert overlay == {}

    async def test_read_write_read_roundtrip(self):
        overlay = {}
        executor = GhostExecutor(_make_real_executor("on-disk"), overlay)
        await executor.execute_write("/tmp/rw.py", "ghost content")
        result = await executor.execute_read("/tmp/rw.py")
        assert result["output"] == "ghost content"
        assert result["ghost"] is True
