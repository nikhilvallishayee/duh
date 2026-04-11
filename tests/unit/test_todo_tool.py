# tests/unit/test_todo_tool.py
"""Tests for duh.tools.todo_tool — TodoWrite structured checklist."""

from __future__ import annotations

import pytest

from duh.tools.todo_tool import TodoWriteTool
from duh.kernel.tool import ToolContext


@pytest.fixture
def tool():
    return TodoWriteTool()


@pytest.fixture
def ctx():
    return ToolContext(cwd="/tmp")


class TestTodoWriteTool:
    def test_name(self, tool):
        assert tool.name == "TodoWrite"

    def test_has_schema(self, tool):
        assert "properties" in tool.input_schema

    async def test_create_todo(self, tool, ctx):
        result = await tool.call({
            "todos": [
                {"id": "1", "text": "Fix the bug", "status": "pending"},
                {"id": "2", "text": "Write tests", "status": "pending"},
            ]
        }, ctx)
        assert not result.is_error
        assert "2 todos" in result.output.lower() or "updated" in result.output.lower()

    async def test_update_todo_status(self, tool, ctx):
        # Create
        await tool.call({
            "todos": [
                {"id": "1", "text": "Fix the bug", "status": "pending"},
            ]
        }, ctx)
        # Update
        result = await tool.call({
            "todos": [
                {"id": "1", "text": "Fix the bug", "status": "done"},
            ]
        }, ctx)
        assert not result.is_error

    async def test_get_all_todos(self, tool, ctx):
        await tool.call({
            "todos": [
                {"id": "1", "text": "Task A", "status": "pending"},
                {"id": "2", "text": "Task B", "status": "done"},
            ]
        }, ctx)
        # Each instance tracks its own state
        assert len(tool._todos) == 2

    async def test_empty_todos_list(self, tool, ctx):
        result = await tool.call({"todos": []}, ctx)
        assert not result.is_error

    async def test_invalid_status_rejected(self, tool, ctx):
        result = await tool.call({
            "todos": [
                {"id": "1", "text": "Bad status", "status": "invalid"},
            ]
        }, ctx)
        assert result.is_error

    def test_is_not_destructive(self, tool):
        assert tool.is_destructive is False

    def test_is_not_read_only(self, tool):
        assert tool.is_read_only is False
