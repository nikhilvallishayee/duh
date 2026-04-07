"""Tests for the task management system (kernel + tool + REPL)."""

from __future__ import annotations

import pytest

from duh.kernel.tasks import Task, TaskManager, VALID_STATUSES
from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.task_tool import TaskTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ctx() -> ToolContext:
    return ToolContext(cwd=".")


# ===========================================================================
# TaskManager (kernel)
# ===========================================================================

class TestTaskManager:
    def test_create_task(self):
        mgr = TaskManager()
        t = mgr.create("Write tests")
        assert t.description == "Write tests"
        assert t.status == "pending"
        assert len(t.id) == 8

    def test_create_multiple(self):
        mgr = TaskManager()
        t1 = mgr.create("First")
        t2 = mgr.create("Second")
        assert t1.id != t2.id
        assert len(mgr.list_all()) == 2

    def test_update_status(self):
        mgr = TaskManager()
        t = mgr.create("Do something")
        mgr.update(t.id, "in_progress")
        assert t.status == "in_progress"
        mgr.update(t.id, "completed")
        assert t.status == "completed"

    def test_update_invalid_status(self):
        mgr = TaskManager()
        t = mgr.create("Task")
        with pytest.raises(ValueError, match="Invalid status"):
            mgr.update(t.id, "bogus")

    def test_update_missing_task(self):
        mgr = TaskManager()
        with pytest.raises(KeyError, match="No task"):
            mgr.update("nonexistent", "pending")

    def test_get_existing(self):
        mgr = TaskManager()
        t = mgr.create("Find me")
        assert mgr.get(t.id) is t

    def test_get_missing(self):
        mgr = TaskManager()
        assert mgr.get("nope") is None

    def test_list_all_order(self):
        mgr = TaskManager()
        t1 = mgr.create("A")
        t2 = mgr.create("B")
        t3 = mgr.create("C")
        ids = [t.id for t in mgr.list_all()]
        assert ids == [t1.id, t2.id, t3.id]

    def test_list_all_returns_copy(self):
        mgr = TaskManager()
        mgr.create("X")
        lst = mgr.list_all()
        lst.clear()
        assert len(mgr.list_all()) == 1  # original unaffected

    def test_summary_empty(self):
        mgr = TaskManager()
        assert mgr.summary() == "No tasks."

    def test_summary_formatting(self):
        mgr = TaskManager()
        t1 = mgr.create("Write tests")
        t2 = mgr.create("Refactor module")
        t3 = mgr.create("Update docs")
        mgr.update(t1.id, "completed")
        mgr.update(t2.id, "in_progress")

        s = mgr.summary()
        assert "1/3 completed" in s
        assert f"[x] {t1.id}" in s
        assert f"[~] {t2.id}" in s
        assert f"[ ] {t3.id}" in s

    def test_summary_all_completed(self):
        mgr = TaskManager()
        t = mgr.create("Done")
        mgr.update(t.id, "completed")
        assert "1/1 completed" in mgr.summary()

    def test_created_at_populated(self):
        mgr = TaskManager()
        t = mgr.create("Check timestamp")
        assert t.created_at  # non-empty ISO string


# ===========================================================================
# Task dataclass
# ===========================================================================

class TestTaskDataclass:
    def test_default_status(self):
        t = Task(id="abc", description="test")
        assert t.status == "pending"

    def test_created_at_auto(self):
        t = Task(id="abc", description="test")
        assert "T" in t.created_at  # ISO format contains T


# ===========================================================================
# VALID_STATUSES
# ===========================================================================

class TestValidStatuses:
    def test_expected_values(self):
        assert VALID_STATUSES == {"pending", "in_progress", "completed"}


# ===========================================================================
# TaskTool (tool layer)
# ===========================================================================

class TestTaskTool:
    def test_protocol_conformance(self):
        tool = TaskTool()
        assert isinstance(tool, Tool)
        assert tool.name == "Task"
        assert tool.input_schema["type"] == "object"
        assert "required" in tool.input_schema

    def test_is_not_read_only(self):
        assert TaskTool().is_read_only is False

    def test_is_not_destructive(self):
        assert TaskTool().is_destructive is False

    # -- create --------------------------------------------------------

    async def test_create(self):
        tool = TaskTool()
        result = await tool.call(
            {"action": "create", "description": "Build feature"}, ctx()
        )
        assert result.is_error is False
        assert "Build feature" in result.output
        assert result.metadata["task_id"]
        assert result.metadata["status"] == "pending"

    async def test_create_missing_description(self):
        tool = TaskTool()
        result = await tool.call({"action": "create"}, ctx())
        assert result.is_error is True
        assert "description" in result.output.lower()

    async def test_create_empty_description(self):
        tool = TaskTool()
        result = await tool.call(
            {"action": "create", "description": "  "}, ctx()
        )
        assert result.is_error is True

    # -- update --------------------------------------------------------

    async def test_update(self):
        tool = TaskTool()
        r1 = await tool.call(
            {"action": "create", "description": "Do thing"}, ctx()
        )
        tid = r1.metadata["task_id"]

        r2 = await tool.call(
            {"action": "update", "task_id": tid, "status": "in_progress"}, ctx()
        )
        assert r2.is_error is False
        assert r2.metadata["status"] == "in_progress"

    async def test_update_to_completed(self):
        tool = TaskTool()
        r1 = await tool.call(
            {"action": "create", "description": "Finish"}, ctx()
        )
        tid = r1.metadata["task_id"]

        r2 = await tool.call(
            {"action": "update", "task_id": tid, "status": "completed"}, ctx()
        )
        assert r2.is_error is False
        assert r2.metadata["status"] == "completed"

    async def test_update_missing_task_id(self):
        tool = TaskTool()
        result = await tool.call(
            {"action": "update", "status": "completed"}, ctx()
        )
        assert result.is_error is True
        assert "task_id" in result.output.lower()

    async def test_update_missing_status(self):
        tool = TaskTool()
        r1 = await tool.call(
            {"action": "create", "description": "X"}, ctx()
        )
        result = await tool.call(
            {"action": "update", "task_id": r1.metadata["task_id"]}, ctx()
        )
        assert result.is_error is True
        assert "status" in result.output.lower()

    async def test_update_invalid_status(self):
        tool = TaskTool()
        r1 = await tool.call(
            {"action": "create", "description": "X"}, ctx()
        )
        result = await tool.call(
            {"action": "update", "task_id": r1.metadata["task_id"], "status": "bogus"},
            ctx(),
        )
        assert result.is_error is True
        assert "invalid" in result.output.lower()

    async def test_update_nonexistent_task(self):
        tool = TaskTool()
        result = await tool.call(
            {"action": "update", "task_id": "nope1234", "status": "completed"},
            ctx(),
        )
        assert result.is_error is True
        assert "nope1234" in result.output

    # -- list ----------------------------------------------------------

    async def test_list_empty(self):
        tool = TaskTool()
        result = await tool.call({"action": "list"}, ctx())
        assert result.is_error is False
        assert "No tasks" in result.output
        assert result.metadata["count"] == 0

    async def test_list_with_tasks(self):
        tool = TaskTool()
        await tool.call(
            {"action": "create", "description": "Alpha"}, ctx()
        )
        await tool.call(
            {"action": "create", "description": "Beta"}, ctx()
        )
        result = await tool.call({"action": "list"}, ctx())
        assert result.is_error is False
        assert "Alpha" in result.output
        assert "Beta" in result.output
        assert result.metadata["count"] == 2

    # -- unknown action ------------------------------------------------

    async def test_unknown_action(self):
        tool = TaskTool()
        result = await tool.call({"action": "delete"}, ctx())
        assert result.is_error is True
        assert "Unknown action" in result.output

    async def test_empty_action(self):
        tool = TaskTool()
        result = await tool.call({}, ctx())
        assert result.is_error is True

    # -- permissions ---------------------------------------------------

    async def test_permissions_always_allowed(self):
        tool = TaskTool()
        result = await tool.check_permissions({}, ctx())
        assert result["allowed"] is True

    # -- shared task manager -------------------------------------------

    async def test_shared_manager(self):
        """TaskTool can receive an external TaskManager."""
        mgr = TaskManager()
        mgr.create("Pre-existing")
        tool = TaskTool(task_manager=mgr)
        result = await tool.call({"action": "list"}, ctx())
        assert "Pre-existing" in result.output
        assert result.metadata["count"] == 1

    async def test_task_manager_property(self):
        mgr = TaskManager()
        tool = TaskTool(task_manager=mgr)
        assert tool.task_manager is mgr


# ===========================================================================
# REPL /tasks slash command
# ===========================================================================

class TestSlashTasks:
    def test_tasks_with_manager(self, capsys):
        from unittest.mock import AsyncMock
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))

        mgr = TaskManager()
        mgr.create("Test task")
        keep, _ = _handle_slash("/tasks", engine, "m", deps, task_manager=mgr)
        assert keep is True
        captured = capsys.readouterr()
        assert "Test task" in captured.out

    def test_tasks_no_manager(self, capsys):
        from unittest.mock import AsyncMock
        from duh.cli.repl import _handle_slash
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))

        keep, _ = _handle_slash("/tasks", engine, "m", deps)
        assert keep is True
        captured = capsys.readouterr()
        assert "No tasks" in captured.out

    def test_tasks_in_slash_commands(self):
        from duh.cli.repl import SLASH_COMMANDS
        assert "/tasks" in SLASH_COMMANDS
