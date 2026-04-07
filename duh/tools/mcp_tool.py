"""MCPToolWrapper — adapts MCP tools to D.U.H.'s Tool protocol.

Each MCP tool discovered by MCPExecutor gets wrapped into an object
that satisfies the Tool protocol (name, description, input_schema, call).
The wrapper delegates execution to MCPExecutor.run().

    info = MCPToolInfo(name="navigate", server_name="playwright", ...)
    wrapper = MCPToolWrapper(info=info, executor=executor)
    result = await wrapper.call({"url": "https://example.com"}, ctx)
"""

from __future__ import annotations

import logging
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult

logger = logging.getLogger(__name__)


class MCPToolWrapper:
    """Wraps a single MCP tool into the D.U.H. Tool protocol.

    Attributes:
        name: Qualified name (mcp__<server>__<tool>).
        description: From the MCP tool's description.
        input_schema: From the MCP tool's JSON Schema.
    """

    def __init__(self, *, info: Any, executor: Any) -> None:
        """Create a wrapper for one MCP tool.

        Args:
            info: MCPToolInfo from the executor's discovery.
            executor: MCPExecutor instance that owns the connection.
        """
        self._info = info
        self._executor = executor

        # Qualified name: mcp__<server>__<tool>
        self.name: str = f"mcp__{info.server_name}__{info.name}"
        self.description: str = info.description or f"MCP tool: {info.name}"
        self.input_schema: dict[str, Any] = info.input_schema or {
            "type": "object",
            "properties": {},
        }

    @property
    def is_read_only(self) -> bool:
        """MCP tools are assumed non-read-only (conservative default)."""
        return False

    @property
    def is_destructive(self) -> bool:
        """MCP tools are assumed non-destructive (permission check handles safety)."""
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        """Execute the MCP tool via the executor."""
        try:
            output = await self._executor.run(
                self.name,
                input,
                tool_use_id=context.tool_use_id,
            )
            return ToolResult(output=output if isinstance(output, str) else str(output))
        except Exception as exc:
            logger.debug("MCP tool %s failed: %s", self.name, exc)
            return ToolResult(output=f"MCP tool error: {exc}", is_error=True)

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        """MCP tools defer permission checks to the approver layer."""
        return {"allowed": True}
