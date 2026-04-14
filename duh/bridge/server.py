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

import logging
import secrets
import uuid
from typing import Any

from duh.bridge.protocol import (
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

from duh._optional_deps import (
    websockets,
    require_websockets as _require_websockets,
)


class BridgeServer:
    """WebSocket server that bridges remote clients to Engine sessions.

    Authentication is simple bearer-token based (from config).
    No OAuth -- just a shared secret.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 9120,
        token: str = "",
        engine_factory: Any = None,
    ) -> None:
        self._host = host
        self._port = port
        # ADR-042: auto-generate a random bearer token when none is supplied.
        # This is printed on start so the local user can share it with remote clients.
        if not token:
            self._token = secrets.token_urlsafe(32)
            self._token_auto_generated = True
        else:
            self._token = token
            self._token_auto_generated = False
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
        if self._token_auto_generated:
            # ADR-042: print the auto-generated token so the local user can share it
            print(f"Remote bridge: ws://{self._host}:{self._port}")
            print(f"Auth token:    {self._token}")
        logger.info("Bridge server starting on %s:%d", self._host, self._port)
        self._server = await websockets.serve(
            self._handle_connection,
            self._host,
            self._port,
            max_size=1_048_576,  # 1MB max message size (prevents DoS)
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
