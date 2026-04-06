"""ToolExecutor port — how D.U.H. runs tools.

The executor finds a tool by name, validates input, checks permissions,
runs it, and returns the result. Different executors handle different
tool transports (native Python tools, MCP servers, shell commands).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolExecutor(Protocol):
    """Abstract interface for tool execution."""

    async def run(
        self,
        tool_name: str,
        input: dict[str, Any],
        *,
        tool_use_id: str = "",
        context: Any = None,
    ) -> str | dict[str, Any]:
        """Execute a tool by name and return its result.

        Returns a string (simple output) or dict (structured output).
        Raises on tool-not-found or execution errors.
        """
        ...
