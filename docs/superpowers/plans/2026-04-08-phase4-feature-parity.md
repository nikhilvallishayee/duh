# Phase 4: Feature Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring D.U.H. to feature parity with Claude Code on three axes: multi-transport MCP (SSE, HTTP, WebSocket alongside existing stdio), an attachment system for images/PDFs/files, and a remote bridge that exposes engine sessions over WebSocket for external UIs and integrations.

**Architecture:** Three new modules, one extended module:
- `duh/adapters/mcp_transports.py` -- SSE, HTTP, WebSocket transport classes behind a common `Transport` protocol
- `duh/adapters/mcp_executor.py` -- extended with transport auto-detection and factory pattern
- `duh/kernel/attachments.py` -- Attachment dataclass + AttachmentManager + ImageBlock content type
- `duh/bridge/` -- WebSocket relay server mapping remote clients to Engine sessions

All changes are backward-compatible. Existing stdio MCP continues to work unchanged. New features are gated behind optional dependencies.

**Tech Stack:** Python 3.12+, httpx (SSE/HTTP), websockets (WS), asyncio. New optional deps: httpx (already in core deps), websockets, pdfplumber.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `duh/adapters/mcp_transports.py` | Transport protocol + SSE, HTTP, WebSocket classes |
| Modify | `duh/adapters/mcp_executor.py` | Transport factory, MCPServerConfig extension, concurrent connect |
| Create | `duh/kernel/attachments.py` | Attachment dataclass, AttachmentManager, file/image/PDF handling |
| Modify | `duh/kernel/messages.py` | Add ImageBlock content type |
| Create | `duh/bridge/__init__.py` | Package init |
| Create | `duh/bridge/protocol.py` | JSON message protocol for bridge |
| Create | `duh/bridge/session_relay.py` | Maps WebSocket connections to Engine sessions |
| Create | `duh/bridge/server.py` | WebSocket server that relays engine events |
| Modify | `duh/cli/parser.py` | Add `bridge` subcommand |
| Modify | `duh/cli/main.py` | Wire `bridge start` command |
| Modify | `pyproject.toml` | Add optional dependency groups: bridge, attachments |
| Create | `tests/unit/test_mcp_sse.py` | SSE transport tests |
| Create | `tests/unit/test_mcp_http.py` | HTTP transport tests |
| Create | `tests/unit/test_mcp_ws.py` | WebSocket transport tests |
| Create | `tests/unit/test_mcp_transport_detection.py` | Auto-detection + factory tests |
| Create | `tests/unit/test_attachments.py` | Attachment system tests |
| Create | `tests/unit/test_bridge_protocol.py` | Bridge protocol tests |
| Create | `tests/unit/test_bridge_server.py` | Bridge server tests |

---

### Task 1: SSE Transport for MCP

**Files:**
- Create: `duh/adapters/mcp_transports.py`
- Create: `tests/unit/test_mcp_sse.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mcp_sse.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_sse.py -v`
Expected: FAIL -- `duh.adapters.mcp_transports` does not exist yet

- [ ] **Step 3: Implement the Transport protocol and SSETransport**

```python
# duh/adapters/mcp_transports.py
"""MCP transport implementations -- SSE, HTTP, WebSocket.

The stdio transport lives in mcp_executor.py (via the ``mcp`` SDK).
These transports handle remote MCP servers over HTTP-based protocols.

Each transport implements the Transport protocol:
    connect() -> (read_stream, write_stream)
    send(message) -> response
    disconnect()

Requires the ``httpx`` package for SSE/HTTP and ``websockets`` for WS.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports -- graceful degradation when optional deps not installed
# ---------------------------------------------------------------------------

try:
    import httpx

    _httpx_available = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    _httpx_available = False

try:
    import websockets

    _ws_available = True
except ImportError:
    websockets = None  # type: ignore[assignment]
    _ws_available = False


def _require_httpx() -> None:
    if not _httpx_available:
        raise RuntimeError(
            "The 'httpx' package is required for SSE/HTTP MCP transport. "
            "Install it with: pip install httpx"
        )


def _require_websockets() -> None:
    if not _ws_available:
        raise RuntimeError(
            "The 'websockets' package is required for WebSocket MCP transport. "
            "Install it with: pip install websockets"
        )


# ---------------------------------------------------------------------------
# Transport protocol
# ---------------------------------------------------------------------------


@dataclass
class TransportConfig:
    """Configuration for a remote MCP transport."""

    url: str
    transport: str = "sse"  # sse | http | ws
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0


@runtime_checkable
class Transport(Protocol):
    """Protocol that all MCP transports implement."""

    @property
    def connected(self) -> bool: ...

    async def connect(self) -> tuple[Any, Any]:
        """Connect and return (read_stream, write_stream) or equivalent."""
        ...

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC message and return the response."""
        ...

    async def disconnect(self) -> None:
        """Disconnect and clean up resources."""
        ...


# ---------------------------------------------------------------------------
# SSE Transport
# ---------------------------------------------------------------------------


class SSETransport:
    """MCP transport over Server-Sent Events.

    The MCP SSE protocol:
    1. Client connects to the SSE endpoint (GET)
    2. Server sends an ``endpoint`` event with the message POST URL
    3. Client sends JSON-RPC requests via POST to that URL
    4. Server streams responses via the SSE connection

    This is the standard remote MCP transport used by most MCP servers.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        _require_httpx()
        self._url = url
        self._headers: dict[str, str] = headers or {}
        self._timeout = timeout
        self._connected = False
        self._client: Any = None  # httpx.AsyncClient
        self._message_endpoint: str = ""
        self._sse_task: asyncio.Task[None] | None = None
        self._response_queues: dict[int | str, asyncio.Queue[dict[str, Any]]] = {}
        self._next_id = 1

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> tuple[Any, Any]:
        """Connect to the SSE endpoint.

        Returns (read_stream, write_stream) as a pair of asyncio.Queue
        objects that mimic the mcp SDK's stream interface. The read_stream
        receives server-initiated messages; the write_stream is used
        internally by send().
        """
        _require_httpx()
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(self._timeout),
        )

        # Initial GET to the SSE endpoint to discover the message URL
        response = await self._client.get(self._url)
        response.raise_for_status()

        # The SSE endpoint may return the message endpoint in JSON or
        # as an SSE event. Handle both patterns.
        if "application/json" in response.headers.get("content-type", ""):
            data = response.json()
            self._message_endpoint = data.get("endpoint", "")
        else:
            # Assume the message endpoint is derived from the SSE URL
            # by replacing /sse with /messages (common MCP convention)
            base = self._url.rsplit("/", 1)[0]
            self._message_endpoint = f"{base}/messages"

        # If the server gave us a relative endpoint, make it absolute
        if self._message_endpoint and not self._message_endpoint.startswith("http"):
            base = self._url.rsplit("/", 1)[0]
            self._message_endpoint = f"{base}/{self._message_endpoint.lstrip('/')}"

        read_stream: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        write_stream: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._connected = True

        logger.info(
            "SSE transport connected: %s -> %s",
            self._url,
            self._message_endpoint,
        )
        return read_stream, write_stream

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC message via HTTP POST to the message endpoint.

        Returns the parsed JSON response.
        """
        if not self._connected or self._client is None:
            raise RuntimeError("SSE transport not connected")

        response = await self._client.post(
            self._message_endpoint,
            json=message,
            headers={"Content-Type": "application/json", **self._headers},
        )
        response.raise_for_status()
        return response.json()

    async def disconnect(self) -> None:
        """Disconnect and clean up the HTTP client."""
        self._connected = False
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._message_endpoint = ""
        logger.info("SSE transport disconnected: %s", self._url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_sse.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/adapters/mcp_transports.py tests/unit/test_mcp_sse.py
git commit -m "feat(mcp): add Transport protocol and SSE transport implementation"
```

---

### Task 2: HTTP Transport for MCP

**Files:**
- Modify: `duh/adapters/mcp_transports.py`
- Create: `tests/unit/test_mcp_http.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mcp_http.py
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
        transport._client = AsyncMock()
        transport._client.aclose = AsyncMock()
        await transport.disconnect()
        assert not transport.connected
        transport._client.aclose.assert_called_once()


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_http.py -v`
Expected: FAIL -- `HTTPTransport` not defined

- [ ] **Step 3: Implement HTTPTransport**

Append the following to `duh/adapters/mcp_transports.py` after the SSETransport class:

```python
# ---------------------------------------------------------------------------
# HTTP Transport
# ---------------------------------------------------------------------------


class HTTPTransport:
    """MCP transport over plain HTTP POST (JSON-RPC).

    Each tool call is a single HTTP request/response. No persistent
    connection -- simple and reliable for serverless deployments.

    The server URL is ``{base_url}{rpc_path}`` (default: /rpc).
    """

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        rpc_path: str = "/rpc",
    ) -> None:
        _require_httpx()
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = headers or {}
        self._timeout = timeout
        self._rpc_path = rpc_path
        self._connected = False
        self._client: Any = None  # httpx.AsyncClient

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> tuple[Any, Any]:
        """Create the HTTP client. No handshake needed for plain HTTP."""
        _require_httpx()
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(self._timeout),
        )
        self._connected = True

        read_stream: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        write_stream: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        logger.info("HTTP transport connected: %s", self._base_url)
        return read_stream, write_stream

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request via POST.

        Returns the parsed JSON response.
        """
        if not self._connected or self._client is None:
            raise RuntimeError("HTTP transport not connected")

        url = f"{self._base_url}{self._rpc_path}"
        response = await self._client.post(
            url,
            json=message,
            headers={"Content-Type": "application/json", **self._headers},
        )
        response.raise_for_status()
        return response.json()

    async def disconnect(self) -> None:
        """Close the HTTP client."""
        self._connected = False
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("HTTP transport disconnected: %s", self._base_url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_http.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/adapters/mcp_transports.py tests/unit/test_mcp_http.py
git commit -m "feat(mcp): add HTTP transport for plain JSON-RPC over POST"
```

---

### Task 3: WebSocket Transport for MCP

**Files:**
- Modify: `duh/adapters/mcp_transports.py`
- Create: `tests/unit/test_mcp_ws.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mcp_ws.py
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

        with patch("duh.adapters.mcp_transports.websockets.connect", return_value=fake_ws):
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

        # Queue the response before sending (since recv is awaited after send)
        expected = {"jsonrpc": "2.0", "result": {"tools": []}, "id": 1}
        fake_ws.feed_response(expected)

        # Start the listener that routes responses
        transport._start_listener()
        await asyncio.sleep(0.01)  # let listener pick up the message

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_ws.py -v`
Expected: FAIL -- `WebSocketTransport` not defined

- [ ] **Step 3: Implement WebSocketTransport**

Append the following to `duh/adapters/mcp_transports.py` after the HTTPTransport class:

```python
# ---------------------------------------------------------------------------
# WebSocket Transport
# ---------------------------------------------------------------------------


class WebSocketTransport:
    """MCP transport over WebSocket (bidirectional JSON-RPC).

    Maintains a persistent WebSocket connection. Messages are sent as
    JSON strings. A background listener task routes incoming messages
    to per-request response queues keyed by JSON-RPC ``id``.

    Supports automatic reconnection on connection drop.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        max_reconnect_attempts: int = 3,
        reconnect_delay: float = 1.0,
    ) -> None:
        _require_websockets()
        self._url = url
        self._headers: dict[str, str] = headers or {}
        self._timeout = timeout
        self._max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_delay = reconnect_delay
        self._connected = False
        self._ws: Any = None  # websockets connection
        self._listener_task: asyncio.Task[None] | None = None
        self._pending: dict[int | str, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> tuple[Any, Any]:
        """Open a WebSocket connection to the MCP server.

        Returns (read_stream, write_stream) as asyncio.Queue objects.
        """
        _require_websockets()
        extra_headers = self._headers if self._headers else None
        self._ws = await websockets.connect(
            self._url,
            additional_headers=extra_headers,
        )
        self._connected = True

        read_stream: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        write_stream: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        logger.info("WebSocket transport connected: %s", self._url)
        return read_stream, write_stream

    def _start_listener(self) -> None:
        """Start the background listener that routes incoming messages."""
        if self._listener_task is None or self._listener_task.done():
            self._listener_task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        """Background task: read messages from WebSocket and route them."""
        try:
            while self._connected and self._ws is not None:
                try:
                    raw = await self._ws.recv()
                    msg = json.loads(raw)
                    msg_id = msg.get("id")
                    if msg_id is not None and msg_id in self._pending:
                        self._pending[msg_id].set_result(msg)
                except Exception:
                    if self._connected:
                        logger.debug("WebSocket listener error", exc_info=True)
                    break
        except asyncio.CancelledError:
            pass

    async def send(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC message and wait for the matching response.

        The message ``id`` is used to correlate the response.
        """
        if not self._connected or self._ws is None:
            raise RuntimeError("WebSocket transport not connected")

        msg_id = message.get("id", self._next_id)
        self._next_id += 1

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[msg_id] = future

        try:
            await self._ws.send(json.dumps(message))
            result = await asyncio.wait_for(future, timeout=self._timeout)
            return result
        finally:
            self._pending.pop(msg_id, None)

    async def _reconnect(self) -> None:
        """Attempt to reconnect to the WebSocket server."""
        for attempt in range(1, self._max_reconnect_attempts + 1):
            try:
                logger.info(
                    "WebSocket reconnect attempt %d/%d: %s",
                    attempt,
                    self._max_reconnect_attempts,
                    self._url,
                )
                extra_headers = self._headers if self._headers else None
                self._ws = await websockets.connect(
                    self._url,
                    additional_headers=extra_headers,
                )
                self._connected = True
                self._start_listener()
                logger.info("WebSocket reconnected: %s", self._url)
                return
            except Exception:
                if attempt < self._max_reconnect_attempts:
                    await asyncio.sleep(self._reconnect_delay * attempt)
                else:
                    logger.error(
                        "WebSocket reconnect failed after %d attempts: %s",
                        self._max_reconnect_attempts,
                        self._url,
                    )
                    raise

    async def disconnect(self) -> None:
        """Close the WebSocket connection and clean up."""
        self._connected = False
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        # Cancel any pending requests
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        logger.info("WebSocket transport disconnected: %s", self._url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_ws.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/adapters/mcp_transports.py tests/unit/test_mcp_ws.py
git commit -m "feat(mcp): add WebSocket transport with reconnection support"
```

---

### Task 4: Transport Auto-Detection + MCPExecutor Integration

**Files:**
- Modify: `duh/adapters/mcp_executor.py`
- Create: `tests/unit/test_mcp_transport_detection.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mcp_transport_detection.py
"""Tests for transport auto-detection and MCPExecutor multi-transport support."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Re-use the fake mcp module from test_mcp_executor.py
# (it must be installed before importing mcp_executor)
if "mcp" not in sys.modules:
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

# Force fresh import
if "duh.adapters.mcp_executor" in sys.modules:
    del sys.modules["duh.adapters.mcp_executor"]

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_transport_detection.py -v`
Expected: FAIL -- `_create_transport` not found, `MCPServerConfig` lacks `transport`/`url` fields

- [ ] **Step 3: Modify MCPServerConfig and MCPExecutor**

In `duh/adapters/mcp_executor.py`, make these changes:

**3a.** Add `transport` and `url` fields to `MCPServerConfig`:

Replace the existing `MCPServerConfig` dataclass (lines 47-53) with:

```python
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
```

**3b.** Add the transport factory function after `MCPConnection`:

```python
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
```

**3c.** Update `connect_all()` to batch local vs remote (replace existing `connect_all` method):

```python
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
```

**3d.** Update `from_config()` to parse transport fields (replace existing `from_config` method):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_mcp_transport_detection.py tests/unit/test_mcp_executor.py -v`
Expected: All PASS (existing tests still pass too)

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/adapters/mcp_executor.py tests/unit/test_mcp_transport_detection.py
git commit -m "feat(mcp): transport auto-detection, factory, and concurrent connect batching"
```

---

### Task 5: Attachment System

**Files:**
- Create: `duh/kernel/attachments.py`
- Modify: `duh/kernel/messages.py`
- Create: `tests/unit/test_attachments.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_attachments.py
"""Tests for duh.kernel.attachments — file, image, and PDF attachment handling."""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from duh.kernel.attachments import (
    Attachment,
    AttachmentManager,
    MAX_ATTACHMENT_SIZE,
)
from duh.kernel.messages import ImageBlock


# ---------------------------------------------------------------------------
# Tests: Attachment dataclass
# ---------------------------------------------------------------------------

class TestAttachment:
    def test_create_text_attachment(self):
        a = Attachment(
            name="readme.txt",
            content_type="text/plain",
            data=b"Hello, world!",
        )
        assert a.name == "readme.txt"
        assert a.content_type == "text/plain"
        assert a.data == b"Hello, world!"
        assert a.metadata == {}

    def test_create_with_metadata(self):
        a = Attachment(
            name="config.json",
            content_type="application/json",
            data=b'{"key": "value"}',
            metadata={"source": "clipboard"},
        )
        assert a.metadata["source"] == "clipboard"

    def test_size_property(self):
        data = b"x" * 1024
        a = Attachment(name="f.bin", content_type="application/octet-stream", data=data)
        assert a.size == 1024

    def test_is_image(self):
        a = Attachment(name="photo.png", content_type="image/png", data=b"\x89PNG")
        assert a.is_image is True

    def test_is_not_image(self):
        a = Attachment(name="doc.txt", content_type="text/plain", data=b"text")
        assert a.is_image is False

    def test_text_property_for_text_file(self):
        a = Attachment(name="f.txt", content_type="text/plain", data=b"hello")
        assert a.text == "hello"

    def test_text_property_for_binary_returns_none(self):
        a = Attachment(name="f.bin", content_type="application/octet-stream", data=b"\x00\x01")
        assert a.text is None

    def test_base64_property(self):
        data = b"test data"
        a = Attachment(name="f.bin", content_type="application/octet-stream", data=data)
        assert a.base64 == base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# Tests: ImageBlock content type
# ---------------------------------------------------------------------------

class TestImageBlock:
    def test_create_image_block(self):
        block = ImageBlock(
            media_type="image/png",
            data=base64.b64encode(b"\x89PNG").decode("ascii"),
        )
        assert block.type == "image"
        assert block.media_type == "image/png"
        assert block.data == base64.b64encode(b"\x89PNG").decode("ascii")

    def test_image_block_is_frozen(self):
        block = ImageBlock(media_type="image/jpeg", data="abc")
        with pytest.raises(AttributeError):
            block.data = "xyz"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests: AttachmentManager — file reading
# ---------------------------------------------------------------------------

class TestAttachmentManagerFiles:
    def test_read_text_file(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("Hello from file", encoding="utf-8")
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        assert att.name == "test.txt"
        assert att.content_type == "text/plain"
        assert att.text == "Hello from file"

    def test_read_json_file(self, tmp_path: Path):
        f = tmp_path / "data.json"
        f.write_text('{"key": 1}', encoding="utf-8")
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        assert att.content_type == "application/json"

    def test_read_python_file(self, tmp_path: Path):
        f = tmp_path / "script.py"
        f.write_text("print('hi')", encoding="utf-8")
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        # Python files should be detected as text
        assert att.text == "print('hi')"

    def test_read_image_file(self, tmp_path: Path):
        f = tmp_path / "photo.png"
        # Minimal PNG header
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        f.write_bytes(png_header)
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        assert att.is_image
        assert att.content_type == "image/png"

    def test_read_nonexistent_file_raises(self):
        mgr = AttachmentManager()
        with pytest.raises(FileNotFoundError):
            mgr.read_file("/nonexistent/file.txt")

    def test_read_file_too_large_raises(self, tmp_path: Path):
        f = tmp_path / "huge.bin"
        # Write just over the limit
        f.write_bytes(b"\x00" * (MAX_ATTACHMENT_SIZE + 1))
        mgr = AttachmentManager()
        with pytest.raises(ValueError, match="exceeds.*limit"):
            mgr.read_file(str(f))


# ---------------------------------------------------------------------------
# Tests: AttachmentManager — image handling
# ---------------------------------------------------------------------------

class TestAttachmentManagerImages:
    def test_to_image_block(self, tmp_path: Path):
        f = tmp_path / "img.png"
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        f.write_bytes(data)
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        block = mgr.to_image_block(att)
        assert isinstance(block, ImageBlock)
        assert block.media_type == "image/png"
        assert block.data == base64.b64encode(data).decode("ascii")

    def test_to_image_block_rejects_non_image(self, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_text("not an image", encoding="utf-8")
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        with pytest.raises(ValueError, match="not an image"):
            mgr.to_image_block(att)


# ---------------------------------------------------------------------------
# Tests: AttachmentManager — content type detection
# ---------------------------------------------------------------------------

class TestContentTypeDetection:
    def test_detect_png(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("photo.png", b"\x89PNG") == "image/png"

    def test_detect_jpeg(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("photo.jpg", b"\xff\xd8\xff") == "image/jpeg"

    def test_detect_gif(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("anim.gif", b"GIF89a") == "image/gif"

    def test_detect_webp(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("img.webp", b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"

    def test_detect_pdf(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("doc.pdf", b"%PDF-1.4") == "application/pdf"

    def test_detect_json_by_extension(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("data.json", b'{"key": 1}') == "application/json"

    def test_detect_python_by_extension(self):
        mgr = AttachmentManager()
        ct = mgr.detect_content_type("script.py", b"print('hi')")
        assert "text" in ct  # text/x-python or text/plain

    def test_detect_unknown_binary(self):
        mgr = AttachmentManager()
        ct = mgr.detect_content_type("mystery.xyz", b"\x00\x01\x02\x03")
        assert ct == "application/octet-stream"

    def test_detect_unknown_text(self):
        mgr = AttachmentManager()
        ct = mgr.detect_content_type("mystery.xyz", b"looks like text content here")
        # Should detect as text when content is printable
        assert "text" in ct


# ---------------------------------------------------------------------------
# Tests: AttachmentManager — PDF handling
# ---------------------------------------------------------------------------

class TestAttachmentManagerPDF:
    def test_extract_pdf_text_basic(self, tmp_path: Path):
        """Test basic PDF text extraction (without pdfplumber, uses fallback)."""
        mgr = AttachmentManager()
        # Create a minimal PDF-like file
        f = tmp_path / "doc.pdf"
        # Real PDF parsing needs pdfplumber; test the fallback path
        f.write_bytes(b"%PDF-1.4 some content stream (Hello World) Tj")
        att = mgr.read_file(str(f))
        assert att.content_type == "application/pdf"
        # The text extraction should at least not crash
        text = mgr.extract_text(att)
        assert isinstance(text, str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_attachments.py -v`
Expected: FAIL -- modules not found

- [ ] **Step 3a: Add ImageBlock to messages.py**

Add the following after `ThinkingBlock` in `duh/kernel/messages.py` (after line 51):

```python
@dataclass(frozen=True)
class ImageBlock:
    """An image content block (base64-encoded)."""
    media_type: str  # image/png, image/jpeg, image/gif, image/webp
    data: str  # base64-encoded image data
    type: str = "image"
```

Then update the `ContentBlock` type alias (line 54) to include `ImageBlock`:

```python
ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | ThinkingBlock | ImageBlock | dict[str, Any]
```

- [ ] **Step 3b: Implement the attachment system**

```python
# duh/kernel/attachments.py
"""Attachment system -- read files, detect types, handle images and PDFs.

Provides the Attachment dataclass and AttachmentManager for converting
files into content blocks that can be sent to AI models.

Image files are base64-encoded into ImageBlock content blocks.
PDF files get text extracted (via pdfplumber if available, fallback otherwise).
Text files are read directly.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from duh.kernel.messages import ImageBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB

# Magic bytes for common image formats
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF", "application/pdf"),
]

# WebP has a more complex signature: RIFF....WEBP
_WEBP_MAGIC = b"RIFF"
_WEBP_MARKER = b"WEBP"

# Extension-based fallbacks for common dev files
_EXT_CONTENT_TYPES: dict[str, str] = {
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".json": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "text/x-toml",
    ".md": "text/markdown",
    ".rst": "text/x-rst",
    ".html": "text/html",
    ".css": "text/css",
    ".xml": "text/xml",
    ".csv": "text/csv",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".zsh": "text/x-shellscript",
    ".rb": "text/x-ruby",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".java": "text/x-java",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
    ".hpp": "text/x-c++",
    ".sql": "text/x-sql",
    ".r": "text/x-r",
    ".lua": "text/x-lua",
    ".swift": "text/x-swift",
    ".kt": "text/x-kotlin",
    ".tex": "text/x-tex",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".cfg": "text/plain",
    ".ini": "text/plain",
    ".env": "text/plain",
}

# Image content types
_IMAGE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "image/svg+xml", "image/bmp", "image/tiff",
})


# ---------------------------------------------------------------------------
# Attachment dataclass
# ---------------------------------------------------------------------------

@dataclass
class Attachment:
    """A file attachment with content type detection."""

    name: str
    content_type: str
    data: bytes
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        """Size in bytes."""
        return len(self.data)

    @property
    def is_image(self) -> bool:
        """True if this attachment is an image."""
        return self.content_type in _IMAGE_TYPES

    @property
    def text(self) -> str | None:
        """Decode as text, or None if binary."""
        if self.content_type.startswith("text/") or self.content_type in (
            "application/json", "application/xml",
        ):
            try:
                return self.data.decode("utf-8")
            except UnicodeDecodeError:
                return None
        # Try decoding anyway for unknown types
        try:
            decoded = self.data.decode("utf-8")
            # If it decoded cleanly and looks like text, return it
            if _is_likely_text(decoded):
                return decoded
        except (UnicodeDecodeError, ValueError):
            pass
        return None

    @property
    def base64(self) -> str:
        """Base64-encoded data as ASCII string."""
        return base64.b64encode(self.data).decode("ascii")


def _is_likely_text(s: str) -> bool:
    """Heuristic: is this string likely text (not binary gibberish)?"""
    if not s:
        return True
    # Count control characters (excluding common whitespace)
    control = sum(1 for c in s[:1024] if ord(c) < 32 and c not in "\n\r\t")
    return control / min(len(s), 1024) < 0.1


# ---------------------------------------------------------------------------
# AttachmentManager
# ---------------------------------------------------------------------------

class AttachmentManager:
    """Reads files, detects content types, and converts to content blocks."""

    def read_file(self, path: str) -> Attachment:
        """Read a file and return an Attachment.

        Raises FileNotFoundError if the file does not exist.
        Raises ValueError if the file exceeds MAX_ATTACHMENT_SIZE.
        """
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        size = p.stat().st_size
        if size > MAX_ATTACHMENT_SIZE:
            raise ValueError(
                f"File '{p.name}' ({size:,} bytes) exceeds the "
                f"{MAX_ATTACHMENT_SIZE:,} byte limit"
            )

        data = p.read_bytes()
        content_type = self.detect_content_type(p.name, data)

        return Attachment(
            name=p.name,
            content_type=content_type,
            data=data,
            metadata={"path": str(p.resolve()), "size": size},
        )

    def detect_content_type(self, filename: str, data: bytes) -> str:
        """Detect content type from filename and magic bytes.

        Priority: magic bytes > extension > heuristic.
        """
        # Check magic bytes first
        for magic, ct in _MAGIC_SIGNATURES:
            if data[:len(magic)] == magic:
                return ct

        # WebP special case: RIFF....WEBP
        if data[:4] == _WEBP_MAGIC and len(data) >= 12 and data[8:12] == _WEBP_MARKER:
            return "image/webp"

        # Check by extension
        ext = Path(filename).suffix.lower()
        if ext in _EXT_CONTENT_TYPES:
            return _EXT_CONTENT_TYPES[ext]

        # Try mimetypes module
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed

        # Heuristic: try decoding as text
        try:
            decoded = data[:4096].decode("utf-8")
            if _is_likely_text(decoded):
                return "text/plain"
        except (UnicodeDecodeError, ValueError):
            pass

        return "application/octet-stream"

    def to_image_block(self, attachment: Attachment) -> ImageBlock:
        """Convert an image attachment to an ImageBlock content block.

        Raises ValueError if the attachment is not an image.
        """
        if not attachment.is_image:
            raise ValueError(
                f"'{attachment.name}' is not an image "
                f"(content_type={attachment.content_type})"
            )
        return ImageBlock(
            media_type=attachment.content_type,
            data=attachment.base64,
        )

    def extract_text(self, attachment: Attachment) -> str:
        """Extract text from an attachment.

        For text files: returns the text content directly.
        For PDFs: uses pdfplumber if available, otherwise basic extraction.
        For images: returns a description placeholder.
        For other types: returns base64 summary.
        """
        # Text files
        if attachment.text is not None:
            return attachment.text

        # PDF
        if attachment.content_type == "application/pdf":
            return self._extract_pdf_text(attachment)

        # Image
        if attachment.is_image:
            return f"[Image: {attachment.name} ({attachment.content_type}, {attachment.size:,} bytes)]"

        # Binary fallback
        return f"[Binary file: {attachment.name} ({attachment.content_type}, {attachment.size:,} bytes)]"

    def _extract_pdf_text(self, attachment: Attachment) -> str:
        """Extract text from a PDF attachment.

        Uses pdfplumber if installed, falls back to basic regex extraction.
        """
        # Try pdfplumber first
        try:
            import pdfplumber
            import io

            with pdfplumber.open(io.BytesIO(attachment.data)) as pdf:
                pages = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n\n".join(pages) if pages else "[PDF: no extractable text]"
        except ImportError:
            logger.debug("pdfplumber not installed, using basic PDF extraction")
        except Exception:
            logger.debug("pdfplumber extraction failed", exc_info=True)

        # Basic fallback: extract text between parentheses in PDF streams
        # This is crude but handles simple PDFs without dependencies
        try:
            text = attachment.data.decode("latin-1")
            # Find text in PDF text objects: (text) Tj or (text) TJ
            matches = re.findall(r"\(([^)]+)\)\s*T[jJ]", text)
            if matches:
                return " ".join(matches)
        except Exception:
            pass

        return f"[PDF: {attachment.name} ({attachment.size:,} bytes, install pdfplumber for text extraction)]"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_attachments.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/kernel/attachments.py duh/kernel/messages.py tests/unit/test_attachments.py
git commit -m "feat(attachments): add Attachment system with image/PDF/text support and ImageBlock"
```

---

### Task 6: Remote Bridge

**Files:**
- Create: `duh/bridge/__init__.py`
- Create: `duh/bridge/protocol.py`
- Create: `duh/bridge/session_relay.py`
- Create: `duh/bridge/server.py`
- Modify: `duh/cli/parser.py`
- Modify: `duh/cli/main.py`
- Create: `tests/unit/test_bridge_protocol.py`
- Create: `tests/unit/test_bridge_server.py`

- [ ] **Step 1: Write the failing tests for the protocol**

```python
# tests/unit/test_bridge_protocol.py
"""Tests for duh.bridge.protocol — bridge message protocol."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from duh.bridge.protocol import (
    BridgeMessage,
    ConnectMessage,
    DisconnectMessage,
    EventMessage,
    PromptMessage,
    ErrorMessage,
    encode_message,
    decode_message,
    validate_token,
)


# ---------------------------------------------------------------------------
# Tests: Message creation
# ---------------------------------------------------------------------------

class TestBridgeMessages:
    def test_connect_message(self):
        msg = ConnectMessage(token="tok123", session_id="sess-1")
        assert msg.type == "connect"
        assert msg.token == "tok123"
        assert msg.session_id == "sess-1"
        assert msg.timestamp > 0

    def test_disconnect_message(self):
        msg = DisconnectMessage(session_id="sess-1")
        assert msg.type == "disconnect"
        assert msg.session_id == "sess-1"

    def test_prompt_message(self):
        msg = PromptMessage(session_id="sess-1", content="Fix the bug")
        assert msg.type == "prompt"
        assert msg.content == "Fix the bug"

    def test_event_message(self):
        msg = EventMessage(
            session_id="sess-1",
            event_type="assistant",
            data={"text": "I'll fix that bug."},
        )
        assert msg.type == "event"
        assert msg.event_type == "assistant"
        assert msg.data["text"] == "I'll fix that bug."

    def test_error_message(self):
        msg = ErrorMessage(
            session_id="sess-1",
            error="Connection refused",
            code=503,
        )
        assert msg.type == "error"
        assert msg.error == "Connection refused"
        assert msg.code == 503


# ---------------------------------------------------------------------------
# Tests: Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_encode_connect(self):
        msg = ConnectMessage(token="abc", session_id="s1")
        raw = encode_message(msg)
        parsed = json.loads(raw)
        assert parsed["type"] == "connect"
        assert parsed["token"] == "abc"
        assert parsed["session_id"] == "s1"
        assert "timestamp" in parsed

    def test_encode_event(self):
        msg = EventMessage(
            session_id="s1",
            event_type="text_delta",
            data={"delta": "hello"},
        )
        raw = encode_message(msg)
        parsed = json.loads(raw)
        assert parsed["type"] == "event"
        assert parsed["event_type"] == "text_delta"
        assert parsed["data"]["delta"] == "hello"

    def test_decode_connect(self):
        raw = json.dumps({
            "type": "connect",
            "token": "tok",
            "session_id": "s1",
            "timestamp": time.time(),
        })
        msg = decode_message(raw)
        assert isinstance(msg, ConnectMessage)
        assert msg.token == "tok"

    def test_decode_prompt(self):
        raw = json.dumps({
            "type": "prompt",
            "session_id": "s1",
            "content": "hello",
            "timestamp": time.time(),
        })
        msg = decode_message(raw)
        assert isinstance(msg, PromptMessage)
        assert msg.content == "hello"

    def test_decode_disconnect(self):
        raw = json.dumps({
            "type": "disconnect",
            "session_id": "s1",
            "timestamp": time.time(),
        })
        msg = decode_message(raw)
        assert isinstance(msg, DisconnectMessage)

    def test_decode_unknown_type_raises(self):
        raw = json.dumps({"type": "unknown", "timestamp": time.time()})
        with pytest.raises(ValueError, match="Unknown.*type"):
            decode_message(raw)

    def test_decode_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid.*JSON"):
            decode_message("not json at all {{{")

    def test_roundtrip_prompt(self):
        original = PromptMessage(session_id="s1", content="test prompt")
        raw = encode_message(original)
        decoded = decode_message(raw)
        assert isinstance(decoded, PromptMessage)
        assert decoded.session_id == original.session_id
        assert decoded.content == original.content


# ---------------------------------------------------------------------------
# Tests: Token validation
# ---------------------------------------------------------------------------

class TestTokenValidation:
    def test_valid_token(self):
        assert validate_token("secret123", "secret123") is True

    def test_invalid_token(self):
        assert validate_token("wrong", "secret123") is False

    def test_empty_expected_allows_all(self):
        """When no token is configured, any token is accepted (open mode)."""
        assert validate_token("anything", "") is True
        assert validate_token("", "") is True

    def test_none_token_rejected_when_required(self):
        assert validate_token("", "required-token") is False
```

- [ ] **Step 2: Write the failing tests for the server**

```python
# tests/unit/test_bridge_server.py
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_bridge_protocol.py tests/unit/test_bridge_server.py -v`
Expected: FAIL -- `duh.bridge` package does not exist

- [ ] **Step 4: Implement the bridge package**

**4a. Package init:**

```python
# duh/bridge/__init__.py
"""Remote bridge -- exposes Engine sessions over WebSocket.

The bridge allows external UIs, IDEs, and tools to connect to a
running D.U.H. instance and interact with it in real-time.

    from duh.bridge.server import BridgeServer

    server = BridgeServer(host="localhost", port=8765, token="secret")
    await server.start()
"""
```

**4b. Protocol module:**

```python
# duh/bridge/protocol.py
"""Bridge message protocol -- JSON messages over WebSocket.

Message types:
    connect     -- Client authenticates and joins a session
    disconnect  -- Client leaves a session
    prompt      -- Client sends a user message
    event       -- Server forwards an engine event
    error       -- Server reports an error

All messages have: type, session_id, timestamp.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Base message
# ---------------------------------------------------------------------------

@dataclass
class BridgeMessage:
    """Base class for all bridge messages."""
    type: str
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Client -> Server messages
# ---------------------------------------------------------------------------

@dataclass
class ConnectMessage(BridgeMessage):
    """Client requests to connect to a session."""
    type: str = "connect"
    token: str = ""


@dataclass
class DisconnectMessage(BridgeMessage):
    """Client disconnects from a session."""
    type: str = "disconnect"


@dataclass
class PromptMessage(BridgeMessage):
    """Client sends a user prompt."""
    type: str = "prompt"
    content: str = ""


# ---------------------------------------------------------------------------
# Server -> Client messages
# ---------------------------------------------------------------------------

@dataclass
class EventMessage(BridgeMessage):
    """Server forwards an engine event to the client."""
    type: str = "event"
    event_type: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorMessage(BridgeMessage):
    """Server reports an error."""
    type: str = "error"
    error: str = ""
    code: int = 0


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def encode_message(msg: BridgeMessage) -> str:
    """Encode a BridgeMessage to a JSON string."""
    d: dict[str, Any] = {}
    for k, v in msg.__dict__.items():
        if not k.startswith("_"):
            d[k] = v
    return json.dumps(d)


_MESSAGE_TYPES: dict[str, type[BridgeMessage]] = {
    "connect": ConnectMessage,
    "disconnect": DisconnectMessage,
    "prompt": PromptMessage,
    "event": EventMessage,
    "error": ErrorMessage,
}


def decode_message(raw: str) -> BridgeMessage:
    """Decode a JSON string into a BridgeMessage.

    Raises ValueError for invalid JSON or unknown message types.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid bridge JSON: {exc}") from exc

    msg_type = data.get("type", "")
    cls = _MESSAGE_TYPES.get(msg_type)
    if cls is None:
        raise ValueError(f"Unknown bridge message type: '{msg_type}'")

    # Build kwargs from data, filtering to only fields the dataclass accepts
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in field_names}
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def validate_token(provided: str, expected: str) -> bool:
    """Validate a bearer token.

    If ``expected`` is empty, any token is accepted (open mode).
    Otherwise, the provided token must match exactly.
    """
    if not expected:
        return True
    return provided == expected
```

**4c. Session relay:**

```python
# duh/bridge/session_relay.py
"""Session relay -- maps WebSocket connections to Engine sessions.

Each connected client is associated with an Engine session by session_id.
Engine events are forwarded to the client's WebSocket connection.
"""

from __future__ import annotations

import logging
from typing import Any

from duh.bridge.protocol import BridgeMessage, encode_message

logger = logging.getLogger(__name__)


class SessionRelay:
    """Routes engine events to WebSocket clients by session ID."""

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}  # session_id -> websocket

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def has_session(self, session_id: str) -> bool:
        return session_id in self._sessions

    def register(self, session_id: str, websocket: Any) -> None:
        """Register a WebSocket connection for a session."""
        self._sessions[session_id] = websocket
        logger.info("Bridge session registered: %s", session_id)

    def unregister(self, session_id: str) -> None:
        """Unregister a session. No-op if not registered."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info("Bridge session unregistered: %s", session_id)

    def get_websocket(self, session_id: str) -> Any | None:
        """Get the WebSocket for a session, or None."""
        return self._sessions.get(session_id)

    async def send_event(self, session_id: str, message: BridgeMessage) -> None:
        """Send a message to a session's WebSocket client.

        No-op if the session is not registered.
        """
        ws = self._sessions.get(session_id)
        if ws is None:
            return
        try:
            await ws.send(encode_message(message))
        except Exception:
            logger.debug(
                "Failed to send event to session %s", session_id, exc_info=True
            )
```

**4d. Bridge server:**

```python
# duh/bridge/server.py
"""WebSocket bridge server -- relays Engine events to remote clients.

    server = BridgeServer(host="localhost", port=8765, token="secret")
    await server.start()

Clients connect via WebSocket:
1. Send a ConnectMessage with token and optional session_id
2. Send PromptMessages to interact with the engine
3. Receive EventMessages as the engine streams responses
4. Send DisconnectMessage or close the WebSocket to end
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from duh.bridge.protocol import (
    BridgeMessage,
    ConnectMessage,
    DisconnectMessage,
    ErrorMessage,
    EventMessage,
    PromptMessage,
    decode_message,
    encode_message,
    validate_token,
)
from duh.bridge.session_relay import SessionRelay

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import -- websockets is optional
# ---------------------------------------------------------------------------

try:
    import websockets
    _ws_available = True
except ImportError:
    websockets = None  # type: ignore[assignment]
    _ws_available = False


def _require_websockets() -> None:
    if not _ws_available:
        raise RuntimeError(
            "The 'websockets' package is required for the bridge server. "
            "Install it with: pip install websockets"
        )


class BridgeServer:
    """WebSocket server that bridges remote clients to Engine sessions.

    Authentication is simple bearer-token based (from config).
    No OAuth -- just a shared secret.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8765,
        token: str = "",
        engine_factory: Any = None,
    ) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._engine_factory = engine_factory  # Callable that creates Engine instances
        self._relay = SessionRelay()
        self._server: Any = None  # websockets server
        self._engines: dict[str, Any] = {}  # session_id -> Engine

    @property
    def relay(self) -> SessionRelay:
        return self._relay

    async def start(self) -> None:
        """Start the WebSocket server."""
        _require_websockets()
        logger.info("Bridge server starting on %s:%d", self._host, self._port)
        self._server = await websockets.serve(
            self._handle_connection,
            self._host,
            self._port,
        )
        logger.info("Bridge server listening on ws://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Stop the server and disconnect all clients."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("Bridge server stopped")

    async def _handle_connection(self, websocket: Any) -> None:
        """Handle a single WebSocket client connection."""
        session_id: str | None = None
        try:
            async for raw in websocket:
                try:
                    msg = decode_message(raw)
                except ValueError as exc:
                    error = ErrorMessage(
                        error=str(exc),
                        code=400,
                    )
                    await websocket.send(encode_message(error))
                    continue

                if isinstance(msg, ConnectMessage):
                    # Authenticate
                    if not validate_token(msg.token, self._token):
                        error = ErrorMessage(
                            session_id=msg.session_id,
                            error="Authentication failed",
                            code=401,
                        )
                        await websocket.send(encode_message(error))
                        await websocket.close()
                        return

                    # Register the session
                    session_id = msg.session_id or str(uuid.uuid4())
                    self._relay.register(session_id, websocket)

                    # Acknowledge connection
                    ack = EventMessage(
                        session_id=session_id,
                        event_type="connected",
                        data={"session_id": session_id},
                    )
                    await websocket.send(encode_message(ack))

                elif isinstance(msg, PromptMessage):
                    if session_id is None:
                        error = ErrorMessage(
                            error="Not connected. Send a connect message first.",
                            code=403,
                        )
                        await websocket.send(encode_message(error))
                        continue

                    # Forward prompt to engine
                    await self._handle_prompt(session_id, msg, websocket)

                elif isinstance(msg, DisconnectMessage):
                    if session_id:
                        self._relay.unregister(session_id)
                    break

        except Exception:
            logger.debug("Bridge connection error", exc_info=True)
        finally:
            if session_id:
                self._relay.unregister(session_id)

    async def _handle_prompt(
        self,
        session_id: str,
        msg: PromptMessage,
        websocket: Any,
    ) -> None:
        """Forward a prompt to the Engine and relay events back."""
        engine = self._engines.get(session_id)

        if engine is None and self._engine_factory is not None:
            engine = await self._engine_factory(session_id)
            self._engines[session_id] = engine

        if engine is None:
            error = ErrorMessage(
                session_id=session_id,
                error="No engine available for this session",
                code=500,
            )
            await websocket.send(encode_message(error))
            return

        try:
            async for event in engine.run(msg.content):
                event_msg = EventMessage(
                    session_id=session_id,
                    event_type=event.get("type", "unknown"),
                    data=event,
                )
                await self._relay.send_event(session_id, event_msg)
        except Exception as exc:
            error = ErrorMessage(
                session_id=session_id,
                error=f"Engine error: {exc}",
                code=500,
            )
            await websocket.send(encode_message(error))
```

- [ ] **Step 5: Wire the `bridge start` CLI command**

**5a.** In `duh/cli/parser.py`, add the bridge subcommand. After the `doctor` subparser (line 90), add:

```python
    bridge_parser = subparsers.add_parser("bridge", help="Start the remote bridge server.")
    bridge_sub = bridge_parser.add_subparsers(dest="bridge_command", required=True)
    start_parser = bridge_sub.add_parser("start", help="Start the WebSocket bridge server.")
    start_parser.add_argument("--host", type=str, default="localhost",
                              help="Host to bind to (default: localhost).")
    start_parser.add_argument("--port", type=int, default=8765,
                              help="Port to bind to (default: 8765).")
    start_parser.add_argument("--token", type=str, default="",
                              help="Bearer token for authentication (empty = open).")
```

**5b.** In `duh/cli/main.py`, add the bridge handler. After the `if args.command == "doctor":` block (line 39), add:

```python
    if args.command == "bridge":
        from duh.bridge.server import BridgeServer

        async def _run_bridge() -> int:
            server = BridgeServer(
                host=args.host,
                port=args.port,
                token=args.token,
            )
            await server.start()
            print(f"Bridge server running on ws://{args.host}:{args.port}")
            print("Press Ctrl+C to stop.")
            try:
                await asyncio.Future()  # run forever
            except asyncio.CancelledError:
                pass
            finally:
                await server.stop()
            return 0

        try:
            return asyncio.run(_run_bridge())
        except KeyboardInterrupt:
            sys.stderr.write("\nBridge server stopped.\n")
            return 0
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/test_bridge_protocol.py tests/unit/test_bridge_server.py -v`
Expected: All PASS

- [ ] **Step 7: Run full test suite to verify nothing is broken**

Run: `cd /Users/nomind/Code/duh && python -m pytest tests/unit/ -v --tb=short 2>&1 | tail -30`
Expected: All existing tests still pass

- [ ] **Step 8: Commit**

```bash
cd /Users/nomind/Code/duh
git add duh/bridge/__init__.py duh/bridge/protocol.py duh/bridge/session_relay.py duh/bridge/server.py
git add duh/cli/parser.py duh/cli/main.py
git add tests/unit/test_bridge_protocol.py tests/unit/test_bridge_server.py
git commit -m "feat(bridge): add WebSocket remote bridge with session relay and token auth"
```

---

## Final: Update pyproject.toml

- [ ] **Step 1: Add optional dependency groups**

Add the following to the `[project.optional-dependencies]` section in `pyproject.toml`:

```toml
bridge = ["websockets>=12.0"]
attachments = ["pdfplumber>=0.10"]
all = ["openai>=1.0", "rich>=13.0", "websockets>=12.0", "pdfplumber>=0.10"]
dev = ["pytest", "pytest-asyncio", "pytest-cov", "openai>=1.0", "rich>=13.0", "websockets>=12.0", "pdfplumber>=0.10"]
```

Note: `httpx` is already a core dependency. `websockets` is needed for WS transport and the bridge. `pdfplumber` is only needed if users want PDF text extraction.

- [ ] **Step 2: Commit**

```bash
cd /Users/nomind/Code/duh
git add pyproject.toml
git commit -m "chore(deps): add bridge and attachments optional dependency groups"
```

---

## Verification Checklist

After all tasks are complete, run:

```bash
cd /Users/nomind/Code/duh

# All new tests
python -m pytest tests/unit/test_mcp_sse.py tests/unit/test_mcp_http.py tests/unit/test_mcp_ws.py tests/unit/test_mcp_transport_detection.py tests/unit/test_attachments.py tests/unit/test_bridge_protocol.py tests/unit/test_bridge_server.py -v

# Full test suite (no regressions)
python -m pytest tests/unit/ -v --tb=short

# Import smoke test
python -c "from duh.adapters.mcp_transports import SSETransport, HTTPTransport, WebSocketTransport; print('transports OK')"
python -c "from duh.kernel.attachments import Attachment, AttachmentManager; print('attachments OK')"
python -c "from duh.bridge.server import BridgeServer; print('bridge OK')"
python -c "from duh.kernel.messages import ImageBlock; print('ImageBlock OK')"

# CLI smoke test
python -m duh bridge start --help
```

All commands should pass with zero errors.
