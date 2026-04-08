"""Tests for duh.bridge.server and duh.bridge.session_relay."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.bridge.protocol import (
    ConnectMessage,
    DisconnectMessage,
    EventMessage,
    PromptMessage,
    encode_message,
)
from duh.bridge.session_relay import SessionRelay
from duh.bridge.server import BridgeServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeWebSocket:
    """Simulates a websockets server-side connection."""

    def __init__(self):
        self.sent: list[str] = []
        self._recv_queue: asyncio.Queue[str] = asyncio.Queue()
        self._closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if self._closed:
            raise Exception("Connection closed")
        return await asyncio.wait_for(self._recv_queue.get(), timeout=1.0)

    async def close(self) -> None:
        self._closed = True

    def feed(self, msg: str) -> None:
        self._recv_queue.put_nowait(msg)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await asyncio.wait_for(self._recv_queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            raise StopAsyncIteration


# ---------------------------------------------------------------------------
# Tests: SessionRelay
# ---------------------------------------------------------------------------

class TestSessionRelay:
    def test_create_relay(self):
        relay = SessionRelay()
        assert relay.session_count == 0

    def test_register_session(self):
        relay = SessionRelay()
        ws = FakeWebSocket()
        relay.register("sess-1", ws)  # type: ignore[arg-type]
        assert relay.session_count == 1
        assert relay.has_session("sess-1")

    def test_unregister_session(self):
        relay = SessionRelay()
        ws = FakeWebSocket()
        relay.register("sess-1", ws)  # type: ignore[arg-type]
        relay.unregister("sess-1")
        assert relay.session_count == 0
        assert not relay.has_session("sess-1")

    def test_unregister_nonexistent_is_noop(self):
        relay = SessionRelay()
        relay.unregister("nope")  # Should not raise

    @pytest.mark.asyncio
    async def test_send_event_to_session(self):
        relay = SessionRelay()
        ws = FakeWebSocket()
        relay.register("sess-1", ws)  # type: ignore[arg-type]

        event = EventMessage(
            session_id="sess-1",
            event_type="text_delta",
            data={"delta": "hello"},
        )
        await relay.send_event("sess-1", event)
        assert len(ws.sent) == 1
        parsed = json.loads(ws.sent[0])
        assert parsed["type"] == "event"
        assert parsed["data"]["delta"] == "hello"

    @pytest.mark.asyncio
    async def test_send_event_to_unknown_session_is_noop(self):
        relay = SessionRelay()
        event = EventMessage(
            session_id="nope",
            event_type="text_delta",
            data={},
        )
        # Should not raise
        await relay.send_event("nope", event)

    def test_get_websocket(self):
        relay = SessionRelay()
        ws = FakeWebSocket()
        relay.register("sess-1", ws)  # type: ignore[arg-type]
        assert relay.get_websocket("sess-1") is ws

    def test_get_websocket_unknown_returns_none(self):
        relay = SessionRelay()
        assert relay.get_websocket("nope") is None


# ---------------------------------------------------------------------------
# Tests: BridgeServer
# ---------------------------------------------------------------------------

class TestBridgeServer:
    def test_create_server(self):
        server = BridgeServer(host="localhost", port=9876)
        assert server._host == "localhost"
        assert server._port == 9876

    def test_create_server_with_token(self):
        server = BridgeServer(host="localhost", port=9876, token="secret")
        assert server._token == "secret"

    def test_default_port(self):
        server = BridgeServer()
        assert server._port == 8765

    @pytest.mark.asyncio
    async def test_handle_connect_valid_token(self):
        server = BridgeServer(token="secret")
        ws = FakeWebSocket()

        connect_msg = encode_message(ConnectMessage(token="secret", session_id="s1"))
        ws.feed(connect_msg)

        # Simulate the connection handler processing one message
        msg = await ws.recv()
        parsed = json.loads(msg)
        assert parsed["type"] == "connect"

        # The server should accept this connection
        from duh.bridge.protocol import decode_message, validate_token
        decoded = decode_message(msg)
        assert isinstance(decoded, ConnectMessage)
        assert validate_token(decoded.token, "secret")

    @pytest.mark.asyncio
    async def test_handle_connect_invalid_token(self):
        server = BridgeServer(token="secret")

        from duh.bridge.protocol import validate_token
        assert not validate_token("wrong", "secret")

    @pytest.mark.asyncio
    async def test_relay_engine_events(self):
        """Verify that engine events are forwarded to the WebSocket client."""
        relay = SessionRelay()
        ws = FakeWebSocket()
        relay.register("sess-1", ws)  # type: ignore[arg-type]

        # Simulate engine events being forwarded
        events = [
            {"type": "session", "session_id": "sess-1", "turn": 1},
            {"type": "text_delta", "text": "Hello"},
            {"type": "done"},
        ]

        for evt in events:
            event_msg = EventMessage(
                session_id="sess-1",
                event_type=evt["type"],
                data=evt,
            )
            await relay.send_event("sess-1", event_msg)

        assert len(ws.sent) == 3
        for i, raw in enumerate(ws.sent):
            parsed = json.loads(raw)
            assert parsed["type"] == "event"
            assert parsed["event_type"] == events[i]["type"]
