"""Tests for duh.kernel.plan_mode — two-phase planning and execution."""

from __future__ import annotations

from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock

import pytest

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.kernel.plan_mode import (
    EXECUTE_PROMPT_PREFIX,
    PLAN_PROMPT_PREFIX,
    PlanMode,
    PlanState,
    PlanStep,
    _parse_steps,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_model_fn(response_text: str):
    """Create a fake call_model that returns a fixed response."""
    async def call_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        msg = Message(
            role="assistant",
            content=[{"type": "text", "text": response_text}],
        )
        yield {"type": "text_delta", "text": response_text}
        yield {"type": "assistant", "message": msg}
        yield {"type": "done", "stop_reason": "end_turn"}
    return call_model


def _make_engine(response_text: str = "1. First step\n2. Second step") -> Engine:
    deps = Deps(call_model=_make_model_fn(response_text))
    config = EngineConfig(model="test-model")
    return Engine(deps=deps, config=config)


async def _collect_events(gen) -> list[dict[str, Any]]:
    """Collect all events from an async generator."""
    events = []
    async for event in gen:
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Step parsing
# ---------------------------------------------------------------------------

class TestParseSteps:
    def test_numbered_dot(self):
        text = "1. Do thing A\n2. Do thing B\n3. Do thing C"
        steps = _parse_steps(text)
        assert len(steps) == 3
        assert steps[0].number == 1
        assert steps[0].description == "Do thing A"
        assert steps[2].number == 3
        assert steps[2].description == "Do thing C"

    def test_numbered_paren(self):
        text = "1) First\n2) Second"
        steps = _parse_steps(text)
        assert len(steps) == 2
        assert steps[0].description == "First"
        assert steps[1].description == "Second"

    def test_mixed_with_prose(self):
        text = (
            "Here is the plan:\n\n"
            "1. Analyze the code\n"
            "2. Refactor module A\n"
            "Some extra text here\n"
            "3. Write tests\n"
        )
        steps = _parse_steps(text)
        assert len(steps) == 3

    def test_empty_text(self):
        assert _parse_steps("") == []

    def test_no_numbered_lines(self):
        assert _parse_steps("Just a paragraph with no steps.") == []


# ---------------------------------------------------------------------------
# PlanMode state lifecycle
# ---------------------------------------------------------------------------

class TestPlanModeState:
    def test_initial_state(self):
        engine = _make_engine()
        pm = PlanMode(engine)
        assert pm.state == PlanState.EMPTY
        assert pm.steps == []
        assert pm.description == ""

    async def test_plan_transitions_to_proposed(self):
        engine = _make_engine("1. Step one\n2. Step two")
        pm = PlanMode(engine)

        await _collect_events(pm.plan("refactor auth"))

        assert pm.state == PlanState.PROPOSED
        assert len(pm.steps) == 2
        assert pm.description == "refactor auth"

    async def test_execute_transitions_to_done(self):
        engine = _make_engine("1. Step one\n2. Step two")
        pm = PlanMode(engine)

        await _collect_events(pm.plan("refactor auth"))
        assert pm.state == PlanState.PROPOSED

        await _collect_events(pm.execute())
        assert pm.state == PlanState.DONE
        assert all(s.done for s in pm.steps)

    def test_clear_resets(self):
        engine = _make_engine()
        pm = PlanMode(engine)
        # Manually set some state
        pm._state = PlanState.PROPOSED
        pm._steps = [PlanStep(number=1, description="x")]
        pm._plan_description = "test"

        pm.clear()
        assert pm.state == PlanState.EMPTY
        assert pm.steps == []
        assert pm.description == ""


# ---------------------------------------------------------------------------
# Planning phase (tool_choice enforcement)
# ---------------------------------------------------------------------------

class TestPlanPhase:
    async def test_tool_choice_none_during_planning(self):
        """During planning, tool_choice must be set to 'none'."""
        captured_tool_choice = []

        async def spy_model(**kwargs):
            captured_tool_choice.append(kwargs.get("tool_choice"))
            msg = Message(
                role="assistant",
                content=[{"type": "text", "text": "1. Step one"}],
            )
            yield {"type": "assistant", "message": msg}
            yield {"type": "done", "stop_reason": "end_turn"}

        deps = Deps(call_model=spy_model)
        config = EngineConfig(model="test-model", tool_choice="auto")
        engine = Engine(deps=deps, config=config)
        pm = PlanMode(engine)

        await _collect_events(pm.plan("do something"))

        # The model should have been called with tool_choice="none"
        assert captured_tool_choice == ["none"]
        # After planning, tool_choice should be restored
        assert engine._config.tool_choice == "auto"

    async def test_tool_choice_restored_on_error(self):
        """tool_choice must be restored even if planning fails."""
        async def failing_model(**kwargs):
            raise RuntimeError("boom")
            yield  # noqa: unreachable — make it a generator

        deps = Deps(call_model=failing_model)
        config = EngineConfig(model="test-model", tool_choice="auto")
        engine = Engine(deps=deps, config=config)
        pm = PlanMode(engine)

        await _collect_events(pm.plan("do something"))

        # tool_choice must be restored despite the error
        assert engine._config.tool_choice == "auto"

    async def test_plan_yields_events(self):
        engine = _make_engine("1. Analyze\n2. Implement")
        pm = PlanMode(engine)

        events = await _collect_events(pm.plan("build feature"))
        types = [e.get("type") for e in events]
        assert "text_delta" in types


# ---------------------------------------------------------------------------
# Execution phase
# ---------------------------------------------------------------------------

class TestExecutePhase:
    async def test_execute_requires_proposed_state(self):
        engine = _make_engine()
        pm = PlanMode(engine)

        with pytest.raises(ValueError, match="EMPTY"):
            await _collect_events(pm.execute())

    async def test_execute_sends_plan_as_prompt(self):
        """Execution prompt should contain the plan steps."""
        captured_messages: list[Any] = []

        async def capture_model(**kwargs):
            captured_messages.extend(kwargs.get("messages", []))
            msg = Message(
                role="assistant",
                content=[{"type": "text", "text": "Done!"}],
            )
            yield {"type": "assistant", "message": msg}
            yield {"type": "done", "stop_reason": "end_turn"}

        deps = Deps(call_model=capture_model)
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))
        pm = PlanMode(engine)

        await _collect_events(pm.plan("build it"))
        captured_messages.clear()

        await _collect_events(pm.execute())

        # The last user message should contain the execution prompt
        last_user = [m for m in captured_messages if m.role == "user"]
        assert len(last_user) >= 1
        prompt_text = last_user[-1].text
        assert EXECUTE_PROMPT_PREFIX in prompt_text or "1." in prompt_text

    async def test_execute_marks_steps_done(self):
        engine = _make_engine("1. First\n2. Second\n3. Third")
        pm = PlanMode(engine)

        await _collect_events(pm.plan("do stuff"))
        assert not any(s.done for s in pm.steps)

        await _collect_events(pm.execute())
        assert all(s.done for s in pm.steps)


# ---------------------------------------------------------------------------
# format_plan
# ---------------------------------------------------------------------------

class TestFormatPlan:
    def test_empty_plan(self):
        engine = _make_engine()
        pm = PlanMode(engine)
        assert pm.format_plan() == "No plan."

    async def test_proposed_plan_format(self):
        engine = _make_engine("1. Analyze code\n2. Write tests")
        pm = PlanMode(engine)
        await _collect_events(pm.plan("improve tests"))

        text = pm.format_plan()
        assert "improve tests" in text
        assert "[ ] 1." in text
        assert "[ ] 2." in text

    async def test_done_plan_format(self):
        engine = _make_engine("1. Analyze code\n2. Write tests")
        pm = PlanMode(engine)
        await _collect_events(pm.plan("improve tests"))
        await _collect_events(pm.execute())

        text = pm.format_plan()
        assert "[x] 1." in text
        assert "[x] 2." in text


# ---------------------------------------------------------------------------
# REPL /plan slash command integration
# ---------------------------------------------------------------------------

class TestSlashPlan:
    """Test /plan subcommands via _handle_slash."""

    def _make_plan_mode(self) -> PlanMode:
        engine = _make_engine()
        return PlanMode(engine)

    def test_plan_show_empty(self, capsys):
        from duh.cli.repl import _handle_slash
        engine = _make_engine()
        deps = Deps(call_model=AsyncMock())
        pm = self._make_plan_mode()

        keep, model = _handle_slash(
            "/plan show", engine, "m", deps, plan_mode=pm,
        )
        assert keep is True
        assert model == "m"
        captured = capsys.readouterr()
        assert "No plan" in captured.out

    def test_plan_clear(self, capsys):
        from duh.cli.repl import _handle_slash
        engine = _make_engine()
        deps = Deps(call_model=AsyncMock())
        pm = self._make_plan_mode()
        pm._state = PlanState.PROPOSED
        pm._steps = [PlanStep(number=1, description="x")]

        keep, model = _handle_slash(
            "/plan clear", engine, "m", deps, plan_mode=pm,
        )
        assert keep is True
        assert pm.state == PlanState.EMPTY
        assert pm.steps == []
        captured = capsys.readouterr()
        assert "cleared" in captured.out.lower()

    def test_plan_bare_shows_usage(self, capsys):
        from duh.cli.repl import _handle_slash
        engine = _make_engine()
        deps = Deps(call_model=AsyncMock())
        pm = self._make_plan_mode()

        keep, model = _handle_slash(
            "/plan", engine, "m", deps, plan_mode=pm,
        )
        assert keep is True
        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_plan_description_returns_signal(self):
        from duh.cli.repl import _handle_slash
        engine = _make_engine()
        deps = Deps(call_model=AsyncMock())
        pm = self._make_plan_mode()

        keep, model = _handle_slash(
            "/plan refactor the auth module", engine, "m", deps,
            plan_mode=pm,
        )
        assert keep is True
        assert model.startswith("\x00plan\x00")
        assert "refactor the auth module" in model

    def test_plan_not_available(self, capsys):
        from duh.cli.repl import _handle_slash
        engine = _make_engine()
        deps = Deps(call_model=AsyncMock())

        keep, model = _handle_slash(
            "/plan show", engine, "m", deps, plan_mode=None,
        )
        assert keep is True
        captured = capsys.readouterr()
        assert "not available" in captured.out.lower()

    def test_plan_in_slash_commands(self):
        from duh.cli.repl import SLASH_COMMANDS
        assert "/plan" in SLASH_COMMANDS
