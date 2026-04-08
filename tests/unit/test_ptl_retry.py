"""Tests for prompt-too-long retry in Engine."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from duh.kernel.engine import Engine, EngineConfig, _is_ptl_error, MAX_PTL_RETRIES
from duh.kernel.deps import Deps
from duh.kernel.messages import Message


def test_ptl_error_detection():
    assert _is_ptl_error("prompt is too long: 200000 tokens > 100000 maximum")
    assert _is_ptl_error("PromptTooLong")
    assert _is_ptl_error("prompt_too_long")
    assert _is_ptl_error("context length exceeded")
    assert not _is_ptl_error("rate_limit_exceeded")
    assert not _is_ptl_error("invalid_api_key")


def test_max_ptl_retries_constant():
    assert MAX_PTL_RETRIES == 3


@pytest.mark.asyncio
async def test_engine_ptl_triggers_compact_and_retry():
    """When a PTL error occurs, engine should compact and retry."""
    call_count = 0

    async def mock_call(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("prompt is too long: 200000 tokens > 100000 maximum")
        yield {"type": "assistant", "message": Message(role="assistant", content="ok")}
        yield {"type": "done", "stop_reason": "end_turn"}

    compact_called = False
    original_messages = None

    async def mock_compact(messages, token_limit=0):
        nonlocal compact_called, original_messages
        compact_called = True
        original_messages = len(messages)
        # Return a shortened version
        return messages[-2:] if len(messages) > 2 else messages

    deps = Deps(
        call_model=mock_call,
        compact=mock_compact,
    )
    engine = Engine(deps=deps, config=EngineConfig(model="test"))

    events = []
    async for event in engine.run("hello"):
        events.append(event)

    assert compact_called
    assert call_count == 2  # first fails, second succeeds
    assert any(e.get("type") == "done" for e in events)
