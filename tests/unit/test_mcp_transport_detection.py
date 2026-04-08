"""Tests for transport auto-detection and MCPExecutor multi-transport support."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure fake mcp module is installed for mcp_executor import.
# We preserve any previously-installed fake to avoid breaking other tests
# that share the process (e.g., test_mcp_executor.py).
# ---------------------------------------------------------------------------

_mcp_was_present = "mcp" in sys.modules

if not _mcp_was_present:
    mcp_mod = ModuleType("mcp")

    class _FakeStdioServerParameters:
        def __init__(self, command="", args=None, env=None):
            pass

    class _FakeClientSession:
        def __init__(self, *a, **kw): pass
        async def initialize(self): pass
        async def list_tools(self): return MagicMock(tools=[])
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    mcp_mod.ClientSession = _FakeClientSession
    mcp_mod.StdioServerParameters = _FakeStdioServerParameters

    mcp_client = ModuleType("mcp.client")
    mcp_client_stdio = ModuleType("mcp.client.stdio")
    mcp_client_stdio.stdio_client = AsyncMock()

    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.client"] = mcp_client
    sys.modules["mcp.client.stdio"] = mcp_client_stdio

from duh.adapters.mcp_executor import (
    MCPExecutor,
    MCPServerConfig,
    _create_transport,
)


# ---------------------------------------------------------------------------
# Tests: MCPServerConfig transport field
# ---------------------------------------------------------------------------

class TestMCPServerConfigTransport:
    def test_default_transport_is_stdio(self):
        cfg = MCPServerConfig(command="echo")
        assert cfg.transport == "stdio"

    def test_sse_transport(self):
        cfg = MCPServerConfig(command="", transport="sse", url="http://localhost:8080/sse")
        assert cfg.transport == "sse"
        assert cfg.url == "http://localhost:8080/sse"

    def test_http_transport(self):
        cfg = MCPServerConfig(command="", transport="http", url="http://localhost:8080")
        assert cfg.transport == "http"

    def test_ws_transport(self):
        cfg = MCPServerConfig(command="", transport="ws", url="ws://localhost:8080/ws")
        assert cfg.transport == "ws"


# ---------------------------------------------------------------------------
# Tests: Transport factory
# ---------------------------------------------------------------------------

class TestCreateTransport:
    def test_stdio_returns_none(self):
        """stdio transport is handled by the existing code path, not the factory."""
        cfg = MCPServerConfig(command="echo", transport="stdio")
        result = _create_transport(cfg)
        assert result is None

    def test_sse_creates_sse_transport(self):
        cfg = MCPServerConfig(
            command="",
            transport="sse",
            url="http://localhost:8080/sse",
        )
        from duh.adapters.mcp_transports import SSETransport
        transport = _create_transport(cfg)
        assert isinstance(transport, SSETransport)

    def test_http_creates_http_transport(self):
        cfg = MCPServerConfig(
            command="",
            transport="http",
            url="http://localhost:8080",
        )
        from duh.adapters.mcp_transports import HTTPTransport
        transport = _create_transport(cfg)
        assert isinstance(transport, HTTPTransport)

    def test_ws_creates_ws_transport(self):
        cfg = MCPServerConfig(
            command="",
            transport="ws",
            url="ws://localhost:8080/ws",
        )
        from duh.adapters.mcp_transports import WebSocketTransport
        transport = _create_transport(cfg)
        assert isinstance(transport, WebSocketTransport)

    def test_unknown_transport_raises(self):
        cfg = MCPServerConfig(command="", transport="grpc", url="localhost:50051")
        with pytest.raises(ValueError, match="Unsupported.*grpc"):
            _create_transport(cfg)


# ---------------------------------------------------------------------------
# Tests: from_config with transport fields
# ---------------------------------------------------------------------------

class TestFromConfigTransport:
    def test_from_config_with_sse(self):
        config = {
            "mcpServers": {
                "remote": {
                    "command": "",
                    "transport": "sse",
                    "url": "http://remote.host:8080/sse",
                }
            }
        }
        executor = MCPExecutor.from_config(config)
        assert executor._servers["remote"].transport == "sse"
        assert executor._servers["remote"].url == "http://remote.host:8080/sse"

    def test_from_config_mixed_transports(self):
        config = {
            "mcpServers": {
                "local": {
                    "command": "npx",
                    "args": ["-y", "mcp-fs"],
                },
                "remote_sse": {
                    "command": "",
                    "transport": "sse",
                    "url": "http://example.com/sse",
                },
                "remote_ws": {
                    "command": "",
                    "transport": "ws",
                    "url": "ws://example.com/ws",
                },
            }
        }
        executor = MCPExecutor.from_config(config)
        assert executor._servers["local"].transport == "stdio"
        assert executor._servers["remote_sse"].transport == "sse"
        assert executor._servers["remote_ws"].transport == "ws"


# ---------------------------------------------------------------------------
# Tests: Concurrent connection batching
# ---------------------------------------------------------------------------

class TestConcurrentConnect:
    @pytest.mark.asyncio
    async def test_connect_all_batches_local_and_remote(self):
        """Local (stdio) servers should connect first, then remote servers."""
        connection_order: list[str] = []

        config = {
            "mcpServers": {
                "local1": {"command": "echo", "args": []},
                "remote1": {"command": "", "transport": "sse", "url": "http://x/sse"},
                "remote2": {"command": "", "transport": "http", "url": "http://y"},
            }
        }
        executor = MCPExecutor.from_config(config)

        original_connect = executor.connect

        async def tracking_connect(name: str) -> list:
            connection_order.append(name)
            return []

        executor.connect = tracking_connect  # type: ignore[assignment]
        await executor.connect_all()

        # local1 must come before remote servers
        local_idx = connection_order.index("local1")
        remote_indices = [
            connection_order.index(n) for n in ("remote1", "remote2")
        ]
        for ri in remote_indices:
            assert local_idx < ri, (
                f"local1 (idx={local_idx}) should connect before remote (idx={ri})"
            )
