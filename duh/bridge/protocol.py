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

import dataclasses
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
