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
