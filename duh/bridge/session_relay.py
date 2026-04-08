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
