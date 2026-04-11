# tests/unit/test_ask_user_tool.py
"""Tests for duh.tools.ask_user_tool — AskUserQuestion tool."""

from __future__ import annotations

import pytest

from duh.tools.ask_user_tool import AskUserQuestionTool
from duh.kernel.tool import ToolContext


@pytest.fixture
def ctx():
    return ToolContext(cwd="/tmp")


class TestAskUserQuestionTool:
    def test_name(self):
        tool = AskUserQuestionTool()
        assert tool.name == "AskUserQuestion"

    def test_has_schema(self):
        tool = AskUserQuestionTool()
        assert "properties" in tool.input_schema

    async def test_asks_user_via_callback(self, ctx):
        async def fake_input(question: str) -> str:
            return "yes, do it"

        tool = AskUserQuestionTool(ask_fn=fake_input)
        result = await tool.call({"question": "Should I proceed?"}, ctx)
        assert not result.is_error
        assert "yes, do it" in result.output

    async def test_empty_question_rejected(self, ctx):
        async def fake_input(question: str) -> str:
            return "answer"

        tool = AskUserQuestionTool(ask_fn=fake_input)
        result = await tool.call({"question": ""}, ctx)
        assert result.is_error

    async def test_no_ask_fn_returns_error(self, ctx):
        tool = AskUserQuestionTool(ask_fn=None)
        result = await tool.call({"question": "hello?"}, ctx)
        assert result.is_error

    def test_is_read_only(self):
        tool = AskUserQuestionTool()
        assert tool.is_read_only is True

    def test_is_not_destructive(self):
        tool = AskUserQuestionTool()
        assert tool.is_destructive is False
