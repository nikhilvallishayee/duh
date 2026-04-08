"""Tests for HTTP transport in duh.adapters.mcp_transports."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.adapters.mcp_transports import HTTPTransport, Transport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeHTTPResponse:
    """Simulates an httpx response."""
    def __init__(self, status_code: int = 200, json_body: dict | None = None):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.headers = {"content-type": "application/json"}

    def json(self) -> dict:
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# Tests: Protocol compliance
# ---------------------------------------------------------------------------

class TestHTTPTransportProtocol:
    def test_is_transport(self):
        t = HTTPTransport(base_url="http://localhost:8080")
        assert isinstance(t, Transport)


# ---------------------------------------------------------------------------
# Tests: Connection lifecycle
# ---------------------------------------------------------------------------

class TestHTTPTransportConnect:
    @pytest.mark.asyncio
    async def test_connect_creates_client(self):
        transport = HTTPTransport(base_url="http://localhost:8080")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("duh.adapters.mcp_transports.httpx.AsyncClient", return_value=mock_client):
            read_stream, write_stream = await transport.connect()
            assert transport.connected
            assert read_stream is not None
            assert write_stream is not None

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        transport = HTTPTransport(base_url="http://localhost:8080")
        transport._connected = True
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        transport._client = mock_client
        await transport.disconnect()
        assert not transport.connected
        mock_client.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Request/Response
# ---------------------------------------------------------------------------

class TestHTTPTransportSend:
    @pytest.mark.asyncio
    async def test_send_posts_json_rpc(self):
        transport = HTTPTransport(base_url="http://localhost:8080")
        transport._connected = True
        mock_client = AsyncMock()
        mock_resp = FakeHTTPResponse(200, {
            "jsonrpc": "2.0",
            "result": {"tools": [{"name": "test"}]},
            "id": 1,
        })
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        result = await transport.send({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 1,
        })
        assert result["jsonrpc"] == "2.0"
        assert "result" in result
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://localhost:8080/rpc"

    @pytest.mark.asyncio
    async def test_send_raises_when_disconnected(self):
        transport = HTTPTransport(base_url="http://localhost:8080")
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})

    @pytest.mark.asyncio
    async def test_send_with_custom_rpc_path(self):
        transport = HTTPTransport(
            base_url="http://localhost:8080",
            rpc_path="/api/mcp",
        )
        transport._connected = True
        mock_client = AsyncMock()
        mock_resp = FakeHTTPResponse(200, {"jsonrpc": "2.0", "result": {}, "id": 1})
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://localhost:8080/api/mcp"

    @pytest.mark.asyncio
    async def test_send_propagates_http_error(self):
        transport = HTTPTransport(base_url="http://localhost:8080")
        transport._connected = True
        mock_client = AsyncMock()
        mock_resp = FakeHTTPResponse(500)
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        with pytest.raises(Exception, match="500"):
            await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})


# ---------------------------------------------------------------------------
# Tests: Auth headers
# ---------------------------------------------------------------------------

class TestHTTPTransportAuth:
    def test_auth_headers_stored(self):
        transport = HTTPTransport(
            base_url="http://localhost:8080",
            headers={"Authorization": "Bearer secret"},
        )
        assert transport._headers["Authorization"] == "Bearer secret"

    @pytest.mark.asyncio
    async def test_auth_headers_sent_with_requests(self):
        transport = HTTPTransport(
            base_url="http://localhost:8080",
            headers={"Authorization": "Bearer tok"},
        )
        transport._connected = True
        mock_client = AsyncMock()
        mock_resp = FakeHTTPResponse(200, {"jsonrpc": "2.0", "result": {}, "id": 1})
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})
        call_kwargs = mock_client.post.call_args[1]
        sent_headers = call_kwargs.get("headers", {})
        assert "Authorization" in sent_headers
        assert sent_headers["Authorization"] == "Bearer tok"
