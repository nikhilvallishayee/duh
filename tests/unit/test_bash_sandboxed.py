"""Tests for sandboxed BashTool execution."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.adapters.sandbox.policy import SandboxCommand, SandboxPolicy, SandboxType
from duh.kernel.tool import ToolContext
from duh.tools.bash import BashTool


class TestToolContextSandboxPolicy:
    def test_default_is_none(self):
        ctx = ToolContext()
        assert ctx.sandbox_policy is None

    def test_accepts_policy(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        ctx = ToolContext(sandbox_policy=policy)
        assert ctx.sandbox_policy is policy


class TestBashToolSandboxed:
    async def test_no_policy_runs_normally(self):
        tool = BashTool()
        ctx = ToolContext(cwd="/tmp", metadata={"skip_permissions": True})
        result = await tool.call({"command": "echo hello"}, ctx)
        assert "hello" in result.output

    @patch("duh.tools.bash.SandboxCommand")
    async def test_with_policy_wraps_command(self, mock_sandbox_cmd):
        """When sandbox_policy is set, the command should be wrapped."""
        policy = SandboxPolicy(writable_paths=["/tmp"])
        ctx = ToolContext(
            cwd="/tmp",
            sandbox_policy=policy,
            metadata={"skip_permissions": True},
        )

        # Mock SandboxCommand.build to return a passthrough
        mock_cmd = MagicMock()
        mock_cmd.argv = ["bash", "-c", "echo sandboxed"]
        mock_cmd.profile_path = None
        mock_cmd.env = None
        mock_cmd.cleanup = MagicMock()
        mock_sandbox_cmd.build.return_value = mock_cmd

        tool = BashTool()
        result = await tool.call({"command": "echo hello"}, ctx)

        # Verify SandboxCommand.build was called
        mock_sandbox_cmd.build.assert_called_once()
        call_args = mock_sandbox_cmd.build.call_args
        assert call_args.kwargs.get("command") == "echo hello" or call_args[0][0] == "echo hello"

    async def test_sandbox_policy_on_context(self):
        """Verify ToolContext carries the sandbox_policy."""
        policy = SandboxPolicy(writable_paths=["/tmp"], network_allowed=False)
        ctx = ToolContext(sandbox_policy=policy)
        assert ctx.sandbox_policy.network_allowed is False
        assert ctx.sandbox_policy.writable_paths == ["/tmp"]

    @patch("duh.tools.bash.detect_sandbox_type", return_value=SandboxType.NONE)
    async def test_none_sandbox_type_no_wrapping(self, mock_detect):
        """With SandboxType.NONE, command should pass through unwrapped."""
        policy = SandboxPolicy(writable_paths=["/tmp"])
        ctx = ToolContext(
            cwd="/tmp",
            sandbox_policy=policy,
            metadata={"skip_permissions": True},
        )
        tool = BashTool()
        result = await tool.call({"command": "echo passthrough"}, ctx)
        # Even with NONE type, the command should still run
        assert "passthrough" in result.output
