"""Tests for per-tool configurable timeouts.

Covers:
- TOOL_TIMEOUTS config and get_tool_timeout() helper
- NativeExecutor timeout wrapping via asyncio.wait_for
- BashTool using TOOL_TIMEOUTS as its default
"""

from __future__ import annotations

import asyncio

import pytest

from duh.kernel.tool import (
    DEFAULT_TIMEOUT,
    TOOL_TIMEOUTS,
    ToolContext,
    ToolResult,
    get_tool_timeout,
)
from duh.adapters.native_executor import NativeExecutor


# ---------------------------------------------------------------------------
# Helpers — mock tools
# ---------------------------------------------------------------------------

class FastTool:
    """Completes instantly."""
    name = "Fast"
    description = "Returns immediately"
    input_schema = {}

    async def call(self, input, context):
        return ToolResult(output="done")


class SlowTool:
    """Sleeps longer than any reasonable timeout."""
    name = "Slow"
    description = "Sleeps forever"
    input_schema = {}

    async def call(self, input, context):
        await asyncio.sleep(9999)
        return ToolResult(output="should not reach")


class CustomNamedTool:
    """Tool whose name matches a TOOL_TIMEOUTS key (Grep = 60s)."""
    name = "Grep"
    description = "Pretend Grep (sleeps forever)"
    input_schema = {}

    async def call(self, input, context):
        await asyncio.sleep(9999)
        return ToolResult(output="should not reach")


class UnknownNameTool:
    """Tool not in TOOL_TIMEOUTS — should get DEFAULT_TIMEOUT."""
    name = "Zyzzyx"
    description = "Unknown tool"
    input_schema = {}

    async def call(self, input, context):
        await asyncio.sleep(9999)
        return ToolResult(output="should not reach")


# ===========================================================================
# get_tool_timeout()
# ===========================================================================

class TestGetToolTimeout:
    def test_known_tool_returns_configured_timeout(self):
        assert get_tool_timeout("Bash") == 300
        assert get_tool_timeout("Read") == 30
        assert get_tool_timeout("Grep") == 60
        assert get_tool_timeout("Task") == 5

    def test_unknown_tool_returns_default(self):
        assert get_tool_timeout("Nonexistent") == DEFAULT_TIMEOUT
        assert get_tool_timeout("") == DEFAULT_TIMEOUT

    def test_all_entries_are_positive_ints(self):
        for name, timeout in TOOL_TIMEOUTS.items():
            assert isinstance(timeout, int), f"{name} timeout is not int"
            assert timeout > 0, f"{name} timeout is not positive"

    def test_default_timeout_is_120(self):
        assert DEFAULT_TIMEOUT == 120


# ===========================================================================
# NativeExecutor — timeout behaviour
# ===========================================================================

class TestExecutorTimeouts:
    async def test_fast_tool_completes_normally(self):
        """A tool that finishes within timeout returns its result."""
        e = NativeExecutor(tools=[FastTool()])
        result = await e.run("Fast", {})
        assert result == "done"

    async def test_slow_tool_times_out(self):
        """A tool that exceeds its timeout returns an error string (no crash)."""
        # Slow is not in TOOL_TIMEOUTS → uses DEFAULT_TIMEOUT (120).
        # We monkey-patch to 0.05s so the test is fast.
        import duh.adapters.native_executor as mod
        original = mod.get_tool_timeout

        mod.get_tool_timeout = lambda name: 0.05  # 50ms
        try:
            e = NativeExecutor(tools=[SlowTool()])
            result = await e.run("Slow", {})
            assert "timed out" in result
            assert "Slow" in result
            assert "0.05s" in result
        finally:
            mod.get_tool_timeout = original

    async def test_timeout_uses_per_tool_value(self):
        """The executor passes the per-tool timeout to wait_for."""
        import duh.adapters.native_executor as mod
        original = mod.get_tool_timeout

        mod.get_tool_timeout = lambda name: 0.05 if name == "Grep" else 9999
        try:
            e = NativeExecutor(tools=[CustomNamedTool()])
            result = await e.run("Grep", {})
            assert "timed out" in result
            assert "Grep" in result
        finally:
            mod.get_tool_timeout = original

    async def test_timeout_error_message_format(self):
        """Error message includes tool name, timeout value, and guidance."""
        import duh.adapters.native_executor as mod
        original = mod.get_tool_timeout

        mod.get_tool_timeout = lambda name: 0.05
        try:
            e = NativeExecutor(tools=[SlowTool()])
            result = await e.run("Slow", {})
            assert result == (
                "Tool 'Slow' timed out after 0.05s."
                " Try a simpler command or increase timeout."
            )
        finally:
            mod.get_tool_timeout = original

    async def test_timeout_does_not_raise(self):
        """Timeout returns a string — does NOT raise an exception."""
        import duh.adapters.native_executor as mod
        original = mod.get_tool_timeout

        mod.get_tool_timeout = lambda name: 0.05
        try:
            e = NativeExecutor(tools=[SlowTool()])
            # Should NOT raise
            result = await e.run("Slow", {})
            assert isinstance(result, str)
        finally:
            mod.get_tool_timeout = original

    async def test_default_timeout_for_unknown_tool(self):
        """A tool not in TOOL_TIMEOUTS gets DEFAULT_TIMEOUT (120s)."""
        # We don't actually wait 120s — just verify the value is used.
        import duh.adapters.native_executor as mod

        captured_timeouts = []
        real_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, *, timeout=None):
            captured_timeouts.append(timeout)
            return await real_wait_for(coro, timeout=timeout)

        original_wf = asyncio.wait_for
        asyncio.wait_for = spy_wait_for
        try:
            e = NativeExecutor(tools=[FastTool()])
            await e.run("Fast", {})
            assert captured_timeouts[-1] == DEFAULT_TIMEOUT
        finally:
            asyncio.wait_for = original_wf

    async def test_known_tool_gets_correct_timeout_value(self):
        """A tool in TOOL_TIMEOUTS gets its configured value passed to wait_for."""
        import duh.adapters.native_executor as mod

        captured_timeouts = []
        real_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, *, timeout=None):
            captured_timeouts.append(timeout)
            return await real_wait_for(coro, timeout=timeout)

        # Use a fast tool but registered under a known name
        class FakeGrep:
            name = "Grep"
            description = "Fast fake grep"
            input_schema = {}
            async def call(self, input, context):
                return ToolResult(output="found")

        original_wf = asyncio.wait_for
        asyncio.wait_for = spy_wait_for
        try:
            e = NativeExecutor(tools=[FakeGrep()])
            result = await e.run("Grep", {})
            assert result == "found"
            assert captured_timeouts[-1] == TOOL_TIMEOUTS["Grep"]  # 60
        finally:
            asyncio.wait_for = original_wf

    async def test_permission_check_still_works_with_timeout(self):
        """Permission denied still raises before timeout wrapping kicks in."""
        class DenyTool:
            name = "Deny"
            description = "Always denied"
            input_schema = {}
            async def call(self, input, context):
                return ToolResult(output="nope")
            async def check_permissions(self, input, context):
                return {"allowed": False, "reason": "nope"}

        e = NativeExecutor(tools=[DenyTool()])
        with pytest.raises(PermissionError, match="nope"):
            await e.run("Deny", {})


# ===========================================================================
# BashTool — default timeout from TOOL_TIMEOUTS
# ===========================================================================

class TestBashToolTimeout:
    def test_bash_default_timeout_matches_tool_timeouts(self):
        """BashTool._DEFAULT_TIMEOUT should come from TOOL_TIMEOUTS['Bash']."""
        from duh.tools.bash import _DEFAULT_TIMEOUT
        assert _DEFAULT_TIMEOUT == TOOL_TIMEOUTS["Bash"]  # 300
        assert _DEFAULT_TIMEOUT == 300
