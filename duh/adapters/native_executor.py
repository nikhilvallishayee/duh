"""Native tool executor — runs Python Tool objects directly.

Finds a tool by name from a registry, validates input against its
JSON Schema, and calls its async `call()` method.
"""

from __future__ import annotations

import asyncio
from typing import Any

from duh.kernel.file_tracker import FileTracker
from duh.kernel.redact import redact_secrets
from duh.kernel.tool import MAX_TOOL_OUTPUT, Tool, ToolContext, ToolResult, get_tool_timeout
from duh.kernel.undo import UndoStack

# Tools whose execution should be recorded as file operations.
_FILE_TOOL_OPS: dict[str, str] = {
    "Read": "read",
    "Write": "write",
    "Edit": "edit",
}

# Tools that mutate files and should be captured by the undo stack.
_UNDO_TOOLS: set[str] = {"Write", "Edit"}


class NativeExecutor:
    """Executes Tool objects registered by name.

    Implements the ToolExecutor port contract.
    """

    def __init__(self, tools: list[Any] | None = None, *, cwd: str = "."):
        self._tools: dict[str, Any] = {}
        self._cwd = cwd
        self.file_tracker = FileTracker()
        self.undo_stack = UndoStack()
        if tools:
            for tool in tools:
                name = getattr(tool, "name", None)
                if name:
                    self._tools[name] = tool

    def register(self, tool: Any) -> None:
        """Register a tool by its name."""
        name = getattr(tool, "name", None)
        if not name:
            raise ValueError("Tool must have a 'name' attribute")
        self._tools[name] = tool

    def get_tool(self, name: str) -> Any | None:
        """Look up a tool by name."""
        return self._tools.get(name)

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    async def run(
        self,
        tool_name: str,
        input: dict[str, Any],
        *,
        tool_use_id: str = "",
        context: Any = None,
    ) -> str | dict[str, Any]:
        """Execute a tool by name.

        Returns the tool's output as a string or dict.
        Raises KeyError if the tool is not found.
        Raises RuntimeError if the tool execution fails.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise KeyError(f"Tool not found: {tool_name}")

        ctx = ToolContext(
            cwd=self._cwd,
            tool_use_id=tool_use_id,
            session_id=getattr(context, "session_id", "") if context else "",
        )

        # Check tool-level permissions
        if hasattr(tool, "check_permissions"):
            perm = await tool.check_permissions(input, ctx)
            if isinstance(perm, dict) and not perm.get("allowed", True):
                reason = perm.get("reason", "Permission denied by tool")
                raise PermissionError(reason)

        # Snapshot file state for undo before mutating tools execute.
        if tool_name in _UNDO_TOOLS:
            file_path = input.get("file_path", "")
            if file_path:
                try:
                    from pathlib import Path
                    p = Path(file_path)
                    if p.is_file():
                        self.undo_stack.push(file_path, p.read_text(encoding="utf-8"))
                    else:
                        # File doesn't exist yet (new Write) — undo = delete.
                        self.undo_stack.push(file_path, None)
                except OSError:
                    pass  # Best-effort; don't block tool execution.

        # Execute with per-tool timeout
        timeout = get_tool_timeout(tool_name)
        try:
            result = await asyncio.wait_for(tool.call(input, ctx), timeout=timeout)
        except asyncio.TimeoutError:
            return (
                f"Tool '{tool_name}' timed out after {timeout}s."
                " Try a simpler command or increase timeout."
            )

        # Record file operations for Read/Write/Edit tools
        op = _FILE_TOOL_OPS.get(tool_name)
        if op:
            file_path = input.get("file_path", "")
            if file_path:
                is_error = isinstance(result, ToolResult) and result.is_error
                if not is_error:
                    self.file_tracker.track(file_path, op)

        # --- Truncate oversized output ---
        if isinstance(result, ToolResult):
            if result.is_error:
                raise RuntimeError(result.output)
            output = result.output
            if isinstance(output, str) and len(output) > MAX_TOOL_OUTPUT:
                original_size = len(output)
                output = (
                    output[:MAX_TOOL_OUTPUT]
                    + "\n\n... (output truncated at 100KB."
                    " Use Read with offset/limit for full content)"
                )
                result.metadata["truncated"] = True
                result.metadata["original_size"] = original_size
            return redact_secrets(output) if isinstance(output, str) else output

        raw = str(result)
        if len(raw) > MAX_TOOL_OUTPUT:
            raw = (
                raw[:MAX_TOOL_OUTPUT]
                + "\n\n... (output truncated at 100KB."
                " Use Read with offset/limit for full content)"
            )
        return redact_secrets(raw)
