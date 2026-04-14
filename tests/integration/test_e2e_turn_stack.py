"""End-to-end tests for the FULL TURN LIFECYCLE + HOOKS + COMPACTION pipeline.

These tests wire up real Engine, Loop, SimpleCompactor, NativeExecutor,
HookRegistry, and QueryGuard. Only call_model is mocked with fake async
generators yielding canned events.

The goal is to exercise every major path and combination in the turn lifecycle:

  TestFullTurnLifecycleE2E  -- Engine.run() with real deps
  TestQueryGuardThroughEngine -- concurrent prevention via REPL-style flow
  TestHookEmissionE2E -- hooks emitted from engine/loop/repl
  TestCompactionE2E -- SimpleCompactor + ModelCompactor + dedup + restore
  TestGhostSnapshotE2E -- SnapshotSession + ReadOnlyExecutor
  TestAttachmentsE2E -- AttachmentManager + ImageBlock
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.loop import query
from duh.kernel.messages import (
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from duh.kernel.query_guard import QueryGuard, QueryState
from duh.kernel.snapshot import ReadOnlyExecutor, SnapshotSession
from duh.kernel.file_tracker import FileTracker
from duh.kernel.tool import ToolContext, ToolResult

from duh.hooks import (
    HookConfig,
    HookEvent,
    HookRegistry,
    HookResult,
    HookType,
    execute_hooks,
    execute_hooks_with_blocking,
)

from duh.adapters.native_executor import NativeExecutor
from duh.adapters.simple_compactor import (
    SimpleCompactor,
    restore_context,
    strip_images,
)
from duh.adapters.model_compactor import ModelCompactor

from duh.kernel.attachments import Attachment, AttachmentManager

from duh.tools.bash import BashTool
from duh.tools.todo_tool import TodoWriteTool


# ===================================================================
# Helpers
# ===================================================================


async def _collect(gen) -> list[dict]:
    """Drain an async generator into a list."""
    return [e async for e in gen]


def _has_tool_result(messages: list[Any]) -> bool:
    """Check if any message in the list contains a tool_result block."""
    for m in messages:
        if isinstance(m, Message) and isinstance(m.content, list):
            for b in m.content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    return True
    return False


def _text_model(text: str = "Hello!"):
    """Return a model that yields a single text assistant message."""
    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": text}],
        )}
    return model


def _tool_then_respond(tool_name: str, tool_input: dict, final_text: str = "Done."):
    """Model that calls one tool, then answers after seeing the tool_result."""
    call_count = [0]

    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        call_count[0] += 1
        messages = kwargs.get("messages", [])
        if _has_tool_result(messages):
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": final_text}],
            )}
        else:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[
                    {"type": "tool_use", "id": f"tu{call_count[0]}",
                     "name": tool_name, "input": tool_input},
                ],
            )}
    return model


def _multi_tool_model(tools_list: list[tuple[str, dict]], final_text: str = "All done."):
    """Model that fires N tool_use blocks in one response, then a final text."""
    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        messages = kwargs.get("messages", [])
        if _has_tool_result(messages):
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": final_text}],
            )}
        else:
            content = [
                {"type": "tool_use", "id": f"tu{i+1}", "name": name, "input": inp}
                for i, (name, inp) in enumerate(tools_list)
            ]
            yield {"type": "assistant", "message": Message(
                role="assistant", content=content,
            )}
    return model


def _forever_tool_model(tool_name: str = "Bash", tool_input: dict | None = None):
    """Model that always returns a tool_use (never stops)."""
    if tool_input is None:
        tool_input = {"command": "echo loop"}
    call_count = [0]

    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        call_count[0] += 1
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[
                {"type": "tool_use", "id": f"tu{call_count[0]}",
                 "name": tool_name, "input": tool_input},
            ],
        )}
    return model


def _partial_model():
    """Model that returns a partial (mid-stream error) message."""
    async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": "partial..."}],
            metadata={"partial": True},
        )}
    return model


# ===================================================================
# TestFullTurnLifecycleE2E
# ===================================================================


class TestFullTurnLifecycleE2E:
    """Engine.run() exercised with real Deps + real tool execution chain."""

    @pytest.mark.asyncio
    async def test_single_turn_text_only(self):
        """Text-only response: assistant message appended, done event emitted."""
        deps = Deps(call_model=_text_model("hello world"))
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))

        events = await _collect(engine.run("hi"))
        types = [e["type"] for e in events]

        assert "assistant" in types
        assert "done" in types
        done = [e for e in events if e["type"] == "done"][0]
        assert done["stop_reason"] == "end_turn"
        # user + assistant in history
        assert len(engine.messages) == 2
        assert engine.messages[0].role == "user"
        assert engine.messages[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_tool_use_turn_with_todo_write(self, tmp_path):
        """Real TodoWriteTool called via NativeExecutor through the loop."""
        todo_tool = TodoWriteTool()
        executor = NativeExecutor(tools=[todo_tool], cwd=str(tmp_path))

        model = _tool_then_respond(
            "TodoWrite",
            {"todos": [{"id": "t1", "text": "first", "status": "pending"}]},
            "Added.",
        )
        deps = Deps(call_model=model, run_tool=executor.run)
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))

        events = await _collect(engine.run("add todo"))
        types = [e["type"] for e in events]

        assert "tool_use" in types
        assert "tool_result" in types
        tool_result = [e for e in events if e["type"] == "tool_result"][0]
        assert not tool_result.get("is_error")
        assert "t1" in tool_result["output"] or "first" in tool_result["output"]

    @pytest.mark.asyncio
    async def test_multi_tool_turn_batched_results(self, tmp_path):
        """Three tool_use blocks in one response → all three run, batched."""
        async def run_tool(name, input, **kwargs):
            return f"ok:{name}:{input.get('key', '')}"

        model = _multi_tool_model([
            ("TodoWrite", {"todos": [{"id": "a", "text": "a", "status": "pending"}]}),
            ("TodoWrite", {"todos": [{"id": "b", "text": "b", "status": "pending"}]}),
            ("TodoWrite", {"todos": [{"id": "c", "text": "c", "status": "pending"}]}),
        ], "All added.")

        deps = Deps(call_model=model, run_tool=run_tool)
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))
        events = await _collect(engine.run("three todos"))

        tool_uses = [e for e in events if e["type"] == "tool_use"]
        tool_results = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_uses) == 3
        assert len(tool_results) == 3

        # Engine history has: user prompt, first assistant (3 tool_use blocks),
        # final assistant text. Tool results live inside the loop's internal
        # current_messages but the first assistant message with its 3 tool_use
        # blocks is what we can verify in engine.messages.
        assistant_msgs = [m for m in engine.messages if m.role == "assistant"]
        first_assistant = assistant_msgs[0]
        tu_blocks = [
            b for b in first_assistant.content
            if (isinstance(b, dict) and b.get("type") == "tool_use")
            or isinstance(b, ToolUseBlock)
        ]
        assert len(tu_blocks) == 3

    @pytest.mark.asyncio
    async def test_max_turns_reached(self):
        """Forever-tool-model → loop stops at max_turns."""
        async def run_tool(name, input, **kwargs):
            return "ok"

        model = _forever_tool_model("Bash", {"command": "echo x"})
        deps = Deps(call_model=model, run_tool=run_tool)
        engine = Engine(
            deps=deps,
            config=EngineConfig(model="test-model", max_turns=2),
        )

        events = await _collect(engine.run("spin"))
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["stop_reason"] == "max_turns"
        assert done[0]["turns"] == 2

    @pytest.mark.asyncio
    async def test_partial_message_exits_error(self):
        """Partial assistant message → done event with stop_reason=error."""
        deps = Deps(call_model=_partial_model())
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))

        events = await _collect(engine.run("hi"))
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["stop_reason"] == "error"

    @pytest.mark.asyncio
    async def test_fallback_model_on_overloaded(self):
        """Primary raises 'overloaded' → fallback model takes over, succeeds."""
        call_count = [0]

        async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            call_count[0] += 1
            if kwargs.get("model") == "primary-model":
                # The loop catches exceptions and yields them as error events,
                # which the engine then detects as fallback-eligible.
                raise Exception("API overloaded — try again")
                yield  # pragma: no cover
            # fallback
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": "fallback answer"}],
            )}

        deps = Deps(call_model=model)
        engine = Engine(deps=deps, config=EngineConfig(
            model="primary-model",
            fallback_model="fallback-model",
        ))

        events = await _collect(engine.run("hi"))
        types = [e["type"] for e in events]

        # The overloaded error must be swallowed, not propagated
        errors = [e for e in events if e["type"] == "error"]
        assert errors == []
        # The fallback produced an assistant message
        assert "assistant" in types
        assert "done" in types
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_budget_warning_at_80_percent(self):
        """Large response triggers budget_warning once per session."""
        big = "x" * 40_000

        async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": big}],
            )}

        deps = Deps(call_model=model)
        engine = Engine(deps=deps, config=EngineConfig(
            model="claude-sonnet-4-6",
            max_cost=0.10,
        ))

        events = await _collect(engine.run("go"))
        warnings = [e for e in events if e.get("type") == "budget_warning"]
        assert len(warnings) == 1
        assert warnings[0]["max_cost"] == 0.10

    @pytest.mark.asyncio
    async def test_budget_exceeded_stops_session(self):
        """Cost >= max_cost → budget_exceeded emitted and engine returns."""
        big = "x" * 40_000

        async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": big}],
            )}

        deps = Deps(call_model=model)
        engine = Engine(deps=deps, config=EngineConfig(
            model="claude-sonnet-4-6",
            max_cost=0.001,  # tiny — will exceed instantly
        ))

        events = await _collect(engine.run("go"))
        types = [e["type"] for e in events]
        assert "budget_exceeded" in types
        exceeded = [e for e in events if e["type"] == "budget_exceeded"][0]
        assert "Session stopped" in exceeded["message"]


# ===================================================================
# TestQueryGuardThroughEngine
# ===================================================================


class TestQueryGuardThroughEngine:
    """QueryGuard state machine exercised alongside engine.run()."""

    def test_normal_reserve_start_end_cycle(self):
        """IDLE → DISPATCHING → RUNNING → IDLE → reservable again."""
        guard = QueryGuard()
        assert guard.state == QueryState.IDLE

        gen = guard.reserve()
        assert guard.state == QueryState.DISPATCHING
        assert guard.try_start(gen) == gen
        assert guard.state == QueryState.RUNNING
        assert guard.end(gen) is True
        assert guard.state == QueryState.IDLE

        # Reservable again
        gen2 = guard.reserve()
        assert gen2 > gen
        assert guard.state == QueryState.DISPATCHING

    def test_stale_generation_rejected(self):
        """force_end bumps gen → old end() returns False."""
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        guard.force_end()  # bumps generation
        # Old holder's end() should fail
        assert guard.end(gen) is False

    def test_concurrent_reserve_raises(self):
        """Reserving while not idle raises RuntimeError."""
        guard = QueryGuard()
        guard.reserve()
        with pytest.raises(RuntimeError, match="not idle"):
            guard.reserve()

    def test_force_end_from_any_state_allows_new_reserve(self):
        """force_end → IDLE → new reserve succeeds."""
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        assert guard.state == QueryState.RUNNING

        guard.force_end()
        assert guard.state == QueryState.IDLE

        new_gen = guard.reserve()
        assert new_gen > gen

    @pytest.mark.asyncio
    async def test_queryguard_alongside_engine_run(self):
        """QueryGuard wraps engine.run() via asyncio.gather."""
        deps = Deps(call_model=_text_model("ok"))
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))
        guard = QueryGuard()

        async def run_query() -> list[dict[str, Any]]:
            gen = guard.reserve()
            try:
                assert guard.try_start(gen) == gen
                events = await _collect(engine.run("hi"))
                return events
            finally:
                guard.end(gen)

        # Single query through guard
        results = await asyncio.gather(run_query())
        assert len(results[0]) >= 1
        assert guard.state == QueryState.IDLE


# ===================================================================
# TestHookEmissionE2E
# ===================================================================


class TestHookEmissionE2E:
    """Hook lifecycle events, blocking, glob matching, error isolation."""

    @pytest.mark.asyncio
    async def test_pre_and_post_tool_use_execute_directly(self):
        """PRE_TOOL_USE and POST_TOOL_USE fire when dispatched."""
        fired: list[str] = []

        async def pre_hook(event, data):
            fired.append(f"pre:{data.get('tool_name')}")
            return HookResult(hook_name="pre", success=True)

        async def post_hook(event, data):
            fired.append(f"post:{data.get('tool_name')}")
            return HookResult(hook_name="post", success=True)

        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.FUNCTION,
            name="pre",
            callback=pre_hook,
        ))
        registry.register(HookConfig(
            event=HookEvent.POST_TOOL_USE,
            hook_type=HookType.FUNCTION,
            name="post",
            callback=post_hook,
        ))

        await execute_hooks(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash", "input": {"command": "ls"}},
        )
        await execute_hooks(
            registry, HookEvent.POST_TOOL_USE,
            {"tool_name": "Bash", "output": "hello"},
        )

        assert fired == ["pre:Bash", "post:Bash"]

    @pytest.mark.asyncio
    async def test_permission_request_and_denied_hooks(self):
        """PERMISSION_REQUEST fires, then PERMISSION_DENIED when approve rejects."""
        state = {"request": 0, "denied": 0}

        async def on_request(event, data):
            state["request"] += 1
            return HookResult(hook_name="req", success=True)

        async def on_denied(event, data):
            state["denied"] += 1
            return HookResult(hook_name="denied", success=True)

        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.PERMISSION_REQUEST,
            hook_type=HookType.FUNCTION,
            name="req",
            callback=on_request,
        ))
        registry.register(HookConfig(
            event=HookEvent.PERMISSION_DENIED,
            hook_type=HookType.FUNCTION,
            name="denied",
            callback=on_denied,
        ))

        async def deny_all(tool_name, input):
            return {"allowed": False, "reason": "test denies everything"}

        async def run_tool(name, input, **kwargs):
            return "should-not-run"

        model = _tool_then_respond("Bash", {"command": "ls"}, "blocked")
        deps = Deps(
            call_model=model,
            run_tool=run_tool,
            approve=deny_all,
            hook_registry=registry,
        )
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))
        events = await _collect(engine.run("try bash"))

        assert state["request"] == 1
        assert state["denied"] == 1
        # Result was an error (denied)
        tr = [e for e in events if e["type"] == "tool_result"][0]
        assert tr.get("is_error") is True

    @pytest.mark.asyncio
    async def test_post_tool_use_failure_on_run_tool_exception(self):
        """POST_TOOL_USE_FAILURE fires when run_tool raises."""
        fired = {"failure": False, "error_seen": None}

        async def on_failure(event, data):
            fired["failure"] = True
            fired["error_seen"] = data.get("error")
            return HookResult(hook_name="failure", success=True)

        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.POST_TOOL_USE_FAILURE,
            hook_type=HookType.FUNCTION,
            name="failure",
            callback=on_failure,
        ))

        async def bad_tool(name, input, **kwargs):
            raise RuntimeError("boom from tool")

        model = _tool_then_respond("Bash", {"command": "ls"}, "after failure")
        deps = Deps(
            call_model=model,
            run_tool=bad_tool,
            hook_registry=registry,
        )
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))
        await _collect(engine.run("trigger failure"))

        assert fired["failure"] is True
        assert "boom from tool" in fired["error_seen"]

    @pytest.mark.asyncio
    async def test_pre_and_post_compact_hooks_fire(self):
        """PRE_COMPACT and POST_COMPACT fire when auto-compaction kicks in."""
        compact_events = {"pre": 0, "post": 0}

        async def on_pre(event, data):
            compact_events["pre"] += 1
            return HookResult(hook_name="pre", success=True)

        async def on_post(event, data):
            compact_events["post"] += 1
            return HookResult(hook_name="post", success=True)

        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.PRE_COMPACT,
            hook_type=HookType.FUNCTION,
            name="pre",
            callback=on_pre,
        ))
        registry.register(HookConfig(
            event=HookEvent.POST_COMPACT,
            hook_type=HookType.FUNCTION,
            name="post",
            callback=on_post,
        ))

        async def fake_compact(messages, token_limit=0):
            return messages[-2:] if len(messages) > 2 else messages

        deps = Deps(
            call_model=_text_model("ok"),
            compact=fake_compact,
            hook_registry=registry,
        )
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))

        # Saturate context so auto-compact fires
        for i in range(100):
            engine._messages.append(Message(
                role="user" if i % 2 == 0 else "assistant",
                content=f"msg {i} " + "y" * 4000,
            ))

        await _collect(engine.run("final"))

        assert compact_events["pre"] >= 1
        assert compact_events["post"] >= 1

    @pytest.mark.asyncio
    async def test_user_prompt_submit_hook(self):
        """USER_PROMPT_SUBMIT fires when manually dispatched with prompt data."""
        captured = {}

        async def on_submit(event, data):
            captured.update(data)
            return HookResult(hook_name="submit", success=True)

        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.USER_PROMPT_SUBMIT,
            hook_type=HookType.FUNCTION,
            name="submit",
            callback=on_submit,
        ))

        # Simulate REPL's USER_PROMPT_SUBMIT dispatch around engine.run()
        deps = Deps(call_model=_text_model("ok"), hook_registry=registry)
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))

        prompt_text = "please fix it"
        await execute_hooks(
            registry, HookEvent.USER_PROMPT_SUBMIT,
            {"prompt": prompt_text, "session_id": engine.session_id},
        )
        await _collect(engine.run(prompt_text))

        assert captured.get("prompt") == prompt_text
        assert captured.get("session_id") == engine.session_id

    @pytest.mark.asyncio
    async def test_hook_matcher_fires_only_for_matching_tool(self):
        """A hook with matcher=Bash fires only for Bash, not Read."""
        counts = {"bash": 0, "read": 0}

        async def hook_fn(event, data):
            counts[data.get("tool_name", "").lower()] = (
                counts.get(data.get("tool_name", "").lower(), 0) + 1
            )
            return HookResult(hook_name="m", success=True)

        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.FUNCTION,
            name="m",
            matcher="Bash",
            callback=hook_fn,
        ))

        await execute_hooks(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"},
            matcher_value="Bash",
        )
        await execute_hooks(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Read"},
            matcher_value="Read",
        )

        assert counts["bash"] == 1
        assert counts["read"] == 0

    @pytest.mark.asyncio
    async def test_command_hook_sets_env_vars(self, tmp_path):
        """Subprocess hook sees TOOL_NAME, TOOL_INPUT, SESSION_ID env vars."""
        out_file = tmp_path / "env.txt"
        registry = HookRegistry()
        # Shell hook prints the env vars to a file
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.COMMAND,
            name="env_probe",
            command=(
                f'printf "%s\\n%s\\n%s\\n" '
                f'"$TOOL_NAME" "$TOOL_INPUT" "$SESSION_ID" '
                f'> {out_file}'
            ),
            timeout=10.0,
        ))

        await execute_hooks(
            registry, HookEvent.PRE_TOOL_USE,
            {
                "tool_name": "Bash",
                "input": {"command": "ls -la"},
                "session_id": "sess-123",
            },
        )

        text = out_file.read_text().strip().splitlines()
        assert text[0] == "Bash"
        # TOOL_INPUT is JSON — just contains the key fields
        assert "command" in text[1]
        assert "ls -la" in text[1]
        assert text[2] == "sess-123"

    @pytest.mark.asyncio
    async def test_hook_decision_block_denies_tool(self):
        """Hook returning decision=block denies the tool call."""
        async def block(event, data):
            return HookResult(
                hook_name="blocker",
                success=True,
                output=json.dumps({"decision": "block", "message": "nope"}),
            )

        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.FUNCTION,
            name="blocker",
            matcher="Bash",
            callback=block,
        ))

        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash", "input": {"command": "ls"}},
            matcher_value="Bash",
        )
        assert response.decision == "block"
        assert "nope" in response.message

    @pytest.mark.asyncio
    async def test_hook_error_isolation(self):
        """One hook raising does not prevent others from running."""
        state = {"good_ran": False}

        async def bad(event, data):
            raise RuntimeError("hook exploded")

        async def good(event, data):
            state["good_ran"] = True
            return HookResult(hook_name="good", success=True)

        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.FUNCTION,
            name="bad",
            callback=bad,
        ))
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.FUNCTION,
            name="good",
            callback=good,
        ))

        results = await execute_hooks(
            registry, HookEvent.PRE_TOOL_USE, {"tool_name": "Bash"},
        )
        assert len(results) == 2
        assert any(not r.success for r in results)
        assert state["good_ran"] is True


# ===================================================================
# TestCompactionE2E
# ===================================================================


class TestCompactionE2E:
    """SimpleCompactor + ModelCompactor + dedup + restore + PTL retry."""

    @pytest.mark.asyncio
    async def test_auto_compact_dedups_duplicate_reads(self):
        """Feed messages with duplicate Read calls → dedup collapses them."""
        compactor = SimpleCompactor(default_limit=100_000)

        messages: list[Message] = [
            Message(role="user", content="hello"),
        ]
        # 50 messages with two duplicate Read calls
        for i in range(24):
            messages.append(Message(role="user", content=f"chat {i}"))
            messages.append(Message(role="assistant", content=f"reply {i}"))
        messages.append(Message(role="assistant", content=[
            ToolUseBlock(id="dup1", name="Read", input={"file_path": "/a.py"}),
        ]))
        messages.append(Message(role="user", content=[
            {"type": "tool_result", "tool_use_id": "dup1", "content": "old"},
        ]))
        messages.append(Message(role="assistant", content=[
            ToolUseBlock(id="dup2", name="Read", input={"file_path": "/a.py"}),
        ]))
        messages.append(Message(role="user", content=[
            {"type": "tool_result", "tool_use_id": "dup2", "content": "new"},
        ]))

        compacted = await compactor.compact(messages, token_limit=100_000)

        # The earlier duplicate (dup1) must have been dropped
        all_blocks: list[Any] = []
        for m in compacted:
            if isinstance(m.content, list):
                all_blocks.extend(m.content)

        dup1_found = any(
            (isinstance(b, ToolUseBlock) and b.id == "dup1")
            or (isinstance(b, dict) and b.get("id") == "dup1")
            or (isinstance(b, dict) and b.get("tool_use_id") == "dup1")
            for b in all_blocks
        )
        assert not dup1_found

    def test_image_blocks_stripped_with_placeholder(self):
        """Messages with images → placeholder replaces the image block."""
        messages = [
            Message(role="user", content=[
                {"type": "text", "text": "see this"},
                {"type": "image", "source": {"type": "base64", "data": "xxx"}},
            ]),
        ]
        result = strip_images(messages)
        assert len(result) == 1
        content = result[0].content
        assert isinstance(content, list)
        has_image = any(
            isinstance(b, dict) and b.get("type") == "image" for b in content
        )
        assert not has_image
        placeholder_found = any(
            (isinstance(b, TextBlock) and "image removed" in b.text.lower())
            or (isinstance(b, dict) and "image removed" in b.get("text", "").lower())
            for b in content
        )
        assert placeholder_found

    @pytest.mark.asyncio
    async def test_partial_compact_keeps_outer_messages_intact(self):
        """partial_compact(from, to) compacts only the window."""
        compactor = SimpleCompactor(default_limit=100_000)
        messages = [
            Message(role="user", content="m0"),
            Message(role="assistant", content="m1"),
            Message(role="user", content="m2"),
            Message(role="assistant", content="m3"),
            Message(role="user", content="m4"),
            Message(role="assistant", content="m5"),
        ]

        result = await compactor.partial_compact(messages, from_idx=2, to_idx=4)

        # Before: m0, m1. Then summary. Then after: m4, m5 (since to_idx=4).
        # NOTE partial_compact slices [from_idx:to_idx) = indices 2, 3.
        assert result[0].content == "m0"
        assert result[1].content == "m1"
        assert result[2].role == "system"
        assert "summary" in result[2].content.lower()
        assert result[3].content == "m4"
        assert result[4].content == "m5"

    def test_restore_context_injects_files_and_skills(self):
        """restore_context merges top file reads and skill context."""
        tracker = FileTracker()
        tracker.track("/proj/a.py", "read")
        tracker.track("/proj/b.py", "read")
        tracker.track("/proj/a.py", "read")  # duplicate → still dedupes

        messages = [Message(role="user", content="after compaction")]
        result = restore_context(
            messages,
            file_tracker=tracker,
            skill_context="active-skill: test-runner",
            token_budget=50_000,
        )

        assert len(result) == 2
        restore_msg = result[-1]
        assert restore_msg.role == "system"
        body = str(restore_msg.content)
        assert "a.py" in body
        assert "b.py" in body
        assert "test-runner" in body

    def test_restore_context_respects_token_budget(self):
        """Huge skill context is truncated to fit the budget."""
        messages = [Message(role="user", content="m0")]
        huge = "z" * 100_000
        result = restore_context(
            messages,
            file_tracker=None,
            skill_context=huge,
            token_budget=100,  # 100 tokens * 4 bytes = 400 chars max
        )
        assert len(result) == 2
        body = str(result[-1].content)
        # Body must fit within budget (400 chars plus header/preamble)
        # Header is "[Post-compaction context restoration]\n"
        # The truncation clips the content section itself
        assert body.endswith("...") or len(body) < 2000

    @pytest.mark.asyncio
    async def test_ptl_retry_compacts_once_and_succeeds(self):
        """One PTL error → engine auto-compacts → retry succeeds."""
        call_count = [0]

        async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("prompt is too long")
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": "after compact"}],
            )}

        compact_count = [0]

        async def fake_compact(messages, token_limit=0):
            compact_count[0] += 1
            return messages[-2:] if len(messages) > 2 else messages

        deps = Deps(call_model=model, compact=fake_compact)
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))
        events = await _collect(engine.run("ptl"))

        assert compact_count[0] >= 1
        assert any(e.get("type") == "done" for e in events)
        # No error yielded since we retried successfully
        errors = [e for e in events if e.get("type") == "error"]
        assert errors == []

    @pytest.mark.asyncio
    async def test_ptl_exhaustion_propagates_error(self):
        """3 PTL errors → engine gives up and yields the error."""
        async def model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            raise Exception("prompt is too long")
            yield  # make this an async generator  # pragma: no cover

        async def fake_compact(messages, token_limit=0):
            return messages[-1:]

        deps = Deps(call_model=model, compact=fake_compact)
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))
        events = await _collect(engine.run("exhaust"))

        errors = [e for e in events if e.get("type") == "error"]
        assert len(errors) >= 1
        assert "too long" in errors[0]["error"].lower()

    @pytest.mark.asyncio
    async def test_model_compactor_falls_back_to_simple(self):
        """ModelCompactor falls back to SimpleCompactor when model raises."""
        async def failing_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            raise RuntimeError("model is broken")
            yield  # pragma: no cover

        compactor = ModelCompactor(
            call_model=failing_model,
            default_limit=100,
            bytes_per_token=1,
        )

        # Build enough messages to force compaction
        messages = [
            Message(role="user", content="x" * 200),
            Message(role="assistant", content="y" * 200),
            Message(role="user", content="z" * 200),
            Message(role="assistant", content="w" * 200),
        ]
        compacted = await compactor.compact(messages, token_limit=100)

        # The result must be shorter than the input (fallback trimmed)
        assert len(compacted) <= len(messages)
        # The result must contain a summary system message (SimpleCompactor)
        system_msgs = [
            m for m in compacted
            if isinstance(m, Message) and m.role == "system"
        ]
        assert len(system_msgs) >= 1


# ===================================================================
# TestGhostSnapshotE2E
# ===================================================================


class TestGhostSnapshotE2E:
    """SnapshotSession + ReadOnlyExecutor — fork, run, discard, apply."""

    @pytest.mark.asyncio
    async def test_snapshot_allows_read_tool(self):
        """Read tool passes through the snapshot."""
        async def inner(tool_name, input, **kwargs):
            return f"read {input.get('file_path', '?')}"

        executor = ReadOnlyExecutor(inner)
        result = await executor.run("Read", {"file_path": "/tmp/x.txt"})
        assert "read /tmp/x.txt" == result

    @pytest.mark.asyncio
    async def test_snapshot_blocks_write_tool(self):
        """Write is blocked with PermissionError."""
        async def inner(tool_name, input, **kwargs):
            return "should not reach"

        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="read-only|Snapshot"):
            await executor.run("Write", {"file_path": "/tmp/x.txt"})

    @pytest.mark.asyncio
    async def test_snapshot_blocks_bash_tool(self):
        """Bash is blocked with PermissionError."""
        async def inner(tool_name, input, **kwargs):
            return "should not reach"

        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="read-only|Snapshot"):
            await executor.run("Bash", {"command": "ls"})

    def test_snapshot_discard_leaves_original_untouched(self):
        """Discarding a snapshot leaves the original engine messages intact."""
        deps = Deps(call_model=_text_model("ok"))
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))
        engine._messages.append(Message(role="user", content="before"))
        engine._messages.append(Message(role="assistant", content="reply"))

        snap = SnapshotSession(engine._messages)
        snap.add_message(Message(role="user", content="snap-added"))
        assert len(snap.messages) == 3

        snap.discard()
        assert snap.is_discarded
        # engine unchanged
        assert len(engine._messages) == 2
        assert engine._messages[0].content == "before"
        assert engine._messages[1].content == "reply"

    def test_snapshot_get_new_messages_can_be_applied_manually(self):
        """Manually merging snapshot.get_new_messages() back into engine works."""
        deps = Deps(call_model=_text_model("ok"))
        engine = Engine(deps=deps, config=EngineConfig(model="test-model"))
        engine._messages.append(Message(role="user", content="u0"))
        engine._messages.append(Message(role="assistant", content="a0"))

        snap = SnapshotSession(engine._messages)
        snap.add_message(Message(role="user", content="u1"))
        snap.add_message(Message(role="assistant", content="a1"))

        new_msgs = snap.get_new_messages()
        assert len(new_msgs) == 2

        # Apply the snapshot manually
        engine._messages.extend(new_msgs)

        assert len(engine._messages) == 4
        assert engine._messages[-2].content == "u1"
        assert engine._messages[-1].content == "a1"


# ===================================================================
# TestAttachmentsE2E
# ===================================================================


class TestAttachmentsE2E:
    """AttachmentManager + ImageBlock + text/image/pdf paths."""

    def test_text_file_attachment_has_text_content_type(self, tmp_path):
        """Reading a .py file → text/x-python content type."""
        mgr = AttachmentManager()
        path = tmp_path / "hello.py"
        path.write_text("print('hi')\n", encoding="utf-8")

        att = mgr.read_file(str(path))
        assert att.name == "hello.py"
        assert att.content_type == "text/x-python"
        assert att.is_image is False
        assert att.text == "print('hi')\n"

    def test_png_detected_by_magic_bytes(self, tmp_path):
        """A PNG file (magic bytes) is detected as an image."""
        mgr = AttachmentManager()
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"  # PNG header
            b"\x00\x00\x00\rIHDR"  # minimal IHDR
            + b"\x00" * 50
        )
        path = tmp_path / "fake.png"
        path.write_bytes(png_bytes)

        att = mgr.read_file(str(path))
        assert att.content_type == "image/png"
        assert att.is_image is True

    def test_image_attachment_converts_to_image_block(self, tmp_path):
        """to_image_block returns an ImageBlock for image attachments."""
        mgr = AttachmentManager()
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
        path = tmp_path / "test.png"
        path.write_bytes(png_bytes)

        att = mgr.read_file(str(path))
        block = mgr.to_image_block(att)

        assert isinstance(block, ImageBlock)
        assert block.media_type == "image/png"
        assert block.type == "image"
        assert len(block.data) > 0  # base64 encoded

    def test_pdf_text_extraction_with_mocked_pdfplumber(self, tmp_path):
        """extract_text on a PDF uses pdfplumber when available."""
        mgr = AttachmentManager()
        pdf_bytes = b"%PDF-1.4\n" + b"\x00" * 100
        path = tmp_path / "doc.pdf"
        path.write_bytes(pdf_bytes)

        att = mgr.read_file(str(path))
        assert att.content_type == "application/pdf"

        # Mock pdfplumber so extract_text returns a known value
        class FakePage:
            def extract_text(self):
                return "Hello from fake PDF"

        class FakePdf:
            pages = [FakePage(), FakePage()]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake_pdfplumber = MagicMock()
        fake_pdfplumber.open = MagicMock(return_value=FakePdf())

        with patch.dict("sys.modules", {"pdfplumber": fake_pdfplumber}):
            text = mgr.extract_text(att)

        assert "Hello from fake PDF" in text
