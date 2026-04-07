"""Tests for duh.kernel.undo — UndoStack class and REPL /undo integration."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from duh.kernel.undo import UndoStack


# ---------------------------------------------------------------------------
# UndoStack basics
# ---------------------------------------------------------------------------


class TestUndoStackConstruction:
    def test_default_maxlen(self):
        s = UndoStack()
        assert s.maxlen == 20
        assert s.depth == 0

    def test_custom_maxlen(self):
        s = UndoStack(maxlen=5)
        assert s.maxlen == 5

    def test_maxlen_must_be_positive(self):
        with pytest.raises(ValueError, match="maxlen must be >= 1"):
            UndoStack(maxlen=0)
        with pytest.raises(ValueError, match="maxlen must be >= 1"):
            UndoStack(maxlen=-1)


class TestPushAndDepth:
    def test_push_increments_depth(self):
        s = UndoStack()
        s.push("/a.py", "content")
        assert s.depth == 1
        s.push("/b.py", None)
        assert s.depth == 2

    def test_ring_buffer_evicts_oldest(self):
        s = UndoStack(maxlen=3)
        s.push("/a.py", "a")
        s.push("/b.py", "b")
        s.push("/c.py", "c")
        assert s.depth == 3
        # Adding a 4th should evict /a.py
        s.push("/d.py", "d")
        assert s.depth == 3
        # Undo should give us /d, /c, /b — not /a
        path, _ = s.undo()
        assert path == "/d.py"
        path, _ = s.undo()
        assert path == "/c.py"
        path, _ = s.undo()
        assert path == "/b.py"
        assert s.depth == 0


class TestPeek:
    def test_peek_empty(self):
        s = UndoStack()
        assert s.peek() is None

    def test_peek_returns_top(self):
        s = UndoStack()
        s.push("/a.py", "content")
        s.push("/b.py", None)
        assert s.peek() == ("/b.py", None)
        # peek should not remove the entry
        assert s.depth == 2


class TestClear:
    def test_clear(self):
        s = UndoStack()
        s.push("/a.py", "x")
        s.push("/b.py", "y")
        s.clear()
        assert s.depth == 0
        assert s.peek() is None


# ---------------------------------------------------------------------------
# UndoStack.undo — file restoration
# ---------------------------------------------------------------------------


class TestUndoRestore:
    def test_undo_empty_raises(self):
        s = UndoStack()
        with pytest.raises(IndexError, match="Nothing to undo"):
            s.undo()

    def test_undo_restores_existing_file(self, tmp_path: Path):
        f = tmp_path / "hello.py"
        original = "print('hello')"
        f.write_text(original, encoding="utf-8")

        s = UndoStack()
        s.push(str(f), original)

        # Simulate a change
        f.write_text("print('goodbye')", encoding="utf-8")
        assert f.read_text() == "print('goodbye')"

        path, msg = s.undo()
        assert path == str(f)
        assert "Restored" in msg
        assert f.read_text(encoding="utf-8") == original
        assert s.depth == 0

    def test_undo_deletes_new_file(self, tmp_path: Path):
        f = tmp_path / "new_file.py"
        s = UndoStack()
        s.push(str(f), None)  # was nonexistent

        # Simulate the Write creating it
        f.write_text("new content", encoding="utf-8")
        assert f.exists()

        path, msg = s.undo()
        assert path == str(f)
        assert "Deleted" in msg
        assert not f.exists()

    def test_undo_delete_already_removed(self, tmp_path: Path):
        f = tmp_path / "gone.py"
        s = UndoStack()
        s.push(str(f), None)
        # File was never actually created (or already deleted)
        path, msg = s.undo()
        assert "already removed" in msg

    def test_undo_multiple_lifo_order(self, tmp_path: Path):
        f1 = tmp_path / "first.py"
        f2 = tmp_path / "second.py"
        f1.write_text("v1", encoding="utf-8")
        f2.write_text("v1", encoding="utf-8")

        s = UndoStack()
        s.push(str(f1), "v1")
        s.push(str(f2), "v1")

        # Both files modified
        f1.write_text("v2", encoding="utf-8")
        f2.write_text("v2", encoding="utf-8")

        # Undo second first (LIFO)
        path, _ = s.undo()
        assert path == str(f2)
        assert f2.read_text() == "v1"
        assert f1.read_text() == "v2"  # not yet undone

        path, _ = s.undo()
        assert path == str(f1)
        assert f1.read_text() == "v1"


# ---------------------------------------------------------------------------
# NativeExecutor integration — undo stack populated on Write/Edit
# ---------------------------------------------------------------------------


class TestNativeExecutorUndo:
    async def test_executor_has_undo_stack(self):
        from duh.adapters.native_executor import NativeExecutor
        e = NativeExecutor()
        assert isinstance(e.undo_stack, UndoStack)
        assert e.undo_stack.depth == 0

    async def test_write_existing_file_pushes_content(self, tmp_path: Path):
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.tool import ToolResult

        target = tmp_path / "target.py"
        target.write_text("original", encoding="utf-8")

        class FakeWrite:
            name = "Write"
            description = ""
            input_schema = {}

            async def call(self, input, context):
                Path(input["file_path"]).write_text(input["content"], encoding="utf-8")
                return ToolResult(output="ok")

            async def check_permissions(self, input, context):
                return {"allowed": True}

        e = NativeExecutor(tools=[FakeWrite()])
        await e.run("Write", {"file_path": str(target), "content": "new"})
        assert e.undo_stack.depth == 1
        entry = e.undo_stack.peek()
        assert entry is not None
        assert entry[0] == str(target)
        assert entry[1] == "original"

    async def test_write_new_file_pushes_none(self, tmp_path: Path):
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.tool import ToolResult

        target = tmp_path / "brand_new.py"
        assert not target.exists()

        class FakeWrite:
            name = "Write"
            description = ""
            input_schema = {}

            async def call(self, input, context):
                Path(input["file_path"]).write_text(input["content"], encoding="utf-8")
                return ToolResult(output="ok")

            async def check_permissions(self, input, context):
                return {"allowed": True}

        e = NativeExecutor(tools=[FakeWrite()])
        await e.run("Write", {"file_path": str(target), "content": "hello"})
        assert e.undo_stack.depth == 1
        entry = e.undo_stack.peek()
        assert entry is not None
        assert entry[0] == str(target)
        assert entry[1] is None  # was new file

    async def test_edit_pushes_content(self, tmp_path: Path):
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.tool import ToolResult

        target = tmp_path / "editable.py"
        target.write_text("old content", encoding="utf-8")

        class FakeEdit:
            name = "Edit"
            description = ""
            input_schema = {}

            async def call(self, input, context):
                p = Path(input["file_path"])
                text = p.read_text()
                p.write_text(text.replace(input["old_string"], input["new_string"]))
                return ToolResult(output="ok")

            async def check_permissions(self, input, context):
                return {"allowed": True}

        e = NativeExecutor(tools=[FakeEdit()])
        await e.run("Edit", {
            "file_path": str(target),
            "old_string": "old",
            "new_string": "new",
        })
        assert e.undo_stack.depth == 1
        assert e.undo_stack.peek()[1] == "old content"

    async def test_read_does_not_push(self):
        from duh.adapters.native_executor import NativeExecutor
        from duh.kernel.tool import ToolResult

        class FakeRead:
            name = "Read"
            description = ""
            input_schema = {}

            async def call(self, input, context):
                return ToolResult(output="file content")

            async def check_permissions(self, input, context):
                return {"allowed": True}

        e = NativeExecutor(tools=[FakeRead()])
        await e.run("Read", {"file_path": "/tmp/whatever.py"})
        assert e.undo_stack.depth == 0


# ---------------------------------------------------------------------------
# REPL /undo slash command
# ---------------------------------------------------------------------------


class TestReplUndoCommand:
    def test_undo_in_slash_commands_dict(self):
        from duh.cli.repl import SLASH_COMMANDS
        assert "/undo" in SLASH_COMMANDS

    def test_help_lists_undo(self, capsys):
        from duh.adapters.native_executor import NativeExecutor
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))
        _handle_slash("/help", engine, "test", deps)
        captured = capsys.readouterr()
        assert "/undo" in captured.out

    def test_undo_no_executor(self, capsys):
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))
        keep, model = _handle_slash("/undo", engine, "test", deps)
        assert keep is True
        captured = capsys.readouterr()
        assert "No executor" in captured.out

    def test_undo_empty_stack(self, capsys):
        from duh.adapters.native_executor import NativeExecutor
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))
        executor = NativeExecutor()

        keep, model = _handle_slash("/undo", engine, "test", deps, executor=executor)
        assert keep is True
        captured = capsys.readouterr()
        assert "Nothing to undo" in captured.out

    def test_undo_restores_file(self, capsys, tmp_path):
        from duh.adapters.native_executor import NativeExecutor
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        f = tmp_path / "test.py"
        f.write_text("modified", encoding="utf-8")

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))
        executor = NativeExecutor()
        executor.undo_stack.push(str(f), "original")

        keep, model = _handle_slash("/undo", engine, "test", deps, executor=executor)
        assert keep is True
        captured = capsys.readouterr()
        assert "Restored" in captured.out
        assert f.read_text() == "original"
