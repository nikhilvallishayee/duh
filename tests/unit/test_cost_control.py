"""Tests for cost control and budget enforcement.

Covers:
- --max-cost CLI flag parsing
- DUH_MAX_COST env var support
- Engine budget_remaining() method
- 80% budget warning event
- 100% budget exceeded event (session stop)
- cost_summary() includes budget info
- No budget events when max_cost is None
- Budget enforcement in fallback loop
- Config merging for max_cost
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator
from unittest.mock import patch

import pytest

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.kernel.tokens import format_cost


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_model(text: str = "Hello!"):
    """Return a model function that yields a successful assistant response."""
    async def model_fn(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": text}],
        )}
    return model_fn


def _big_model(text_length: int = 40000):
    """Return a model that yields a response with *text_length* chars.

    At ~4 chars/token, 40 000 chars ~ 10 000 tokens.
    """
    big_text = "x" * text_length

    async def model_fn(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": big_text}],
        )}
    return model_fn


async def _collect(engine: Engine, prompt: str) -> list[dict[str, Any]]:
    """Run a prompt and collect all events."""
    events: list[dict[str, Any]] = []
    async for e in engine.run(prompt):
        events.append(e)
    return events


# ---------------------------------------------------------------------------
# CLI flag parsing
# ---------------------------------------------------------------------------

class TestMaxCostCLIFlag:
    def test_max_cost_flag_parsed(self):
        """--max-cost should be accepted by the argument parser."""
        from duh.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["--max-cost", "5.00"])
        assert args.max_cost == 5.00

    def test_max_cost_flag_default_none(self):
        """--max-cost defaults to None when not specified."""
        from duh.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args([])
        assert args.max_cost is None

    def test_max_cost_flag_float(self):
        """--max-cost accepts arbitrary float values."""
        from duh.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["--max-cost", "0.50"])
        assert args.max_cost == 0.50


# ---------------------------------------------------------------------------
# EngineConfig
# ---------------------------------------------------------------------------

class TestEngineConfigMaxCost:
    def test_default_none(self):
        config = EngineConfig(model="test")
        assert config.max_cost is None

    def test_explicit_value(self):
        config = EngineConfig(model="test", max_cost=2.50)
        assert config.max_cost == 2.50


# ---------------------------------------------------------------------------
# Engine.budget_remaining
# ---------------------------------------------------------------------------

class TestBudgetRemaining:
    async def test_no_budget_returns_none(self):
        deps = Deps(call_model=_ok_model())
        engine = Engine(deps=deps, config=EngineConfig(model="claude-sonnet-4-6"))
        assert engine.budget_remaining() is None

    async def test_full_budget_at_start(self):
        deps = Deps(call_model=_ok_model())
        config = EngineConfig(model="claude-sonnet-4-6", max_cost=10.0)
        engine = Engine(deps=deps, config=config)
        remaining = engine.budget_remaining()
        assert remaining is not None
        assert remaining == 10.0  # no tokens consumed yet

    async def test_budget_decreases_after_run(self):
        deps = Deps(call_model=_ok_model("short reply"))
        config = EngineConfig(model="claude-sonnet-4-6", max_cost=10.0)
        engine = Engine(deps=deps, config=config)
        await _collect(engine, "hello")
        remaining = engine.budget_remaining()
        assert remaining is not None
        assert remaining < 10.0  # some cost incurred


# ---------------------------------------------------------------------------
# Budget warning at 80%
# ---------------------------------------------------------------------------

class TestBudgetWarning80:
    async def test_warning_at_80_pct(self):
        """When cost reaches 80% of max_cost, a budget_warning event is emitted."""
        # Use a tiny budget that will be exceeded by even a small response.
        # claude-sonnet-4-6 pricing: $3/1M input, $15/1M output
        # A 40_000-char response ~ 10_000 tokens output ~ $0.15
        deps = Deps(call_model=_big_model(40000))
        config = EngineConfig(model="claude-sonnet-4-6", max_cost=0.10)
        engine = Engine(deps=deps, config=config)

        events = await _collect(engine, "go")
        types = [e["type"] for e in events]
        assert "budget_warning" in types

        warning = next(e for e in events if e["type"] == "budget_warning")
        assert "80%" in warning["message"] or "Approaching" in warning["message"]
        assert warning["max_cost"] == 0.10

    async def test_80_warning_only_once(self):
        """The 80% warning should fire at most once per session."""
        deps = Deps(call_model=_big_model(40000))
        config = EngineConfig(model="claude-sonnet-4-6", max_cost=0.10)
        engine = Engine(deps=deps, config=config)

        events1 = await _collect(engine, "first")
        # Manually reset so we can run again (budget already exceeded,
        # but let's verify the 80% flag doesn't fire again).
        # Reset the exceeded state to test re-run:
        # Engine cost is already over, but _budget_warned_80 should be True.
        assert engine._budget_warned_80 is True

        # Run a second prompt (will also exceed).  The 80% warning should
        # NOT appear again because _budget_warned_80 is already True.
        events2 = await _collect(engine, "second")
        warnings = [e for e in events2 if e["type"] == "budget_warning"]
        assert len(warnings) == 0


# ---------------------------------------------------------------------------
# Budget exceeded at 100%
# ---------------------------------------------------------------------------

class TestBudgetExceeded:
    async def test_exceeded_event(self):
        """When cost >= max_cost, a budget_exceeded event is emitted."""
        deps = Deps(call_model=_big_model(40000))
        config = EngineConfig(model="claude-sonnet-4-6", max_cost=0.001)
        engine = Engine(deps=deps, config=config)

        events = await _collect(engine, "go")
        types = [e["type"] for e in events]
        assert "budget_exceeded" in types

        exceeded = next(e for e in events if e["type"] == "budget_exceeded")
        assert "Budget limit reached" in exceeded["message"]
        assert "Session stopped" in exceeded["message"]

    async def test_exceeded_stops_session(self):
        """After budget_exceeded, engine.run returns (no more events)."""
        deps = Deps(call_model=_big_model(40000))
        config = EngineConfig(model="claude-sonnet-4-6", max_cost=0.001)
        engine = Engine(deps=deps, config=config)

        # First run — will exceed
        events1 = await _collect(engine, "first")
        assert any(e["type"] == "budget_exceeded" for e in events1)

        # Second run — budget already exceeded, should stop immediately
        events2 = await _collect(engine, "second")
        # Should still yield session event, then done, then budget_exceeded
        exceeded = [e for e in events2 if e["type"] == "budget_exceeded"]
        assert len(exceeded) >= 1


# ---------------------------------------------------------------------------
# No budget events when max_cost is None
# ---------------------------------------------------------------------------

class TestNoBudgetWhenNone:
    async def test_no_budget_events_without_max_cost(self):
        """When max_cost is None, no budget events are emitted."""
        deps = Deps(call_model=_big_model(40000))
        config = EngineConfig(model="claude-sonnet-4-6")  # max_cost=None
        engine = Engine(deps=deps, config=config)

        events = await _collect(engine, "go")
        budget_events = [
            e for e in events
            if e.get("type") in ("budget_warning", "budget_exceeded")
        ]
        assert len(budget_events) == 0


# ---------------------------------------------------------------------------
# cost_summary includes budget info
# ---------------------------------------------------------------------------

class TestCostSummaryBudget:
    async def test_cost_summary_shows_remaining(self):
        deps = Deps(call_model=_ok_model())
        config = EngineConfig(model="claude-sonnet-4-6", max_cost=5.0)
        engine = Engine(deps=deps, config=config)

        summary = engine.cost_summary()
        assert "Budget remaining" in summary
        assert "$5.00" in summary or "$5.0000" in summary

    async def test_cost_summary_no_budget_line_without_max_cost(self):
        deps = Deps(call_model=_ok_model())
        config = EngineConfig(model="claude-sonnet-4-6")
        engine = Engine(deps=deps, config=config)

        summary = engine.cost_summary()
        assert "Budget remaining" not in summary


# ---------------------------------------------------------------------------
# DUH_MAX_COST env var
# ---------------------------------------------------------------------------

class TestDUHMaxCostEnv:
    def test_env_var_in_config(self):
        """DUH_MAX_COST env var should be merged into Config."""
        from duh.config import Config, _apply_env
        config = Config()
        with patch.dict(os.environ, {"DUH_MAX_COST": "3.50"}):
            _apply_env(config)
        assert config.max_cost == 3.50

    def test_env_var_invalid_ignored(self):
        """Non-numeric DUH_MAX_COST should not crash."""
        from duh.config import Config, _apply_env
        config = Config()
        with patch.dict(os.environ, {"DUH_MAX_COST": "not_a_number"}):
            _apply_env(config)
        assert config.max_cost is None

    def test_config_merge_max_cost(self):
        """Config._merge_into should handle max_cost from JSON config."""
        from duh.config import Config, _merge_into
        config = Config()
        _merge_into(config, {"max_cost": 7.25})
        assert config.max_cost == 7.25


# ---------------------------------------------------------------------------
# _check_budget unit tests
# ---------------------------------------------------------------------------

class TestCheckBudgetMethod:
    async def test_no_events_when_no_budget(self):
        deps = Deps(call_model=_ok_model())
        engine = Engine(deps=deps, config=EngineConfig(model="test"))
        events = engine._check_budget()
        assert events == []

    async def test_no_events_when_under_budget(self):
        deps = Deps(call_model=_ok_model())
        config = EngineConfig(model="test", max_cost=999.0)
        engine = Engine(deps=deps, config=config)
        events = engine._check_budget()
        assert events == []

    async def test_warning_and_exceeded_when_over(self):
        """When cost >= max_cost, both warning and exceeded events fire."""
        deps = Deps(call_model=_ok_model())
        config = EngineConfig(model="claude-sonnet-4-6", max_cost=0.0001)
        engine = Engine(deps=deps, config=config)
        # Simulate some token usage
        engine._total_input_tokens = 100000
        engine._total_output_tokens = 100000

        events = engine._check_budget()
        types = [e["type"] for e in events]
        assert "budget_warning" in types
        assert "budget_exceeded" in types

    async def test_zero_max_cost_no_events(self):
        """max_cost=0 should not produce budget events (guard clause)."""
        deps = Deps(call_model=_ok_model())
        config = EngineConfig(model="test", max_cost=0.0)
        engine = Engine(deps=deps, config=config)
        events = engine._check_budget()
        assert events == []
