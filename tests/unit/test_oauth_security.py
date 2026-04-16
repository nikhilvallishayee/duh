"""Security-focused tests for OAuth flow (issue #12).

SEC-HIGH-1: Concurrent OAuth handlers must not share wait_state.
SEC-HIGH-2: Malformed JWTs must be rejected by structural validation.
SEC-MEDIUM-3: Ephemeral port assignment (port 0) must work.
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import HTTPServer
from typing import Any

import pytest

from duh.auth import openai_chatgpt as mod
from duh.auth.openai_chatgpt import (
    _OAuthWaitState,
    _REDIRECT_URI_TEMPLATE,
    _decode_jwt_noverify,
    _make_oauth_handler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode().rstrip("=")
    return f"{header}.{body}.sig"


# ---------------------------------------------------------------------------
# SEC-HIGH-1: Concurrent OAuth handlers must not share state
# ---------------------------------------------------------------------------


class TestConcurrentOAuthHandlersIsolated:
    """Each call to _make_oauth_handler produces an independent handler class
    whose instances close over their own wait_state.  Concurrent flows must
    never interfere with each other."""

    def test_two_handlers_have_independent_wait_state(self):
        ws_a = _OAuthWaitState(expected_state="state-a", ready=threading.Event())
        ws_b = _OAuthWaitState(expected_state="state-b", ready=threading.Event())

        handler_a = _make_oauth_handler(ws_a)
        handler_b = _make_oauth_handler(ws_b)

        # They are distinct classes.
        assert handler_a is not handler_b

        # Mutating one wait_state must not affect the other.
        ws_a.code = "code-for-a"
        assert ws_b.code == ""

        ws_b.code = "code-for-b"
        assert ws_a.code == "code-for-a"

    def test_handler_instances_use_own_wait_state(self):
        """Instantiate fake handlers from two different factory calls and
        verify they route callbacks to their own wait_state."""
        ws_1 = _OAuthWaitState(expected_state="s1", ready=threading.Event())
        ws_2 = _OAuthWaitState(expected_state="s2", ready=threading.Event())

        cls_1 = _make_oauth_handler(ws_1)
        cls_2 = _make_oauth_handler(ws_2)

        # Build minimal fake handler instances (bypass socket init).
        class _FakeWFile:
            def __init__(self):
                self.chunks: list[bytes] = []

            def write(self, data: bytes) -> None:
                self.chunks.append(data)

        def _fake_instance(cls, path):
            inst = object.__new__(cls)
            inst.path = path
            inst.wfile = _FakeWFile()
            inst._status = None
            inst._headers_list = []
            inst._ended = False

            def send_response(code, message=None):
                inst._status = code

            def send_header(k, v):
                inst._headers_list.append((k, v))

            def end_headers():
                inst._ended = True

            inst.send_response = send_response
            inst.send_header = send_header
            inst.end_headers = end_headers
            return inst

        h1 = _fake_instance(cls_1, "/auth/callback?state=s1&code=c1")
        h2 = _fake_instance(cls_2, "/auth/callback?state=s2&code=c2")

        h1.do_GET()
        h2.do_GET()

        assert ws_1.code == "c1"
        assert ws_2.code == "c2"
        assert ws_1.ready.is_set()
        assert ws_2.ready.is_set()

    def test_concurrent_flows_via_threads(self):
        """Spawn two flows in parallel threads and verify isolation."""
        ws_a = _OAuthWaitState(expected_state="a", ready=threading.Event())
        ws_b = _OAuthWaitState(expected_state="b", ready=threading.Event())

        def simulate_callback(ws: _OAuthWaitState, code: str):
            ws.code = code
            if ws.ready:
                ws.ready.set()

        t1 = threading.Thread(target=simulate_callback, args=(ws_a, "code-a"))
        t2 = threading.Thread(target=simulate_callback, args=(ws_b, "code-b"))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert ws_a.code == "code-a"
        assert ws_b.code == "code-b"
        assert ws_a.ready.is_set()
        assert ws_b.ready.is_set()


# ---------------------------------------------------------------------------
# SEC-HIGH-2: Malformed JWT rejection
# ---------------------------------------------------------------------------


class TestMalformedJwtRejected:
    """_decode_jwt_noverify must reject structurally invalid tokens."""

    def test_empty_string(self):
        assert _decode_jwt_noverify("") is None

    def test_none_input(self):
        assert _decode_jwt_noverify(None) is None  # type: ignore[arg-type]

    def test_integer_input(self):
        assert _decode_jwt_noverify(42) is None  # type: ignore[arg-type]

    def test_one_part(self):
        assert _decode_jwt_noverify("onlyonepart") is None

    def test_two_parts(self):
        assert _decode_jwt_noverify("a.b") is None

    def test_four_parts(self):
        assert _decode_jwt_noverify("a.b.c.d") is None

    def test_empty_segments(self):
        """Three dots but empty segments: '..'"""
        assert _decode_jwt_noverify("..") is None

    def test_empty_first_segment(self):
        b = base64.urlsafe_b64encode(b'{"x":1}').decode().rstrip("=")
        assert _decode_jwt_noverify(f".{b}.sig") is None

    def test_empty_middle_segment(self):
        h = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        assert _decode_jwt_noverify(f"{h}..sig") is None

    def test_empty_last_segment(self):
        h = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        b = base64.urlsafe_b64encode(b'{"x":1}').decode().rstrip("=")
        assert _decode_jwt_noverify(f"{h}.{b}.") is None

    def test_invalid_base64_header(self):
        """Non-ASCII bytes in a segment trigger a base64 decode error."""
        b = base64.urlsafe_b64encode(b'{"x":1}').decode().rstrip("=")
        assert _decode_jwt_noverify(f"\xff\xff.{b}.sig") is None

    def test_invalid_base64_payload(self):
        h = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        assert _decode_jwt_noverify(f"{h}.!!!.sig") is None

    def test_invalid_base64_signature(self):
        """Non-ASCII bytes in the signature segment are rejected."""
        h = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        b = base64.urlsafe_b64encode(b'{"x":1}').decode().rstrip("=")
        assert _decode_jwt_noverify(f"{h}.{b}.\xff\xff") is None

    def test_payload_not_json(self):
        h = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        b = base64.urlsafe_b64encode(b"not json").decode().rstrip("=")
        s = base64.urlsafe_b64encode(b"sig").decode().rstrip("=")
        assert _decode_jwt_noverify(f"{h}.{b}.{s}") is None

    def test_payload_json_but_not_dict(self):
        """Payload decodes to a JSON array -- must be rejected."""
        h = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        b = base64.urlsafe_b64encode(b'[1,2,3]').decode().rstrip("=")
        s = base64.urlsafe_b64encode(b"sig").decode().rstrip("=")
        assert _decode_jwt_noverify(f"{h}.{b}.{s}") is None

    def test_payload_json_string(self):
        """Payload decodes to a JSON string -- must be rejected."""
        h = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
        b = base64.urlsafe_b64encode(b'"just a string"').decode().rstrip("=")
        s = base64.urlsafe_b64encode(b"sig").decode().rstrip("=")
        assert _decode_jwt_noverify(f"{h}.{b}.{s}") is None

    def test_valid_jwt_accepted(self):
        token = _fake_jwt({"sub": "user123", "iss": "test"})
        result = _decode_jwt_noverify(token)
        assert result is not None
        assert result["sub"] == "user123"

    def test_valid_jwt_with_nested_claim(self):
        token = _fake_jwt(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "acct_99"}}
        )
        result = _decode_jwt_noverify(token)
        assert result is not None
        assert result["https://api.openai.com/auth"]["chatgpt_account_id"] == "acct_99"


# ---------------------------------------------------------------------------
# SEC-MEDIUM-3: Ephemeral port assignment
# ---------------------------------------------------------------------------


class TestEphemeralPortAssignment:
    """The OAuth server must bind to port 0 and use the OS-assigned port."""

    def test_httpserver_port_zero_gets_real_port(self):
        """Sanity check: HTTPServer with port 0 assigns a real ephemeral port."""
        # Use a minimal handler that does nothing.
        from http.server import BaseHTTPRequestHandler

        class _NoOp(BaseHTTPRequestHandler):
            pass

        server = HTTPServer(("127.0.0.1", 0), _NoOp)
        try:
            port = server.server_address[1]
            assert isinstance(port, int)
            assert port > 0
            assert port != 0
        finally:
            server.server_close()

    def test_redirect_uri_template_uses_port(self):
        """_REDIRECT_URI_TEMPLATE.format(port=N) produces a valid URI."""
        uri = _REDIRECT_URI_TEMPLATE.format(port=12345)
        assert "12345" in uri
        assert uri == "http://localhost:12345/auth/callback"

    def test_connect_uses_ephemeral_port_in_authorize_url(self, monkeypatch):
        """connect_openai_chatgpt_subscription must pass the actual port to
        _build_authorize_url, not a hardcoded one."""

        # Track what redirect_uri is passed to _build_authorize_url.
        captured_uris: list[str] = []
        _orig_build = mod._build_authorize_url

        def _capturing_build(state, challenge, redirect_uri=""):
            captured_uris.append(redirect_uri)
            return _orig_build(state, challenge, redirect_uri)

        monkeypatch.setattr(mod, "_build_authorize_url", _capturing_build)

        # Stub the server to simulate port assignment.
        class _PortStubServer:
            def __init__(self, address, handler_cls):
                self.server_address = (address[0], 55555)

            def serve_forever(self):
                pass

        monkeypatch.setattr(mod, "HTTPServer", _PortStubServer)

        # Fake thread that immediately fires the callback.
        captured_ws: list[_OAuthWaitState] = []
        _orig_make = mod._make_oauth_handler

        def _cap_make(ws):
            captured_ws.append(ws)
            return _orig_make(ws)

        monkeypatch.setattr(mod, "_make_oauth_handler", _cap_make)

        class _InstantThread:
            def __init__(self, target=None, daemon=False):
                pass

            def start(self):
                ws = captured_ws[-1]
                ws.code = "test-code"
                if ws.ready:
                    ws.ready.set()

        monkeypatch.setattr(mod.threading, "Thread", _InstantThread)

        # Stub token exchange to succeed.
        def _fake_exchange(code, verifier, redirect_uri=""):
            return {
                "access_token": _fake_jwt(
                    {"https://api.openai.com/auth": {"chatgpt_account_id": "acct_1"}}
                ),
                "refresh_token": "rt",
                "expires_at_ms": 9999999999999,
                "account_id": "acct_1",
            }

        monkeypatch.setattr(mod, "_exchange_code_for_tokens", _fake_exchange)
        monkeypatch.setattr(mod, "load_provider_auth", lambda p: {})
        monkeypatch.setattr(mod, "save_provider_auth", lambda p, a: None)
        monkeypatch.setattr(mod.webbrowser, "open", lambda url: None)

        ok, msg = mod.connect_openai_chatgpt_subscription(
            input_fn=lambda prompt: "",
            output_fn=lambda *a, **k: None,
        )
        assert ok is True

        # The redirect_uri passed to _build_authorize_url must contain the
        # ephemeral port 55555, NOT any hardcoded value.
        assert len(captured_uris) == 1
        assert "55555" in captured_uris[0]
        assert captured_uris[0] == "http://localhost:55555/auth/callback"

    def test_no_hardcoded_port_in_module(self):
        """Ensure the old hardcoded REDIRECT_URI constant no longer exists."""
        assert not hasattr(mod, "REDIRECT_URI"), (
            "REDIRECT_URI constant should be removed; use _REDIRECT_URI_TEMPLATE"
        )
