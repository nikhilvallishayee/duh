"""BashTool — execute shell commands via asyncio subprocess."""

from __future__ import annotations

import asyncio
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult

_DEFAULT_TIMEOUT = 120  # seconds


class BashTool:
    """Execute a shell command and return its output."""

    name = "Bash"
    description = "Execute a bash command and return stdout/stderr."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Default: 120.",
                "minimum": 1,
            },
        },
        "required": ["command"],
    }

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        command = input.get("command", "")
        timeout = input.get("timeout", _DEFAULT_TIMEOUT)

        if not command:
            return ToolResult(output="command is required", is_error=True)

        cwd = context.cwd if context.cwd and context.cwd != "." else None

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()  # type: ignore[union-attr]
            except ProcessLookupError:
                pass
            return ToolResult(
                output=f"Command timed out after {timeout}s", is_error=True
            )
        except Exception as exc:
            return ToolResult(output=f"Error running command: {exc}", is_error=True)

        stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
        returncode = proc.returncode or 0

        output_parts: list[str] = []
        if stdout_text:
            output_parts.append(stdout_text)
        if stderr_text:
            output_parts.append(stderr_text)

        output = "\n".join(output_parts) if output_parts else ""

        return ToolResult(
            output=output,
            is_error=returncode != 0,
            metadata={"returncode": returncode},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
