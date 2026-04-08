# tests/unit/test_hook_events_extended.py
"""Tests for the 22 new HookEvent enum members.

Verifies:
1. All 28 events (6 original + 22 new) exist in the enum.
2. Each new event can be registered, dispatched, and executed.
3. The existing dispatch mechanism handles all events identically.
"""

from __future__ import annotations

from typing import Any

import pytest

from duh.hooks import (
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

def _fn_hook(
    event: HookEvent,
    name: str = "test_hook",
) -> HookConfig:
    def _cb(ev: HookEvent, data: dict[str, Any]) -> HookResult:
        return HookResult(hook_name=name, success=True, output=ev.value)
    return HookConfig(
        event=event,
        hook_type=HookType.FUNCTION,
        name=name,
        callback=_cb,
    )


# ===========================================================================
# All 28 events exist
# ===========================================================================

class TestAllEventsExist:
    """Verify every expected HookEvent member is defined."""

    EXPECTED_EVENTS = [
        # Original 6
        "PRE_TOOL_USE",
        "POST_TOOL_USE",
        "SESSION_START",
        "SESSION_END",
        "NOTIFICATION",
        "STOP",
        # New 22
        "POST_TOOL_USE_FAILURE",
        "SUBAGENT_START",
        "SUBAGENT_STOP",
        "TASK_CREATED",
        "TASK_COMPLETED",
        "CONFIG_CHANGE",
        "CWD_CHANGED",
        "FILE_CHANGED",
        "INSTRUCTIONS_LOADED",
        "USER_PROMPT_SUBMIT",
        "PERMISSION_REQUEST",
        "PERMISSION_DENIED",
        "PRE_COMPACT",
        "POST_COMPACT",
        "ELICITATION",
        "ELICITATION_RESULT",
        "STATUS_LINE",
        "FILE_SUGGESTION",
        "WORKTREE_CREATE",
        "WORKTREE_REMOVE",
        "SETUP",
        "TEAMMATE_IDLE",
    ]

    @pytest.mark.parametrize("event_name", EXPECTED_EVENTS)
    def test_event_exists(self, event_name: str):
        assert hasattr(HookEvent, event_name), f"HookEvent.{event_name} missing"

    def test_total_count(self):
        """There should be exactly 28 events (6 original + 22 new)."""
        assert len(HookEvent) == 28


# ===========================================================================
# Each new event dispatches correctly
# ===========================================================================

class TestNewEventDispatch:
    """Every new event should work with the existing dispatch mechanism."""

    NEW_EVENTS = [
        HookEvent.POST_TOOL_USE_FAILURE,
        HookEvent.SUBAGENT_START,
        HookEvent.SUBAGENT_STOP,
        HookEvent.TASK_CREATED,
        HookEvent.TASK_COMPLETED,
        HookEvent.CONFIG_CHANGE,
        HookEvent.CWD_CHANGED,
        HookEvent.FILE_CHANGED,
        HookEvent.INSTRUCTIONS_LOADED,
        HookEvent.USER_PROMPT_SUBMIT,
        HookEvent.PERMISSION_REQUEST,
        HookEvent.PERMISSION_DENIED,
        HookEvent.PRE_COMPACT,
        HookEvent.POST_COMPACT,
        HookEvent.ELICITATION,
        HookEvent.ELICITATION_RESULT,
        HookEvent.STATUS_LINE,
        HookEvent.FILE_SUGGESTION,
        HookEvent.WORKTREE_CREATE,
        HookEvent.WORKTREE_REMOVE,
        HookEvent.SETUP,
        HookEvent.TEAMMATE_IDLE,
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("event", NEW_EVENTS, ids=lambda e: e.name)
    async def test_dispatch_new_event(self, event: HookEvent):
        """Register a function hook for each new event and verify it fires."""
        reg = HookRegistry()
        reg.register(_fn_hook(event, name=f"hook_{event.name}"))

        results = await execute_hooks(
            reg, event, {"source": "test"}, timeout=5.0
        )
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].output == event.value


# ===========================================================================
# Registry handles all events
# ===========================================================================

class TestRegistryMultiEvent:
    def test_register_all_events(self):
        """Registering hooks for all 28 events should work."""
        reg = HookRegistry()
        for event in HookEvent:
            reg.register(_fn_hook(event, name=f"hook_{event.name}"))
        assert len(reg.list_all()) == 28

    def test_get_hooks_per_event(self):
        """Each event should have exactly one hook after registering one per event."""
        reg = HookRegistry()
        for event in HookEvent:
            reg.register(_fn_hook(event, name=f"hook_{event.name}"))
        for event in HookEvent:
            hooks = reg.get_hooks(event)
            assert len(hooks) == 1, f"Expected 1 hook for {event.name}, got {len(hooks)}"


# ===========================================================================
# Config loading with new events
# ===========================================================================

class TestConfigLoadingNewEvents:
    def test_new_event_from_config(self):
        """New events should be loadable from config dict."""
        config = {
            "hooks": {
                "PreCompact": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "echo pre-compact"}
                        ],
                    }
                ],
                "PostCompact": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "echo post-compact"}
                        ],
                    }
                ],
                "UserPromptSubmit": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "echo prompt"}
                        ],
                    }
                ],
            }
        }
        reg = HookRegistry.from_config(config)
        assert len(reg.get_hooks(HookEvent.PRE_COMPACT)) == 1
        assert len(reg.get_hooks(HookEvent.POST_COMPACT)) == 1
        assert len(reg.get_hooks(HookEvent.USER_PROMPT_SUBMIT)) == 1

    def test_unknown_event_still_skipped(self):
        config = {
            "hooks": {
                "BogusNewEvent": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "echo x"}]}
                ]
            }
        }
        reg = HookRegistry.from_config(config)
        assert reg.list_all() == []


# ===========================================================================
# Matcher filtering works for new events
# ===========================================================================

class TestMatcherNewEvents:
    @pytest.mark.asyncio
    async def test_matcher_on_new_event(self):
        """Matcher filtering should work on new events too."""
        calls: list[str] = []

        def cb_a(ev: HookEvent, data: dict[str, Any]) -> HookResult:
            calls.append("a")
            return HookResult(hook_name="a", success=True)

        def cb_b(ev: HookEvent, data: dict[str, Any]) -> HookResult:
            calls.append("b")
            return HookResult(hook_name="b", success=True)

        reg = HookRegistry()
        reg.register(HookConfig(
            event=HookEvent.FILE_CHANGED,
            hook_type=HookType.FUNCTION,
            name="a",
            matcher="*.py",
            callback=cb_a,
        ))
        reg.register(HookConfig(
            event=HookEvent.FILE_CHANGED,
            hook_type=HookType.FUNCTION,
            name="b",
            matcher="*.js",
            callback=cb_b,
        ))

        results = await execute_hooks(
            reg, HookEvent.FILE_CHANGED,
            {"path": "foo.py"},
            matcher_value="*.py",
        )
        assert len(results) == 1
        assert calls == ["a"]
