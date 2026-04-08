"""Tests for SSE transport in duh.adapters.mcp_transports."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.adapters.mcp_transports import (
    SSETransport,
    Transport,
    TransportConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeSSEEvent:
    """Simulates an httpx SSE event."""
    def __init__(self, data: str, event: str = "message"):
        self.data = data
        self.event = event


class FakeSSEStream:
    """Simulates an async SSE event iterator."""
    def __init__(self, events: list[FakeSSEEvent]):
        self._events = events
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._events):
            raise StopAsyncIteration
        evt = self._events[self._index]
        self._index += 1
        return evt


class FakeResponse:
    """Simulates an httpx response for SSE."""
    def __init__(self, status_code: int = 200, json_body: dict | None = None):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.headers = {"content-type": "text/event-stream"}

    def json(self) -> dict:
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# Tests: Transport protocol compliance
# ---------------------------------------------------------------------------

class TestTransportProtocol:
    def test_sse_transport_is_transport(self):
        t = SSETransport(url="http://localhost:8080/sse")
        assert isinstance(t, Transport)

    def test_transport_config_defaults(self):
        cfg = TransportConfig(url="http://localhost:8080")
        assert cfg.transport == "sse"
        assert cfg.headers == {}
        assert cfg.timeout == 30.0


# ---------------------------------------------------------------------------
# Tests: SSE connection
# ---------------------------------------------------------------------------

class TestSSETransportConnect:
    @pytest.mark.asyncio
    async def test_connect_sets_connected(self):
        transport = SSETransport(url="http://localhost:8080/sse")
        # Mock the httpx client
        mock_client = AsyncMock()
        mock_response = FakeResponse(200, {"endpoint": "/messages"})
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("duh.adapters.mcp_transports.httpx.AsyncClient", return_value=mock_client):
            # connect() should not raise
            read_stream, write_stream = await transport.connect()
            assert read_stream is not None
            assert write_stream is not None
            assert transport.connected

    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self):
        transport = SSETransport(url="http://localhost:8080/sse")
        transport._connected = True
        transport._client = AsyncMock()
        transport._client.aclose = AsyncMock()
        await transport.disconnect()
        assert not transport.connected


# ---------------------------------------------------------------------------
# Tests: SSE message sending
# ---------------------------------------------------------------------------

class TestSSETransportSend:
    @pytest.mark.asyncio
    async def test_send_posts_json_rpc(self):
        transport = SSETransport(url="http://localhost:8080/sse")
        transport._connected = True
        transport._message_endpoint = "http://localhost:8080/messages"
        mock_client = AsyncMock()
        mock_resp = FakeResponse(200, {"jsonrpc": "2.0", "result": {"tools": []}})
        mock_client.post = AsyncMock(return_value=mock_resp)
        transport._client = mock_client

        result = await transport.send({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 1,
        })
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://localhost:8080/messages"

    @pytest.mark.asyncio
    async def test_send_raises_when_disconnected(self):
        transport = SSETransport(url="http://localhost:8080/sse")
        transport._connected = False
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})


# ---------------------------------------------------------------------------
# Tests: SSE configuration
# ---------------------------------------------------------------------------

class TestSSEConfig:
    def test_custom_headers(self):
        transport = SSETransport(
            url="http://localhost:8080/sse",
            headers={"Authorization": "Bearer tok123"},
        )
        assert transport._headers["Authorization"] == "Bearer tok123"

    def test_custom_timeout(self):
        transport = SSETransport(url="http://localhost:8080/sse", timeout=60.0)
        assert transport._timeout == 60.0

    def test_default_timeout(self):
        transport = SSETransport(url="http://localhost:8080/sse")
        assert transport._timeout == 30.0
