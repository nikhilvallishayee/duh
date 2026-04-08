"""Tests for duh.bridge.protocol -- bridge message protocol."""

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
