"""Tests for multi-agent model selection.

Covers:
- AGENT_TYPE_DEFAULTS mapping (all ``inherit`` after tier-aware refactor)
- _resolve_model() logic with parent_model + tier resolution
- run_agent() model parameter propagation to EngineConfig
- AGENT_TOOL_SCHEMA model field (small/medium/large/inherit enum)
- AgentTool passing model + parent_model through to run_agent
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
# AGENT_TYPE_DEFAULTS — all types inherit after tier-aware refactor
# ---------------------------------------------------------------------------


class TestAgentTypeDefaults:
    def test_general_inherits(self):
        assert AGENT_TYPE_DEFAULTS["general"] == "inherit"

    def test_coder_inherits(self):
        assert AGENT_TYPE_DEFAULTS["coder"] == "inherit"

    def test_researcher_inherits(self):
        assert AGENT_TYPE_DEFAULTS["researcher"] == "inherit"

    def test_planner_inherits(self):
        assert AGENT_TYPE_DEFAULTS["planner"] == "inherit"

    def test_covers_all_agent_types(self):
        from duh.agents import AGENT_TYPES
        for t in AGENT_TYPES:
            assert t in AGENT_TYPE_DEFAULTS, f"Missing default for {t!r}"

    def test_all_values_are_valid(self):
        """Every default is either 'inherit' or a real tier alias."""
        valid = {"small", "medium", "large", "inherit"}
        for agent_type, model in AGENT_TYPE_DEFAULTS.items():
            assert model in valid, f"{agent_type} has invalid default {model!r}"


# ---------------------------------------------------------------------------
# _resolve_model
# ---------------------------------------------------------------------------


class TestResolveModel:
    """Tests for ``_resolve_model(model, agent_type, parent_model)``."""

    # --- Tier aliases resolved against parent provider ---

    def test_small_on_anthropic_parent(self):
        assert _resolve_model("small", "general", "claude-sonnet-4-6") == "claude-haiku-4-5"

    def test_large_on_gemini_parent(self):
        # ``large`` on Gemini = 3.1-pro-preview (live-verified 2026-04-19).
        assert _resolve_model("large", "general", "gemini-2.5-flash") == "gemini-3.1-pro-preview"

    def test_medium_on_groq_parent(self):
        assert (
            _resolve_model("medium", "general", "groq/llama-3.1-8b-instant")
            == "llama-3.3-70b-versatile"
        )

    # --- 'inherit' returns parent_model unchanged ---

    def test_explicit_inherit_returns_parent(self):
        assert _resolve_model("inherit", "coder", "gpt-4o") == "gpt-4o"

    def test_explicit_inherit_empty_parent(self):
        """'inherit' with no parent → empty string."""
        assert _resolve_model("inherit", "coder", "") == ""

    # --- Empty string falls back to agent type default (also inherit) ---

    def test_empty_with_parent(self):
        assert _resolve_model("", "general", "claude-opus-4-6") == "claude-opus-4-6"

    def test_empty_no_parent(self):
        """Empty model + empty parent → empty string (provider default)."""
        assert _resolve_model("", "general", "") == ""

    # --- Unknown agent type falls back to inherit semantics ---

    def test_unknown_type_with_parent(self):
        """Unknown agent type with no explicit model → parent_model."""
        assert _resolve_model("", "unknown_type", "gpt-4o") == "gpt-4o"

    # --- Literal model names pass through ---

    def test_literal_model_pass_through(self):
        """Literal (non-tier) names are preserved for backwards compat."""
        assert (
            _resolve_model("claude-haiku-4-5", "coder", "gemini-2.5-pro")
            == "claude-haiku-4-5"
        )


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
        assert set(enum) == {"small", "medium", "large", "inherit"}

    def test_model_default_is_inherit(self):
        assert AGENT_TOOL_SCHEMA["properties"]["model"]["default"] == "inherit"

    def test_model_has_description(self):
        desc = AGENT_TOOL_SCHEMA["properties"]["model"]["description"]
        assert isinstance(desc, str)
        assert len(desc) > 10
        # Description mentions the per-provider resolution intent.
        assert "provider" in desc.lower() or "tier" in desc.lower()

    def test_model_not_required(self):
        """model is optional — not in 'required'."""
        assert "model" not in AGENT_TOOL_SCHEMA["required"]


# ---------------------------------------------------------------------------
# run_agent() — model propagation to EngineConfig
# ---------------------------------------------------------------------------


class TestRunAgentModel:
    @pytest.mark.asyncio
    async def test_coder_default_inherits_parent(self):
        """Coder agent with no model uses parent_model in EngineConfig."""
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            await run_agent(
                prompt="write code",
                agent_type="coder",
                parent_model="claude-sonnet-4-6",
                deps=MagicMock(),
            )

        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_researcher_tier_resolves_on_gemini_parent(self):
        """Researcher with model='small' + Gemini parent → gemini-2.5-flash."""
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "found it"}
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            await run_agent(
                prompt="search",
                agent_type="researcher",
                model="small",
                parent_model="gemini-2.5-pro",
                deps=MagicMock(),
            )

        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_planner_tier_resolves_on_anthropic_parent(self):
        """Planner with model='large' + Anthropic parent → claude-opus-4-6."""
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "plan ready"}
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            await run_agent(
                prompt="plan",
                agent_type="planner",
                model="large",
                parent_model="claude-haiku-4-5",
                deps=MagicMock(),
            )

        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_general_default_model_inherits(self):
        """General agent with no model + no parent gets '' in EngineConfig."""
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
    async def test_explicit_large_overrides_inherit_default(self):
        """Explicit model='large' overrides the coder default 'inherit'."""
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
                model="large",
                parent_model="claude-sonnet-4-6",
                deps=MagicMock(),
            )

        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_inherit_model_returns_parent(self):
        """model='inherit' with parent_model='gpt-4o' → 'gpt-4o'."""
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
                parent_model="gpt-4o",
                deps=MagicMock(),
            )

        _, kwargs = mock_engine_cls.call_args
        config = kwargs["config"]
        assert config.model == "gpt-4o"

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
        assert set(enum) == {"small", "medium", "large", "inherit"}

    def test_model_default(self):
        assert AgentTool.input_schema["properties"]["model"]["default"] == "inherit"

    def test_model_not_required(self):
        assert "model" not in AgentTool.input_schema["required"]


class TestAgentToolModelCall:
    @pytest.mark.asyncio
    async def test_call_passes_model_and_parent_to_run_agent(self):
        """AgentTool forwards tier + parent_model from input to run_agent."""
        tool = AgentTool(
            parent_deps=MagicMock(), parent_model="claude-sonnet-4-6"
        )
        fake = FakeAgentResult(result_text="done")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            await tool.call(
                {"prompt": "do it", "agent_type": "coder", "model": "large"},
                ctx(),
            )

        mock_run.assert_awaited_once_with(
            prompt="do it",
            agent_type="coder",
            model="large",
            parent_model="claude-sonnet-4-6",
            deps=tool._parent_deps,
            tools=[],
        )

    @pytest.mark.asyncio
    async def test_call_passes_small_tier(self):
        tool = AgentTool(
            parent_deps=MagicMock(), parent_model="gemini-2.5-pro"
        )
        fake = FakeAgentResult(result_text="ok")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            await tool.call(
                {"prompt": "search", "agent_type": "researcher", "model": "small"},
                ctx(),
            )

        _, kwargs = mock_run.call_args
        assert kwargs["model"] == "small"
        assert kwargs["parent_model"] == "gemini-2.5-pro"

    @pytest.mark.asyncio
    async def test_call_passes_inherit(self):
        tool = AgentTool(parent_deps=MagicMock(), parent_model="gpt-4o")
        fake = FakeAgentResult(result_text="ok")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            await tool.call(
                {"prompt": "plan", "model": "inherit"},
                ctx(),
            )

        _, kwargs = mock_run.call_args
        assert kwargs["model"] == "inherit"
        assert kwargs["parent_model"] == "gpt-4o"

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
        tool = AgentTool(
            parent_deps=MagicMock(), parent_model="claude-sonnet-4-6"
        )
        fake = FakeAgentResult(result_text="model-selected result")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            result = await tool.call(
                {"prompt": "test", "agent_type": "planner", "model": "large"},
                ctx(),
            )

        assert isinstance(result, ToolResult)
        assert result.output == "model-selected result"
        assert result.is_error is False
