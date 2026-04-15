"""Tests for multi-agent model selection.

Covers:
- AGENT_TYPE_DEFAULTS mapping
- _resolve_model() logic
- run_agent() model parameter propagation to EngineConfig
- AGENT_TOOL_SCHEMA model field
- AgentTool passing model through to run_agent
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.agents import (
    AGENT_TOOL_SCHEMA,
    AGENT_TYPE_DEFAULTS,
    _resolve_model,
    run_agent,
)
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
# AGENT_TYPE_DEFAULTS
# ---------------------------------------------------------------------------


class TestAgentTypeDefaults:
    def test_general_inherits(self):
        assert AGENT_TYPE_DEFAULTS["general"] == "inherit"

    def test_coder_uses_sonnet(self):
        assert AGENT_TYPE_DEFAULTS["coder"] == "sonnet"

    def test_researcher_uses_haiku(self):
        assert AGENT_TYPE_DEFAULTS["researcher"] == "haiku"

    def test_planner_uses_opus(self):
        assert AGENT_TYPE_DEFAULTS["planner"] == "opus"

    def test_covers_all_agent_types(self):
        from duh.agents import AGENT_TYPES
        for t in AGENT_TYPES:
            assert t in AGENT_TYPE_DEFAULTS, f"Missing default for {t!r}"

    def test_all_values_are_valid(self):
        valid = {"haiku", "sonnet", "opus", "inherit"}
        for agent_type, model in AGENT_TYPE_DEFAULTS.items():
            assert model in valid, f"{agent_type} has invalid default {model!r}"


# ---------------------------------------------------------------------------
# _resolve_model
# ---------------------------------------------------------------------------


class TestResolveModel:
    """Tests for _resolve_model(model, agent_type)."""

    # --- Explicit model takes priority ---

    def test_explicit_haiku(self):
        assert _resolve_model("haiku", "general") == "haiku"

    def test_explicit_sonnet(self):
        assert _resolve_model("sonnet", "planner") == "sonnet"

    def test_explicit_opus(self):
        assert _resolve_model("opus", "researcher") == "opus"

    def test_explicit_inherit_returns_empty(self):
        """'inherit' means use parent — resolved as empty string."""
        assert _resolve_model("inherit", "coder") == ""

    # --- Empty string falls back to agent type default ---

    def test_empty_general_inherits(self):
        assert _resolve_model("", "general") == ""

    def test_empty_coder_gets_sonnet(self):
        assert _resolve_model("", "coder") == "sonnet"

    def test_empty_researcher_gets_haiku(self):
        assert _resolve_model("", "researcher") == "haiku"

    def test_empty_planner_gets_opus(self):
        assert _resolve_model("", "planner") == "opus"

    # --- Unknown agent type with no explicit model ---

    def test_unknown_type_inherits(self):
        """Unknown agent type with no model defaults to inherit (empty)."""
        assert _resolve_model("", "unknown_type") == ""

    # --- Explicit model overrides type default ---

    def test_opus_overrides_coder_default(self):
        """Explicit 'opus' overrides coder's default 'sonnet'."""
        assert _resolve_model("opus", "coder") == "opus"

    def test_haiku_overrides_planner_default(self):
        """Explicit 'haiku' overrides planner's default 'opus'."""
        assert _resolve_model("haiku", "planner") == "haiku"


# ---------------------------------------------------------------------------
# AGENT_TOOL_SCHEMA — model field
# ---------------------------------------------------------------------------


class TestAgentToolSchemaModel:
    def test_schema_has_model_property(self):
        assert "model" in AGENT_TOOL_SCHEMA["properties"]

    def test_model_type_is_string(self):
        assert AGENT_TOOL_SCHEMA["properties"]["model"]["type"] == "string"

    def test_model_enum_values(self):
        enum = AGENT_TOOL_SCHEMA["properties"]["model"]["enum"]
        assert set(enum) == {"haiku", "sonnet", "opus", "inherit"}

    def test_model_has_description(self):
        desc = AGENT_TOOL_SCHEMA["properties"]["model"]["description"]
        assert isinstance(desc, str)
        assert len(desc) > 10

    def test_model_not_required(self):
        """model is optional — not in 'required'."""
        assert "model" not in AGENT_TOOL_SCHEMA["required"]


# ---------------------------------------------------------------------------
# run_agent() — model propagation to EngineConfig
# ---------------------------------------------------------------------------


class TestRunAgentModel:
    @pytest.mark.asyncio
    async def test_coder_default_model_sonnet(self):
        """Coder agent with no model gets 'sonnet' in EngineConfig."""
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            await run_agent(prompt="write code", agent_type="coder", deps=MagicMock())

        # Inspect the EngineConfig passed to Engine()
        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == "sonnet"

    @pytest.mark.asyncio
    async def test_researcher_default_model_haiku(self):
        """Researcher agent with no model gets 'haiku' in EngineConfig."""
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "found it"}
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            await run_agent(prompt="search", agent_type="researcher", deps=MagicMock())

        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == "haiku"

    @pytest.mark.asyncio
    async def test_planner_default_model_opus(self):
        """Planner agent with no model gets 'opus' in EngineConfig."""
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "plan ready"}
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            await run_agent(prompt="plan", agent_type="planner", deps=MagicMock())

        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == "opus"

    @pytest.mark.asyncio
    async def test_general_default_model_inherits(self):
        """General agent with no model gets '' (inherit) in EngineConfig."""
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            await run_agent(prompt="do it", agent_type="general", deps=MagicMock())

        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == ""

    @pytest.mark.asyncio
    async def test_explicit_model_overrides_default(self):
        """Explicit model='opus' overrides coder's default 'sonnet'."""
        async def fake_run(prompt, **kwargs):
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            await run_agent(
                prompt="write code",
                agent_type="coder",
                model="opus",
                deps=MagicMock(),
            )

        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == "opus"

    @pytest.mark.asyncio
    async def test_inherit_model_gives_empty(self):
        """model='inherit' resolves to '' in EngineConfig."""
        async def fake_run(prompt, **kwargs):
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            await run_agent(
                prompt="plan",
                agent_type="planner",
                model="inherit",
                deps=MagicMock(),
            )

        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == ""

    @pytest.mark.asyncio
    async def test_model_param_backward_compatible(self):
        """Calling run_agent without model= still works (no positional breakage)."""
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            result = await run_agent(prompt="hi", deps=MagicMock())

        assert result.result_text == "ok"


# ---------------------------------------------------------------------------
# AgentTool — model field in schema and call()
# ---------------------------------------------------------------------------


class TestAgentToolModelSchema:
    def test_input_schema_has_model(self):
        props = AgentTool.input_schema["properties"]
        assert "model" in props

    def test_model_enum(self):
        enum = AgentTool.input_schema["properties"]["model"]["enum"]
        assert set(enum) == {"haiku", "sonnet", "opus", "inherit"}

    def test_model_not_required(self):
        assert "model" not in AgentTool.input_schema["required"]


class TestAgentToolModelCall:
    @pytest.mark.asyncio
    async def test_call_passes_model_to_run_agent(self):
        """AgentTool forwards model from input to run_agent."""
        tool = AgentTool(parent_deps=MagicMock())
        fake = FakeAgentResult(result_text="done")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            await tool.call(
                {"prompt": "do it", "agent_type": "coder", "model": "opus"},
                ctx(),
            )

        mock_run.assert_awaited_once_with(
            prompt="do it",
            agent_type="coder",
            model="opus",
            deps=tool._parent_deps,
            tools=[],
        )

    @pytest.mark.asyncio
    async def test_call_passes_haiku(self):
        tool = AgentTool(parent_deps=MagicMock())
        fake = FakeAgentResult(result_text="ok")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            await tool.call(
                {"prompt": "search", "agent_type": "researcher", "model": "haiku"},
                ctx(),
            )

        _, kwargs = mock_run.call_args
        assert kwargs["model"] == "haiku"

    @pytest.mark.asyncio
    async def test_call_passes_inherit(self):
        tool = AgentTool(parent_deps=MagicMock())
        fake = FakeAgentResult(result_text="ok")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            await tool.call(
                {"prompt": "plan", "model": "inherit"},
                ctx(),
            )

        _, kwargs = mock_run.call_args
        assert kwargs["model"] == "inherit"

    @pytest.mark.asyncio
    async def test_call_no_model_passes_empty(self):
        """When model is not in input, empty string is passed."""
        tool = AgentTool(parent_deps=MagicMock())
        fake = FakeAgentResult(result_text="ok")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            await tool.call({"prompt": "do"}, ctx())

        _, kwargs = mock_run.call_args
        assert kwargs["model"] == ""

    @pytest.mark.asyncio
    async def test_call_with_model_returns_result(self):
        """Full round-trip: model param doesn't break result extraction."""
        tool = AgentTool(parent_deps=MagicMock())
        fake = FakeAgentResult(result_text="model-selected result")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            result = await tool.call(
                {"prompt": "test", "agent_type": "planner", "model": "opus"},
                ctx(),
            )

        assert isinstance(result, ToolResult)
        assert result.output == "model-selected result"
        assert result.is_error is False
