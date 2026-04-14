"""Tests for duh.kernel.loop — the core agentic loop.

These tests use fake deps (no real API calls) to verify the loop's
control flow: single turn, multi-turn tool use, error handling,
max turns, approval denied.
"""

import asyncio
from typing import Any, AsyncGenerator

import pytest

from duh.kernel.deps import Deps
from duh.kernel.loop import query
from duh.kernel.messages import Message, UserMessage


# ---------------------------------------------------------------------------
# Fake model providers for testing
# ---------------------------------------------------------------------------

async def simple_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that returns a simple text response."""
    yield {"type": "assistant", "message": Message(
        role="assistant",
        content=[{"type": "text", "text": "Hello!"}],
    )}


async def tool_use_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that calls a tool on first turn, responds on second."""
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
            content=[{"type": "text", "text": "The file says hello."}],
        )}
    else:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[
                {"type": "text", "text": "Let me read that."},
                {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"path": "test.txt"}},
            ],
        )}


async def multi_tool_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that calls two tools simultaneously."""
    messages = kwargs.get("messages", [])
    has_results = any(
        isinstance(m.content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in m.content
        )
        for m in messages if isinstance(m, Message)
    )

    if has_results:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": "Both done."}],
        )}
    else:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[
                {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"path": "a.txt"}},
                {"type": "tool_use", "id": "tu2", "name": "Read", "input": {"path": "b.txt"}},
            ],
        )}


async def error_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that raises an error."""
    raise RuntimeError("API connection failed")
    yield  # make it a generator  # noqa


async def streaming_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    """Model that streams text deltas before the assistant message."""
    yield {"type": "text_delta", "text": "Hel"}
    yield {"type": "text_delta", "text": "lo!"}
    yield {"type": "assistant", "message": Message(
        role="assistant",
        content=[{"type": "text", "text": "Hello!"}],
    )}


# ---------------------------------------------------------------------------
# Fake tool executor
# ---------------------------------------------------------------------------

async def fake_run_tool(name: str, input: dict) -> str:
    """Simple tool executor that returns canned responses."""
    if name == "Read":
        return f"contents of {input.get('path', '?')}"
    if name == "Fail":
        raise RuntimeError("tool crashed")
    return f"ran {name}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSimpleTurn:
    async def test_single_turn_text_response(self):
        deps = Deps(call_model=simple_model)
        events = []
        async for e in query(
            messages=[Message(role="user", content="hi")],
            deps=deps,
        ):
            events.append(e)

        types = [e["type"] for e in events]
        assert "assistant" in types
        assert "done" in types
        assert events[-1]["stop_reason"] == "end_turn"

    async def test_streaming_deltas(self):
        deps = Deps(call_model=streaming_model)
        events = []
        async for e in query(
            messages=[Message(role="user", content="hi")],
            deps=deps,
        ):
            events.append(e)

        deltas = [e for e in events if e["type"] == "text_delta"]
        assert len(deltas) == 2
        assert deltas[0]["text"] == "Hel"
        assert deltas[1]["text"] == "lo!"


class TestToolUse:
    async def test_single_tool_call(self):
        deps = Deps(call_model=tool_use_model, run_tool=fake_run_tool)
        events = []
        async for e in query(
            messages=[Message(role="user", content="read test.txt")],
            deps=deps,
        ):
            events.append(e)

        types = [e["type"] for e in events]
        assert "tool_use" in types
        assert "tool_result" in types
        # Should have two assistant messages (tool_use + final response)
        assistants = [e for e in events if e["type"] == "assistant"]
        assert len(assistants) == 2

        # Final response should reference the tool result
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_results) == 1
        assert "contents of test.txt" in tool_results[0]["output"]

    async def test_parallel_tool_calls(self):
        deps = Deps(call_model=multi_tool_model, run_tool=fake_run_tool)
        events = []
        async for e in query(
            messages=[Message(role="user", content="read both")],
            deps=deps,
        ):
            events.append(e)

        tool_uses = [e for e in events if e["type"] == "tool_use"]
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_uses) == 2
        assert len(tool_results) == 2
        assert tool_uses[0]["name"] == "Read"
        assert tool_uses[1]["name"] == "Read"

    async def test_tool_error_caught(self):
        async def fail_tool(name, input):
            raise RuntimeError("disk full")

        deps = Deps(call_model=tool_use_model, run_tool=fail_tool)
        events = []
        async for e in query(
            messages=[Message(role="user", content="read")],
            deps=deps,
        ):
            events.append(e)

        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["is_error"] is True
        assert "disk full" in results[0]["output"]

    async def test_no_tool_executor(self):
        deps = Deps(call_model=tool_use_model)  # no run_tool
        events = []
        async for e in query(
            messages=[Message(role="user", content="read")],
            deps=deps,
        ):
            events.append(e)

        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["is_error"] is True
        assert "No tool executor" in results[0]["output"]


class TestApproval:
    async def test_approval_denied(self):
        async def deny_all(name, input):
            return {"allowed": False, "reason": "not allowed in test"}

        deps = Deps(
            call_model=tool_use_model,
            run_tool=fake_run_tool,
            approve=deny_all,
        )
        events = []
        async for e in query(
            messages=[Message(role="user", content="read")],
            deps=deps,
        ):
            events.append(e)

        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["is_error"] is True
        assert "denied" in results[0]["output"].lower()

    async def test_approval_allowed(self):
        async def allow_all(name, input):
            return {"allowed": True}

        deps = Deps(
            call_model=tool_use_model,
            run_tool=fake_run_tool,
            approve=allow_all,
        )
        events = []
        async for e in query(
            messages=[Message(role="user", content="read")],
            deps=deps,
        ):
            events.append(e)

        results = [e for e in events if e["type"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["is_error"] is False


class TestErrorHandling:
    async def test_model_error(self):
        deps = Deps(call_model=error_model)
        events = []
        async for e in query(
            messages=[Message(role="user", content="hi")],
            deps=deps,
        ):
            events.append(e)

        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert "API connection failed" in errors[0]["error"]

    async def test_no_model_configured(self):
        deps = Deps()  # no call_model
        events = []
        async for e in query(
            messages=[Message(role="user", content="hi")],
            deps=deps,
        ):
            events.append(e)

        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert "No model provider" in errors[0]["error"]


class TestMaxTurns:
    async def test_max_turns_enforced(self):
        """A model that always calls tools should stop at max_turns."""
        call_count = [0]

        async def infinite_tool_model(**kwargs):
            call_count[0] += 1
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "tool_use", "id": f"tu{call_count[0]}", "name": "Echo", "input": {"text": "loop"}}],
            )}

        deps = Deps(call_model=infinite_tool_model, run_tool=fake_run_tool)
        events = []
        async for e in query(
            messages=[Message(role="user", content="loop forever")],
            deps=deps,
            max_turns=3,
        ):
            events.append(e)

        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["stop_reason"] == "max_turns"
        assert done[0]["turns"] == 3


class TestPassthrough:
    async def test_system_prompt_passed(self):
        """Verify system_prompt reaches the model."""
        received_kwargs = {}

        async def capture_model(**kwargs):
            received_kwargs.update(kwargs)
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok"
            )}

        deps = Deps(call_model=capture_model)
        async for _ in query(
            messages=[Message(role="user", content="hi")],
            system_prompt="You are a pirate",
            deps=deps,
        ):
            pass

        assert received_kwargs["system_prompt"] == "You are a pirate"

    async def test_model_name_passed(self):
        received_kwargs = {}

        async def capture_model(**kwargs):
            received_kwargs.update(kwargs)
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok"
            )}

        deps = Deps(call_model=capture_model)
        async for _ in query(
            messages=[Message(role="user", content="hi")],
            deps=deps,
            model="claude-opus-4-6",
        ):
            pass

        assert received_kwargs["model"] == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# Confirmation gate tests (7.2.7)
# ---------------------------------------------------------------------------

def _make_engine():
    """Test helper — create a minimal Engine with a fake model."""
    from duh.kernel.deps import Deps
    from duh.kernel.engine import Engine
    deps = Deps(call_model=simple_model)
    return Engine(deps=deps)


def test_loop_blocks_tainted_dangerous_tool() -> None:
    """A Bash tool_use originating from MODEL_OUTPUT context must be blocked."""
    from duh.kernel.untrusted import TaintSource, UntrustedStr

    engine = _make_engine()
    tainted_msg = UntrustedStr("run rm -rf /", TaintSource.MODEL_OUTPUT)
    result = engine._check_confirmation_gate(
        tool="Bash",
        input_obj={"command": "rm -rf /"},
        chain=[tainted_msg],
        token=None,
    )
    assert result.action == "block"
