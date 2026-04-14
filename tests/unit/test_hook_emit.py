"""Tests for hook event emission across engine, loop, and REPL.

Part 1: Verify new hook events can be registered and dispatched
        (pure HookRegistry tests -- no wiring needed).

Part 2: Integration tests that verify loop.py and engine.py actually
        emit the hooks when hook_registry is set on Deps.
"""

from __future__ import annotations

import asyncio
import pytest

from duh.hooks import HookEvent, HookRegistry, HookConfig, HookType, HookResult, execute_hooks


class _Recorder:
    """Records hook events fired during tests."""

    def __init__(self):
        self.events: list[tuple[HookEvent, dict]] = []

    async def callback(self, event: HookEvent, data: dict) -> HookResult:
        self.events.append((event, data))
        return HookResult(hook_name="recorder", success=True)

    def has_event(self, event: HookEvent) -> bool:
        return any(e == event for e, _ in self.events)

    def get_data(self, event: HookEvent) -> dict | None:
        for e, d in self.events:
            if e == event:
                return d
        return None

    def count(self, event: HookEvent) -> int:
        return sum(1 for e, _ in self.events if e == event)


# -----------------------------------------------------------------------
# Part 1: Pure dispatch tests (no wiring into deps/loop/engine)
# -----------------------------------------------------------------------


class TestHookRegistryForNewEvents:
    """Verify new hook events can be registered and dispatched."""

    @pytest.mark.asyncio
    async def test_permission_request_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.PERMISSION_REQUEST,
            hook_type=HookType.FUNCTION,
            name="perm_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.PERMISSION_REQUEST, {
            "tool_name": "Bash",
            "input": {"command": "ls"},
        })
        assert recorder.has_event(HookEvent.PERMISSION_REQUEST)

    @pytest.mark.asyncio
    async def test_permission_denied_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.PERMISSION_DENIED,
            hook_type=HookType.FUNCTION,
            name="denied_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.PERMISSION_DENIED, {
            "tool_name": "Bash",
            "reason": "user rejected",
        })
        assert recorder.has_event(HookEvent.PERMISSION_DENIED)

    @pytest.mark.asyncio
    async def test_pre_compact_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.PRE_COMPACT,
            hook_type=HookType.FUNCTION,
            name="precompact_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.PRE_COMPACT, {
            "message_count": 50,
            "token_estimate": 120000,
        })
        assert recorder.has_event(HookEvent.PRE_COMPACT)

    @pytest.mark.asyncio
    async def test_post_compact_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.POST_COMPACT,
            hook_type=HookType.FUNCTION,
            name="postcompact_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.POST_COMPACT, {
            "message_count_before": 50,
            "message_count_after": 10,
        })
        assert recorder.has_event(HookEvent.POST_COMPACT)

    @pytest.mark.asyncio
    async def test_user_prompt_submit_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.USER_PROMPT_SUBMIT,
            hook_type=HookType.FUNCTION,
            name="prompt_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.USER_PROMPT_SUBMIT, {
            "prompt": "fix the bug",
            "session_id": "abc-123",
        })
        assert recorder.has_event(HookEvent.USER_PROMPT_SUBMIT)
        data = recorder.get_data(HookEvent.USER_PROMPT_SUBMIT)
        assert data["prompt"] == "fix the bug"

    @pytest.mark.asyncio
    async def test_post_tool_use_failure_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.POST_TOOL_USE_FAILURE,
            hook_type=HookType.FUNCTION,
            name="failure_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.POST_TOOL_USE_FAILURE, {
            "tool_name": "Bash",
            "error": "timeout after 300s",
        })
        assert recorder.has_event(HookEvent.POST_TOOL_USE_FAILURE)

    @pytest.mark.asyncio
    async def test_status_line_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.STATUS_LINE,
            hook_type=HookType.FUNCTION,
            name="status_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.STATUS_LINE, {
            "model": "claude-sonnet-4-6",
            "turn": 3,
        })
        assert recorder.has_event(HookEvent.STATUS_LINE)

    @pytest.mark.asyncio
    async def test_cwd_changed_event_fires(self):
        registry = HookRegistry()
        recorder = _Recorder()
        registry.register(HookConfig(
            event=HookEvent.CWD_CHANGED,
            hook_type=HookType.FUNCTION,
            name="cwd_test",
            callback=recorder.callback,
        ))
        await execute_hooks(registry, HookEvent.CWD_CHANGED, {
            "old_cwd": "/old",
            "new_cwd": "/new",
        })
        assert recorder.has_event(HookEvent.CWD_CHANGED)


# -----------------------------------------------------------------------
# Part 2: Integration tests — verify deps.hook_registry threading
# -----------------------------------------------------------------------


class TestDepsHookRegistry:
    """Verify that Deps has a hook_registry field."""

    def test_deps_has_hook_registry_field(self):
        from duh.kernel.deps import Deps
        deps = Deps()
        assert hasattr(deps, "hook_registry")
        assert deps.hook_registry is None

    def test_deps_accepts_hook_registry(self):
        from duh.kernel.deps import Deps
        registry = HookRegistry()
        deps = Deps(hook_registry=registry)
        assert deps.hook_registry is registry


class TestLoopEmitsPermissionHooks:
    """Verify that the query loop emits PERMISSION_REQUEST/DENIED hooks."""

    @pytest.mark.asyncio
    async def test_permission_request_emitted_before_approval(self):
        """When approval is checked, PERMISSION_REQUEST fires first."""
        from duh.kernel.deps import Deps
        from duh.kernel.loop import query
        from duh.kernel.messages import Message

        recorder = _Recorder()
        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.PERMISSION_REQUEST,
            hook_type=HookType.FUNCTION,
            name="perm_req",
            callback=recorder.callback,
        ))

        # Model returns a tool_use, then approval allows it
        async def fake_model(**kw):
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "Bash",
                    "input": {"command": "ls"},
                }],
                metadata={"stop_reason": "tool_use"},
            )}

        async def fake_approve(name, inp):
            return {"allowed": True}

        async def fake_run(name, inp, **kw):
            return "ok"

        deps = Deps(
            call_model=fake_model,
            approve=fake_approve,
            run_tool=fake_run,
            hook_registry=registry,
        )
        events = []
        async for ev in query(
            messages=[Message(role="user", content="test")],
            deps=deps,
            tools=[],
        ):
            events.append(ev)

        assert recorder.has_event(HookEvent.PERMISSION_REQUEST)
        data = recorder.get_data(HookEvent.PERMISSION_REQUEST)
        assert data["tool_name"] == "Bash"

    @pytest.mark.asyncio
    async def test_permission_denied_emitted_on_rejection(self):
        """When approval is denied, PERMISSION_DENIED fires."""
        from duh.kernel.deps import Deps
        from duh.kernel.loop import query
        from duh.kernel.messages import Message

        recorder = _Recorder()
        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.PERMISSION_DENIED,
            hook_type=HookType.FUNCTION,
            name="perm_denied",
            callback=recorder.callback,
        ))

        async def fake_model(**kw):
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "Bash",
                    "input": {"command": "rm -rf /"},
                }],
                metadata={"stop_reason": "tool_use"},
            )}

        async def fake_approve(name, inp):
            return {"allowed": False, "reason": "dangerous command"}

        deps = Deps(
            call_model=fake_model,
            approve=fake_approve,
            hook_registry=registry,
        )
        events = []
        async for ev in query(
            messages=[Message(role="user", content="test")],
            deps=deps,
            tools=[],
        ):
            events.append(ev)

        assert recorder.has_event(HookEvent.PERMISSION_DENIED)
        data = recorder.get_data(HookEvent.PERMISSION_DENIED)
        assert data["reason"] == "dangerous command"

    @pytest.mark.asyncio
    async def test_no_hooks_emitted_when_registry_absent(self):
        """When hook_registry is None, loop runs without error."""
        from duh.kernel.deps import Deps
        from duh.kernel.loop import query
        from duh.kernel.messages import Message

        async def fake_model(**kw):
            yield {"type": "text_delta", "text": "hi"}
            yield {"type": "assistant", "message": Message(
                role="assistant", content="hi",
                metadata={"stop_reason": "end_turn"},
            )}

        deps = Deps(call_model=fake_model)
        assert deps.hook_registry is None  # no registry

        events = []
        async for ev in query(
            messages=[Message(role="user", content="test")],
            deps=deps,
            tools=[],
        ):
            events.append(ev)

        # Should complete normally with no hook emission errors
        assert any(e.get("type") == "done" for e in events)


class TestLoopEmitsToolFailureHook:
    """Verify POST_TOOL_USE_FAILURE fires when a tool raises."""

    @pytest.mark.asyncio
    async def test_post_tool_use_failure_on_exception(self):
        from duh.kernel.deps import Deps
        from duh.kernel.loop import query
        from duh.kernel.messages import Message

        recorder = _Recorder()
        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.POST_TOOL_USE_FAILURE,
            hook_type=HookType.FUNCTION,
            name="fail_hook",
            callback=recorder.callback,
        ))

        async def fake_model(**kw):
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "Bash",
                    "input": {"command": "fail"},
                }],
                metadata={"stop_reason": "tool_use"},
            )}

        async def fake_run(name, inp, **kw):
            raise RuntimeError("tool exploded")

        deps = Deps(
            call_model=fake_model,
            run_tool=fake_run,
            hook_registry=registry,
        )
        events = []
        async for ev in query(
            messages=[Message(role="user", content="test")],
            deps=deps,
            tools=[],
        ):
            events.append(ev)

        assert recorder.has_event(HookEvent.POST_TOOL_USE_FAILURE)
        data = recorder.get_data(HookEvent.POST_TOOL_USE_FAILURE)
        assert data["tool_name"] == "Bash"
        assert "tool exploded" in data["error"]


class TestEngineEmitsCompactHooks:
    """Verify PRE_COMPACT/POST_COMPACT fire during auto-compaction."""

    @pytest.mark.asyncio
    async def test_compact_hooks_fire_during_auto_compact(self):
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        recorder = _Recorder()
        registry = HookRegistry()
        for evt in (HookEvent.PRE_COMPACT, HookEvent.POST_COMPACT):
            registry.register(HookConfig(
                event=evt,
                hook_type=HookType.FUNCTION,
                name=f"compact_{evt.value}",
                callback=recorder.callback,
            ))

        call_count = 0

        async def fake_model(**kw):
            nonlocal call_count
            call_count += 1
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "assistant", "message": Message(
                role="assistant", content="done",
                metadata={"stop_reason": "end_turn"},
            )}

        async def fake_compact(messages, **kw):
            # Return just last 2 messages
            return messages[-2:] if len(messages) > 2 else messages

        deps = Deps(
            call_model=fake_model,
            compact=fake_compact,
            hook_registry=registry,
        )
        config = EngineConfig(
            model="test-model",
            system_prompt="x" * 100,
        )
        engine = Engine(deps=deps, config=config)

        # Fill history to trigger auto-compact (need input_estimate > 80% of context limit)
        # The auto-compact threshold is 80% of context_limit.
        # We need the token estimate to exceed the threshold.
        # Stuff the message history with large messages.
        for i in range(50):
            engine._messages.append(Message(role="user", content="x" * 5000))
            engine._messages.append(Message(role="assistant", content="y" * 5000))

        events = []
        async for ev in engine.run("compact me"):
            events.append(ev)

        assert recorder.has_event(HookEvent.PRE_COMPACT)
        assert recorder.has_event(HookEvent.POST_COMPACT)

        pre_data = recorder.get_data(HookEvent.PRE_COMPACT)
        assert "message_count" in pre_data
        assert "token_estimate" in pre_data

        post_data = recorder.get_data(HookEvent.POST_COMPACT)
        assert "message_count_before" in post_data
        assert "message_count_after" in post_data

    @pytest.mark.asyncio
    async def test_no_compact_hooks_without_registry(self):
        """Engine works normally when hook_registry is None."""
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        async def fake_model(**kw):
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok",
                metadata={"stop_reason": "end_turn"},
            )}

        deps = Deps(call_model=fake_model)
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        events = []
        async for ev in engine.run("hello"):
            events.append(ev)

        assert any(e.get("type") == "done" for e in events)


class TestEngineEmitsSetupAndTaskHooks:
    """Verify SETUP, TASK_CREATED, and TASK_COMPLETED are emitted by the engine."""

    @pytest.mark.asyncio
    async def test_setup_hook_emitted_on_engine_run(self):
        """SETUP hook fires at the beginning of the first engine.run() call."""
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        recorder = _Recorder()
        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.SETUP,
            hook_type=HookType.FUNCTION,
            name="setup_hook",
            callback=recorder.callback,
        ))

        async def fake_model(**kw):
            yield {"type": "text_delta", "text": "ready"}
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ready",
                metadata={"stop_reason": "end_turn"},
            )}

        deps = Deps(call_model=fake_model, hook_registry=registry)
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        events = []
        async for ev in engine.run("hello"):
            events.append(ev)

        assert recorder.has_event(HookEvent.SETUP), "SETUP hook should fire on first run"
        data = recorder.get_data(HookEvent.SETUP)
        assert "session_id" in data

    @pytest.mark.asyncio
    async def test_task_created_hook_emitted_on_run(self):
        """TASK_CREATED hook fires when engine.run() begins processing a prompt."""
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        recorder = _Recorder()
        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.TASK_CREATED,
            hook_type=HookType.FUNCTION,
            name="task_created_hook",
            callback=recorder.callback,
        ))

        async def fake_model(**kw):
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok",
                metadata={"stop_reason": "end_turn"},
            )}

        deps = Deps(call_model=fake_model, hook_registry=registry)
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        events = []
        async for ev in engine.run("do the task"):
            events.append(ev)

        assert recorder.has_event(HookEvent.TASK_CREATED), "TASK_CREATED should fire"
        data = recorder.get_data(HookEvent.TASK_CREATED)
        assert "session_id" in data
        assert "turn" in data

    @pytest.mark.asyncio
    async def test_task_completed_hook_emitted_after_done(self):
        """TASK_COMPLETED hook fires after the query loop emits 'done'."""
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        recorder = _Recorder()
        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.TASK_COMPLETED,
            hook_type=HookType.FUNCTION,
            name="task_completed_hook",
            callback=recorder.callback,
        ))

        async def fake_model(**kw):
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "assistant", "message": Message(
                role="assistant", content="done",
                metadata={"stop_reason": "end_turn"},
            )}

        deps = Deps(call_model=fake_model, hook_registry=registry)
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        events = []
        async for ev in engine.run("finish this"):
            events.append(ev)

        assert recorder.has_event(HookEvent.TASK_COMPLETED), "TASK_COMPLETED should fire"
        data = recorder.get_data(HookEvent.TASK_COMPLETED)
        assert "session_id" in data
        assert "stop_reason" in data

    @pytest.mark.asyncio
    async def test_setup_fires_only_on_first_run(self):
        """SETUP fires once per engine session, not on every run() call."""
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        recorder = _Recorder()
        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.SETUP,
            hook_type=HookType.FUNCTION,
            name="setup_hook",
            callback=recorder.callback,
        ))

        async def fake_model(**kw):
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok",
                metadata={"stop_reason": "end_turn"},
            )}

        deps = Deps(call_model=fake_model, hook_registry=registry)
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        # Run twice
        async for _ in engine.run("first"):
            pass
        async for _ in engine.run("second"):
            pass

        assert recorder.count(HookEvent.SETUP) == 1, "SETUP should fire only once"

    @pytest.mark.asyncio
    async def test_task_hooks_fire_each_run(self):
        """TASK_CREATED and TASK_COMPLETED fire on each engine.run() call."""
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        recorder = _Recorder()
        registry = HookRegistry()
        for event in (HookEvent.TASK_CREATED, HookEvent.TASK_COMPLETED):
            registry.register(HookConfig(
                event=event,
                hook_type=HookType.FUNCTION,
                name=f"hook_{event.value}",
                callback=recorder.callback,
            ))

        async def fake_model(**kw):
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok",
                metadata={"stop_reason": "end_turn"},
            )}

        deps = Deps(call_model=fake_model, hook_registry=registry)
        config = EngineConfig(model="test-model")
        engine = Engine(deps=deps, config=config)

        async for _ in engine.run("turn 1"):
            pass
        async for _ in engine.run("turn 2"):
            pass

        assert recorder.count(HookEvent.TASK_CREATED) == 2
        assert recorder.count(HookEvent.TASK_COMPLETED) == 2
