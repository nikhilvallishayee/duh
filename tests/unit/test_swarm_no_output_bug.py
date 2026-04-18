"""Regression test for the SwarmTool "(no output)" hallucination bug.

When a sub-agent completes 1 turn successfully but produces no `text_delta`
events (e.g. the provider streamed only an `assistant` message block),
`run_agent` returned an empty `result_text`, the swarm then formatted it as
"(no output)", and the parent model hallucinated sub-agent content.

This test file reproduces the specific scenario before the fix and locks
the behavior in after the fix:
    - sub-agent emits `assistant` event with text content (no `text_delta`)
    - `run_agent` must still extract that text into `result_text`
    - SwarmTool output must NOT contain "(no output)"
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from duh.agents import run_agent
from duh.kernel.messages import Message
from duh.kernel.tool import ToolContext
from duh.tools.swarm_tool import SwarmTool


def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


# ---------------------------------------------------------------------------
# Reproducer — the bug, reduced to its essence
# ---------------------------------------------------------------------------


class TestNoOutputBugReproducer:
    """Reproduce the live bug: assistant-only event, no text_delta."""

    @pytest.mark.asyncio
    async def test_run_agent_captures_assistant_only_response(self):
        """run_agent must accumulate text from `assistant` events, not just
        `text_delta`.

        Many provider adapters short-circuit when the response is text-only
        (Anthropic batches tokens, OpenAI ChatGPT's Responses API returns a
        completed response, the stub provider when used mid-test) and emit
        the `assistant` event before/without emitting incremental
        `text_delta` events in the way run_agent expects.
        """
        async def assistant_only(prompt, **kwargs):
            # Simulate a real provider that only yields the assistant event
            # (no text_delta events at all) — this is the failure mode.
            msg = Message(
                role="assistant",
                content=[{"type": "text", "text": "Hi — I finished the task."}],
                metadata={"stop_reason": "end_turn"},
            )
            yield {"type": "assistant", "message": msg}
            yield {"type": "done", "stop_reason": "end_turn", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = assistant_only
        mock_engine_cls.return_value = mock_engine

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            result = await run_agent(
                prompt="say hi",
                agent_type="general",
                deps=MagicMock(),
            )

        # Before the fix: result_text == "" → swarm shows "(no output)".
        # After the fix: result_text contains the assistant message text.
        assert result.result_text == "Hi — I finished the task.", (
            f"run_agent lost text from assistant event: {result.result_text!r}"
        )
        assert result.is_error is False
        assert result.turns_used == 1

    @pytest.mark.asyncio
    async def test_swarm_does_not_show_no_output_for_assistant_only(self):
        """End-to-end: SwarmTool output must contain real text, not the
        misleading '(no output)' placeholder that caused the parent to
        hallucinate results.
        """
        async def assistant_only(prompt, **kwargs):
            msg = Message(
                role="assistant",
                content=[
                    {"type": "text",
                     "text": "Researcher output: found three files."}
                ],
                metadata={"stop_reason": "end_turn"},
            )
            yield {"type": "assistant", "message": msg}
            yield {"type": "done", "stop_reason": "end_turn", "turns": 1}

        mock_engine_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.run = assistant_only
        mock_engine_cls.return_value = mock_engine

        tool = SwarmTool(parent_deps=MagicMock())

        with patch("duh.kernel.engine.Engine", mock_engine_cls):
            result = await tool.call(
                {
                    "tasks": [
                        {"prompt": "research things",
                         "agent_type": "researcher"}
                    ]
                },
                ctx(),
            )

        assert result.is_error is False
        assert "(no output)" not in result.output, (
            f"SwarmTool still shows the hallucination-inducing "
            f"(no output) placeholder: {result.output!r}"
        )
        assert "Researcher output: found three files." in result.output
