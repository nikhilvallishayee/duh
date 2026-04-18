"""Comprehensive tests for swarm / run_agent output capture.

Complements `test_swarm_no_output_bug.py` (the focused reproducer). This
file locks in the reconciled text-capture contract:

- `text_delta` events are accumulated into the result
- `assistant` events' text are captured (and preferred over deltas)
- Parent model selection propagates to the EngineConfig
- Default `max_turns` in `run_agent` allows multi-turn work
- Empty output from an otherwise-successful run is surfaced as an error
- Multiple swarm sub-agents each produce distinct outputs
- A failing sub-agent does not poison sibling results

Every test verifies real unit behavior — no coverage-only tests.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from duh.agents import AgentResult, run_agent
from duh.kernel.messages import Message
from duh.kernel.tool import ToolContext
from duh.tools.swarm_tool import SwarmTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


def _patch_engine_with(run_fn):
    """Return a ``patch(...)`` context manager that replaces Engine with a
    mock whose ``run`` method is ``run_fn``.

    Usage::

        with _patch_engine_with(my_async_gen_fn):
            await run_agent(...)
    """
    mock_engine_cls = MagicMock()
    mock_engine = MagicMock()
    mock_engine.run = run_fn
    mock_engine_cls.return_value = mock_engine
    return patch("duh.kernel.engine.Engine", mock_engine_cls), mock_engine_cls


# ---------------------------------------------------------------------------
# run_agent text-capture contract
# ---------------------------------------------------------------------------


class TestRunAgentTextCapture:
    """Cover every way a provider can deliver text to run_agent."""

    @pytest.mark.asyncio
    async def test_text_only_via_deltas(self):
        """A provider that emits only text_delta events still populates
        result_text.
        """
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "Hello "}
            yield {"type": "text_delta", "text": "world"}
            yield {"type": "done", "turns": 1, "stop_reason": "end_turn"}

        patcher, _ = _patch_engine_with(fake_run)
        with patcher:
            result = await run_agent(
                prompt="say hi", agent_type="general", deps=MagicMock(),
            )
        assert result.result_text == "Hello world"
        assert result.is_error is False
        assert result.turns_used == 1

    @pytest.mark.asyncio
    async def test_text_only_via_assistant_event(self):
        """A provider that emits only the final assistant event (no deltas)
        still populates result_text — the case that caused the live bug.
        """
        async def fake_run(prompt, **kwargs):
            msg = Message(
                role="assistant",
                content=[{"type": "text", "text": "Final answer here."}],
                metadata={"stop_reason": "end_turn"},
            )
            yield {"type": "assistant", "message": msg}
            yield {"type": "done", "turns": 1, "stop_reason": "end_turn"}

        patcher, _ = _patch_engine_with(fake_run)
        with patcher:
            result = await run_agent(
                prompt="do it", agent_type="general", deps=MagicMock(),
            )
        assert result.result_text == "Final answer here."
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_assistant_event_preferred_over_deltas(self):
        """When both are present the final assistant event is authoritative
        (it's the reconciled message; deltas can be partial/interleaved).
        """
        async def fake_run(prompt, **kwargs):
            # deltas may have been partial/interrupted
            yield {"type": "text_delta", "text": "Hell"}
            msg = Message(
                role="assistant",
                content=[{"type": "text", "text": "Hello, complete answer."}],
                metadata={"stop_reason": "end_turn"},
            )
            yield {"type": "assistant", "message": msg}
            yield {"type": "done", "turns": 1, "stop_reason": "end_turn"}

        patcher, _ = _patch_engine_with(fake_run)
        with patcher:
            result = await run_agent(
                prompt="hi", agent_type="general", deps=MagicMock(),
            )
        # The complete assistant message wins, not the truncated delta.
        assert result.result_text == "Hello, complete answer."

    @pytest.mark.asyncio
    async def test_tool_use_then_final_text(self):
        """Multi-turn run: intermediate tool-using assistant, then final
        text assistant. Only the final (text) message should be captured.
        """
        async def fake_run(prompt, **kwargs):
            # Turn 1 — assistant issues a tool_use (no user-facing text).
            intermediate = Message(
                role="assistant",
                content=[{
                    "type": "tool_use", "id": "tu_1",
                    "name": "Read", "input": {"path": "/etc/hosts"},
                }],
                metadata={"stop_reason": "tool_use"},
            )
            yield {"type": "assistant", "message": intermediate}
            yield {"type": "tool_use", "id": "tu_1",
                   "name": "Read", "input": {"path": "/etc/hosts"}}
            yield {"type": "tool_result", "tool_use_id": "tu_1",
                   "output": "127.0.0.1 localhost", "is_error": False}
            # Turn 2 — final assistant text response.
            final = Message(
                role="assistant",
                content=[{"type": "text",
                          "text": "The hosts file starts with 127.0.0.1."}],
                metadata={"stop_reason": "end_turn"},
            )
            yield {"type": "assistant", "message": final}
            yield {"type": "done", "turns": 2, "stop_reason": "end_turn"}

        patcher, _ = _patch_engine_with(fake_run)
        with patcher:
            result = await run_agent(
                prompt="read hosts", agent_type="coder", deps=MagicMock(),
            )
        assert "127.0.0.1" in result.result_text
        assert result.turns_used == 2
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_empty_success_surfaced_as_error(self):
        """A provider that yields neither text_delta nor assistant text
        (no errors, just silence) must NOT silently succeed with empty
        output — that was the bug.
        """
        async def fake_run(prompt, **kwargs):
            yield {"type": "done", "turns": 1, "stop_reason": "end_turn"}

        patcher, _ = _patch_engine_with(fake_run)
        with patcher:
            result = await run_agent(
                prompt="hi", agent_type="general", deps=MagicMock(),
            )
        assert result.is_error is True
        assert "without producing output" in result.error
        assert result.result_text == ""

    @pytest.mark.asyncio
    async def test_assistant_empty_content_surfaced_as_error(self):
        """Assistant event with empty text (e.g. only a non-text block)
        still counts as no output.
        """
        async def fake_run(prompt, **kwargs):
            msg = Message(
                role="assistant",
                content=[{"type": "text", "text": ""}],
                metadata={"stop_reason": "end_turn"},
            )
            yield {"type": "assistant", "message": msg}
            yield {"type": "done", "turns": 1, "stop_reason": "end_turn"}

        patcher, _ = _patch_engine_with(fake_run)
        with patcher:
            result = await run_agent(
                prompt="hi", agent_type="general", deps=MagicMock(),
            )
        assert result.is_error is True
        assert "without producing output" in result.error


# ---------------------------------------------------------------------------
# Model propagation (parent model → sub-agent EngineConfig)
# ---------------------------------------------------------------------------


class TestParentModelPropagation:
    @pytest.mark.asyncio
    async def test_general_inherits_parent_model(self):
        """general agent type resolves to '' — Engine falls back to parent's
        configured model at call_model time.
        """
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "turns": 1}

        patcher, mock_cls = _patch_engine_with(fake_run)
        with patcher:
            await run_agent(
                prompt="task", agent_type="general", deps=MagicMock(),
            )
        _, kwargs = mock_cls.call_args
        # '' means inherit — Engine/query will use deps.call_model's default.
        assert kwargs["config"].model == ""

    @pytest.mark.asyncio
    async def test_inherit_model_parameter_resolves_empty(self):
        """Passing model='inherit' explicitly resolves to '' (inherit parent).
        This is what SwarmTool sends when the user leaves the default.
        """
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "turns": 1}

        patcher, mock_cls = _patch_engine_with(fake_run)
        with patcher:
            await run_agent(
                prompt="task", agent_type="coder", model="inherit",
                deps=MagicMock(),
            )
        _, kwargs = mock_cls.call_args
        assert kwargs["config"].model == ""


# ---------------------------------------------------------------------------
# max_turns default — avoid one-shot failures
# ---------------------------------------------------------------------------


class TestMaxTurnsDefault:
    """The original bug-hunt hypothesis was ``max_turns=1``. Guard against
    any future regression that would restrict sub-agents to a single turn.
    """

    def test_run_agent_default_max_turns_allows_multi_turn(self):
        """run_agent's own default max_turns must allow tool-using agents
        to make progress (>=5 turns realistically, >1 minimally).
        """
        import inspect
        sig = inspect.signature(run_agent)
        default = sig.parameters["max_turns"].default
        assert isinstance(default, int)
        assert default > 1, (
            f"run_agent default max_turns={default} is too restrictive — "
            "sub-agents need multiple turns for tool calls."
        )
        # Sanity: 50 matches the AgentDef default and is the project's norm.
        assert default >= 5

    @pytest.mark.asyncio
    async def test_run_agent_passes_effective_max_turns_to_engine(self):
        """run_agent forwards min(max_turns, agent_def.max_turns) to the
        engine config — verifying the arithmetic.
        """
        async def fake_run(prompt, **kwargs):
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "turns": 1}

        patcher, mock_cls = _patch_engine_with(fake_run)
        with patcher:
            await run_agent(
                prompt="task", agent_type="general", max_turns=10,
                deps=MagicMock(),
            )
        _, kwargs = mock_cls.call_args
        # agent_def default is 50; min(10, 50) == 10.
        assert kwargs["config"].max_turns == 10


# ---------------------------------------------------------------------------
# End-to-end SwarmTool: multiple sub-agents, distinct outputs, partial failure
# ---------------------------------------------------------------------------


class TestSwarmEndToEnd:
    @pytest.mark.asyncio
    async def test_multiple_agents_distinct_outputs(self):
        """Three sub-agents each emit their own assistant-text response via
        the real run_agent path — all three outputs must appear in order.
        """
        responses = [
            "Researcher: 3 files found",
            "Coder: patched 2 lines",
            "Reviewer: no issues",
        ]

        call_counter = {"n": 0}

        async def fake_run_factory(prompt, **kwargs):
            idx = call_counter["n"]
            call_counter["n"] += 1
            text = responses[idx]
            msg = Message(
                role="assistant",
                content=[{"type": "text", "text": text}],
                metadata={"stop_reason": "end_turn"},
            )
            yield {"type": "assistant", "message": msg}
            yield {"type": "done", "turns": 1, "stop_reason": "end_turn"}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = fake_run_factory
        mock_engine_cls.return_value = mock_engine

        tool = SwarmTool(parent_deps=MagicMock())

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            result = await tool.call(
                {
                    "tasks": [
                        {"prompt": "research", "agent_type": "researcher"},
                        {"prompt": "code", "agent_type": "coder"},
                        {"prompt": "review", "agent_type": "reviewer"},
                    ]
                },
                ctx(),
            )

        assert result.is_error is False
        for r in responses:
            assert r in result.output, (
                f"Missing {r!r} in swarm output:\n{result.output}"
            )
        assert "(no output)" not in result.output
        assert "Task 1/3" in result.output
        assert "Task 3/3" in result.output

    @pytest.mark.asyncio
    async def test_exception_in_one_agent_does_not_kill_others(self):
        """asyncio.gather(return_exceptions=True) means one agent crashing
        is caught and the others' real outputs still appear.
        """
        call_counter = {"n": 0}

        async def mixed_run(prompt, **kwargs):
            idx = call_counter["n"]
            call_counter["n"] += 1
            if idx == 1:
                # Middle sub-agent raises before yielding anything.
                raise RuntimeError("connection reset by peer")
            msg = Message(
                role="assistant",
                content=[{"type": "text",
                          "text": f"sibling {idx} ok"}],
                metadata={"stop_reason": "end_turn"},
            )
            yield {"type": "assistant", "message": msg}
            yield {"type": "done", "turns": 1, "stop_reason": "end_turn"}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = mixed_run
        mock_engine_cls.return_value = mock_engine

        tool = SwarmTool(parent_deps=MagicMock())

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            result = await tool.call(
                {
                    "tasks": [
                        {"prompt": "a", "agent_type": "general"},
                        {"prompt": "b", "agent_type": "general"},
                        {"prompt": "c", "agent_type": "general"},
                    ]
                },
                ctx(),
            )

        # Siblings 0 and 2 succeed; sibling 1 shows the error.
        assert "sibling 0 ok" in result.output
        assert "sibling 2 ok" in result.output
        assert "connection reset by peer" in result.output
        assert "Task 2/3" in result.output
        # At least one succeeded, so overall is not an error.
        assert result.is_error is False


# ---------------------------------------------------------------------------
# Empty-output defense applies to the swarm layer
# ---------------------------------------------------------------------------


class TestEmptyOutputSurfacedAtSwarm:
    @pytest.mark.asyncio
    async def test_silently_empty_run_appears_as_error_in_swarm(self):
        """A sub-agent that succeeds with zero output must be reported as
        an ERROR in the swarm output (so the parent model cannot mistake
        it for success and hallucinate content).
        """
        async def silent(prompt, **kwargs):
            yield {"type": "done", "turns": 1, "stop_reason": "end_turn"}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = silent
        mock_engine_cls.return_value = mock_engine

        tool = SwarmTool(parent_deps=MagicMock())

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            result = await tool.call(
                {"tasks": [{"prompt": "silent"}]},
                ctx(),
            )

        assert "ERROR" in result.output
        assert "without producing output" in result.output
        assert "(no output)" not in result.output
