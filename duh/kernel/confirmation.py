"""HMAC-bound confirmation tokens for dangerous tool calls (ADR-054, 7.2).

Only user-origin events can mint tokens. Tokens are single-use, session-bound,
tool-bound, and input-bound. They expire after 5 minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

__all__ = ["ConfirmationMinter"]


class ConfirmationMinter:
    """Mints and validates single-use confirmation tokens."""

    __slots__ = ("_key", "_issued")

    def __init__(self, session_key: bytes) -> None:
        self._key = session_key
        self._issued: set[str] = set()

    def mint(self, session_id: str, tool: str, input_obj: dict) -> str:
        input_hash = hashlib.sha256(
            json.dumps(input_obj, sort_keys=True).encode()
        ).hexdigest()
        ts = int(time.time())
        payload = f"{session_id}|{tool}|{input_hash}|{ts}"
        sig = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        return f"duh-confirm-{ts}-{sig}"

    def validate(
        self, token: str, session_id: str, tool: str, input_obj: dict
    ) -> bool:
        if token in self._issued:
            return False
        try:
            parts = token.split("-")
            if len(parts) < 4 or parts[0] != "duh" or parts[1] != "confirm":
                return False
            ts = int(parts[2])
            sig = parts[3]
        except (ValueError, IndexError):
            return False
        if time.time() - ts > 300:
            return False
        input_hash = hashlib.sha256(
            json.dumps(input_obj, sort_keys=True).encode()
        ).hexdigest()
        payload = f"{session_id}|{tool}|{input_hash}|{ts}"
        expected = hmac.new(
            self._key, payload.encode(), hashlib.sha256
        ).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return False
        self._issued.add(token)
        return True
