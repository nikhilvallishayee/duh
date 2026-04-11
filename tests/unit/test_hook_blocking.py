"""Tests for hook blocking semantics -- hooks that can veto tool calls."""

from __future__ import annotations

import json

import pytest

from duh.hooks import (
    HookConfig,
    HookEvent,
    HookRegistry,
    HookResponse,
    HookResult,
    HookType,
    execute_hooks_with_blocking,
    _glob_match,
)


class TestHookResponse:
    def test_default_is_continue(self):
        r = HookResponse()
        assert r.decision == "continue"
        assert r.suppress_output is False
        assert r.message == ""

    def test_block_decision(self):
        r = HookResponse(decision="block", message="denied by policy")
        assert r.decision == "block"
        assert r.message == "denied by policy"

    def test_allow_decision(self):
        r = HookResponse(decision="allow")
        assert r.decision == "allow"

    def test_from_json_block(self):
        raw = json.dumps({"decision": "block", "message": "nope"})
        r = HookResponse.from_json(raw)
        assert r.decision == "block"
        assert r.message == "nope"

    def test_from_json_invalid_falls_back_to_continue(self):
        r = HookResponse.from_json("not json at all")
        assert r.decision == "continue"

    def test_from_json_empty_string(self):
        r = HookResponse.from_json("")
        assert r.decision == "continue"

    def test_from_json_suppress_output(self):
        raw = json.dumps({"decision": "allow", "suppress_output": True})
        r = HookResponse.from_json(raw)
        assert r.suppress_output is True

    def test_from_json_non_dict_falls_back(self):
        r = HookResponse.from_json(json.dumps([1, 2, 3]))
        assert r.decision == "continue"


class TestGlobMatcher:
    def test_exact_match(self):
        assert _glob_match("Bash", "Bash") is True

    def test_wildcard_match(self):
        assert _glob_match("Bash(git *)", "Bash(git push)") is True

    def test_wildcard_no_match(self):
        assert _glob_match("Bash(git *)", "Bash(rm -rf /)") is False

    def test_empty_matcher_matches_all(self):
        assert _glob_match("", "anything") is True

    def test_star_matches_everything(self):
        assert _glob_match("*", "Bash") is True

    def test_question_mark(self):
        assert _glob_match("Bas?", "Bash") is True
        assert _glob_match("Bas?", "Bass") is True
        assert _glob_match("Bas?", "Basic") is False


class TestBlockingExecution:
    @pytest.mark.asyncio
    async def test_no_hooks_returns_continue(self):
        registry = HookRegistry()
        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"}, matcher_value="Bash",
        )
        assert response.decision == "continue"

    @pytest.mark.asyncio
    async def test_blocking_hook_returns_block(self):
        registry = HookRegistry()

        async def blocker(event, data):
            return HookResult(
                hook_name="blocker",
                success=True,
                output=json.dumps({"decision": "block", "message": "blocked by test"}),
            )

        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.FUNCTION,
            name="blocker",
            callback=blocker,
        ))
        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"}, matcher_value="Bash",
        )
        assert response.decision == "block"
        assert response.message == "blocked by test"

    @pytest.mark.asyncio
    async def test_allow_hook_returns_allow(self):
        registry = HookRegistry()

        async def allower(event, data):
            return HookResult(
                hook_name="allower",
                success=True,
                output=json.dumps({"decision": "allow"}),
            )

        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.FUNCTION,
            name="allower",
            callback=allower,
        ))
        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Read"}, matcher_value="Read",
        )
        assert response.decision == "allow"

    @pytest.mark.asyncio
    async def test_first_block_wins(self):
        """If any hook returns block, the result is block."""
        registry = HookRegistry()

        async def allower(event, data):
            return HookResult(hook_name="allower", success=True,
                              output=json.dumps({"decision": "allow"}))

        async def blocker(event, data):
            return HookResult(hook_name="blocker", success=True,
                              output=json.dumps({"decision": "block", "message": "no"}))

        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE, hook_type=HookType.FUNCTION,
            name="allower", callback=allower,
        ))
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE, hook_type=HookType.FUNCTION,
            name="blocker", callback=blocker,
        ))
        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"}, matcher_value="Bash",
        )
        assert response.decision == "block"

    @pytest.mark.asyncio
    async def test_hook_with_no_json_output_is_continue(self):
        """Hook that returns plain text (not JSON) is treated as continue."""
        registry = HookRegistry()

        async def plain_hook(event, data):
            return HookResult(hook_name="plain", success=True, output="just text")

        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE, hook_type=HookType.FUNCTION,
            name="plain", callback=plain_hook,
        ))
        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"}, matcher_value="Bash",
        )
        assert response.decision == "continue"

    @pytest.mark.asyncio
    async def test_failed_hook_is_not_blocking(self):
        """A hook that fails (success=False) should not produce a block."""
        registry = HookRegistry()

        async def failing_hook(event, data):
            raise RuntimeError("boom")

        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE, hook_type=HookType.FUNCTION,
            name="failing", callback=failing_hook,
        ))
        response = await execute_hooks_with_blocking(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash"}, matcher_value="Bash",
        )
        assert response.decision == "continue"


class TestGlobMatchInRegistry:
    """Test that glob matching works within the registry's get_hooks."""

    def test_glob_pattern_in_registry(self):
        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.COMMAND,
            name="git_hook",
            matcher="Bash(git *)",
            command="echo blocked",
        ))
        # Should match
        hooks = registry.get_hooks(
            HookEvent.PRE_TOOL_USE, matcher_value="Bash(git push)"
        )
        assert len(hooks) == 1

        # Should not match
        hooks = registry.get_hooks(
            HookEvent.PRE_TOOL_USE, matcher_value="Bash(rm -rf /)"
        )
        assert len(hooks) == 0

    def test_exact_match_still_works(self):
        registry = HookRegistry()
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.COMMAND,
            name="bash_hook",
            matcher="Bash",
            command="echo ok",
        ))
        hooks = registry.get_hooks(
            HookEvent.PRE_TOOL_USE, matcher_value="Bash"
        )
        assert len(hooks) == 1


class TestCommandHookEnvVars:
    """Test that command hooks receive TOOL_NAME, TOOL_INPUT, SESSION_ID env vars."""

    @pytest.mark.asyncio
    async def test_env_vars_set_on_command_hook(self):
        """Verify env vars are passed to the subprocess."""
        registry = HookRegistry()
        # Use a command that prints the env vars
        registry.register(HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.COMMAND,
            name="env_check",
            command='echo "$TOOL_NAME|$TOOL_INPUT|$SESSION_ID"',
        ))
        from duh.hooks import execute_hooks
        results = await execute_hooks(
            registry, HookEvent.PRE_TOOL_USE,
            {"tool_name": "Bash", "input": {"command": "ls"}, "session_id": "sess-42"},
            matcher_value="Bash",
        )
        assert len(results) == 1
        assert results[0].success is True
        parts = results[0].output.strip().split("|")
        assert parts[0] == "Bash"
        assert "ls" in parts[1]  # JSON of input
        assert parts[2] == "sess-42"
