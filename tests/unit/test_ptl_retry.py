"""Tests for prompt-too-long retry in Engine."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from duh.kernel.engine import Engine, EngineConfig, _is_ptl_error, MAX_PTL_RETRIES, _PTL_COMPACTION_TARGETS
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


# ---------------------------------------------------------------------------
# ADR-031 gap fix: progressive compaction targets
# ---------------------------------------------------------------------------

def test_ptl_compaction_targets_are_progressive():
    """_PTL_COMPACTION_TARGETS must follow the 70/50/30 progressive sequence."""
    assert _PTL_COMPACTION_TARGETS == [0.70, 0.50, 0.30]


@pytest.mark.asyncio
async def test_ptl_retry_uses_progressive_targets():
    """Each successive PTL retry must compact to a progressively smaller target ratio."""
    call_count = 0
    compact_targets: list[int] = []

    # Simulate a model that always fails with PTL except on the third attempt
    async def mock_call(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("prompt is too long: exceeded maximum context length")
        yield {"type": "assistant", "message": Message(role="assistant", content="ok")}
        yield {"type": "done", "stop_reason": "end_turn"}

    async def mock_compact(messages, token_limit=0):
        compact_targets.append(token_limit)
        return messages[-1:] if messages else messages

    deps = Deps(call_model=mock_call, compact=mock_compact)
    engine = Engine(deps=deps, config=EngineConfig(model="test"))

    events = []
    async for event in engine.run("hello"):
        events.append(event)

    # Two PTL retries → two compaction calls with decreasing targets
    assert len(compact_targets) >= 2, f"Expected at least 2 compaction calls, got {compact_targets}"

    # The first retry should use 70% target, second should use 50% (smaller than 70%)
    # We check relative ordering: each target must be <= the previous
    ptl_compact_targets = compact_targets[-len(compact_targets):]  # all from PTL path
    for i in range(1, len(ptl_compact_targets)):
        assert ptl_compact_targets[i] <= ptl_compact_targets[i - 1], (
            f"PTL compaction target should decrease on each retry, "
            f"but got {ptl_compact_targets}"
        )

    assert any(e.get("type") == "done" for e in events)
