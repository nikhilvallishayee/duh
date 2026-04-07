"""Tests for duh.hooks — lifecycle hook system.

Covers: registration, dispatch, shell command hooks, function hooks,
timeout handling, error isolation, matcher filtering, config loading.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.hooks import (
    HookCallback,
    HookConfig,
    HookEvent,
    HookRegistry,
    HookResult,
    HookType,
    execute_hooks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cmd_hook(
    event: HookEvent = HookEvent.PRE_TOOL_USE,
    command: str = "echo ok",
    matcher: str = "",
    name: str = "",
    timeout: float = 30.0,
) -> HookConfig:
    return HookConfig(
        event=event,
        hook_type=HookType.COMMAND,
        name=name,
        matcher=matcher,
        command=command,
        timeout=timeout,
    )


def _fn_hook(
    event: HookEvent = HookEvent.PRE_TOOL_USE,
    callback: HookCallback | None = None,
    matcher: str = "",
    name: str = "",
    timeout: float = 30.0,
) -> HookConfig:
    return HookConfig(
        event=event,
        hook_type=HookType.FUNCTION,
        name=name,
        matcher=matcher,
        callback=callback,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Tests: HookRegistry
# ---------------------------------------------------------------------------


class TestHookRegistry:
    def test_register_and_get(self) -> None:
        reg = HookRegistry()
        hook = _cmd_hook(event=HookEvent.PRE_TOOL_USE)
        reg.register(hook)
        assert reg.get_hooks(HookEvent.PRE_TOOL_USE) == [hook]

    def test_get_empty_event(self) -> None:
        reg = HookRegistry()
        assert reg.get_hooks(HookEvent.SESSION_START) == []

    def test_register_multiple_events(self) -> None:
        reg = HookRegistry()
        h1 = _cmd_hook(event=HookEvent.PRE_TOOL_USE)
        h2 = _cmd_hook(event=HookEvent.POST_TOOL_USE)
        h3 = _cmd_hook(event=HookEvent.PRE_TOOL_USE)
        reg.register(h1)
        reg.register(h2)
        reg.register(h3)
        assert reg.get_hooks(HookEvent.PRE_TOOL_USE) == [h1, h3]
        assert reg.get_hooks(HookEvent.POST_TOOL_USE) == [h2]

    def test_unregister(self) -> None:
        reg = HookRegistry()
        hook = _cmd_hook()
        reg.register(hook)
        assert reg.unregister(hook) is True
        assert reg.get_hooks(HookEvent.PRE_TOOL_USE) == []

    def test_unregister_not_found(self) -> None:
        reg = HookRegistry()
        hook = _cmd_hook()
        assert reg.unregister(hook) is False

    def test_list_all(self) -> None:
        reg = HookRegistry()
        h1 = _cmd_hook(event=HookEvent.PRE_TOOL_USE)
        h2 = _cmd_hook(event=HookEvent.SESSION_START)
        reg.register(h1)
        reg.register(h2)
        all_hooks = reg.list_all()
        assert len(all_hooks) == 2
        assert h1 in all_hooks
        assert h2 in all_hooks

    def test_clear(self) -> None:
        reg = HookRegistry()
        reg.register(_cmd_hook())
        reg.register(_cmd_hook(event=HookEvent.SESSION_END))
        reg.clear()
        assert reg.list_all() == []

    def test_matcher_filtering(self) -> None:
        reg = HookRegistry()
        h_bash = _cmd_hook(matcher="Bash")
        h_read = _cmd_hook(matcher="Read")
        h_all = _cmd_hook(matcher="")  # matches everything
        reg.register(h_bash)
        reg.register(h_read)
        reg.register(h_all)

        # Filter by "Bash" — should get h_bash and h_all
        matches = reg.get_hooks(HookEvent.PRE_TOOL_USE, matcher_value="Bash")
        assert h_bash in matches
        assert h_all in matches
        assert h_read not in matches

    def test_matcher_none_returns_all(self) -> None:
        reg = HookRegistry()
        h1 = _cmd_hook(matcher="Bash")
        h2 = _cmd_hook(matcher="Read")
        reg.register(h1)
        reg.register(h2)
        # No matcher_value = return all
        assert len(reg.get_hooks(HookEvent.PRE_TOOL_USE)) == 2


# ---------------------------------------------------------------------------
# Tests: Config loading
# ---------------------------------------------------------------------------


class TestFromConfig:
    def test_basic_config(self) -> None:
        config = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": "echo pre-bash"}
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "echo post-all"}
                        ],
                    }
                ],
            }
        }
        reg = HookRegistry.from_config(config)
        pre = reg.get_hooks(HookEvent.PRE_TOOL_USE)
        assert len(pre) == 1
        assert pre[0].matcher == "Bash"
        assert pre[0].command == "echo pre-bash"

        post = reg.get_hooks(HookEvent.POST_TOOL_USE)
        assert len(post) == 1
        assert post[0].matcher == ""

    def test_empty_config(self) -> None:
        reg = HookRegistry.from_config({})
        assert reg.list_all() == []

    def test_unknown_event_skipped(self) -> None:
        config = {
            "hooks": {
                "BogusEvent": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "echo x"}]}
                ]
            }
        }
        reg = HookRegistry.from_config(config)
        assert reg.list_all() == []

    def test_unknown_hook_type_skipped(self) -> None:
        config = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "", "hooks": [{"type": "alien", "command": "echo x"}]}
                ]
            }
        }
        reg = HookRegistry.from_config(config)
        assert reg.list_all() == []

    def test_multiple_hooks_per_matcher(self) -> None:
        config = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "echo one"},
                            {"type": "command", "command": "echo two"},
                        ],
                    }
                ]
            }
        }
        reg = HookRegistry.from_config(config)
        hooks = reg.get_hooks(HookEvent.SESSION_START)
        assert len(hooks) == 2


# ---------------------------------------------------------------------------
# Tests: Shell command hooks
# ---------------------------------------------------------------------------


class TestCommandHookExecution:
    @pytest.mark.asyncio
    async def test_echo_command(self) -> None:
        reg = HookRegistry()
        reg.register(_cmd_hook(command="echo hello"))
        results = await execute_hooks(
            reg, HookEvent.PRE_TOOL_USE, {"tool_name": "Bash"}
        )
        assert len(results) == 1
        assert results[0].success is True
        assert "hello" in results[0].output

    @pytest.mark.asyncio
    async def test_failing_command(self) -> None:
        reg = HookRegistry()
        reg.register(_cmd_hook(command="exit 1"))
        results = await execute_hooks(reg, HookEvent.PRE_TOOL_USE, {})
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].exit_code == 1

    @pytest.mark.asyncio
    async def test_command_receives_json_stdin(self) -> None:
        # Use cat to echo back what it receives on stdin
        reg = HookRegistry()
        reg.register(_cmd_hook(command="cat"))
        data = {"tool_name": "Read", "path": "/tmp/test"}
        results = await execute_hooks(reg, HookEvent.PRE_TOOL_USE, data)
        assert len(results) == 1
        assert results[0].success is True
        import json
        output = json.loads(results[0].output)
        assert output["tool_name"] == "Read"
        assert output["path"] == "/tmp/test"

    @pytest.mark.asyncio
    async def test_command_timeout(self) -> None:
        reg = HookRegistry()
        reg.register(_cmd_hook(command="sleep 60", timeout=0.2))
        results = await execute_hooks(reg, HookEvent.PRE_TOOL_USE, {})
        assert len(results) == 1
        assert results[0].success is False
        assert "timed out" in results[0].error.lower()

    @pytest.mark.asyncio
    async def test_command_stderr(self) -> None:
        reg = HookRegistry()
        reg.register(_cmd_hook(command="echo error >&2; exit 2"))
        results = await execute_hooks(reg, HookEvent.PRE_TOOL_USE, {})
        assert len(results) == 1
        assert results[0].success is False
        assert "error" in results[0].error
        assert results[0].exit_code == 2


# ---------------------------------------------------------------------------
# Tests: Function hooks
# ---------------------------------------------------------------------------


class TestFunctionHookExecution:
    @pytest.mark.asyncio
    async def test_sync_callback(self) -> None:
        def my_callback(event: HookEvent, data: dict[str, Any]) -> HookResult:
            return HookResult(
                hook_name="my_hook",
                success=True,
                output=f"processed {data.get('tool_name', '')}",
            )

        reg = HookRegistry()
        reg.register(_fn_hook(callback=my_callback, name="my_hook"))
        results = await execute_hooks(
            reg, HookEvent.PRE_TOOL_USE, {"tool_name": "Edit"}
        )
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].output == "processed Edit"

    @pytest.mark.asyncio
    async def test_async_callback(self) -> None:
        async def my_async_callback(
            event: HookEvent, data: dict[str, Any]
        ) -> HookResult:
            return HookResult(hook_name="async_hook", success=True, output="async ok")

        reg = HookRegistry()
        reg.register(_fn_hook(callback=my_async_callback, name="async_hook"))
        results = await execute_hooks(reg, HookEvent.PRE_TOOL_USE, {})
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].output == "async ok"

    @pytest.mark.asyncio
    async def test_callback_exception_caught(self) -> None:
        def failing_cb(event: HookEvent, data: dict[str, Any]) -> HookResult:
            raise ValueError("hook crashed")

        reg = HookRegistry()
        reg.register(_fn_hook(callback=failing_cb, name="crasher"))
        results = await execute_hooks(reg, HookEvent.PRE_TOOL_USE, {})
        assert len(results) == 1
        assert results[0].success is False
        assert "hook crashed" in results[0].error

    @pytest.mark.asyncio
    async def test_callback_timeout(self) -> None:
        async def slow_cb(event: HookEvent, data: dict[str, Any]) -> HookResult:
            await asyncio.sleep(60)
            return HookResult(hook_name="slow", success=True)

        reg = HookRegistry()
        reg.register(_fn_hook(callback=slow_cb, name="slow", timeout=0.2))
        results = await execute_hooks(reg, HookEvent.PRE_TOOL_USE, {})
        assert len(results) == 1
        assert results[0].success is False
        assert "timed out" in results[0].error.lower()

    @pytest.mark.asyncio
    async def test_no_callback_fails(self) -> None:
        reg = HookRegistry()
        reg.register(_fn_hook(callback=None, name="no_cb"))
        results = await execute_hooks(reg, HookEvent.PRE_TOOL_USE, {})
        assert len(results) == 1
        assert results[0].success is False
        assert "no callback" in results[0].error.lower()

    @pytest.mark.asyncio
    async def test_callback_returns_non_hookresult(self) -> None:
        """If callback returns a plain string, it gets wrapped."""

        def string_cb(event: HookEvent, data: dict[str, Any]) -> Any:
            return "just a string"

        reg = HookRegistry()
        reg.register(_fn_hook(callback=string_cb, name="string_hook"))
        results = await execute_hooks(reg, HookEvent.PRE_TOOL_USE, {})
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].output == "just a string"


# ---------------------------------------------------------------------------
# Tests: Error isolation and multiple hooks
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_failing_hook_does_not_block_others(self) -> None:
        """All hooks run even if one fails."""

        def fail_cb(event: HookEvent, data: dict[str, Any]) -> HookResult:
            raise RuntimeError("kaboom")

        def ok_cb(event: HookEvent, data: dict[str, Any]) -> HookResult:
            return HookResult(hook_name="ok", success=True, output="fine")

        reg = HookRegistry()
        reg.register(_fn_hook(callback=fail_cb, name="fail"))
        reg.register(_fn_hook(callback=ok_cb, name="ok"))
        results = await execute_hooks(reg, HookEvent.PRE_TOOL_USE, {})
        assert len(results) == 2
        assert results[0].success is False
        assert results[1].success is True

    @pytest.mark.asyncio
    async def test_no_hooks_returns_empty(self) -> None:
        reg = HookRegistry()
        results = await execute_hooks(reg, HookEvent.SESSION_START, {})
        assert results == []


# ---------------------------------------------------------------------------
# Tests: Pre/Post tool use events
# ---------------------------------------------------------------------------


class TestToolUseEvents:
    @pytest.mark.asyncio
    async def test_pre_tool_use_with_matcher(self) -> None:
        calls: list[str] = []

        def bash_hook(event: HookEvent, data: dict[str, Any]) -> HookResult:
            calls.append("bash")
            return HookResult(hook_name="bash_hook", success=True)

        def read_hook(event: HookEvent, data: dict[str, Any]) -> HookResult:
            calls.append("read")
            return HookResult(hook_name="read_hook", success=True)

        reg = HookRegistry()
        reg.register(_fn_hook(callback=bash_hook, matcher="Bash", name="bash_hook"))
        reg.register(_fn_hook(callback=read_hook, matcher="Read", name="read_hook"))

        # Fire for Bash — only bash_hook should run
        results = await execute_hooks(
            reg,
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"},
            matcher_value="Bash",
        )
        assert len(results) == 1
        assert calls == ["bash"]

    @pytest.mark.asyncio
    async def test_post_tool_use_event(self) -> None:
        log: list[dict[str, Any]] = []

        def log_hook(event: HookEvent, data: dict[str, Any]) -> HookResult:
            log.append({"event": event, "tool": data.get("tool_name")})
            return HookResult(hook_name="logger", success=True)

        reg = HookRegistry()
        reg.register(_fn_hook(
            event=HookEvent.POST_TOOL_USE,
            callback=log_hook,
            name="logger",
        ))
        await execute_hooks(
            reg,
            HookEvent.POST_TOOL_USE,
            {"tool_name": "Write", "result": "ok"},
        )
        assert len(log) == 1
        assert log[0]["event"] == HookEvent.POST_TOOL_USE
        assert log[0]["tool"] == "Write"


# ---------------------------------------------------------------------------
# Tests: Timeout override
# ---------------------------------------------------------------------------


class TestTimeoutOverride:
    @pytest.mark.asyncio
    async def test_global_timeout_overrides_hook_timeout(self) -> None:
        """The timeout kwarg to execute_hooks overrides per-hook timeout."""
        async def slow_cb(event: HookEvent, data: dict[str, Any]) -> HookResult:
            await asyncio.sleep(60)
            return HookResult(hook_name="slow", success=True)

        reg = HookRegistry()
        # Hook has generous 300s timeout, but we override to 0.1s
        reg.register(_fn_hook(callback=slow_cb, name="slow", timeout=300.0))
        results = await execute_hooks(
            reg, HookEvent.PRE_TOOL_USE, {}, timeout=0.1
        )
        assert len(results) == 1
        assert results[0].success is False
        assert "timed out" in results[0].error.lower()


# ---------------------------------------------------------------------------
# Tests: All event types
# ---------------------------------------------------------------------------


class TestAllEventTypes:
    @pytest.mark.asyncio
    async def test_session_start_hook(self) -> None:
        reg = HookRegistry()
        reg.register(_cmd_hook(event=HookEvent.SESSION_START, command="echo started"))
        results = await execute_hooks(
            reg, HookEvent.SESSION_START, {"source": "startup"}
        )
        assert len(results) == 1
        assert results[0].success is True

    @pytest.mark.asyncio
    async def test_session_end_hook(self) -> None:
        reg = HookRegistry()
        reg.register(_cmd_hook(event=HookEvent.SESSION_END, command="echo ended"))
        results = await execute_hooks(
            reg, HookEvent.SESSION_END, {"reason": "clear"}
        )
        assert len(results) == 1
        assert results[0].success is True

    @pytest.mark.asyncio
    async def test_stop_hook(self) -> None:
        reg = HookRegistry()
        reg.register(_cmd_hook(event=HookEvent.STOP, command="echo stop"))
        results = await execute_hooks(reg, HookEvent.STOP, {})
        assert len(results) == 1
        assert results[0].success is True

    @pytest.mark.asyncio
    async def test_notification_hook(self) -> None:
        reg = HookRegistry()
        reg.register(_cmd_hook(event=HookEvent.NOTIFICATION, command="echo notified"))
        results = await execute_hooks(
            reg, HookEvent.NOTIFICATION, {"message": "test", "type": "info"}
        )
        assert len(results) == 1
        assert results[0].success is True
