"""Tests for duh.tools.agent_tool — AgentTool coverage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.kernel.tool import ToolContext, ToolResult
from duh.tools.agent_tool import AgentTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


@dataclass
class FakeAgentResult:
    result_text: str
    agent_type: str = "general"
    error: str = ""

    @property
    def is_error(self) -> bool:
        return bool(self.error)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestAgentToolConstruction:
    def test_default_construction(self):
        tool = AgentTool()
        assert tool._parent_deps is None
        assert tool._parent_tools is None

    def test_construction_with_deps(self):
        deps = MagicMock()
        tool = AgentTool(parent_deps=deps)
        assert tool._parent_deps is deps


# ---------------------------------------------------------------------------
# Schema / class attributes
# ---------------------------------------------------------------------------


class TestAgentToolSchema:
    def test_name(self):
        assert AgentTool.name == "Agent"

    def test_description_nonempty(self):
        assert isinstance(AgentTool.description, str)
        assert len(AgentTool.description) > 10

    def test_input_schema_type(self):
        assert AgentTool.input_schema["type"] == "object"

    def test_input_schema_has_prompt(self):
        props = AgentTool.input_schema["properties"]
        assert "prompt" in props
        assert props["prompt"]["type"] == "string"

    def test_input_schema_has_agent_type(self):
        props = AgentTool.input_schema["properties"]
        assert "agent_type" in props
        assert "enum" in props["agent_type"]

    def test_input_schema_required(self):
        assert "prompt" in AgentTool.input_schema["required"]

    def test_is_read_only_false(self):
        assert AgentTool.is_read_only is False

    def test_is_destructive_false(self):
        assert AgentTool.is_destructive is False


# ---------------------------------------------------------------------------
# call() with mocked run_agent
# ---------------------------------------------------------------------------


class TestAgentToolCall:
    @pytest.mark.asyncio
    async def test_call_success_with_result_text(self):
        """call() returns the agent's result_text on success."""
        tool = AgentTool(parent_deps=MagicMock())
        fake_result = FakeAgentResult(result_text="Agent says hello")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake_result
            result = await tool.call(
                {"prompt": "say hello", "agent_type": "general"},
                ctx(),
            )

        assert isinstance(result, ToolResult)
        assert result.output == "Agent says hello"
        assert result.is_error is False
        mock_run.assert_awaited_once_with(
            prompt="say hello",
            agent_type="general",
            model="",
            parent_model="",
            deps=tool._parent_deps,
            tools=[],
        )

    @pytest.mark.asyncio
    async def test_call_uses_default_agent_type(self):
        """When agent_type is missing, defaults to 'general'."""
        tool = AgentTool(parent_deps=MagicMock())
        fake_result = FakeAgentResult(result_text="ok")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake_result
            await tool.call({"prompt": "do stuff"}, ctx())

        mock_run.assert_awaited_once()
        _, kwargs = mock_run.call_args
        assert kwargs["agent_type"] == "general"

    @pytest.mark.asyncio
    async def test_call_uses_default_prompt(self):
        """When prompt is missing, defaults to empty string."""
        tool = AgentTool(parent_deps=MagicMock())
        fake_result = FakeAgentResult(result_text="ok")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake_result
            await tool.call({}, ctx())

        _, kwargs = mock_run.call_args
        assert kwargs["prompt"] == ""

    @pytest.mark.asyncio
    async def test_call_passes_coder_type(self):
        """agent_type='coder' is forwarded to run_agent."""
        tool = AgentTool(parent_deps=MagicMock())
        fake_result = FakeAgentResult(result_text="coded it")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake_result
            result = await tool.call(
                {"prompt": "write code", "agent_type": "coder"},
                ctx(),
            )

        assert result.output == "coded it"
        _, kwargs = mock_run.call_args
        assert kwargs["agent_type"] == "coder"

    @pytest.mark.asyncio
    async def test_call_empty_result_text(self):
        """If agent produces no text, returns placeholder message."""
        tool = AgentTool(parent_deps=MagicMock())
        fake_result = FakeAgentResult(result_text="")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake_result
            result = await tool.call({"prompt": "test"}, ctx())

        assert result.output == "(agent produced no output)"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_call_error_handling(self):
        """Exceptions from run_agent are caught and returned as errors."""
        tool = AgentTool(parent_deps=MagicMock())

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = RuntimeError("agent crashed")
            result = await tool.call({"prompt": "boom"}, ctx())

        assert result.is_error is True
        assert "Agent error:" in result.output
        assert "agent crashed" in result.output

    @pytest.mark.asyncio
    async def test_call_error_with_value_error(self):
        """ValueError (e.g., invalid agent type) is caught gracefully."""
        tool = AgentTool(parent_deps=MagicMock())

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = ValueError("Unknown agent type: 'bad'")
            result = await tool.call(
                {"prompt": "test", "agent_type": "bad"},
                ctx(),
            )

        assert result.is_error is True
        assert "Unknown agent type" in result.output


# ---------------------------------------------------------------------------
# No-deps guard
# ---------------------------------------------------------------------------


class TestAgentToolNoDeps:
    @pytest.mark.asyncio
    async def test_call_without_deps_returns_error(self):
        """AgentTool without parent_deps returns a clear error."""
        tool = AgentTool()  # no deps
        result = await tool.call({"prompt": "hello"}, ctx())
        assert result.is_error is True
        assert "no parent deps" in result.output


# ---------------------------------------------------------------------------
# Child tools filtering
# ---------------------------------------------------------------------------


class TestAgentToolChildTools:
    def test_child_tools_excludes_agent(self):
        """Child tools list excludes AgentTool to prevent recursion."""

        class FakeTool:
            name = "Read"

        class FakeAgent:
            name = "Agent"

        tool = AgentTool(parent_tools=[FakeTool(), FakeAgent(), FakeTool()])
        children = tool._child_tools()
        names = [getattr(t, "name", "") for t in children]
        assert "Agent" not in names
        assert names == ["Read", "Read"]

    def test_child_tools_empty_when_no_parent_tools(self):
        """No parent tools → empty child tools."""
        tool = AgentTool()
        assert tool._child_tools() == []


# ---------------------------------------------------------------------------
# check_permissions
# ---------------------------------------------------------------------------


class TestAgentToolPermissions:
    @pytest.mark.asyncio
    async def test_check_permissions_always_allowed(self):
        tool = AgentTool(parent_deps=MagicMock())
        perm = await tool.check_permissions({"prompt": "anything"}, ctx())
        assert perm == {"allowed": True}
