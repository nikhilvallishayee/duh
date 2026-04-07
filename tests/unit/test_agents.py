"""Tests for multi-agent support (ADR-012)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from duh.agents import (
    AGENT_PROMPTS,
    AGENT_TOOL_SCHEMA,
    AGENT_TYPES,
    AgentDef,
    AgentResult,
    run_agent,
)


# ---------------------------------------------------------------------------
# AgentDef
# ---------------------------------------------------------------------------


class TestAgentDef:
    def test_from_type_general(self):
        d = AgentDef.from_type("general")
        assert d.agent_type == "general"
        assert d.system_prompt == AGENT_PROMPTS["general"]
        assert d.tools is None

    def test_from_type_coder(self):
        d = AgentDef.from_type("coder")
        assert d.agent_type == "coder"
        assert "code" in d.system_prompt.lower()

    def test_from_type_researcher(self):
        d = AgentDef.from_type("researcher")
        assert d.agent_type == "researcher"
        assert "research" in d.system_prompt.lower()

    def test_from_type_planner(self):
        d = AgentDef.from_type("planner")
        assert d.agent_type == "planner"
        assert "plan" in d.system_prompt.lower()

    def test_from_type_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown agent type"):
            AgentDef.from_type("nonexistent")

    def test_from_type_error_lists_available(self):
        with pytest.raises(ValueError, match="general"):
            AgentDef.from_type("xyz")


# ---------------------------------------------------------------------------
# AgentResult
# ---------------------------------------------------------------------------


class TestAgentResult:
    def test_is_error_false_when_no_error(self):
        r = AgentResult(agent_type="general", result_text="ok")
        assert r.is_error is False

    def test_is_error_true_when_error(self):
        r = AgentResult(agent_type="general", result_text="", error="boom")
        assert r.is_error is True


# ---------------------------------------------------------------------------
# AGENT_TOOL_SCHEMA
# ---------------------------------------------------------------------------


class TestAgentToolSchema:
    def test_has_required_fields(self):
        assert AGENT_TOOL_SCHEMA["type"] == "object"
        assert "prompt" in AGENT_TOOL_SCHEMA["properties"]
        assert "agent_type" in AGENT_TOOL_SCHEMA["properties"]
        assert "prompt" in AGENT_TOOL_SCHEMA["required"]

    def test_agent_type_enum_matches_prompts(self):
        enum_values = AGENT_TOOL_SCHEMA["properties"]["agent_type"]["enum"]
        assert set(enum_values) == set(AGENT_PROMPTS.keys())


# ---------------------------------------------------------------------------
# AGENT_TYPES
# ---------------------------------------------------------------------------


class TestAgentTypes:
    def test_all_types_have_prompts(self):
        for t in AGENT_TYPES:
            assert t in AGENT_PROMPTS

    def test_prompts_are_nonempty(self):
        for t, prompt in AGENT_PROMPTS.items():
            assert len(prompt) > 20, f"{t} prompt is too short"

    def test_four_types(self):
        assert len(AGENT_TYPES) == 4
        assert "general" in AGENT_TYPES
        assert "coder" in AGENT_TYPES
        assert "researcher" in AGENT_TYPES
        assert "planner" in AGENT_TYPES


# ---------------------------------------------------------------------------
# run_agent
# ---------------------------------------------------------------------------


class TestRunAgent:
    @pytest.mark.asyncio
    async def test_run_agent_returns_result(self):
        """run_agent creates a child Engine and returns AgentResult."""
        from unittest.mock import patch, MagicMock

        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "Hello from agent"}
            yield {"type": "done", "turns": 1, "stop_reason": "end_turn"}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            result = await run_agent(
                prompt="do something",
                agent_type="general",
                deps=MagicMock(),
            )

        assert isinstance(result, AgentResult)
        assert result.agent_type == "general"
        assert result.result_text == "Hello from agent"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_run_agent_captures_error_event(self):
        from unittest.mock import patch, MagicMock

        async def fake_run(prompt, **kwargs):
            yield {"type": "error", "error": "model failed"}
            yield {"type": "done", "turns": 0}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            result = await run_agent(
                prompt="do something",
                agent_type="coder",
                deps=MagicMock(),
            )

        assert result.is_error is True
        assert "model failed" in result.error

    @pytest.mark.asyncio
    async def test_run_agent_captures_exception(self):
        from unittest.mock import patch, MagicMock

        async def fake_run(prompt, **kwargs):
            raise RuntimeError("engine exploded")
            yield  # pragma: no cover

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            result = await run_agent(
                prompt="do something",
                agent_type="general",
                deps=MagicMock(),
            )

        assert result.is_error is True
        assert "engine exploded" in result.error

    @pytest.mark.asyncio
    async def test_run_agent_invalid_type(self):
        with pytest.raises(ValueError, match="Unknown agent type"):
            await run_agent(
                prompt="do something",
                agent_type="nonexistent",
                deps=None,
            )
