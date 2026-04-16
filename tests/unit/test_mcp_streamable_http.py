"""Tests for Streamable HTTP transport in duh.adapters.mcp_transports.

Covers transport type detection, request formatting, response parsing
(both application/json and text/event-stream), session-id tracking,
and the factory integration in mcp_executor._create_transport.
"""

from __future__ import annotations

import json
import sys
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.adapters.mcp_transports import StreamableHTTPTransport, Transport

# ---------------------------------------------------------------------------
# Ensure fake mcp module is installed so mcp_executor can import
# ---------------------------------------------------------------------------

_mcp_was_present = "mcp" in sys.modules

if not _mcp_was_present:
    mcp_mod = ModuleType("mcp")

    class _FakeStdioServerParameters:
        def __init__(self, command="", args=None, env=None):
            pass

    class _FakeClientSession:
        def __init__(self, *a, **kw):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return MagicMock(tools=[])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    mcp_mod.ClientSession = _FakeClientSession  # type: ignore[attr-defined]
    mcp_mod.StdioServerParameters = _FakeStdioServerParameters  # type: ignore[attr-defined]

    mcp_client = ModuleType("mcp.client")
    mcp_client_stdio = ModuleType("mcp.client.stdio")
    mcp_client_stdio.stdio_client = AsyncMock()  # type: ignore[attr-defined]

    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.client"] = mcp_client
    sys.modules["mcp.client.stdio"] = mcp_client_stdio

from duh.adapters.mcp_executor import (
    MCPExecutor,
    MCPServerConfig,
    _create_transport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeHTTPResponse:
    """Simulates an httpx response."""

    def __init__(
        self,
        status_code: int = 200,
        json_body: dict | None = None,
        text: str = "",
        content_type: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = text
        self.headers: dict[str, str] = {"content-type": content_type}
        if extra_headers:
            self.headers.update(extra_headers)

    def json(self) -> dict:
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# Tests: Protocol compliance
# ---------------------------------------------------------------------------


class TestStreamableHTTPProtocol:
    def test_is_transport(self):
        t = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        assert isinstance(t, Transport)


# ---------------------------------------------------------------------------
# Tests: Transport detection / factory
# ---------------------------------------------------------------------------


class TestStreamableHTTPDetection:
    def test_server_config_accepts_streamable_http(self):
        cfg = MCPServerConfig(
            command="",
            transport="streamable-http",
            url="http://localhost:8080/mcp",
        )
        assert cfg.transport == "streamable-http"
        assert cfg.url == "http://localhost:8080/mcp"

    def test_create_transport_returns_streamable_http(self):
        cfg = MCPServerConfig(
            command="",
            transport="streamable-http",
            url="http://localhost:8080/mcp",
        )
        transport = _create_transport(cfg)
        assert isinstance(transport, StreamableHTTPTransport)

    def test_from_config_parses_streamable_http(self):
        config = {
            "mcpServers": {
                "remote": {
                    "command": "",
                    "transport": "streamable-http",
                    "url": "http://remote.host:8080/mcp",
                    "headers": {"Authorization": "Bearer tok"},
                }
            }
        }
        executor = MCPExecutor.from_config(config)
        assert executor._servers["remote"].transport == "streamable-http"
        assert executor._servers["remote"].url == "http://remote.host:8080/mcp"
        assert executor._servers["remote"].headers["Authorization"] == "Bearer tok"


# ---------------------------------------------------------------------------
# Tests: Connection lifecycle
# ---------------------------------------------------------------------------


class TestStreamableHTTPConnect:
    @pytest.mark.asyncio
    async def test_connect_creates_client(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        mock_client = AsyncMock()

        with patch(
            "duh.adapters.mcp_transports.httpx.AsyncClient",
            return_value=mock_client,
        ):
            read_stream, write_stream = await transport.connect()
            assert transport.connected
            assert read_stream is not None
            assert write_stream is not None

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        transport._client = mock_client
        transport._session_id = "sess-123"

        await transport.disconnect()

        assert not transport.connected
        assert transport.session_id is None
        mock_client.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Request formatting
# ---------------------------------------------------------------------------


class TestStreamableHTTPRequestFormat:
    @pytest.mark.asyncio
    async def test_send_posts_json_rpc_to_url(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()
        mock_resp = FakeHTTPResponse(
            200,
            json_body={"jsonrpc": "2.0", "result": {"tools": []}, "id": 1},
        )
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        result = await transport.send(
            {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        )

        assert result["jsonrpc"] == "2.0"
        assert "result" in result
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://localhost:8080/mcp"

    @pytest.mark.asyncio
    async def test_send_includes_accept_header(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()
        mock_resp = FakeHTTPResponse(
            200,
            json_body={"jsonrpc": "2.0", "result": {}, "id": 1},
        )
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})

        call_kwargs = mock_client.post.call_args[1]
        headers = call_kwargs.get("headers", {})
        assert "Accept" in headers
        assert "text/event-stream" in headers["Accept"]
        assert "application/json" in headers["Accept"]

    @pytest.mark.asyncio
    async def test_send_includes_session_id_when_present(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        transport._session_id = "sess-abc"
        mock_client = AsyncMock()
        mock_resp = FakeHTTPResponse(
            200,
            json_body={"jsonrpc": "2.0", "result": {}, "id": 1},
        )
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})

        call_kwargs = mock_client.post.call_args[1]
        headers = call_kwargs.get("headers", {})
        assert headers.get("Mcp-Session-Id") == "sess-abc"

    @pytest.mark.asyncio
    async def test_send_includes_custom_headers(self):
        transport = StreamableHTTPTransport(
            url="http://localhost:8080/mcp",
            headers={"Authorization": "Bearer secret"},
        )
        transport._connected = True
        mock_client = AsyncMock()
        mock_resp = FakeHTTPResponse(
            200,
            json_body={"jsonrpc": "2.0", "result": {}, "id": 1},
        )
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})

        call_kwargs = mock_client.post.call_args[1]
        headers = call_kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer secret"

    @pytest.mark.asyncio
    async def test_send_raises_when_disconnected(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})


# ---------------------------------------------------------------------------
# Tests: Response parsing -- application/json
# ---------------------------------------------------------------------------


class TestStreamableHTTPJsonResponse:
    @pytest.mark.asyncio
    async def test_json_response_parsed(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()
        body = {"jsonrpc": "2.0", "result": {"name": "tool1"}, "id": 42}
        mock_resp = FakeHTTPResponse(200, json_body=body)
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        result = await transport.send(
            {"jsonrpc": "2.0", "method": "tools/call", "id": 42}
        )
        assert result == body


# ---------------------------------------------------------------------------
# Tests: Response parsing -- text/event-stream (streaming JSON lines)
# ---------------------------------------------------------------------------


class TestStreamableHTTPStreamingResponse:
    @pytest.mark.asyncio
    async def test_streaming_ndjson_returns_last_matching_id(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()

        lines = [
            json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 50}}),
            json.dumps({"jsonrpc": "2.0", "result": {"content": [{"text": "hello"}]}, "id": 7}),
        ]
        body_text = "\n".join(lines)

        mock_resp = FakeHTTPResponse(
            200,
            text=body_text,
            content_type="text/event-stream",
        )
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        result = await transport.send(
            {"jsonrpc": "2.0", "method": "tools/call", "id": 7}
        )
        assert result["id"] == 7
        assert "result" in result

    @pytest.mark.asyncio
    async def test_streaming_with_sse_data_prefix(self):
        """Server may send SSE-style 'data:' prefixed lines."""
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()

        body_text = (
            "data: " + json.dumps({"jsonrpc": "2.0", "result": {"ok": True}, "id": 3}) + "\n"
        )
        mock_resp = FakeHTTPResponse(
            200,
            text=body_text,
            content_type="text/event-stream",
        )
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        result = await transport.send(
            {"jsonrpc": "2.0", "method": "test", "id": 3}
        )
        assert result["id"] == 3
        assert result["result"]["ok"] is True

    @pytest.mark.asyncio
    async def test_streaming_falls_back_to_last_parsed(self):
        """When no line matches the request id, return the last parsed object."""
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()

        body_text = json.dumps({"jsonrpc": "2.0", "result": {"fallback": True}, "id": 999})
        mock_resp = FakeHTTPResponse(
            200,
            text=body_text,
            content_type="text/event-stream",
        )
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        # Request id=1 but response has id=999 -- falls back to last parsed
        result = await transport.send(
            {"jsonrpc": "2.0", "method": "test", "id": 1}
        )
        assert result["id"] == 999

    @pytest.mark.asyncio
    async def test_streaming_empty_body_raises(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()

        mock_resp = FakeHTTPResponse(
            200,
            text="",
            content_type="text/event-stream",
        )
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        with pytest.raises(RuntimeError, match="empty or unparseable"):
            await transport.send(
                {"jsonrpc": "2.0", "method": "test", "id": 1}
            )

    @pytest.mark.asyncio
    async def test_streaming_skips_non_json_lines(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()

        body_text = "\n".join([
            ": comment line",
            "event: ping",
            "",
            json.dumps({"jsonrpc": "2.0", "result": {"ok": True}, "id": 5}),
        ])
        mock_resp = FakeHTTPResponse(
            200,
            text=body_text,
            content_type="text/event-stream",
        )
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        result = await transport.send(
            {"jsonrpc": "2.0", "method": "test", "id": 5}
        )
        assert result["id"] == 5


# ---------------------------------------------------------------------------
# Tests: Session ID tracking
# ---------------------------------------------------------------------------


class TestStreamableHTTPSessionId:
    @pytest.mark.asyncio
    async def test_captures_session_id_from_response(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()

        mock_resp = FakeHTTPResponse(
            200,
            json_body={"jsonrpc": "2.0", "result": {}, "id": 1},
            extra_headers={"mcp-session-id": "sess-xyz-789"},
        )
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        await transport.send({"jsonrpc": "2.0", "method": "initialize", "id": 1})
        assert transport.session_id == "sess-xyz-789"

    @pytest.mark.asyncio
    async def test_session_id_none_initially(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        assert transport.session_id is None

    @pytest.mark.asyncio
    async def test_session_id_cleared_on_disconnect(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        transport._session_id = "sess-123"
        transport._client = AsyncMock()
        transport._client.aclose = AsyncMock()

        await transport.disconnect()
        assert transport.session_id is None


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


class TestStreamableHTTPErrors:
    @pytest.mark.asyncio
    async def test_http_error_propagated(self):
        transport = StreamableHTTPTransport(url="http://localhost:8080/mcp")
        transport._connected = True
        mock_client = AsyncMock()
        mock_resp = FakeHTTPResponse(500)
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        with pytest.raises(Exception, match="500"):
            await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})
