"""DockerTool -- interact with Docker containers via the CLI.

Supports build, run, ps, logs, exec, and images actions.  All commands
delegate to the ``docker`` binary via :mod:`subprocess` so no Python
Docker SDK is required.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult

_DEFAULT_TIMEOUT = 120  # seconds
_TAIL_LINES = 50


def _docker_available() -> bool:
    """Return True if the ``docker`` CLI is on PATH."""
    return shutil.which("docker") is not None


def _run(
    args: list[str],
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a docker command and return the CompletedProcess."""
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )


class DockerTool:
    """Manage Docker containers via the ``docker`` CLI."""

    name = "Docker"
    description = (
        "Run Docker CLI commands: build, run, ps, logs, exec, images."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["build", "run", "ps", "logs", "exec", "images"],
                "description": "The Docker action to perform.",
            },
            "tag": {
                "type": "string",
                "description": "Image tag (used by build).",
            },
            "path": {
                "type": "string",
                "description": "Build context path (used by build).",
            },
            "image": {
                "type": "string",
                "description": "Image name (used by run).",
            },
            "command": {
                "type": "string",
                "description": "Command to run inside the container (used by run/exec).",
            },
            "container": {
                "type": "string",
                "description": "Container ID or name (used by logs/exec).",
            },
            "mount_cwd": {
                "type": "boolean",
                "description": "Mount current working directory into the container (used by run). Default false.",
            },
            "tail": {
                "type": "integer",
                "description": "Number of log lines to tail (used by logs). Default 50.",
                "minimum": 1,
            },
        },
        "required": ["action"],
    }

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        if not _docker_available():
            return ToolResult(
                output="Docker is not installed or not on PATH. Install Docker to use this tool.",
                is_error=True,
            )

        action = input.get("action", "")
        if not action:
            return ToolResult(output="action is required", is_error=True)

        handler = {
            "build": self._build,
            "run": self._run,
            "ps": self._ps,
            "logs": self._logs,
            "exec": self._exec,
            "images": self._images,
        }.get(action)

        if handler is None:
            return ToolResult(
                output=f"Unknown action: {action!r}. Must be one of: build, run, ps, logs, exec, images.",
                is_error=True,
            )

        return await handler(input, context)

    # ----- action handlers ---------------------------------------------------

    async def _build(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        tag = input.get("tag", "")
        path = input.get("path", "")
        if not tag:
            return ToolResult(output="'tag' is required for build", is_error=True)
        if not path:
            return ToolResult(output="'path' is required for build", is_error=True)

        args = ["build", "-t", tag, path]
        return self._exec_docker(args)

    async def _run(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        image = input.get("image", "")
        if not image:
            return ToolResult(output="'image' is required for run", is_error=True)

        command = input.get("command", "")
        mount_cwd = input.get("mount_cwd", False)

        args = ["run", "--rm"]
        if mount_cwd:
            cwd = context.cwd if context.cwd and context.cwd != "." else None
            if cwd:
                args.extend(["-v", f"{cwd}:/workspace", "-w", "/workspace"])
        args.append(image)
        if command:
            args.extend(["sh", "-c", command])

        return self._exec_docker(args)

    async def _ps(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        return self._exec_docker(["ps", "--format", "json"])

    async def _logs(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        container = input.get("container", "")
        if not container:
            return ToolResult(output="'container' is required for logs", is_error=True)

        tail = input.get("tail", _TAIL_LINES)
        return self._exec_docker(["logs", container, "--tail", str(tail)])

    async def _exec(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        container = input.get("container", "")
        command = input.get("command", "")
        if not container:
            return ToolResult(output="'container' is required for exec", is_error=True)
        if not command:
            return ToolResult(output="'command' is required for exec", is_error=True)

        return self._exec_docker(["exec", container, "sh", "-c", command])

    async def _images(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        return self._exec_docker(["images", "--format", "json"])

    # ----- helpers -----------------------------------------------------------

    def _exec_docker(self, args: list[str]) -> ToolResult:
        """Run a docker command and translate to ToolResult."""
        try:
            result = _run(args, timeout=_DEFAULT_TIMEOUT)
        except subprocess.TimeoutExpired:
            return ToolResult(
                output=f"Docker command timed out after {_DEFAULT_TIMEOUT}s",
                is_error=True,
            )
        except FileNotFoundError:
            return ToolResult(
                output="Docker is not installed or not on PATH.",
                is_error=True,
            )
        except Exception as exc:
            return ToolResult(
                output=f"Error running docker command: {exc}",
                is_error=True,
            )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        parts: list[str] = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(stderr)
        output = "\n".join(parts) if parts else "(no output)"

        return ToolResult(
            output=output,
            is_error=result.returncode != 0,
            metadata={"returncode": result.returncode},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
