"""Tests for duh.kernel.file_tracker — FileTracker class."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from duh.kernel.file_tracker import FileOp, FileTracker


# ---------------------------------------------------------------------------
# FileOp dataclass
# ---------------------------------------------------------------------------


class TestFileOp:
    def test_fields(self):
        ts = datetime.now(timezone.utc)
        op = FileOp(path="/a.py", operation="read", timestamp=ts)
        assert op.path == "/a.py"
        assert op.operation == "read"
        assert op.timestamp == ts

    def test_immutable(self):
        op = FileOp(path="/a.py", operation="read", timestamp=datetime.now(timezone.utc))
        with pytest.raises(AttributeError):
            op.path = "/b.py"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# FileTracker.track / .ops / .clear
# ---------------------------------------------------------------------------


class TestTrack:
    def test_track_records_op(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        assert len(ft.ops) == 1
        assert ft.ops[0].path == "/a.py"
        assert ft.ops[0].operation == "read"

    def test_track_preserves_order(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        ft.track("/b.py", "write")
        ft.track("/c.py", "edit")
        assert [op.path for op in ft.ops] == ["/a.py", "/b.py", "/c.py"]

    def test_timestamp_is_utc(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        assert ft.ops[0].timestamp.tzinfo == timezone.utc

    def test_ops_returns_copy(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        ops = ft.ops
        ops.clear()
        assert len(ft.ops) == 1  # internal list unaffected

    def test_clear(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        ft.track("/b.py", "write")
        ft.clear()
        assert ft.ops == []

    def test_duplicate_paths_recorded(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        ft.track("/a.py", "edit")
        assert len(ft.ops) == 2


# ---------------------------------------------------------------------------
# FileTracker.summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_empty(self):
        ft = FileTracker()
        assert ft.summary() == "No file operations recorded."

    def test_single_read(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        s = ft.summary()
        assert "Reads (1):" in s
        assert "/a.py" in s

    def test_single_write(self):
        ft = FileTracker()
        ft.track("/a.py", "write")
        s = ft.summary()
        assert "Writes (1):" in s
        assert "/a.py" in s

    def test_single_edit(self):
        ft = FileTracker()
        ft.track("/a.py", "edit")
        s = ft.summary()
        assert "Edits (1):" in s
        assert "/a.py" in s

    def test_mixed_operations(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        ft.track("/b.py", "write")
        ft.track("/a.py", "edit")
        s = ft.summary()
        assert "Reads (1):" in s
        assert "Writes (1):" in s
        assert "Edits (1):" in s

    def test_deduplicates_paths_within_group(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        ft.track("/a.py", "read")
        ft.track("/a.py", "read")
        s = ft.summary()
        # Should show 1 unique path, not 3
        assert "Reads (1):" in s
        assert s.count("/a.py") == 1

    def test_multiple_unique_paths(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        ft.track("/b.py", "read")
        s = ft.summary()
        assert "Reads (2):" in s
        assert "/a.py" in s
        assert "/b.py" in s

    def test_operation_order_read_write_edit(self):
        ft = FileTracker()
        # Add in reverse order
        ft.track("/e.py", "edit")
        ft.track("/w.py", "write")
        ft.track("/r.py", "read")
        s = ft.summary()
        # Reads should appear before Writes, Writes before Edits
        read_pos = s.index("Reads")
        write_pos = s.index("Writes")
        edit_pos = s.index("Edits")
        assert read_pos < write_pos < edit_pos

    def test_unknown_operation_included(self):
        ft = FileTracker()
        ft.track("/a.py", "delete")
        s = ft.summary()
        assert "Deletes (1):" in s
        assert "/a.py" in s


# ---------------------------------------------------------------------------
# FileTracker.diff_summary
# ---------------------------------------------------------------------------


class TestDiffSummary:
    def test_no_modifications(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        assert ft.diff_summary() == "No files modified."

    def test_empty_tracker(self):
        ft = FileTracker()
        assert ft.diff_summary() == "No files modified."

    def test_write_calls_git_diff(self):
        ft = FileTracker()
        ft.track("/a.py", "write")

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=" /a.py | 5 ++---\n", stderr=""
        )
        with patch("duh.kernel.file_tracker.subprocess.run", return_value=fake_result) as mock_run:
            result = ft.diff_summary(cwd="/project")
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == ["git", "diff", "--stat", "--", "/a.py"]
            assert call_args[1]["cwd"] == "/project"
        assert "/a.py" in result

    def test_edit_calls_git_diff(self):
        ft = FileTracker()
        ft.track("/b.py", "edit")

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=" /b.py | 2 +-\n", stderr=""
        )
        with patch("duh.kernel.file_tracker.subprocess.run", return_value=fake_result):
            result = ft.diff_summary()
        assert "/b.py" in result

    def test_new_untracked_file(self):
        ft = FileTracker()
        ft.track("/new.py", "write")

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with patch("duh.kernel.file_tracker.subprocess.run", return_value=fake_result):
            result = ft.diff_summary()
        assert "new/untracked" in result

    def test_git_unavailable(self):
        ft = FileTracker()
        ft.track("/a.py", "write")

        with patch("duh.kernel.file_tracker.subprocess.run", side_effect=FileNotFoundError):
            result = ft.diff_summary()
        assert "git unavailable" in result

    def test_deduplicates_modified_paths(self):
        ft = FileTracker()
        ft.track("/a.py", "write")
        ft.track("/a.py", "edit")
        ft.track("/a.py", "write")

        call_count = 0

        def fake_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=" /a.py | 1 +\n", stderr=""
            )

        with patch("duh.kernel.file_tracker.subprocess.run", side_effect=fake_run):
            ft.diff_summary()
        assert call_count == 1  # called once, not three times

    def test_read_only_not_included(self):
        ft = FileTracker()
        ft.track("/a.py", "read")
        ft.track("/b.py", "read")
        assert ft.diff_summary() == "No files modified."


# ---------------------------------------------------------------------------
# Integration with NativeExecutor
# ---------------------------------------------------------------------------


class TestNativeExecutorIntegration:
    """Verify FileTracker is wired into NativeExecutor correctly."""

    async def test_executor_has_file_tracker(self):
        from duh.adapters.native_executor import NativeExecutor
        e = NativeExecutor()
        assert isinstance(e.file_tracker, FileTracker)

    async def test_read_tool_tracked(self, tmp_path):
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.tool import ToolResult

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        class FakeRead:
            name = "Read"
            description = ""
            input_schema = {}

            async def call(self, input, context):
                return ToolResult(output="hello")

            async def check_permissions(self, input, context):
                return {"allowed": True}

        e = NativeExecutor(tools=[FakeRead()])
        await e.run("Read", {"file_path": str(test_file)})
        assert len(e.file_tracker.ops) == 1
        assert e.file_tracker.ops[0].operation == "read"

    async def test_write_tool_tracked(self):
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.tool import ToolResult

        class FakeWrite:
            name = "Write"
            description = ""
            input_schema = {}

            async def call(self, input, context):
                return ToolResult(output="ok")

            async def check_permissions(self, input, context):
                return {"allowed": True}

        e = NativeExecutor(tools=[FakeWrite()])
        await e.run("Write", {"file_path": "/tmp/out.py", "content": "x"})
        assert len(e.file_tracker.ops) == 1
        assert e.file_tracker.ops[0].operation == "write"

    async def test_edit_tool_tracked(self):
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.tool import ToolResult

        class FakeEdit:
            name = "Edit"
            description = ""
            input_schema = {}

            async def call(self, input, context):
                return ToolResult(output="ok")

            async def check_permissions(self, input, context):
                return {"allowed": True}

        e = NativeExecutor(tools=[FakeEdit()])
        await e.run("Edit", {"file_path": "/tmp/f.py", "old_string": "a", "new_string": "b"})
        assert len(e.file_tracker.ops) == 1
        assert e.file_tracker.ops[0].operation == "edit"

    async def test_non_file_tool_not_tracked(self):
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.tool import ToolResult

        class FakeBash:
            name = "Bash"
            description = ""
            input_schema = {}

            async def call(self, input, context):
                return ToolResult(output="ok")

        e = NativeExecutor(tools=[FakeBash()])
        await e.run("Bash", {"command": "echo hi"})
        assert len(e.file_tracker.ops) == 0

    async def test_error_result_not_tracked(self):
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.tool import ToolResult

        class FailRead:
            name = "Read"
            description = ""
            input_schema = {}

            async def call(self, input, context):
                return ToolResult(output="File not found", is_error=True)

            async def check_permissions(self, input, context):
                return {"allowed": True}

        e = NativeExecutor(tools=[FailRead()])
        with pytest.raises(RuntimeError):
            await e.run("Read", {"file_path": "/nope.py"})
        assert len(e.file_tracker.ops) == 0

    async def test_missing_file_path_not_tracked(self):
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.tool import ToolResult

        class FakeRead:
            name = "Read"
            description = ""
            input_schema = {}

            async def call(self, input, context):
                return ToolResult(output="ok")

            async def check_permissions(self, input, context):
                return {"allowed": True}

        e = NativeExecutor(tools=[FakeRead()])
        await e.run("Read", {})  # no file_path
        assert len(e.file_tracker.ops) == 0


# ---------------------------------------------------------------------------
# REPL /changes command
# ---------------------------------------------------------------------------


class TestReplChangesCommand:
    """Verify /changes slash command works."""

    def test_changes_with_executor(self, capsys):
        from unittest.mock import AsyncMock
        from duh.adapters.native_executor import NativeExecutor
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))
        executor = NativeExecutor()
        executor.file_tracker.track("/src/main.py", "read")
        executor.file_tracker.track("/src/main.py", "edit")

        keep, model = _handle_slash(
            "/changes", engine, "test", deps, executor=executor
        )
        assert keep is True
        captured = capsys.readouterr()
        assert "Reads" in captured.out
        assert "Edits" in captured.out
        assert "/src/main.py" in captured.out

    def test_changes_no_executor(self, capsys):
        from unittest.mock import AsyncMock
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))

        keep, model = _handle_slash("/changes", engine, "test", deps)
        assert keep is True
        captured = capsys.readouterr()
        assert "No file tracker" in captured.out

    def test_changes_empty(self, capsys):
        from unittest.mock import AsyncMock
        from duh.adapters.native_executor import NativeExecutor
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))
        executor = NativeExecutor()

        keep, model = _handle_slash(
            "/changes", engine, "test", deps, executor=executor
        )
        assert keep is True
        captured = capsys.readouterr()
        assert "No file operations" in captured.out

    def test_changes_in_slash_commands_dict(self):
        from duh.cli.repl import SLASH_COMMANDS
        assert "/changes" in SLASH_COMMANDS

    def test_help_lists_changes(self, capsys):
        from unittest.mock import AsyncMock
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))
        _handle_slash("/help", engine, "test", deps)
        captured = capsys.readouterr()
        assert "/changes" in captured.out
