"""Tests for duh.adapters.mcp_executor — MCP tool executor adapter.

All tests use mocks; no real subprocess or MCP server is spawned.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# We need to test graceful degradation when `mcp` is not installed AND
# full functionality with mocks.  To do both without actually depending
# on the mcp package, we build a fake `mcp` module tree and inject it
# before importing our module under test.
# ---------------------------------------------------------------------------


@dataclass
class _FakeToolInfo:
    name: str
    description: str = ""
    inputSchema: dict[str, Any] = field(default_factory=dict)


@dataclass
class _FakeListToolsResult:
    tools: list[_FakeToolInfo] = field(default_factory=list)


@dataclass
class _FakeContentBlock:
    text: str = ""


@dataclass
class _FakeCallToolResult:
    content: list[_FakeContentBlock] = field(default_factory=list)


class _FakeClientSession:
    """Fake mcp.ClientSession."""

    def __init__(self, read_stream: Any = None, write_stream: Any = None):
        self._tools: list[_FakeToolInfo] = []
        self._call_results: dict[str, _FakeCallToolResult] = {}
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self) -> _FakeListToolsResult:
        return _FakeListToolsResult(tools=self._tools)

    async def call_tool(
        self, name: str, *, arguments: dict[str, Any] | None = None
    ) -> _FakeCallToolResult:
        if name in self._call_results:
            return self._call_results[name]
        raise RuntimeError(f"Tool call failed: {name}")

    async def __aexit__(self, *args: Any) -> None:
        pass


class _FakeStdioServerParameters:
    def __init__(self, command: str, args: list[str] | None = None, env: dict[str, str] | None = None):
        self.command = command
        self.args = args or []
        self.env = env


def _install_fake_mcp() -> _FakeClientSession:
    """Install a fake ``mcp`` package into sys.modules."""
    fake_session = _FakeClientSession()

    # Build module tree
    mcp_mod = ModuleType("mcp")
    mcp_mod.ClientSession = _FakeClientSession
    mcp_mod.StdioServerParameters = _FakeStdioServerParameters

    mcp_client = ModuleType("mcp.client")
    mcp_client_stdio = ModuleType("mcp.client.stdio")

    async def fake_stdio_client(params: Any) -> Any:
        pass

    mcp_client_stdio.stdio_client = fake_stdio_client

    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.client"] = mcp_client
    sys.modules["mcp.client.stdio"] = mcp_client_stdio

    return fake_session


# Install fakes before importing the module under test
_fake_session = _install_fake_mcp()

# Force re-import so the module picks up our fake mcp
if "duh.adapters.mcp_executor" in sys.modules:
    del sys.modules["duh.adapters.mcp_executor"]

from duh.adapters.mcp_executor import (
    MCPExecutor,
    MCPServerConfig,
    MCPToolInfo,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_executor(*servers: tuple[str, str, list[str]]) -> MCPExecutor:
    """Helper to create an MCPExecutor with configs."""
    configs = {}
    for name, cmd, args in servers:
        configs[name] = MCPServerConfig(command=cmd, args=args)
    return MCPExecutor(configs)


def _make_session_with_tools(
    tools: list[tuple[str, str, dict[str, Any]]],
    call_results: dict[str, str] | None = None,
) -> _FakeClientSession:
    """Create a fake session with pre-loaded tools and call results."""
    session = _FakeClientSession()
    session._tools = [
        _FakeToolInfo(name=name, description=desc, inputSchema=schema)
        for name, desc, schema in tools
    ]
    if call_results:
        for tool_name, text in call_results.items():
            session._call_results[tool_name] = _FakeCallToolResult(
                content=[_FakeContentBlock(text=text)]
            )
    return session


# ---------------------------------------------------------------------------
# Tests: Construction and config
# ---------------------------------------------------------------------------


class TestMCPExecutorConstruction:
    def test_create_empty(self) -> None:
        executor = MCPExecutor()
        assert executor.tool_names == []

    def test_create_with_servers(self) -> None:
        executor = _make_executor(("test", "echo", ["hello"]))
        assert executor.tool_names == []  # No tools until connected

    def test_from_config(self) -> None:
        config = {
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "mcp-github"],
                    "env": {"TOKEN": "abc"},
                },
                "fs": {
                    "command": "npx",
                    "args": ["-y", "mcp-fs"],
                },
            }
        }
        executor = MCPExecutor.from_config(config)
        assert len(executor._servers) == 2
        assert executor._servers["github"].command == "npx"
        assert executor._servers["github"].env == {"TOKEN": "abc"}
        assert executor._servers["fs"].args == ["-y", "mcp-fs"]

    def test_from_config_empty(self) -> None:
        executor = MCPExecutor.from_config({})
        assert len(executor._servers) == 0

    def test_from_config_missing_mcp_servers_key(self) -> None:
        executor = MCPExecutor.from_config({"other": "stuff"})
        assert len(executor._servers) == 0


# ---------------------------------------------------------------------------
# Tests: Connection and tool discovery
# ---------------------------------------------------------------------------


class TestMCPConnection:
    @pytest.mark.asyncio
    async def test_connect_discovers_tools(self) -> None:
        executor = _make_executor(("myserver", "echo", []))
        session = _make_session_with_tools([
            ("read_file", "Read a file", {"type": "object"}),
            ("write_file", "Write a file", {"type": "object"}),
        ])

        async def fake_start_stdio(params: Any) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        executor._start_stdio = fake_start_stdio  # type: ignore[assignment]

        # Patch ClientSession to return our prepared session
        with patch("duh.adapters.mcp_executor.ClientSession", return_value=session):
            tools = await executor.connect("myserver")

        assert len(tools) == 2
        assert tools[0].name == "read_file"
        assert tools[1].name == "write_file"
        assert "mcp__myserver__read_file" in executor.tool_names
        assert "mcp__myserver__write_file" in executor.tool_names

    @pytest.mark.asyncio
    async def test_connect_unknown_server_raises(self) -> None:
        executor = MCPExecutor()
        with pytest.raises(KeyError, match="not configured"):
            await executor.connect("nonexistent")

    @pytest.mark.asyncio
    async def test_connect_failure_raises_runtime_error(self) -> None:
        executor = _make_executor(("bad", "false", []))

        async def fail_start(params: Any) -> tuple[Any, Any]:
            raise OSError("Cannot start process")

        executor._start_stdio = fail_start  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="Failed to connect"):
            await executor.connect("bad")

    @pytest.mark.asyncio
    async def test_disconnect_removes_tools(self) -> None:
        executor = _make_executor(("s1", "echo", []))
        session = _make_session_with_tools([("tool1", "t1", {})])

        async def fake_start(params: Any) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        executor._start_stdio = fake_start  # type: ignore[assignment]

        with patch("duh.adapters.mcp_executor.ClientSession", return_value=session):
            await executor.connect("s1")

        assert len(executor.tool_names) == 1
        await executor.disconnect("s1")
        assert len(executor.tool_names) == 0

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_is_noop(self) -> None:
        executor = MCPExecutor()
        await executor.disconnect("nope")  # Should not raise

    @pytest.mark.asyncio
    async def test_connect_all(self) -> None:
        executor = _make_executor(
            ("s1", "echo", []),
            ("s2", "echo", []),
        )
        session1 = _make_session_with_tools([("t1", "tool1", {})])
        session2 = _make_session_with_tools([("t2", "tool2", {})])

        sessions = iter([session1, session2])

        async def fake_start(params: Any) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        executor._start_stdio = fake_start  # type: ignore[assignment]

        with patch(
            "duh.adapters.mcp_executor.ClientSession",
            side_effect=lambda *a, **kw: next(sessions),
        ):
            results = await executor.connect_all()

        assert len(results) == 2
        assert len(results["s1"]) == 1
        assert len(results["s2"]) == 1
        assert len(executor.tool_names) == 2


# ---------------------------------------------------------------------------
# Tests: Tool execution
# ---------------------------------------------------------------------------


class TestMCPToolExecution:
    @pytest.mark.asyncio
    async def test_run_tool_success(self) -> None:
        executor = _make_executor(("srv", "echo", []))
        session = _make_session_with_tools(
            [("greet", "Say hello", {})],
            call_results={"greet": "Hello, world!"},
        )

        async def fake_start(params: Any) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        executor._start_stdio = fake_start  # type: ignore[assignment]

        with patch("duh.adapters.mcp_executor.ClientSession", return_value=session):
            await executor.connect("srv")

        result = await executor.run("mcp__srv__greet", {"name": "test"})
        assert result == "Hello, world!"

    @pytest.mark.asyncio
    async def test_run_tool_not_found(self) -> None:
        executor = MCPExecutor()
        with pytest.raises(KeyError, match="not found"):
            await executor.run("mcp__nope__nope", {})

    @pytest.mark.asyncio
    async def test_run_tool_server_disconnected(self) -> None:
        executor = _make_executor(("srv", "echo", []))
        # Manually add a tool to the index without a connection
        executor._tool_index["mcp__srv__orphan"] = MCPToolInfo(
            name="orphan", server_name="srv"
        )
        with pytest.raises(RuntimeError, match="not connected"):
            await executor.run("mcp__srv__orphan", {})

    @pytest.mark.asyncio
    async def test_run_tool_call_failure(self) -> None:
        executor = _make_executor(("srv", "echo", []))
        session = _make_session_with_tools(
            [("fail_tool", "Fails", {})],
            call_results={},  # No result for fail_tool -> will raise
        )

        async def fake_start(params: Any) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        executor._start_stdio = fake_start  # type: ignore[assignment]

        with patch("duh.adapters.mcp_executor.ClientSession", return_value=session):
            await executor.connect("srv")

        with pytest.raises(RuntimeError, match="tool call failed"):
            await executor.run("mcp__srv__fail_tool", {})

    @pytest.mark.asyncio
    async def test_run_tool_empty_content(self) -> None:
        executor = _make_executor(("srv", "echo", []))
        session = _make_session_with_tools(
            [("empty", "Returns nothing", {})],
        )
        # Set up a result with empty content
        session._call_results["empty"] = _FakeCallToolResult(content=[])

        async def fake_start(params: Any) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        executor._start_stdio = fake_start  # type: ignore[assignment]

        with patch("duh.adapters.mcp_executor.ClientSession", return_value=session):
            await executor.connect("srv")

        result = await executor.run("mcp__srv__empty", {})
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: Tool info
# ---------------------------------------------------------------------------


class TestMCPToolInfo:
    @pytest.mark.asyncio
    async def test_get_tool_info(self) -> None:
        executor = _make_executor(("srv", "echo", []))
        session = _make_session_with_tools([
            ("mytool", "My tool description", {"type": "object", "properties": {"x": {"type": "string"}}}),
        ])

        async def fake_start(params: Any) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        executor._start_stdio = fake_start  # type: ignore[assignment]

        with patch("duh.adapters.mcp_executor.ClientSession", return_value=session):
            await executor.connect("srv")

        info = executor.get_tool_info("mcp__srv__mytool")
        assert info is not None
        assert info.name == "mytool"
        assert info.server_name == "srv"
        assert info.description == "My tool description"
        assert info.input_schema["type"] == "object"

    def test_get_tool_info_not_found(self) -> None:
        executor = MCPExecutor()
        assert executor.get_tool_info("mcp__nope__nope") is None

    @pytest.mark.asyncio
    async def test_list_tools(self) -> None:
        executor = _make_executor(("srv", "echo", []))
        session = _make_session_with_tools([
            ("a", "Tool A", {}),
            ("b", "Tool B", {}),
        ])

        async def fake_start(params: Any) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        executor._start_stdio = fake_start  # type: ignore[assignment]

        with patch("duh.adapters.mcp_executor.ClientSession", return_value=session):
            await executor.connect("srv")

        tools = executor.list_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"a", "b"}
