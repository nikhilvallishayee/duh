"""MCP tool executor -- connects to MCP servers and runs their tools.

Implements the ToolExecutor port for MCP (Model Context Protocol) servers.
Discovers tools from connected servers and dispatches tool calls via the
MCP JSON-RPC protocol over stdio transport.

Requires the ``mcp`` Python package (optional dependency).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy MCP SDK import -- graceful degradation when not installed
# ---------------------------------------------------------------------------

_mcp_available = False
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    _mcp_available = True
except ImportError:
    pass


def _require_mcp() -> None:
    if not _mcp_available:
        raise RuntimeError(
            "The 'mcp' package is required for MCP support. "
            "Install it with: pip install mcp"
        )


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server (stdio transport)."""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


@dataclass
class MCPToolInfo:
    """Metadata for a discovered MCP tool."""

    name: str
    server_name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPConnection:
    """A live connection to an MCP server."""

    server_name: str
    config: MCPServerConfig
    session: Any = None  # ClientSession when connected
    tools: list[MCPToolInfo] = field(default_factory=list)
    _stdio_ctx: Any = None  # stdio_client context manager (must exit on disconnect)
    _cleanup: Any = None  # cleanup callable from context manager


# ---------------------------------------------------------------------------
# MCPExecutor -- the ToolExecutor adapter
# ---------------------------------------------------------------------------


class MCPExecutor:
    """Executes tools hosted on MCP servers via stdio transport.

    Implements the ToolExecutor port contract:
        async def run(tool_name, input, *, tool_use_id, context) -> str | dict

    Tool names follow the ``mcp__<server>__<tool>`` convention so MCP tools
    can coexist with native tools in a single registry without collisions.

    Usage::

        configs = {"github": MCPServerConfig(command="npx", args=["-y", "mcp-github"])}
        executor = MCPExecutor(configs)
        await executor.connect_all()
        result = await executor.run("mcp__github__create_issue", {"title": "bug"})
        await executor.disconnect_all()
    """

    def __init__(self, servers: dict[str, MCPServerConfig] | None = None) -> None:
        _require_mcp()
        self._servers: dict[str, MCPServerConfig] = servers or {}
        self._connections: dict[str, MCPConnection] = {}
        self._tool_index: dict[str, MCPToolInfo] = {}

    # -- Connection lifecycle -----------------------------------------------

    async def connect(self, server_name: str) -> list[MCPToolInfo]:
        """Connect to a single MCP server and discover its tools.

        Returns the list of tools discovered from this server.
        Raises RuntimeError if the server is not configured or connection fails.
        """
        _require_mcp()

        config = self._servers.get(server_name)
        if config is None:
            raise KeyError(f"MCP server not configured: {server_name}")

        params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env,
        )

        stdio_ctx = None
        session = None
        try:
            stdio_ctx, read_stream, write_stream = await self._start_stdio(params)
            session = ClientSession(read_stream, write_stream)
            await session.__aenter__()
            await session.initialize()

            # Discover tools
            tools_result = await session.list_tools()
            tools: list[MCPToolInfo] = []
            for tool in tools_result.tools:
                info = MCPToolInfo(
                    name=tool.name,
                    server_name=server_name,
                    description=getattr(tool, "description", "") or "",
                    input_schema=getattr(tool, "inputSchema", {}) or {},
                )
                qualified = f"mcp__{server_name}__{tool.name}"
                self._tool_index[qualified] = info
                tools.append(info)

            conn = MCPConnection(
                server_name=server_name,
                config=config,
                session=session,
                tools=tools,
                _stdio_ctx=stdio_ctx,
            )
            self._connections[server_name] = conn
            logger.info(
                "Connected to MCP server %s: %d tools discovered",
                server_name,
                len(tools),
            )
            return tools

        except Exception as exc:
            # Clean up on failure
            if session is not None:
                try:
                    await session.__aexit__(None, None, None)
                except Exception:
                    pass
            if stdio_ctx is not None:
                try:
                    await stdio_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
            raise RuntimeError(
                f"Failed to connect to MCP server '{server_name}': {exc}"
            ) from exc

    async def _start_stdio(self, params: Any) -> tuple[Any, Any, Any]:
        """Start the stdio transport. Returns (ctx, read_stream, write_stream).

        The caller must keep *ctx* alive and eventually call
        ``ctx.__aexit__(None, None, None)`` to clean up the subprocess.
        Separated for testability.
        """
        ctx = stdio_client(params)
        read_stream, write_stream = await ctx.__aenter__()
        return ctx, read_stream, write_stream

    async def connect_all(self) -> dict[str, list[MCPToolInfo]]:
        """Connect to all configured servers. Returns {server_name: [tools]}."""
        results: dict[str, list[MCPToolInfo]] = {}
        for name in self._servers:
            try:
                results[name] = await self.connect(name)
            except Exception:
                logger.exception("Failed to connect to MCP server: %s", name)
                results[name] = []
        return results

    async def disconnect(self, server_name: str) -> None:
        """Disconnect from a specific server."""
        conn = self._connections.pop(server_name, None)
        if conn is None:
            return
        # Remove tools from index
        to_remove = [k for k, v in self._tool_index.items() if v.server_name == server_name]
        for k in to_remove:
            del self._tool_index[k]

        if conn.session is not None:
            try:
                await conn.session.__aexit__(None, None, None)
            except Exception:
                pass
        if conn._stdio_ctx is not None:
            try:
                await conn._stdio_ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        names = list(self._connections.keys())
        for name in names:
            await self.disconnect(name)

    # -- Tool discovery -----------------------------------------------------

    @property
    def tool_names(self) -> list[str]:
        """All qualified tool names from connected servers."""
        return list(self._tool_index.keys())

    def get_tool_info(self, qualified_name: str) -> MCPToolInfo | None:
        """Look up tool info by qualified name (mcp__server__tool)."""
        return self._tool_index.get(qualified_name)

    def list_tools(self) -> list[MCPToolInfo]:
        """All discovered tools across all connected servers."""
        return list(self._tool_index.values())

    # -- Tool execution (ToolExecutor port) ---------------------------------

    async def run(
        self,
        tool_name: str,
        input: dict[str, Any],
        *,
        tool_use_id: str = "",
        context: Any = None,
    ) -> str | dict[str, Any]:
        """Execute an MCP tool by its qualified name.

        The qualified name format is ``mcp__<server>__<tool>``.
        Raises KeyError if the tool is not found.
        Raises RuntimeError if execution fails.
        """
        info = self._tool_index.get(tool_name)
        if info is None:
            raise KeyError(f"MCP tool not found: {tool_name}")

        conn = self._connections.get(info.server_name)
        if conn is None or conn.session is None:
            raise RuntimeError(
                f"MCP server '{info.server_name}' is not connected"
            )

        try:
            result = await conn.session.call_tool(info.name, arguments=input)
        except Exception as exc:
            raise RuntimeError(
                f"MCP tool call failed ({tool_name}): {exc}"
            ) from exc

        # Extract content from MCP result
        if hasattr(result, "content") and result.content:
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "data"):
                    parts.append(str(block.data))
                else:
                    parts.append(str(block))
            return "\n".join(parts) if parts else ""

        return ""

    # -- Config helpers -----------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "MCPExecutor":
        """Create an MCPExecutor from a config dict.

        Expected format::

            {
                "mcpServers": {
                    "name": {"command": "...", "args": [...], "env": {...}}
                }
            }
        """
        _require_mcp()
        servers: dict[str, MCPServerConfig] = {}
        mcp_servers = config.get("mcpServers", {})
        for name, srv in mcp_servers.items():
            servers[name] = MCPServerConfig(
                command=srv["command"],
                args=srv.get("args", []),
                env=srv.get("env"),
            )
        return cls(servers)
