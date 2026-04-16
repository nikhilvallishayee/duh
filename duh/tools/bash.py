"""BashTool — execute shell commands via asyncio subprocess.

Supports cross-platform execution: bash/sh on Unix, PowerShell on Windows.
The ``shell`` parameter (or ``--shell`` CLI flag) selects which backend to use.
``"auto"`` (the default) picks PowerShell on Windows and bash everywhere else.

Commands prefixed with ``bg:`` are submitted to the global
:class:`~duh.kernel.job_queue.JobQueue` and run in the background
instead of blocking.  Example::

    bg: pytest tests/ -q

The tool returns the job id immediately so the caller can check on it
later via ``/jobs <id>``.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from duh.kernel.tool import MAX_TOOL_OUTPUT, TOOL_TIMEOUTS, ToolContext, ToolResult
from duh.tools.bash_security import classify_command
from duh.adapters.sandbox.policy import SandboxCommand, detect_sandbox_type
from duh.security.trifecta import Capability

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = TOOL_TIMEOUTS.get("Bash", 300)  # from central config


# ---------------------------------------------------------------------------
# Shared job queue singleton (lazily created per-process)
# ---------------------------------------------------------------------------

_job_queue: Any = None


def get_job_queue() -> Any:
    """Return the process-wide :class:`JobQueue` singleton."""
    global _job_queue
    if _job_queue is None:
        from duh.kernel.job_queue import JobQueue
        _job_queue = JobQueue()
    return _job_queue


# ---------------------------------------------------------------------------
# Cross-platform shell helpers
# ---------------------------------------------------------------------------

def detect_shell() -> str:
    """Return ``"powershell"`` on Windows, ``"bash"`` everywhere else."""
    return "powershell" if sys.platform == "win32" else "bash"


def resolve_shell(shell: str) -> str:
    """Resolve ``"auto"`` to the platform-appropriate shell.

    Valid values: ``"auto"``, ``"bash"``, ``"powershell"``.
    """
    if shell == "auto":
        return detect_shell()
    if shell not in ("bash", "powershell"):
        raise ValueError(f"Unknown shell: {shell!r} (expected 'auto', 'bash', or 'powershell')")
    return shell


def build_shell_command(command: str, shell: str) -> list[str]:
    """Build the argv list for *command* under the given *shell*.

    * ``"bash"`` → ``["bash", "-c", command]``
    * ``"powershell"`` → ``["powershell", "-Command", command]``
    """
    resolved = resolve_shell(shell)
    if resolved == "powershell":
        return ["powershell", "-Command", command]
    return ["bash", "-c", command]


class BashTool:
    """Execute a shell command and return its output."""

    name = "Bash"
    capabilities = Capability.EXEC | Capability.NETWORK_EGRESS | Capability.FS_WRITE
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
            "shell": {
                "type": "string",
                "description": "Shell backend: 'auto' (default), 'bash', or 'powershell'.",
                "enum": ["auto", "bash", "powershell"],
                "default": "auto",
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
        shell = input.get("shell", "auto")

        if not command:
            return ToolResult(output="command is required", is_error=True)

        # Resolve the shell backend once for both security + execution
        resolved_shell = resolve_shell(shell)

        # --- Security check BEFORE bg: handling (applies to ALL commands) ---
        is_background = command.startswith("bg:")
        check_cmd = command[3:].strip() if is_background else command

        # SEC-MEDIUM-1: ``skip_permissions`` bypasses the dangerous-command
        # block-list. It is set in two well-defined places:
        #   * cli/runner.py — when the user passes
        #     ``--dangerously-skip-permissions`` or chooses an automation
        #     ``--permission-mode`` (``bypassPermissions``/``dontAsk``).
        #   * cli/repl.py   — same flag, REPL entry path.
        # Both paths surface this opt-in to the user explicitly. Whenever the
        # bypass actually fires on a *dangerous* command we emit a WARNING so
        # the audit trail records the elevated risk.
        skip_permissions = context.metadata.get("skip_permissions", False)
        classification = classify_command(check_cmd, shell=resolved_shell)
        if not skip_permissions:
            if classification["risk"] == "dangerous":
                return ToolResult(
                    output=f"Command blocked: {classification['reason']}",
                    is_error=True,
                    metadata={"blocked": True, "risk": "dangerous",
                              "reason": classification["reason"]},
                )
        elif classification["risk"] in ("dangerous", "moderate"):
            logger.warning(
                "skip_permissions bypass: allowing %s command (%s): %s",
                classification["risk"],
                classification["reason"],
                check_cmd[:200],
            )

        # --- Background job handling (AFTER security check) ---
        if is_background:
            if not check_cmd:
                return ToolResult(output="bg: requires a command", is_error=True)
            return await self._submit_background(check_cmd, timeout, shell, context)

        cwd = context.cwd if context.cwd and context.cwd != "." else None

        # --- Sandbox wrapping (when policy is set on context) ---
        sandbox_cmd = None
        if context.sandbox_policy is not None:
            sandbox_type = detect_sandbox_type()
            sandbox_cmd = SandboxCommand.build(
                command=command,
                policy=context.sandbox_policy,
                sandbox_type=sandbox_type,
            )
            argv = sandbox_cmd.argv
        else:
            argv = build_shell_command(command, resolved_shell)

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
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

        # Clean up sandbox temp files
        if sandbox_cmd is not None:
            sandbox_cmd.cleanup()

        stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
        returncode = proc.returncode or 0

        output_parts: list[str] = []
        if stdout_text:
            output_parts.append(stdout_text)
        if stderr_text:
            output_parts.append(stderr_text)

        output = "\n".join(output_parts) if output_parts else ""

        metadata: dict[str, Any] = {"returncode": returncode}

        # Attach warning for moderate-risk commands
        if classification["risk"] == "moderate":
            output = f"[WARNING: {classification['reason']}]\n{output}"
            metadata["risk"] = "moderate"
            metadata["reason"] = classification["reason"]

        # Truncate oversized output
        if len(output) > MAX_TOOL_OUTPUT:
            original_size = len(output)
            output = (
                output[:MAX_TOOL_OUTPUT]
                + "\n\n... Output truncated."
                " Pipe to a file: command > output.txt"
            )
            metadata["truncated"] = True
            metadata["original_size"] = original_size

        return ToolResult(
            output=output,
            is_error=returncode != 0,
            metadata=metadata,
        )

    async def _submit_background(
        self,
        command: str,
        timeout: int,
        shell: str,
        context: ToolContext,
    ) -> ToolResult:
        """Submit *command* as a background job and return immediately."""
        resolved_shell = resolve_shell(shell)
        cwd = context.cwd if context.cwd and context.cwd != "." else None

        async def _bg_run() -> str:
            argv = build_shell_command(command, resolved_shell)
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
            parts = [p for p in (stdout_text, stderr_text) if p]
            output = "\n".join(parts)
            if len(output) > MAX_TOOL_OUTPUT:
                output = output[:MAX_TOOL_OUTPUT] + "\n\n... Output truncated."
            return output

        queue = get_job_queue()
        job_id = queue.submit(command, _bg_run())
        return ToolResult(
            output=f"Background job submitted: {job_id}\nUse /jobs {job_id} to check results.",
            metadata={"job_id": job_id, "background": True},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
