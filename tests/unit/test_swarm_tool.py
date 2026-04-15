"""Tests for duh.tools.swarm_tool — SwarmTool coverage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.kernel.tool import ToolContext, ToolResult
from duh.tools.swarm_tool import SwarmTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


@dataclass
class FakeAgentResult:
    result_text: str
    agent_type: str = "general"
    turns_used: int = 1
    error: str = ""

    @property
    def is_error(self) -> bool:
        return bool(self.error)


# ---------------------------------------------------------------------------
# Schema / class attributes
# ---------------------------------------------------------------------------


class TestSwarmToolSchema:
    def test_name(self):
        assert SwarmTool.name == "Swarm"

    def test_description_nonempty(self):
        assert isinstance(SwarmTool.description, str)
        assert len(SwarmTool.description) > 10

    def test_input_schema_type(self):
        assert SwarmTool.input_schema["type"] == "object"

    def test_input_schema_has_tasks(self):
        props = SwarmTool.input_schema["properties"]
        assert "tasks" in props
        assert props["tasks"]["type"] == "array"

    def test_input_schema_tasks_items_have_prompt(self):
        items = SwarmTool.input_schema["properties"]["tasks"]["items"]
        assert "prompt" in items["properties"]
        assert items["properties"]["prompt"]["type"] == "string"

    def test_input_schema_tasks_items_have_agent_type(self):
        items = SwarmTool.input_schema["properties"]["tasks"]["items"]
        assert "agent_type" in items["properties"]
        assert "enum" in items["properties"]["agent_type"]

    def test_input_schema_tasks_items_have_model(self):
        items = SwarmTool.input_schema["properties"]["tasks"]["items"]
        assert "model" in items["properties"]
        assert "enum" in items["properties"]["model"]

    def test_input_schema_required(self):
        assert "tasks" in SwarmTool.input_schema["required"]

    def test_input_schema_tasks_min_items(self):
        assert SwarmTool.input_schema["properties"]["tasks"]["minItems"] == 1

    def test_input_schema_tasks_max_items(self):
        assert SwarmTool.input_schema["properties"]["tasks"]["maxItems"] == 5

    def test_is_read_only_false(self):
        assert SwarmTool.is_read_only is False

    def test_is_destructive_false(self):
        assert SwarmTool.is_destructive is False


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestSwarmToolConstruction:
    def test_default_construction(self):
        tool = SwarmTool()
        assert tool._parent_deps is None
        assert tool._parent_tools is None

    def test_construction_with_deps(self):
        deps = MagicMock()
        tool = SwarmTool(parent_deps=deps)
        assert tool._parent_deps is deps

    def test_construction_with_tools(self):
        tools = [MagicMock()]
        tool = SwarmTool(parent_tools=tools)
        assert tool._parent_tools is tools


# ---------------------------------------------------------------------------
# Parallel execution (mock run_agent, verify gather)
# ---------------------------------------------------------------------------


class TestSwarmToolCall:
    @pytest.mark.asyncio
    async def test_call_two_tasks_parallel(self):
        """Two tasks run via asyncio.gather and both results appear."""
        tool = SwarmTool(parent_deps=MagicMock())
        results = [
            FakeAgentResult(result_text="Result A", agent_type="general", turns_used=2),
            FakeAgentResult(result_text="Result B", agent_type="coder", turns_used=3),
        ]

        call_count = 0

        async def mock_run_agent(**kwargs):
            nonlocal call_count
            idx = call_count
            call_count += 1
            return results[idx]

        with patch("duh.agents.run_agent", side_effect=mock_run_agent):
            result = await tool.call(
                {
                    "tasks": [
                        {"prompt": "Task A", "agent_type": "general"},
                        {"prompt": "Task B", "agent_type": "coder"},
                    ]
                },
                ctx(),
            )

        assert isinstance(result, ToolResult)
        assert result.is_error is False
        assert "Result A" in result.output
        assert "Result B" in result.output
        assert "Task 1/2" in result.output
        assert "Task 2/2" in result.output
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_call_single_task(self):
        """A single-task swarm works correctly."""
        tool = SwarmTool(parent_deps=MagicMock())
        fake = FakeAgentResult(result_text="Done", turns_used=1)

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            result = await tool.call(
                {"tasks": [{"prompt": "solo task"}]},
                ctx(),
            )

        assert result.is_error is False
        assert "Done" in result.output
        assert "Task 1/1" in result.output
        mock_run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_passes_model_and_agent_type(self):
        """Model and agent_type are forwarded to run_agent."""
        tool = SwarmTool(parent_deps=MagicMock())
        fake = FakeAgentResult(result_text="ok")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            await tool.call(
                {
                    "tasks": [
                        {"prompt": "x", "agent_type": "researcher", "model": "haiku"}
                    ]
                },
                ctx(),
            )

        _, kwargs = mock_run.call_args
        assert kwargs["agent_type"] == "researcher"
        assert kwargs["model"] == "haiku"

    @pytest.mark.asyncio
    async def test_call_defaults_agent_type_general(self):
        """Missing agent_type defaults to 'general'."""
        tool = SwarmTool(parent_deps=MagicMock())
        fake = FakeAgentResult(result_text="ok")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            await tool.call(
                {"tasks": [{"prompt": "x"}]},
                ctx(),
            )

        _, kwargs = mock_run.call_args
        assert kwargs["agent_type"] == "general"

    @pytest.mark.asyncio
    async def test_call_empty_tasks_returns_error(self):
        """Empty tasks list returns error."""
        tool = SwarmTool(parent_deps=MagicMock())
        result = await tool.call({"tasks": []}, ctx())
        assert result.is_error is True
        assert "no tasks" in result.output

    @pytest.mark.asyncio
    async def test_call_missing_tasks_returns_error(self):
        """Missing tasks key returns error."""
        tool = SwarmTool(parent_deps=MagicMock())
        result = await tool.call({}, ctx())
        assert result.is_error is True
        assert "no tasks" in result.output

    @pytest.mark.asyncio
    async def test_turns_reported_in_output(self):
        """Turns used appears in successful output."""
        tool = SwarmTool(parent_deps=MagicMock())
        fake = FakeAgentResult(result_text="done", turns_used=7)

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            result = await tool.call(
                {"tasks": [{"prompt": "x"}]},
                ctx(),
            )

        assert "7 turns" in result.output


# ---------------------------------------------------------------------------
# Partial failure handling
# ---------------------------------------------------------------------------


class TestSwarmToolPartialFailure:
    @pytest.mark.asyncio
    async def test_one_success_one_failure(self):
        """Mixed results: one succeeds, one fails. Overall is not error."""
        tool = SwarmTool(parent_deps=MagicMock())

        call_count = 0

        async def mock_run_agent(**kwargs):
            nonlocal call_count
            idx = call_count
            call_count += 1
            if idx == 0:
                return FakeAgentResult(result_text="good", turns_used=2)
            else:
                return FakeAgentResult(result_text="", error="agent crashed")

        with patch("duh.agents.run_agent", side_effect=mock_run_agent):
            result = await tool.call(
                {
                    "tasks": [
                        {"prompt": "ok task"},
                        {"prompt": "bad task"},
                    ]
                },
                ctx(),
            )

        # Partial failure: not overall error (some succeeded)
        assert result.is_error is False
        assert "good" in result.output
        assert "agent crashed" in result.output
        assert "OK" in result.output
        assert "ERROR" in result.output

    @pytest.mark.asyncio
    async def test_all_tasks_fail_is_error(self):
        """All tasks failing marks overall result as error."""
        tool = SwarmTool(parent_deps=MagicMock())

        async def mock_run_agent(**kwargs):
            return FakeAgentResult(result_text="", error="boom")

        with patch("duh.agents.run_agent", side_effect=mock_run_agent):
            result = await tool.call(
                {
                    "tasks": [
                        {"prompt": "bad1"},
                        {"prompt": "bad2"},
                    ]
                },
                ctx(),
            )

        assert result.is_error is True
        assert "boom" in result.output

    @pytest.mark.asyncio
    async def test_exception_in_run_agent(self):
        """Exception from run_agent is caught and reported."""
        tool = SwarmTool(parent_deps=MagicMock())

        async def mock_run_agent(**kwargs):
            raise RuntimeError("connection lost")

        with patch("duh.agents.run_agent", side_effect=mock_run_agent):
            result = await tool.call(
                {"tasks": [{"prompt": "x"}]},
                ctx(),
            )

        assert result.is_error is True
        assert "connection lost" in result.output


# ---------------------------------------------------------------------------
# No-deps guard
# ---------------------------------------------------------------------------


class TestSwarmToolNoDeps:
    @pytest.mark.asyncio
    async def test_call_without_deps_returns_error(self):
        """SwarmTool without parent_deps returns a clear error."""
        tool = SwarmTool()  # no deps
        result = await tool.call(
            {"tasks": [{"prompt": "hello"}]},
            ctx(),
        )
        assert result.is_error is True
        assert "no parent deps" in result.output


# ---------------------------------------------------------------------------
# Child tools exclude Agent and Swarm
# ---------------------------------------------------------------------------


class TestSwarmToolChildTools:
    def test_child_tools_excludes_agent_and_swarm(self):
        """Child tools list excludes both AgentTool and SwarmTool."""

        class FakeRead:
            name = "Read"

        class FakeBash:
            name = "Bash"

        class FakeAgent:
            name = "Agent"

        class FakeSwarm:
            name = "Swarm"

        tool = SwarmTool(
            parent_tools=[FakeRead(), FakeAgent(), FakeBash(), FakeSwarm()]
        )
        children = tool._child_tools()
        names = [getattr(t, "name", "") for t in children]
        assert "Agent" not in names
        assert "Swarm" not in names
        assert names == ["Read", "Bash"]

    def test_child_tools_empty_when_no_parent_tools(self):
        """No parent tools -> empty child tools."""
        tool = SwarmTool()
        assert tool._child_tools() == []

    def test_child_tools_all_excluded(self):
        """If only Agent and Swarm are in parent tools, child list is empty."""

        class FakeAgent:
            name = "Agent"

        class FakeSwarm:
            name = "Swarm"

        tool = SwarmTool(parent_tools=[FakeAgent(), FakeSwarm()])
        assert tool._child_tools() == []


# ---------------------------------------------------------------------------
# check_permissions
# ---------------------------------------------------------------------------


class TestSwarmToolPermissions:
    @pytest.mark.asyncio
    async def test_check_permissions_always_allowed(self):
        tool = SwarmTool(parent_deps=MagicMock())
        perm = await tool.check_permissions(
            {"tasks": [{"prompt": "anything"}]}, ctx()
        )
        assert perm == {"allowed": True}
