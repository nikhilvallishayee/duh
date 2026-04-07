"""Tests for DockerTool -- Docker container integration."""

from __future__ import annotations

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.docker_tool import DockerTool, _docker_available


def ctx(cwd: str = "/tmp/project") -> ToolContext:
    return ToolContext(cwd=cwd)


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestDockerProtocol:

    def test_satisfies_tool_protocol(self):
        tool = DockerTool()
        assert isinstance(tool, Tool)

    def test_name(self):
        assert DockerTool().name == "Docker"

    def test_description_non_empty(self):
        assert DockerTool().description

    def test_input_schema_structure(self):
        schema = DockerTool().input_schema
        assert schema["type"] == "object"
        assert "action" in schema["properties"]
        assert "action" in schema["required"]

    def test_is_not_read_only(self):
        assert DockerTool().is_read_only is False

    def test_is_not_destructive(self):
        assert DockerTool().is_destructive is False

    async def test_check_permissions(self):
        result = await DockerTool().check_permissions({}, ctx())
        assert result["allowed"] is True


# ===========================================================================
# Docker not installed
# ===========================================================================


class TestDockerNotInstalled:
    tool = DockerTool()

    async def test_graceful_error_when_docker_missing(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=False):
            result = await self.tool.call({"action": "ps"}, ctx())
        assert result.is_error is True
        assert "not installed" in result.output.lower()

    async def test_all_actions_fail_gracefully(self):
        """Every action should return the same 'not installed' message."""
        for action in ("build", "run", "ps", "logs", "exec", "images"):
            with patch("duh.tools.docker_tool._docker_available", return_value=False):
                result = await self.tool.call({"action": action}, ctx())
            assert result.is_error is True
            assert "docker" in result.output.lower()


# ===========================================================================
# Input validation
# ===========================================================================


class TestDockerValidation:
    tool = DockerTool()

    async def test_missing_action(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True):
            result = await self.tool.call({}, ctx())
        assert result.is_error is True
        assert "action" in result.output.lower()

    async def test_unknown_action(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True):
            result = await self.tool.call({"action": "destroy"}, ctx())
        assert result.is_error is True
        assert "unknown" in result.output.lower()

    async def test_build_requires_tag(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True):
            result = await self.tool.call({"action": "build", "path": "."}, ctx())
        assert result.is_error is True
        assert "tag" in result.output.lower()

    async def test_build_requires_path(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True):
            result = await self.tool.call({"action": "build", "tag": "myimg"}, ctx())
        assert result.is_error is True
        assert "path" in result.output.lower()

    async def test_run_requires_image(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True):
            result = await self.tool.call({"action": "run"}, ctx())
        assert result.is_error is True
        assert "image" in result.output.lower()

    async def test_logs_requires_container(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True):
            result = await self.tool.call({"action": "logs"}, ctx())
        assert result.is_error is True
        assert "container" in result.output.lower()

    async def test_exec_requires_container(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True):
            result = await self.tool.call({"action": "exec", "command": "ls"}, ctx())
        assert result.is_error is True
        assert "container" in result.output.lower()

    async def test_exec_requires_command(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True):
            result = await self.tool.call({"action": "exec", "container": "abc"}, ctx())
        assert result.is_error is True
        assert "command" in result.output.lower()


# ===========================================================================
# Action: build
# ===========================================================================


def _mock_run(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a mock subprocess.CompletedProcess."""
    cp = subprocess.CompletedProcess(
        args=["docker"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
    return cp


class TestDockerBuild:
    tool = DockerTool()

    async def test_build_success(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout="Successfully built abc123")):
            result = await self.tool.call(
                {"action": "build", "tag": "myapp:latest", "path": "."},
                ctx(),
            )
        assert result.is_error is False
        assert "abc123" in result.output

    async def test_build_failure(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stderr="no Dockerfile", returncode=1)):
            result = await self.tool.call(
                {"action": "build", "tag": "myapp", "path": "/bad"},
                ctx(),
            )
        assert result.is_error is True
        assert "Dockerfile" in result.output


# ===========================================================================
# Action: run
# ===========================================================================


class TestDockerRun:
    tool = DockerTool()

    async def test_run_simple(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout="hello world")) as mock:
            result = await self.tool.call(
                {"action": "run", "image": "alpine"},
                ctx(),
            )
        assert result.is_error is False
        assert "hello world" in result.output
        # Verify --rm is passed
        call_args = mock.call_args[0][0]
        assert "--rm" in call_args

    async def test_run_with_command(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout="bin  etc  usr")) as mock:
            result = await self.tool.call(
                {"action": "run", "image": "alpine", "command": "ls /"},
                ctx(),
            )
        assert result.is_error is False
        call_args = mock.call_args[0][0]
        assert "sh" in call_args
        assert "-c" in call_args
        assert "ls /" in call_args

    async def test_run_with_mount_cwd(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout="ok")) as mock:
            result = await self.tool.call(
                {"action": "run", "image": "alpine", "mount_cwd": True},
                ctx(cwd="/home/user/project"),
            )
        assert result.is_error is False
        call_args = mock.call_args[0][0]
        assert "-v" in call_args
        assert "/home/user/project:/workspace" in call_args
        assert "-w" in call_args

    async def test_run_mount_cwd_ignored_when_dot(self):
        """When cwd is '.' mount_cwd should not add -v flag."""
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout="ok")) as mock:
            result = await self.tool.call(
                {"action": "run", "image": "alpine", "mount_cwd": True},
                ctx(cwd="."),
            )
        assert result.is_error is False
        call_args = mock.call_args[0][0]
        assert "-v" not in call_args


# ===========================================================================
# Action: ps
# ===========================================================================


class TestDockerPs:
    tool = DockerTool()

    async def test_ps_returns_json(self):
        json_output = '{"ID":"abc","Image":"nginx","Status":"Up 2 hours"}'
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout=json_output)) as mock:
            result = await self.tool.call({"action": "ps"}, ctx())
        assert result.is_error is False
        assert "abc" in result.output
        call_args = mock.call_args[0][0]
        assert "--format" in call_args
        assert "json" in call_args

    async def test_ps_no_containers(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout="")):
            result = await self.tool.call({"action": "ps"}, ctx())
        assert result.is_error is False
        assert "no output" in result.output.lower()


# ===========================================================================
# Action: logs
# ===========================================================================


class TestDockerLogs:
    tool = DockerTool()

    async def test_logs_default_tail(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout="log line 1\nlog line 2")) as mock:
            result = await self.tool.call(
                {"action": "logs", "container": "mycontainer"},
                ctx(),
            )
        assert result.is_error is False
        assert "log line 1" in result.output
        call_args = mock.call_args[0][0]
        assert "--tail" in call_args
        assert "50" in call_args

    async def test_logs_custom_tail(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout="last line")) as mock:
            result = await self.tool.call(
                {"action": "logs", "container": "abc", "tail": 10},
                ctx(),
            )
        call_args = mock.call_args[0][0]
        assert "10" in call_args


# ===========================================================================
# Action: exec
# ===========================================================================


class TestDockerExec:
    tool = DockerTool()

    async def test_exec_success(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout="root")) as mock:
            result = await self.tool.call(
                {"action": "exec", "container": "abc", "command": "whoami"},
                ctx(),
            )
        assert result.is_error is False
        assert "root" in result.output
        call_args = mock.call_args[0][0]
        assert "exec" in call_args
        assert "abc" in call_args
        assert "sh" in call_args
        assert "whoami" in call_args


# ===========================================================================
# Action: images
# ===========================================================================


class TestDockerImages:
    tool = DockerTool()

    async def test_images_returns_json(self):
        json_output = '{"Repository":"nginx","Tag":"latest","Size":"150MB"}'
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stdout=json_output)) as mock:
            result = await self.tool.call({"action": "images"}, ctx())
        assert result.is_error is False
        assert "nginx" in result.output
        call_args = mock.call_args[0][0]
        assert "--format" in call_args
        assert "json" in call_args


# ===========================================================================
# Error handling
# ===========================================================================


class TestDockerErrors:
    tool = DockerTool()

    async def test_timeout_error(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", side_effect=subprocess.TimeoutExpired("docker", 120)):
            result = await self.tool.call({"action": "ps"}, ctx())
        assert result.is_error is True
        assert "timed out" in result.output.lower()

    async def test_file_not_found_error(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", side_effect=FileNotFoundError("docker")):
            result = await self.tool.call({"action": "ps"}, ctx())
        assert result.is_error is True
        assert "not installed" in result.output.lower()

    async def test_generic_exception(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", side_effect=RuntimeError("unexpected")):
            result = await self.tool.call({"action": "ps"}, ctx())
        assert result.is_error is True
        assert "error" in result.output.lower()

    async def test_nonzero_returncode_is_error(self):
        with patch("duh.tools.docker_tool._docker_available", return_value=True), \
             patch("duh.tools.docker_tool._run", return_value=_mock_run(stderr="permission denied", returncode=1)):
            result = await self.tool.call({"action": "ps"}, ctx())
        assert result.is_error is True
        assert "permission denied" in result.output


# ===========================================================================
# Registry integration
# ===========================================================================


class TestDockerRegistry:

    def test_docker_tool_in_registry(self):
        from duh.tools.registry import get_all_tools
        tools = get_all_tools()
        names = [t.name for t in tools]
        assert "Docker" in names
