"""Tests for the decomposition of ``Engine.run()`` (CQ-2, issue #17).

The original ``Engine.run()`` was a 393-line method with cyclomatic
complexity 35. Issue #17 split it into focused helpers:

* ``_auto_compact``            — context-window management
* ``_run_with_ptl_retry``      — primary query loop with progressive
                                 compaction on prompt-too-long errors
* ``_run_fallback``            — fallback model execution
* ``_process_query_events``    — shared event handling between primary
                                 and fallback paths

These tests verify each helper preserves the legacy behaviour and that
the primary + fallback paths produce the same event sequence for
identical input.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

import pytest

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig, _TurnState
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ok_assistant(text: str = "ok") -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": Message(
            role="assistant",
            content=[{"type": "text", "text": text}],
        ),
    }


def _done() -> dict[str, Any]:
    return {"type": "done", "stop_reason": "end_turn"}


def _make_ok_model(text: str = "ok"):
    """A model that yields a simple successful turn."""

    async def model_fn(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        yield _ok_assistant(text)
        yield _done()

    return model_fn


# ---------------------------------------------------------------------------
# _auto_compact
# ---------------------------------------------------------------------------


class TestAutoCompactThreshold:
    """``_auto_compact`` runs only when the 80% threshold is exceeded."""

    @pytest.mark.asyncio
    async def test_no_compact_under_threshold(self):
        """When the input estimate is well under 80% of the limit, the
        compactor is never called."""
        compact_calls: list[int] = []

        async def fake_compact(messages, token_limit=0):
            compact_calls.append(token_limit)
            return messages

        engine = Engine(
            deps=Deps(call_model=_make_ok_model(), compact=fake_compact),
            config=EngineConfig(model="claude-sonnet-4-6"),
        )
        # 1 token is far below the 80% threshold of any real model.
        await engine._auto_compact(
            input_estimate=1,
            effective_model="claude-sonnet-4-6",
            compact_fn=fake_compact,
        )
        assert compact_calls == []

    @pytest.mark.asyncio
    async def test_compact_runs_when_threshold_exceeded(self):
        """When the estimate exceeds the threshold, the compactor runs and
        analytics + cache invalidation happen."""
        compact_calls: list[int] = []

        async def fake_compact(messages, token_limit=0):
            compact_calls.append(token_limit)
            # Return a small subset to simulate freed context.
            return messages[-1:] if messages else messages

        engine = Engine(
            deps=Deps(call_model=_make_ok_model(), compact=fake_compact),
            config=EngineConfig(model="claude-sonnet-4-6"),
        )
        # Seed history with a couple of messages so compact has something
        # to operate on.
        engine._messages.append(Message(role="user", content="hi"))
        engine._messages.append(Message(role="assistant", content="hello"))
        engine._rebuild_token_cache("claude-sonnet-4-6")

        # Use a deliberately huge estimate that always exceeds 80% of any
        # supported model's context window.
        huge = 10 ** 9
        await engine._auto_compact(
            input_estimate=huge,
            effective_model="claude-sonnet-4-6",
            compact_fn=fake_compact,
        )
        assert len(compact_calls) == 1
        # The compactor was called with a token_limit that is the 80%
        # threshold of the model's context limit (a positive int).
        assert compact_calls[0] > 0
        # Compaction was recorded in analytics under the "auto" kind.
        assert engine.compact_stats.total_compactions >= 1


# ---------------------------------------------------------------------------
# _run_with_ptl_retry — progressive compaction on PTL errors
# ---------------------------------------------------------------------------


class TestRunWithPtlRetry:
    """``_run_with_ptl_retry`` retries with progressively smaller targets."""

    @pytest.mark.asyncio
    async def test_progressive_compaction_targets(self):
        """Two consecutive PTL errors must call the compactor with
        decreasing token_limit targets (70% → 50% → 30%)."""
        calls = 0
        targets: list[int] = []

        async def model_fn(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            nonlocal calls
            calls += 1
            if calls < 3:
                # Match the PTL trigger pattern.
                raise Exception(
                    "prompt is too long: exceeds maximum context length",
                )
            yield _ok_assistant("recovered")
            yield _done()

        async def fake_compact(messages, token_limit=0):
            targets.append(token_limit)
            return messages[-1:] if messages else messages

        engine = Engine(
            deps=Deps(call_model=model_fn, compact=fake_compact),
            config=EngineConfig(model="claude-sonnet-4-6"),
        )

        events: list[dict[str, Any]] = []
        async for event in engine.run("hello"):
            events.append(event)

        # Two retries → at least two compact calls, monotonically
        # non-increasing token_limit (the progressive 70→50→30 ratios).
        assert len(targets) >= 2
        assert all(targets[i] <= targets[i - 1] for i in range(1, len(targets)))
        # The third attempt yielded a successful turn → done event present.
        assert any(e.get("type") == "done" for e in events)
        # Final assistant message reached the caller.
        assistant = [e for e in events if e.get("type") == "assistant"]
        assert assistant and assistant[-1]["message"].text == "recovered"

    @pytest.mark.asyncio
    async def test_no_retry_without_compact_fn(self):
        """When ``deps.compact`` is None, the engine still uses the
        ``AdaptiveCompactor`` shim. We just verify the run completes
        normally for a happy path."""
        engine = Engine(
            deps=Deps(call_model=_make_ok_model("plain")),
            config=EngineConfig(model="claude-sonnet-4-6"),
        )
        events = [e async for e in engine.run("hi")]
        assert any(e.get("type") == "done" for e in events)


# ---------------------------------------------------------------------------
# _run_fallback — fallback model is used when primary fails
# ---------------------------------------------------------------------------


class TestRunFallback:
    """``_run_fallback`` switches to the fallback model on overload errors."""

    @pytest.mark.asyncio
    async def test_fallback_used_when_primary_overloaded(self):
        """An overload error on the primary model routes to the fallback."""
        calls: list[str] = []

        async def model_fn(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            calls.append(kwargs.get("model", ""))
            if kwargs.get("model") == "fallback-model":
                yield _ok_assistant("fallback-ok")
                yield _done()
                return
            raise Exception("API is overloaded")
            yield {}  # pragma: no cover

        engine = Engine(
            deps=Deps(call_model=model_fn),
            config=EngineConfig(
                model="primary-model",
                fallback_model="fallback-model",
            ),
        )
        events = [e async for e in engine.run("hi")]

        # Both models were called, fallback last.
        assert calls == ["primary-model", "fallback-model"]
        assistant = [e for e in events if e.get("type") == "assistant"]
        assert assistant[-1]["message"].text == "fallback-ok"
        # The original overload error was suppressed (not yielded).
        errors = [e for e in events if e.get("type") == "error"]
        assert errors == []

    @pytest.mark.asyncio
    async def test_fallback_updates_token_cache(self):
        """The fallback path must keep the incremental token cache in
        sync — assistant messages from the fallback model should appear
        in ``_msg_token_cache``."""
        async def model_fn(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            if kwargs.get("model") == "fallback-model":
                yield _ok_assistant("from-fallback")
                yield _done()
                return
            raise Exception("API is overloaded")
            yield {}  # pragma: no cover

        engine = Engine(
            deps=Deps(call_model=model_fn),
            config=EngineConfig(
                model="primary-model",
                fallback_model="fallback-model",
            ),
        )
        async for _ in engine.run("hi"):
            pass

        # The fallback assistant message must be in history *and* cached.
        assistant_msgs = [m for m in engine.messages if m.role == "assistant"]
        assert assistant_msgs, "fallback assistant message missing from history"
        last = assistant_msgs[-1]
        assert last.text == "from-fallback"
        assert last.id in engine._msg_token_cache, (
            "fallback path failed to update token cache"
        )


# ---------------------------------------------------------------------------
# _process_query_events — shared event handling
# ---------------------------------------------------------------------------


class TestProcessQueryEvents:
    """The shared helper produces an identical event sequence whether it
    drives the primary or the fallback path."""

    @pytest.mark.asyncio
    async def test_identical_event_sequence_primary_vs_fallback(self):
        """Drive ``_process_query_events`` directly with the same query
        stream once per path and verify the user-visible event types
        match."""
        async def query_iter() -> AsyncGenerator[dict[str, Any], None]:
            yield _ok_assistant("hi")
            yield _done()

        # --- Primary-path invocation. ---
        engine_a = Engine(
            deps=Deps(call_model=_make_ok_model()),
            config=EngineConfig(model="claude-sonnet-4-6"),
        )
        # The engine needs a user message and turn count to be valid.
        engine_a._messages.append(Message(role="user", content="hi"))
        engine_a._turn_count = 1
        engine_a._rebuild_token_cache("claude-sonnet-4-6")
        state_a = _TurnState()
        primary_events: list[dict[str, Any]] = []
        async for ev in engine_a._process_query_events(
            query_iter(),
            effective_model="claude-sonnet-4-6",
            state=state_a,
            compact_fn=None,
            fallback_model=None,
            ptl_retries=0,
            enable_hooks=True,
            enable_auto_memory=True,
        ):
            primary_events.append(ev)

        # --- Fallback-path invocation (same query stream). ---
        engine_b = Engine(
            deps=Deps(call_model=_make_ok_model()),
            config=EngineConfig(model="claude-sonnet-4-6"),
        )
        engine_b._messages.append(Message(role="user", content="hi"))
        engine_b._turn_count = 1
        engine_b._rebuild_token_cache("claude-sonnet-4-6")
        state_b = _TurnState()
        fallback_events: list[dict[str, Any]] = []
        async for ev in engine_b._process_query_events(
            query_iter(),
            effective_model="claude-sonnet-4-6",
            state=state_b,
            compact_fn=None,
            fallback_model=None,
            ptl_retries=0,
            enable_hooks=False,
            enable_auto_memory=False,
        ):
            fallback_events.append(ev)

        # Both paths must yield the same sequence of event types.
        primary_types = [e.get("type") for e in primary_events]
        fallback_types = [e.get("type") for e in fallback_events]
        assert primary_types == fallback_types
        # And both must contain the assistant + done events.
        assert "assistant" in primary_types
        assert "done" in primary_types
        # Both paths must update history with the assistant message.
        roles_a = [m.role for m in engine_a.messages]
        roles_b = [m.role for m in engine_b.messages]
        assert roles_a == roles_b
        assert "assistant" in roles_a
        # Both paths must update the per-turn output token counter.
        assert state_a.output_tokens > 0
        assert state_b.output_tokens > 0

    @pytest.mark.asyncio
    async def test_tool_result_messages_are_internal(self):
        """``tool_result_message`` events must be captured into history but
        never surfaced to the caller."""
        async def query_iter() -> AsyncGenerator[dict[str, Any], None]:
            yield _ok_assistant("calling tool")
            yield {
                "type": "tool_result_message",
                "message": Message(
                    role="user",
                    content=[{"type": "tool_result", "tool_use_id": "1",
                              "content": "result"}],
                ),
            }
            yield _done()

        engine = Engine(
            deps=Deps(call_model=_make_ok_model()),
            config=EngineConfig(model="claude-sonnet-4-6"),
        )
        engine._messages.append(Message(role="user", content="hi"))
        engine._turn_count = 1
        engine._rebuild_token_cache("claude-sonnet-4-6")
        state = _TurnState()
        emitted: list[dict[str, Any]] = []
        async for ev in engine._process_query_events(
            query_iter(),
            effective_model="claude-sonnet-4-6",
            state=state,
            compact_fn=None,
            fallback_model=None,
            ptl_retries=0,
            enable_hooks=True,
            enable_auto_memory=False,
        ):
            emitted.append(ev)

        types = [e.get("type") for e in emitted]
        assert "tool_result_message" not in types, (
            "tool_result_message must not be yielded to the caller"
        )
        # But the tool_result message *was* captured into history.
        roles = [m.role for m in engine.messages]
        assert roles.count("user") == 2  # original prompt + tool_result

    @pytest.mark.asyncio
    async def test_ptl_error_swallowed_when_retry_allowed(self):
        """A PTL error must not be yielded when the helper is in a
        position to retry (compact_fn supplied, retries available)."""
        async def query_iter() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "error",
                   "error": "prompt is too long: 200000 tokens"}

        async def fake_compact(messages, token_limit=0):
            return messages

        engine = Engine(
            deps=Deps(call_model=_make_ok_model(), compact=fake_compact),
            config=EngineConfig(model="claude-sonnet-4-6"),
        )
        engine._messages.append(Message(role="user", content="hi"))
        engine._turn_count = 1
        engine._rebuild_token_cache("claude-sonnet-4-6")
        state = _TurnState()
        emitted = [
            ev async for ev in engine._process_query_events(
                query_iter(),
                effective_model="claude-sonnet-4-6",
                state=state,
                compact_fn=fake_compact,
                fallback_model=None,
                ptl_retries=0,
                enable_hooks=True,
                enable_auto_memory=False,
            )
        ]

        assert state.ptl_detected is True
        # Error event must be suppressed, not yielded to the caller.
        assert all(e.get("type") != "error" for e in emitted)

    @pytest.mark.asyncio
    async def test_overload_error_swallowed_with_fallback_configured(self):
        """An overload error must be suppressed when fallback_model is
        configured — the orchestrator will retry on the fallback."""
        async def query_iter() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "error", "error": "API is overloaded"}

        engine = Engine(
            deps=Deps(call_model=_make_ok_model()),
            config=EngineConfig(
                model="primary-model",
                fallback_model="fallback-model",
            ),
        )
        engine._messages.append(Message(role="user", content="hi"))
        engine._turn_count = 1
        engine._rebuild_token_cache("primary-model")
        state = _TurnState()
        emitted = [
            ev async for ev in engine._process_query_events(
                query_iter(),
                effective_model="primary-model",
                state=state,
                compact_fn=None,
                fallback_model="fallback-model",
                ptl_retries=0,
                enable_hooks=True,
                enable_auto_memory=False,
            )
        ]

        assert state.should_fallback is True
        assert all(e.get("type") != "error" for e in emitted)
