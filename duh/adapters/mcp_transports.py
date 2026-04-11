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

from duh._optional_deps import (
    httpx,
    httpx_available as _httpx_available,
    require_httpx as _require_httpx,
    websockets,
    ws_available as _ws_available,
    require_websockets as _require_websockets,
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
