"""Tests for generic sub-agent model tier resolution.

Covers :func:`duh.providers.registry.resolve_agent_tier` and its
integration with :func:`duh.agents.run_agent` / ``AgentTool`` / ``SwarmTool``.

The harness exposes generic tiers (``small`` / ``medium`` / ``large`` /
``inherit``) instead of Anthropic-specific aliases. Tiers are resolved
per-provider at call time against the parent's current model so a
Gemini-parent spawning a ``small`` child gets ``gemini-2.5-flash``, not
``haiku`` (which 404s everywhere outside Anthropic).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.providers.registry import (
    PROVIDER_TIER_MODELS,
    TIER_ALIASES,
    resolve_agent_tier,
)
from duh.kernel.tool import ToolContext
from duh.tools.agent_tool import AgentTool
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
# resolve_agent_tier — core behaviour
# ---------------------------------------------------------------------------


class TestResolveAgentTier:
    def test_small_on_anthropic_sonnet_parent(self):
        assert (
            resolve_agent_tier("small", "claude-sonnet-4-6") == "claude-haiku-4-5"
        )

    def test_large_on_gemini_bare_parent(self):
        """Bare form ``gemini-2.5-flash`` (no slash) still routes to gemini.

        ``large`` on Gemini = ``gemini-3.1-pro-preview`` (live-verified
        against v1beta/models on 2026-04-19).
        """
        assert resolve_agent_tier("large", "gemini-2.5-flash") == "gemini-3.1-pro-preview"

    def test_medium_on_groq_namespaced_parent(self):
        """Namespaced ``groq/llama-...`` parent routes to groq tier map."""
        assert (
            resolve_agent_tier("medium", "groq/llama-3.1-8b-instant")
            == "llama-3.3-70b-versatile"
        )

    def test_inherit_returns_parent(self):
        assert resolve_agent_tier("inherit", "any-model-name") == "any-model-name"

    def test_empty_tier_returns_parent(self):
        """Empty tier behaves like inherit."""
        assert resolve_agent_tier("", "any-model") == "any-model"

    def test_literal_model_pass_through(self):
        """A literal (non-tier) name is returned unchanged, regardless of parent."""
        assert (
            resolve_agent_tier("claude-haiku-4-5", "gemini-2.5-pro")
            == "claude-haiku-4-5"
        )

    def test_literal_openai_pass_through(self):
        assert resolve_agent_tier("gpt-4o", "claude-sonnet-4-6") == "gpt-4o"

    def test_unknown_provider_falls_back_to_parent(self):
        """When provider can't be inferred, tier falls back to parent_model."""
        # A bare name with no keyword hints → provider inference returns None
        # → fall back to parent_model per the documented contract.
        result = resolve_agent_tier("small", "totally-unknown-model-xyz")
        assert result == "totally-unknown-model-xyz"

    def test_small_on_openai_parent(self):
        assert resolve_agent_tier("small", "gpt-4o") == "gpt-4o-mini"

    def test_large_on_openai_parent(self):
        assert resolve_agent_tier("large", "gpt-4o") == "o1"

    # --- Structural guarantees ---

    def test_tier_aliases_contains_four_values(self):
        assert TIER_ALIASES == {"small", "medium", "large", "inherit"}

    def test_every_provider_has_all_three_tiers(self):
        """No half-populated tier maps — small/medium/large must all exist."""
        for provider, tier_map in PROVIDER_TIER_MODELS.items():
            for tier in ("small", "medium", "large"):
                assert tier in tier_map, f"{provider} missing {tier!r}"
                assert tier_map[tier], f"{provider}/{tier} is empty"


# ---------------------------------------------------------------------------
# AgentTool end-to-end — tier string + parent_model flows to run_agent
# ---------------------------------------------------------------------------


class TestAgentToolTierInvocation:
    @pytest.mark.asyncio
    async def test_small_tier_on_gemini_parent_passes_through(self):
        """AgentTool with Gemini parent + model='small' passes both to run_agent.

        The actual tier→concrete resolution happens inside run_agent, but we
        verify the parent_model + tier make it through unmolested.
        """
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
    async def test_parent_model_getter_callable(self):
        """A callable parent_model (for /model live switches) is invoked per call."""
        current = ["claude-sonnet-4-6"]
        tool = AgentTool(
            parent_deps=MagicMock(),
            parent_model=lambda: current[0],
        )
        fake = FakeAgentResult(result_text="ok")

        with patch("duh.agents.run_agent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = fake
            await tool.call({"prompt": "x", "model": "small"}, ctx())
            _, kwargs = mock_run.call_args
            assert kwargs["parent_model"] == "claude-sonnet-4-6"

            # Simulate /model switch.
            current[0] = "gemini-2.5-pro"
            await tool.call({"prompt": "y", "model": "small"}, ctx())
            _, kwargs = mock_run.call_args
            assert kwargs["parent_model"] == "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# SwarmTool — mixed tiers resolve per-provider
# ---------------------------------------------------------------------------


class TestSwarmToolMixedTiers:
    @pytest.mark.asyncio
    async def test_mixed_tiers_on_gemini_parent(self):
        """Swarm with [small, large] + Gemini parent hits both tier branches.

        We inspect the run_agent calls to confirm each task got its tier
        string and the Gemini parent_model.
        """
        tool = SwarmTool(
            parent_deps=MagicMock(), parent_model="gemini-2.5-pro"
        )

        seen_models: list[tuple[str, str]] = []

        async def mock_run_agent(**kwargs):
            seen_models.append((kwargs["model"], kwargs["parent_model"]))
            return FakeAgentResult(result_text=f"done:{kwargs['model']}")

        with patch("duh.agents.run_agent", side_effect=mock_run_agent):
            result = await tool.call(
                {
                    "tasks": [
                        {"prompt": "fast", "model": "small"},
                        {"prompt": "deep", "model": "large"},
                    ]
                },
                ctx(),
            )

        assert result.is_error is False
        assert ("small", "gemini-2.5-pro") in seen_models
        assert ("large", "gemini-2.5-pro") in seen_models


# ---------------------------------------------------------------------------
# run_agent — tier resolves to concrete model in EngineConfig
# ---------------------------------------------------------------------------


class TestRunAgentTierResolution:
    @pytest.mark.asyncio
    async def test_small_tier_anthropic_becomes_haiku_in_config(self):
        """End-to-end: run_agent with tier 'small' + Anthropic parent → haiku."""
        from duh.agents import run_agent

        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "ok"}
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
                parent_model="claude-sonnet-4-6",
                deps=MagicMock(),
            )

        _, kwargs = mock_engine_cls.call_args
        assert kwargs["config"].model == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_large_tier_gemini_becomes_pro_in_config(self):
        from duh.agents import run_agent

        async def fake_run(prompt, **kwargs):
            yield {"type": "done", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            await run_agent(
                prompt="deep",
                agent_type="planner",
                model="large",
                parent_model="gemini-2.5-flash",
                deps=MagicMock(),
            )

        _, kwargs = mock_engine_cls.call_args
        assert kwargs["config"].model == "gemini-3.1-pro-preview"
