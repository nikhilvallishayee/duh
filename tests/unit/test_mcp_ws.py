"""Tests for WebSocket transport in duh.adapters.mcp_transports."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from duh.adapters.mcp_transports import WebSocketTransport, Transport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeWebSocket:
    """Simulates a websockets connection."""

    def __init__(self):
        self.sent: list[str] = []
        self._recv_queue: asyncio.Queue[str] = asyncio.Queue()
        self._closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        return await self._recv_queue.get()

    async def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def feed_response(self, data: dict[str, Any]) -> None:
        """Queue a JSON response for recv()."""
        self._recv_queue.put_nowait(json.dumps(data))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


# ---------------------------------------------------------------------------
# Tests: Protocol compliance
# ---------------------------------------------------------------------------

class TestWSTransportProtocol:
    def test_is_transport(self):
        t = WebSocketTransport(url="ws://localhost:8080/ws")
        assert isinstance(t, Transport)


# ---------------------------------------------------------------------------
# Tests: Connection lifecycle
# ---------------------------------------------------------------------------

class TestWSTransportConnect:
    @pytest.mark.asyncio
    async def test_connect_opens_websocket(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        fake_ws = FakeWebSocket()

        async def mock_connect(*args, **kwargs):
            return fake_ws

        with patch("duh.adapters.mcp_transports.websockets.connect", side_effect=mock_connect):
            read_stream, write_stream = await transport.connect()
            assert transport.connected
            assert read_stream is not None
            assert write_stream is not None

    @pytest.mark.asyncio
    async def test_disconnect_closes_websocket(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        fake_ws = FakeWebSocket()
        transport._ws = fake_ws
        transport._connected = True

        await transport.disconnect()
        assert not transport.connected
        assert fake_ws._closed


# ---------------------------------------------------------------------------
# Tests: Message exchange
# ---------------------------------------------------------------------------

class TestWSTransportSend:
    @pytest.mark.asyncio
    async def test_send_writes_json_and_reads_response(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        fake_ws = FakeWebSocket()
        transport._ws = fake_ws
        transport._connected = True

        expected = {"jsonrpc": "2.0", "result": {"tools": []}, "id": 1}

        # Start the listener
        transport._start_listener()

        # Feed the response after a small delay so send() registers the future first
        async def _feed_delayed():
            await asyncio.sleep(0.02)
            fake_ws.feed_response(expected)

        asyncio.create_task(_feed_delayed())

        result = await transport.send({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 1,
        })

        assert len(fake_ws.sent) == 1
        sent_msg = json.loads(fake_ws.sent[0])
        assert sent_msg["method"] == "tools/list"
        assert result == expected

    @pytest.mark.asyncio
    async def test_send_raises_when_disconnected(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.send({"jsonrpc": "2.0", "method": "test", "id": 1})


# ---------------------------------------------------------------------------
# Tests: Reconnection
# ---------------------------------------------------------------------------

class TestWSTransportReconnect:
    def test_reconnect_attempts_default(self):
        transport = WebSocketTransport(url="ws://localhost:8080/ws")
        assert transport._max_reconnect_attempts == 3

    def test_reconnect_attempts_custom(self):
        transport = WebSocketTransport(
            url="ws://localhost:8080/ws",
            max_reconnect_attempts=10,
        )
        assert transport._max_reconnect_attempts == 10

    @pytest.mark.asyncio
    async def test_reconnect_on_connection_lost(self):
        transport = WebSocketTransport(
            url="ws://localhost:8080/ws",
            max_reconnect_attempts=2,
        )
        # Simulate a connection drop followed by a successful reconnect
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        call_count = 0

        async def mock_connect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws1
            return ws2

        with patch("duh.adapters.mcp_transports.websockets.connect", side_effect=mock_connect):
            await transport.connect()
            assert transport.connected

            # Simulate disconnect
            transport._connected = False
            transport._ws = None

            # Reconnect should create a new connection
            await transport._reconnect()
            assert transport.connected
            assert call_count == 2


# ---------------------------------------------------------------------------
# Tests: Configuration
# ---------------------------------------------------------------------------

class TestWSTransportConfig:
    def test_custom_headers(self):
        transport = WebSocketTransport(
            url="ws://localhost:8080/ws",
            headers={"Authorization": "Bearer tok"},
        )
        assert transport._headers["Authorization"] == "Bearer tok"

    def test_url_stored(self):
        transport = WebSocketTransport(url="ws://example.com:9090/mcp")
        assert transport._url == "ws://example.com:9090/mcp"
