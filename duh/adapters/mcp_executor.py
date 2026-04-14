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

from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.adapters.mcp_unicode import normalize_mcp_description
from duh.adapters.mcp_manifest import MCPManifest, DEFAULT_MCP_MANIFEST
from duh.adapters.sandbox.policy import (
    SandboxCommand,
    SandboxPolicy,
    SandboxType,
    detect_sandbox_type,
)


def _wrap_mcp_output(text: str) -> UntrustedStr:
    """Tag MCP tool output as MCP_OUTPUT."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.MCP_OUTPUT)


# ---------------------------------------------------------------------------
# Lazy MCP SDK import -- graceful degradation when not installed
# ---------------------------------------------------------------------------

_mcp_available = False
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    _mcp_available = True
except ImportError:  # pragma: no cover - mcp is installed in test env
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
    """Configuration for a single MCP server.

    For stdio transport: set ``command`` and ``args``.
    For remote transports (sse, http, ws): set ``transport`` and ``url``.
    """

    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    transport: str = "stdio"  # stdio | sse | http | ws
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


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


def _create_transport(config: MCPServerConfig) -> Any | None:
    """Create a transport instance based on config, or None for stdio.

    Returns None for stdio (handled by existing code path).
    Raises ValueError for unknown transport types.
    """
    transport_type = config.transport.lower()

    if transport_type == "stdio":
        return None

    from duh.adapters.mcp_transports import SSETransport, HTTPTransport, WebSocketTransport

    if transport_type == "sse":
        return SSETransport(
            url=config.url,
            headers=config.headers or {},
        )
    elif transport_type == "http":
        return HTTPTransport(
            base_url=config.url,
            headers=config.headers or {},
        )
    elif transport_type == "ws":
        return WebSocketTransport(
            url=config.url,
            headers=config.headers or {},
        )
    else:
        raise ValueError(
            f"Unsupported MCP transport type: '{transport_type}'. "
            f"Valid options: stdio, sse, http, ws"
        )


# Session management constants (from Claude Code TS)
MAX_SESSION_RETRIES = 1
MAX_ERRORS_BEFORE_RECONNECT = 3


def _is_session_expired(status_code: int, message: str) -> bool:
    """Detect MCP session expiry from error response."""
    return status_code == 404 and "session not found" in message.lower()


# ---------------------------------------------------------------------------
# Workstream 7.6: Unicode normalization + subprocess sandbox
# ---------------------------------------------------------------------------


class MCPUnicodeError(RuntimeError):
    """Raised when an MCP server has suspicious Unicode in tool descriptions.

    Triggered during the handshake if any tool description or parameter
    description contains zero-width characters, bidi overrides, Unicode Tag
    Characters, or invisible variation selectors (GlassWorm-style injection).
    """


def _validate_mcp_tool_descriptions(tools: list[dict[str, Any]]) -> list[str]:
    """Validate all tool descriptions at handshake time.

    Scans both the top-level ``description`` field and every parameter
    description inside ``inputSchema.properties.*.description``.

    Args:
        tools: List of tool dicts as returned by the MCP ``list_tools`` RPC
               (each with at minimum ``name`` and optionally ``description``
               and ``inputSchema``).

    Returns:
        A list of human-readable issue strings.  Empty means all clear.
    """
    all_issues: list[str] = []
    for tool in tools:
        name = tool.get("name", "<unnamed>")
        desc = tool.get("description", "") or ""
        _, issues = normalize_mcp_description(desc)
        for issue in issues:
            all_issues.append(f"tool '{name}': {issue}")

        # Also scan parameter descriptions inside inputSchema.properties
        input_schema = tool.get("inputSchema", {}) or {}
        props = input_schema.get("properties", {}) or {}
        for param_name, param_schema in props.items():
            param_desc = (param_schema.get("description", "") or "") if isinstance(param_schema, dict) else ""
            _, param_issues = normalize_mcp_description(param_desc)
            for issue in param_issues:
                all_issues.append(f"tool '{name}' param '{param_name}': {issue}")

    return all_issues


def _compute_mcp_sandbox_policy(manifest: MCPManifest) -> SandboxPolicy:
    """Derive a SandboxPolicy from an MCPManifest.

    Converts the frozen-set Path objects in the manifest to the string lists
    that SandboxPolicy expects.

    Args:
        manifest: The server's capability manifest.

    Returns:
        A :class:`~duh.adapters.sandbox.policy.SandboxPolicy` ready to pass
        to :meth:`~duh.adapters.sandbox.policy.SandboxCommand.build`.
    """
    return SandboxPolicy(
        writable_paths=[str(p) for p in manifest.writable_paths],
        readable_paths=[str(p) for p in manifest.readable_paths],
        network_allowed=manifest.network_allowed,
    )


def _sandbox_available() -> bool:
    """Return True if OS-level sandboxing is available on this platform."""
    try:
        return detect_sandbox_type() != SandboxType.NONE
    except Exception:
        return False


def _build_sandboxed_command(
    command: str,
    args: list[str],
    manifest: MCPManifest,
) -> list[str] | None:
    """Wrap an MCP stdio command in an OS sandbox.

    Derives a :class:`SandboxPolicy` from *manifest*, then uses
    :class:`SandboxCommand` to wrap the command with the platform-appropriate
    sandbox (Seatbelt on macOS, Landlock on Linux).

    Args:
        command: The bare executable (e.g. ``"node"``).
        args: Positional arguments to the executable.
        manifest: The server's capability manifest.

    Returns:
        A complete ``argv`` list to pass to ``asyncio.create_subprocess_exec``,
        or ``None`` if no sandbox is available on this platform.
    """
    if not _sandbox_available():
        return None
    policy = _compute_mcp_sandbox_policy(manifest)
    sandbox_type = detect_sandbox_type()
    # Build with just the command (no args); args are appended after
    sandbox_cmd = SandboxCommand.build(
        command=command,
        policy=policy,
        sandbox_type=sandbox_type,
    )
    # SandboxCommand.argv already ends with ["bash", "-c", command] for
    # Seatbelt/NONE, or the python-landlock wrapper for Landlock.
    # We return it as-is; the caller (MCPExecutor._start_stdio) will pass the
    # full argv to StdioServerParameters which starts the real subprocess.
    return sandbox_cmd.argv + args


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
        self._error_counts: dict[str, int] = {}  # server_name -> consecutive errors

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
            raw_tool_dicts: list[dict[str, Any]] = []
            for tool in tools_result.tools:
                info = MCPToolInfo(
                    name=tool.name,
                    server_name=server_name,
                    description=getattr(tool, "description", "") or "",
                    input_schema=getattr(tool, "inputSchema", {}) or {},
                )
                raw_tool_dicts.append({
                    "name": tool.name,
                    "description": getattr(tool, "description", "") or "",
                    "inputSchema": getattr(tool, "inputSchema", {}) or {},
                })
                qualified = f"mcp__{server_name}__{tool.name}"
                self._tool_index[qualified] = info
                tools.append(info)

            # Validate Unicode safety of all tool descriptions at handshake time
            unicode_issues = _validate_mcp_tool_descriptions(raw_tool_dicts)
            if unicode_issues:
                summary = "; ".join(unicode_issues[:3])
                if len(unicode_issues) > 3:
                    summary += f" (and {len(unicode_issues) - 3} more)"
                raise MCPUnicodeError(
                    f"MCP server '{server_name}' has suspicious Unicode in tool "
                    f"descriptions: {summary}"
                )

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
        """Connect to all configured servers.

        Connects in two phases:
        1. Local (stdio) servers -- up to 3 concurrent
        2. Remote (sse/http/ws) servers -- up to 20 concurrent

        Returns {server_name: [tools]}.
        """
        local_names = [
            n for n, c in self._servers.items() if c.transport == "stdio"
        ]
        remote_names = [
            n for n, c in self._servers.items() if c.transport != "stdio"
        ]

        results: dict[str, list[MCPToolInfo]] = {}

        # Phase 1: Local servers (limited concurrency)
        sem_local = asyncio.Semaphore(3)

        async def _connect_with_sem(name: str, sem: asyncio.Semaphore) -> tuple[str, list[MCPToolInfo]]:
            async with sem:
                try:
                    tools = await self.connect(name)
                    return name, tools
                except Exception:
                    logger.exception("Failed to connect to MCP server: %s", name)
                    return name, []

        if local_names:
            local_tasks = [
                _connect_with_sem(n, sem_local) for n in local_names
            ]
            for name, tools in await asyncio.gather(*local_tasks):
                results[name] = tools

        # Phase 2: Remote servers (higher concurrency)
        sem_remote = asyncio.Semaphore(20)

        if remote_names:
            remote_tasks = [
                _connect_with_sem(n, sem_remote) for n in remote_names
            ]
            for name, tools in await asyncio.gather(*remote_tasks):
                results[name] = tools

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

        retries = 0
        while True:
            try:
                result = await conn.session.call_tool(info.name, arguments=input)
                # Reset error counter on success
                self._error_counts[info.server_name] = 0
                break
            except Exception as exc:
                exc_str = str(exc)
                status = getattr(exc, "status_code", getattr(exc, "code", 0))

                # Track consecutive errors
                count = self._error_counts.get(info.server_name, 0) + 1
                self._error_counts[info.server_name] = count

                # Session expiry: reconnect and retry once
                if (_is_session_expired(status, exc_str)
                        and retries < MAX_SESSION_RETRIES):
                    retries += 1
                    logger.info(
                        "MCP session expired for %s, reconnecting...",
                        info.server_name,
                    )
                    await self.disconnect(info.server_name)
                    await self.connect(info.server_name)
                    conn = self._connections.get(info.server_name)
                    if conn is None or conn.session is None:
                        raise RuntimeError(
                            f"Reconnection to '{info.server_name}' failed"
                        ) from exc
                    continue

                # Too many consecutive errors: reconnect
                if count >= MAX_ERRORS_BEFORE_RECONNECT:
                    logger.warning(
                        "MCP server %s: %d consecutive errors, reconnecting",
                        info.server_name, count,
                    )
                    self._error_counts[info.server_name] = 0
                    await self.disconnect(info.server_name)
                    try:
                        await self.connect(info.server_name)
                    except Exception:
                        pass  # reconnect is best-effort

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
                    "name": {
                        "command": "...",
                        "args": [...],
                        "env": {...},
                        "transport": "stdio|sse|http|ws",
                        "url": "http://...",
                        "headers": {...}
                    }
                }
            }
        """
        _require_mcp()
        servers: dict[str, MCPServerConfig] = {}
        mcp_servers = config.get("mcpServers", {})
        for name, srv in mcp_servers.items():
            servers[name] = MCPServerConfig(
                command=srv.get("command", ""),
                args=srv.get("args", []),
                env=srv.get("env"),
                transport=srv.get("transport", "stdio"),
                url=srv.get("url", ""),
                headers=srv.get("headers", {}),
            )
        return cls(servers)
