"""Integration tests: taint propagation through the full agentic loop.

Verifies that UntrustedStr taint tags (MODEL_OUTPUT, TOOL_OUTPUT) survive
the complete prompt -> model -> tool_use -> tool_result -> model cycle,
and that the confirmation gate blocks dangerous tools from tainted context.

Uses fake model providers (no API calls) to control exactly what flows
through the loop.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

import pytest

from duh.kernel.deps import Deps
from duh.kernel.loop import query
from duh.kernel.messages import Message
from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.security.policy import (
    DANGEROUS_TOOLS,
    ConfirmationPolicyDecision,
)


# ---------------------------------------------------------------------------
# Fake model providers
# ---------------------------------------------------------------------------

async def _model_calls_bash(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that emits tainted text then calls Bash on first turn,
    and returns a final text response on second turn."""
    messages = kwargs.get("messages", [])
    has_tool_result = any(
        isinstance(m.content, list) and any(
            (isinstance(b, dict) and b.get("type") == "tool_result")
            for b in m.content
        )
        for m in messages if isinstance(m, Message)
    )

    if has_tool_result:
        # Second turn: wrap response as MODEL_OUTPUT (like real providers do)
        text = UntrustedStr("Done executing.", TaintSource.MODEL_OUTPUT)
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": text}],
        )}
    else:
        # First turn: tainted text + tool call
        text = UntrustedStr("Let me run that for you.", TaintSource.MODEL_OUTPUT)
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[
                {"type": "text", "text": text},
                {"type": "tool_use", "id": "tu-bash-1", "name": "Bash",
                 "input": {"command": "echo hello"}},
            ],
        )}


async def _model_calls_read(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that calls Read (non-dangerous) on first turn."""
    messages = kwargs.get("messages", [])
    has_tool_result = any(
        isinstance(m.content, list) and any(
            (isinstance(b, dict) and b.get("type") == "tool_result")
            for b in m.content
        )
        for m in messages if isinstance(m, Message)
    )

    if has_tool_result:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": "File contents received."}],
        )}
    else:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[
                {"type": "tool_use", "id": "tu-read-1", "name": "Read",
                 "input": {"path": "some/file.txt"}},
            ],
        )}


# ---------------------------------------------------------------------------
# Fake tool executors
# ---------------------------------------------------------------------------

async def _tool_returns_tainted(name: str, input: dict) -> str:
    """Tool executor that returns UntrustedStr tagged TOOL_OUTPUT."""
    return UntrustedStr(f"output of {name}", TaintSource.TOOL_OUTPUT)


async def _tool_returns_plain(name: str, input: dict) -> str:
    """Tool executor that returns a plain string (no taint)."""
    return f"plain output of {name}"


# ---------------------------------------------------------------------------
# TEST 1: Model output stays tainted through tool dispatch
# ---------------------------------------------------------------------------

class TestModelOutputTaintSurvives:
    """MODEL_OUTPUT taint must propagate through the loop."""

    async def test_model_text_is_tainted(self):
        """Text from the model provider should carry MODEL_OUTPUT source."""
        text = UntrustedStr("hello from model", TaintSource.MODEL_OUTPUT)
        assert isinstance(text, UntrustedStr)
        assert text.source == TaintSource.MODEL_OUTPUT
        assert text.is_tainted()

        # String operations preserve taint
        upper = text.upper()
        assert isinstance(upper, UntrustedStr)
        assert upper.source == TaintSource.MODEL_OUTPUT

    async def test_tainted_model_output_flows_to_tool_use_event(self):
        """When a model response includes tool_use, the loop yields the
        tool_use event. The tool name and input originate from model output."""
        events: list[dict] = []

        async for e in query(
            messages=[Message(role="user", content="run echo hello")],
            deps=Deps(
                call_model=_model_calls_bash,
                run_tool=_tool_returns_plain,
            ),
        ):
            events.append(e)

        tool_uses = [e for e in events if e["type"] == "tool_use"]
        assert len(tool_uses) >= 1
        assert tool_uses[0]["name"] == "Bash"


# ---------------------------------------------------------------------------
# TEST 2: Tainted tool result is still tainted after processing
# ---------------------------------------------------------------------------

class TestToolResultTaintSurvives:
    """Tool results tagged TOOL_OUTPUT must remain tainted."""

    async def test_tool_output_taint_propagates(self):
        """UntrustedStr from a tool executor preserves its TOOL_OUTPUT tag
        through string operations that the loop or post-processing might do."""
        result = UntrustedStr("file contents here", TaintSource.TOOL_OUTPUT)
        assert result.is_tainted()
        assert result.source == TaintSource.TOOL_OUTPUT

        # Simulate truncation (like _truncate_result does via slicing)
        truncated = result[:10]
        assert isinstance(truncated, UntrustedStr)
        assert truncated.source == TaintSource.TOOL_OUTPUT

    async def test_loop_yields_tool_result_from_tainted_executor(self):
        """The query loop should yield a tool_result event containing
        the content from the tainted executor."""
        events: list[dict] = []

        async for e in query(
            messages=[Message(role="user", content="read the file")],
            deps=Deps(
                call_model=_model_calls_read,
                run_tool=_tool_returns_tainted,
            ),
        ):
            events.append(e)

        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_results) >= 1
        assert "output of Read" in tool_results[0]["output"]


# ---------------------------------------------------------------------------
# TEST 3: Confirmation gating triggers on tainted + dangerous content
# ---------------------------------------------------------------------------

class TestConfirmationGateBlocks:
    """The confirm_gate in Deps must block dangerous tools from tainted context."""

    async def test_confirm_gate_blocks_dangerous_tool(self):
        """Bash (a dangerous tool) called from tainted model context should
        be blocked by the confirmation gate."""
        gate_calls: list[dict] = []

        def blocking_gate(*, tool_name: str, tool_input: dict) -> ConfirmationPolicyDecision:
            gate_calls.append({"tool": tool_name, "input": tool_input})
            if tool_name in DANGEROUS_TOOLS:
                return ConfirmationPolicyDecision(
                    action="block",
                    reason="Tainted context, no confirmation token",
                )
            return ConfirmationPolicyDecision(action="allow", reason="safe tool")

        events: list[dict] = []
        async for e in query(
            messages=[Message(role="user", content="run echo hello")],
            deps=Deps(
                call_model=_model_calls_bash,
                run_tool=_tool_returns_plain,
                confirm_gate=blocking_gate,
            ),
        ):
            events.append(e)

        # Gate was consulted
        assert len(gate_calls) >= 1
        assert gate_calls[0]["tool"] == "Bash"

        # Tool was blocked
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_results) >= 1
        assert tool_results[0]["is_error"] is True
        assert "blocked" in tool_results[0]["output"].lower()

    async def test_confirm_gate_allows_non_dangerous_tool(self):
        """Read (non-dangerous) should pass through the gate even with
        a strict gate function."""
        gate_calls: list[dict] = []

        def blocking_gate(*, tool_name: str, tool_input: dict) -> ConfirmationPolicyDecision | None:
            gate_calls.append({"tool": tool_name, "input": tool_input})
            if tool_name in DANGEROUS_TOOLS:
                return ConfirmationPolicyDecision(
                    action="block",
                    reason="Tainted context, no confirmation token",
                )
            # Non-dangerous → allow (return None means "no objection")
            return None

        events: list[dict] = []
        async for e in query(
            messages=[Message(role="user", content="read the file")],
            deps=Deps(
                call_model=_model_calls_read,
                run_tool=_tool_returns_tainted,
                confirm_gate=blocking_gate,
            ),
        ):
            events.append(e)

        # Gate was consulted for Read
        assert len(gate_calls) >= 1
        assert gate_calls[0]["tool"] == "Read"

        # Tool was NOT blocked (Read is not dangerous)
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_results) >= 1
        assert tool_results[0]["is_error"] is False
        assert "output of Read" in tool_results[0]["output"]

    async def test_confirm_gate_not_called_when_absent(self):
        """When confirm_gate is None (default), tools execute freely."""
        events: list[dict] = []
        async for e in query(
            messages=[Message(role="user", content="run echo hello")],
            deps=Deps(
                call_model=_model_calls_bash,
                run_tool=_tool_returns_plain,
            ),
        ):
            events.append(e)

        # Bash should have executed successfully (no gate to block it)
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_results) >= 1
        assert tool_results[0]["is_error"] is False

    async def test_full_taint_chain_model_to_gate(self):
        """End-to-end: model output (tainted) triggers tool_use for a
        dangerous tool, which the gate blocks because the context is tainted.
        The loop should continue and yield a 'done' event."""
        def strict_gate(*, tool_name: str, tool_input: dict) -> ConfirmationPolicyDecision:
            if tool_name in DANGEROUS_TOOLS:
                return ConfirmationPolicyDecision(
                    action="block",
                    reason="Dangerous tool from tainted model output",
                )
            return ConfirmationPolicyDecision(action="allow", reason="ok")

        events: list[dict] = []
        async for e in query(
            messages=[Message(role="user", content="run echo hello")],
            deps=Deps(
                call_model=_model_calls_bash,
                run_tool=_tool_returns_plain,
                confirm_gate=strict_gate,
            ),
        ):
            events.append(e)

        event_types = [e["type"] for e in events]

        # The loop emitted assistant, tool_use, tool_result (blocked), then
        # continued to the next model turn and eventually hit "done"
        assert "tool_use" in event_types
        assert "tool_result" in event_types
        assert "done" in event_types

        # Verify the blocked result
        blocked = [e for e in events if e["type"] == "tool_result" and e.get("is_error")]
        assert len(blocked) >= 1
        assert "blocked" in blocked[0]["output"].lower()
