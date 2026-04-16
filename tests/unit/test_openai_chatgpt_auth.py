"""Tests for duh.auth.openai_chatgpt - OpenAI ChatGPT OAuth flow with PKCE."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.parse
from typing import Any

import pytest

from duh.auth import openai_chatgpt as mod
from duh.auth.openai_chatgpt import (
    _OAuthWaitState,
    _b64url_no_pad,
    _build_authorize_url,
    _decode_jwt_noverify,
    _exchange_code_for_tokens,
    _extract_account_id,
    _load_oauth,
    _make_oauth_handler,
    _pkce_pair,
    _refresh_tokens,
    connect_openai_api_key,
    connect_openai_chatgpt_subscription,
    get_saved_openai_api_key,
    get_valid_openai_chatgpt_oauth,
    has_openai_chatgpt_oauth,
)


# ---------------------------------------------------------------------------
# Fixtures: in-memory auth store
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_store(monkeypatch):
    """Patch load_provider_auth / save_provider_auth to an in-memory dict."""
    store: dict[str, dict[str, Any]] = {}

    def fake_load(provider: str) -> dict[str, Any]:
        return dict(store.get(provider, {}))

    def fake_save(provider: str, auth: dict[str, Any]) -> None:
        store[provider] = dict(auth)

    monkeypatch.setattr(mod, "load_provider_auth", fake_load)
    monkeypatch.setattr(mod, "save_provider_auth", fake_save)
    return store


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode().rstrip("=")
    return f"{header}.{body}.sig"


# ---------------------------------------------------------------------------
# _b64url_no_pad / _pkce_pair
# ---------------------------------------------------------------------------


def test_b64url_no_pad_roundtrip():
    raw = b"hello world!!"
    encoded = _b64url_no_pad(raw)
    assert "=" not in encoded
    # Re-pad and decode.
    padded = encoded + "=" * (-len(encoded) % 4)
    assert base64.urlsafe_b64decode(padded) == raw


def test_b64url_no_pad_empty():
    assert _b64url_no_pad(b"") == ""


def test_pkce_pair_lengths_and_rederivable():
    verifier, challenge = _pkce_pair()
    # 48 bytes base64url-encoded without padding = 64 chars.
    assert len(verifier) == 64
    # SHA-256 (32 bytes) base64url without padding = 43 chars.
    assert len(challenge) == 43
    # Re-derive challenge from verifier.
    expected = _b64url_no_pad(hashlib.sha256(verifier.encode("utf-8")).digest())
    assert challenge == expected


# ---------------------------------------------------------------------------
# _decode_jwt_noverify
# ---------------------------------------------------------------------------


def test_decode_jwt_valid():
    token = _fake_jwt({"foo": "bar"})
    assert _decode_jwt_noverify(token) == {"foo": "bar"}


def test_decode_jwt_one_part():
    assert _decode_jwt_noverify("onlyonepart") is None


def test_decode_jwt_two_parts():
    assert _decode_jwt_noverify("a.b") is None


def test_decode_jwt_invalid_base64():
    # Three parts but middle is not valid base64/json.
    assert _decode_jwt_noverify("a.!!not-base64!!.c") is None


# ---------------------------------------------------------------------------
# _extract_account_id
# ---------------------------------------------------------------------------


def test_extract_account_id_with_claim():
    token = _fake_jwt(
        {"https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"}}
    )
    assert _extract_account_id(token) == "acct_123"


def test_extract_account_id_no_claim():
    token = _fake_jwt({"other": "value"})
    assert _extract_account_id(token) == ""


def test_extract_account_id_non_dict_claim():
    token = _fake_jwt({"https://api.openai.com/auth": "not-a-dict"})
    assert _extract_account_id(token) == ""


def test_extract_account_id_non_string_inner_value():
    token = _fake_jwt(
        {"https://api.openai.com/auth": {"chatgpt_account_id": 12345}}
    )
    assert _extract_account_id(token) == ""


def test_extract_account_id_decode_failure():
    # _decode_jwt_noverify returns None -> payload = {} -> "".
    assert _extract_account_id("not.a.jwt") == ""


# ---------------------------------------------------------------------------
# Fake httpx.Client
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    """Context-manager stub for httpx.Client."""

    def __init__(
        self,
        *,
        response: _FakeResponse | None = None,
        exc: Exception | None = None,
    ):
        self._response = response
        self._exc = exc
        self.posted: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def post(self, url: str, data: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        self.posted.append((url, data))
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


def _install_fake_client(monkeypatch, *, response=None, exc=None):
    holder = {}

    def factory(*args: Any, **kwargs: Any) -> _FakeClient:
        client = _FakeClient(response=response, exc=exc)
        holder["client"] = client
        return client

    monkeypatch.setattr(mod.httpx, "Client", factory)
    return holder


# ---------------------------------------------------------------------------
# _exchange_code_for_tokens
# ---------------------------------------------------------------------------


def _good_token_body(access_token: str = "") -> dict[str, Any]:
    tok = access_token or _fake_jwt(
        {"https://api.openai.com/auth": {"chatgpt_account_id": "acct_xyz"}}
    )
    return {
        "access_token": tok,
        "refresh_token": "refresh-abc",
        "expires_in": 3600,
    }


def test_exchange_happy_path(monkeypatch):
    body = _good_token_body()
    _install_fake_client(monkeypatch, response=_FakeResponse(200, body))
    result = _exchange_code_for_tokens("the-code", "the-verifier")
    assert result is not None
    assert result["access_token"] == body["access_token"]
    assert result["refresh_token"] == "refresh-abc"
    assert result["account_id"] == "acct_xyz"
    assert result["expires_at_ms"] > 0


def test_exchange_4xx(monkeypatch):
    _install_fake_client(monkeypatch, response=_FakeResponse(400, {"error": "bad"}))
    assert _exchange_code_for_tokens("c", "v") is None


def test_exchange_network_exception(monkeypatch):
    _install_fake_client(monkeypatch, exc=RuntimeError("boom"))
    assert _exchange_code_for_tokens("c", "v") is None


def test_exchange_missing_access_token(monkeypatch):
    body = _good_token_body()
    body["access_token"] = ""
    _install_fake_client(monkeypatch, response=_FakeResponse(200, body))
    assert _exchange_code_for_tokens("c", "v") is None


def test_exchange_missing_refresh_token(monkeypatch):
    body = _good_token_body()
    body["refresh_token"] = ""
    _install_fake_client(monkeypatch, response=_FakeResponse(200, body))
    assert _exchange_code_for_tokens("c", "v") is None


def test_exchange_missing_expires_in(monkeypatch):
    body = _good_token_body()
    body["expires_in"] = "not-an-int"
    _install_fake_client(monkeypatch, response=_FakeResponse(200, body))
    assert _exchange_code_for_tokens("c", "v") is None


# ---------------------------------------------------------------------------
# _refresh_tokens (same matrix)
# ---------------------------------------------------------------------------


def test_refresh_happy_path(monkeypatch):
    body = _good_token_body()
    _install_fake_client(monkeypatch, response=_FakeResponse(200, body))
    result = _refresh_tokens("rt")
    assert result is not None
    assert result["refresh_token"] == "refresh-abc"
    assert result["account_id"] == "acct_xyz"


def test_refresh_4xx(monkeypatch):
    _install_fake_client(monkeypatch, response=_FakeResponse(401, {}))
    assert _refresh_tokens("rt") is None


def test_refresh_network_exception(monkeypatch):
    _install_fake_client(monkeypatch, exc=RuntimeError("network down"))
    assert _refresh_tokens("rt") is None


def test_refresh_missing_access_token(monkeypatch):
    body = _good_token_body()
    body["access_token"] = ""
    _install_fake_client(monkeypatch, response=_FakeResponse(200, body))
    assert _refresh_tokens("rt") is None


def test_refresh_missing_refresh_token(monkeypatch):
    body = _good_token_body()
    body["refresh_token"] = ""
    _install_fake_client(monkeypatch, response=_FakeResponse(200, body))
    assert _refresh_tokens("rt") is None


def test_refresh_missing_expires_in(monkeypatch):
    body = _good_token_body()
    body["expires_in"] = None
    _install_fake_client(monkeypatch, response=_FakeResponse(200, body))
    assert _refresh_tokens("rt") is None


# ---------------------------------------------------------------------------
# _make_oauth_handler / do_GET / log_message
# ---------------------------------------------------------------------------


class _FakeWFile:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)


def _make_fake_handler(wait_state: _OAuthWaitState):
    """Build a fake handler class (no socket) bound to *wait_state*."""
    handler_cls = _make_oauth_handler(wait_state)

    class _FakeHandler(handler_cls):
        """Bypass BaseHTTPRequestHandler.__init__ (no socket)."""

        def __init__(self, path: str) -> None:
            self.path = path
            self.wfile = _FakeWFile()
            self._status: int | None = None
            self._headers: list[tuple[str, str]] = []
            self._ended = False

        def send_response(self, code: int, message: str | None = None) -> None:  # type: ignore[override]
            self._status = code

        def send_header(self, keyword: str, value: str) -> None:  # type: ignore[override]
            self._headers.append((keyword, value))

        def end_headers(self) -> None:  # type: ignore[override]
            self._ended = True

    return _FakeHandler


def _set_wait_state(expected: str = "the-state"):
    import threading

    ws = _OAuthWaitState(expected_state=expected, ready=threading.Event())
    return ws


def test_do_get_wrong_path():
    ws = _set_wait_state()
    FakeHandler = _make_fake_handler(ws)
    h = FakeHandler("/other")
    h.do_GET()
    assert h._status == 404
    assert b"Not found" in b"".join(h.wfile.chunks)


def test_do_get_missing_code():
    ws = _set_wait_state("st")
    FakeHandler = _make_fake_handler(ws)
    h = FakeHandler("/auth/callback?state=st")  # no code
    h.do_GET()
    assert h._status == 400
    assert b"Invalid OAuth callback" in b"".join(h.wfile.chunks)
    assert not ws.ready.is_set()


def test_do_get_wrong_state():
    ws = _set_wait_state("expected")
    FakeHandler = _make_fake_handler(ws)
    h = FakeHandler("/auth/callback?state=other&code=abc")
    h.do_GET()
    assert h._status == 400
    assert not ws.ready.is_set()


def test_do_get_valid_callback():
    ws = _set_wait_state("st")
    FakeHandler = _make_fake_handler(ws)
    h = FakeHandler("/auth/callback?state=st&code=mycode")
    h.do_GET()
    assert h._status == 200
    assert ws.code == "mycode"
    assert ws.ready.is_set()
    assert b"<html>" in b"".join(h.wfile.chunks)
    # Content-Type header sent.
    assert any(k == "Content-Type" for k, _ in h._headers)


def test_do_get_valid_callback_no_ready_event():
    """wait_state.ready is None -> should still send 200 without error."""
    ws = _OAuthWaitState(expected_state="st", ready=None)
    FakeHandler = _make_fake_handler(ws)
    h = FakeHandler("/auth/callback?state=st&code=mycode")
    h.do_GET()
    assert h._status == 200
    assert ws.code == "mycode"


def test_log_message_is_noop():
    ws = _set_wait_state()
    FakeHandler = _make_fake_handler(ws)
    h = FakeHandler("/auth/callback")
    # Should not raise.
    assert h.log_message("fmt %s", "arg") is None


# ---------------------------------------------------------------------------
# _build_authorize_url
# ---------------------------------------------------------------------------


def test_build_authorize_url_contains_all_params():
    redirect = "http://localhost:9999/auth/callback"
    url = _build_authorize_url("my-state", "my-challenge", redirect)
    assert url.startswith(mod.AUTHORIZE_URL + "?")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == [mod.CLIENT_ID]
    assert q["redirect_uri"] == [redirect]
    assert q["scope"] == [mod.SCOPE]
    assert q["code_challenge"] == ["my-challenge"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == ["my-state"]
    assert q["id_token_add_organizations"] == ["true"]
    assert q["codex_cli_simplified_flow"] == ["true"]
    assert q["originator"] == ["codex_cli_rs"]


# ---------------------------------------------------------------------------
# connect_openai_chatgpt_subscription
# ---------------------------------------------------------------------------


class _StubServer:
    """Replaces HTTPServer - records creation and provides no-op serve loop."""

    instances: list["_StubServer"] = []

    def __init__(self, address: tuple[str, int], handler_cls: Any):
        self.address = address
        # Simulate OS-assigned ephemeral port when port 0 is requested.
        self.server_address = (address[0], address[1] if address[1] else 54321)
        self.handler_cls = handler_cls
        self.shutdown_called = False
        self.server_closed = False
        _StubServer.instances.append(self)

    def serve_forever(self) -> None:
        return None

    def shutdown(self) -> None:
        self.shutdown_called = True

    def server_close(self) -> None:
        self.server_closed = True


class _ExplodingServer:
    def __init__(self, *args: Any, **kwargs: Any):
        raise OSError("port in use")


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    monkeypatch.setattr(mod.webbrowser, "open", lambda url: None)


def _install_stub_server(monkeypatch):
    _StubServer.instances.clear()
    monkeypatch.setattr(mod, "HTTPServer", _StubServer)


def test_connect_chatgpt_happy_path(monkeypatch, fake_store):
    _install_stub_server(monkeypatch)
    _install_fake_client(monkeypatch, response=_FakeResponse(200, _good_token_body()))

    # Capture the wait_state that _make_oauth_handler receives so that the
    # fake thread can set the code on it (SEC-HIGH-1: wait_state is no longer
    # a class attribute).
    captured_ws: list[_OAuthWaitState] = []
    _orig_make = mod._make_oauth_handler

    def _capturing_make(ws: _OAuthWaitState):
        captured_ws.append(ws)
        return _orig_make(ws)

    monkeypatch.setattr(mod, "_make_oauth_handler", _capturing_make)

    # Simulate that the browser immediately fires the callback.
    class InstantThread:
        def __init__(self, target: Any, daemon: bool = False):
            self._target = target
            self.daemon = daemon

        def start(self) -> None:
            ws = captured_ws[-1]
            ws.code = "code-from-browser"
            if ws.ready:
                ws.ready.set()

    monkeypatch.setattr(mod.threading, "Thread", InstantThread)

    ok, msg = connect_openai_chatgpt_subscription(
        input_fn=lambda prompt: "",
        output_fn=lambda *a, **k: None,
    )
    assert ok is True
    assert "connected" in msg
    assert "chatgpt_oauth" in fake_store["openai"]
    assert fake_store["openai"]["chatgpt_oauth"]["account_id"] == "acct_xyz"


def test_connect_chatgpt_cancelled_empty_paste(monkeypatch, fake_store):
    """Server fails to start, user pastes nothing -> cancelled."""
    monkeypatch.setattr(mod, "HTTPServer", _ExplodingServer)
    ok, msg = connect_openai_chatgpt_subscription(
        input_fn=lambda prompt: "",
        output_fn=lambda *a, **k: None,
    )
    assert ok is False
    assert msg == "Cancelled."


def test_connect_chatgpt_pasted_redirect_url(monkeypatch, fake_store):
    """Server start fails, user pastes a redirect URL containing ?code=..."""
    monkeypatch.setattr(mod, "HTTPServer", _ExplodingServer)
    body = _good_token_body()
    _install_fake_client(monkeypatch, response=_FakeResponse(200, body))
    pasted_url = "http://localhost:1455/auth/callback?code=PASTED&state=s"

    ok, msg = connect_openai_chatgpt_subscription(
        input_fn=lambda prompt: pasted_url,
        output_fn=lambda *a, **k: None,
    )
    assert ok is True
    assert fake_store["openai"]["chatgpt_oauth"]["refresh_token"] == "refresh-abc"


def test_connect_chatgpt_pasted_raw_code(monkeypatch, fake_store):
    """Server start fails, user pastes bare code (no query string)."""
    monkeypatch.setattr(mod, "HTTPServer", _ExplodingServer)
    _install_fake_client(monkeypatch, response=_FakeResponse(200, _good_token_body()))

    ok, _ = connect_openai_chatgpt_subscription(
        input_fn=lambda prompt: "raw-code-abc",
        output_fn=lambda *a, **k: None,
    )
    assert ok is True


def test_connect_chatgpt_token_exchange_returns_none(monkeypatch, fake_store):
    monkeypatch.setattr(mod, "HTTPServer", _ExplodingServer)
    _install_fake_client(monkeypatch, response=_FakeResponse(500, {}))

    ok, msg = connect_openai_chatgpt_subscription(
        input_fn=lambda prompt: "somecode",
        output_fn=lambda *a, **k: None,
    )
    assert ok is False
    # New format includes status & remediation hint -- still starts with the
    # canonical "OAuth token exchange failed" prefix and now surfaces the
    # HTTP status and a `duh doctor` hint per QX-5.
    assert msg.startswith("OAuth token exchange failed")
    assert "HTTP 500" in msg
    assert "duh doctor" in msg


def test_connect_chatgpt_missing_account_id(monkeypatch, fake_store):
    monkeypatch.setattr(mod, "HTTPServer", _ExplodingServer)
    # Access token that has no chatgpt_account_id claim.
    bare_token = _fake_jwt({"sub": "user"})
    body = {
        "access_token": bare_token,
        "refresh_token": "rt",
        "expires_in": 3600,
    }
    _install_fake_client(monkeypatch, response=_FakeResponse(200, body))

    ok, msg = connect_openai_chatgpt_subscription(
        input_fn=lambda prompt: "somecode",
        output_fn=lambda *a, **k: None,
    )
    assert ok is False
    assert "chatgpt_account_id" in msg


def test_connect_chatgpt_webbrowser_raises(monkeypatch, fake_store):
    """webbrowser.open raising should be swallowed."""
    monkeypatch.setattr(mod, "HTTPServer", _ExplodingServer)

    def boom(url: str) -> None:
        raise RuntimeError("no browser")

    monkeypatch.setattr(mod.webbrowser, "open", boom)
    ok, msg = connect_openai_chatgpt_subscription(
        input_fn=lambda prompt: "",
        output_fn=lambda *a, **k: None,
    )
    assert ok is False
    assert msg == "Cancelled."


def test_connect_chatgpt_urlparse_exception(monkeypatch, fake_store):
    """If urllib.parse.urlparse raises, fall through to code=pasted."""
    monkeypatch.setattr(mod, "HTTPServer", _ExplodingServer)
    _install_fake_client(monkeypatch, response=_FakeResponse(200, _good_token_body()))

    def boom_urlparse(value: str) -> Any:
        raise ValueError("bad url")

    monkeypatch.setattr(mod.urllib.parse, "urlparse", boom_urlparse)

    ok, _ = connect_openai_chatgpt_subscription(
        input_fn=lambda prompt: "raw-fallback",
        output_fn=lambda *a, **k: None,
    )
    assert ok is True


def test_connect_chatgpt_provider_not_dict(monkeypatch, fake_store):
    """load_provider_auth returns non-dict -> start fresh."""
    _install_stub_server(monkeypatch)
    _install_fake_client(monkeypatch, response=_FakeResponse(200, _good_token_body()))

    captured_ws: list[_OAuthWaitState] = []
    _orig_make = mod._make_oauth_handler

    def _capturing_make(ws: _OAuthWaitState):
        captured_ws.append(ws)
        return _orig_make(ws)

    monkeypatch.setattr(mod, "_make_oauth_handler", _capturing_make)

    class InstantThread:
        def __init__(self, target: Any, daemon: bool = False):
            self._target = target

        def start(self) -> None:
            ws = captured_ws[-1]
            ws.code = "code-x"
            if ws.ready:
                ws.ready.set()

    monkeypatch.setattr(mod.threading, "Thread", InstantThread)
    monkeypatch.setattr(mod, "load_provider_auth", lambda provider: "not a dict")

    saved: dict[str, Any] = {}

    def fake_save(provider: str, auth: dict[str, Any]) -> None:
        saved[provider] = auth

    monkeypatch.setattr(mod, "save_provider_auth", fake_save)

    ok, _ = connect_openai_chatgpt_subscription(
        input_fn=lambda prompt: "",
        output_fn=lambda *a, **k: None,
    )
    assert ok is True
    assert "chatgpt_oauth" in saved["openai"]


# ---------------------------------------------------------------------------
# connect_openai_api_key
# ---------------------------------------------------------------------------


def test_connect_api_key_empty(fake_store):
    ok, msg = connect_openai_api_key(input_fn=lambda prompt: "   ")
    assert ok is False
    assert msg == "No key entered."
    assert "openai" not in fake_store


def test_connect_api_key_valid(fake_store):
    ok, msg = connect_openai_api_key(input_fn=lambda prompt: "sk-test")
    assert ok is True
    assert fake_store["openai"]["api_key"] == "sk-test"


def test_connect_api_key_broken_provider(monkeypatch, fake_store):
    """load_provider_auth returns non-dict -> fresh dict."""
    monkeypatch.setattr(mod, "load_provider_auth", lambda provider: ["not", "a", "dict"])

    saved: dict[str, Any] = {}

    def fake_save(provider: str, auth: dict[str, Any]) -> None:
        saved[provider] = auth

    monkeypatch.setattr(mod, "save_provider_auth", fake_save)

    ok, _ = connect_openai_api_key(input_fn=lambda prompt: "sk-new")
    assert ok is True
    assert saved["openai"]["api_key"] == "sk-new"


# ---------------------------------------------------------------------------
# get_saved_openai_api_key
# ---------------------------------------------------------------------------


def test_get_saved_api_key_empty_store(fake_store):
    assert get_saved_openai_api_key() == ""


def test_get_saved_api_key_non_string(monkeypatch):
    monkeypatch.setattr(mod, "load_provider_auth", lambda provider: {"api_key": 123})
    assert get_saved_openai_api_key() == ""


def test_get_saved_api_key_valid(monkeypatch):
    monkeypatch.setattr(mod, "load_provider_auth", lambda provider: {"api_key": "sk-abc"})
    assert get_saved_openai_api_key() == "sk-abc"


# ---------------------------------------------------------------------------
# _load_oauth
# ---------------------------------------------------------------------------


def test_load_oauth_empty(fake_store):
    assert _load_oauth() is None


def test_load_oauth_valid(monkeypatch):
    monkeypatch.setattr(
        mod, "load_provider_auth", lambda provider: {"chatgpt_oauth": {"a": 1}}
    )
    assert _load_oauth() == {"a": 1}


def test_load_oauth_non_dict(monkeypatch):
    monkeypatch.setattr(
        mod,
        "load_provider_auth",
        lambda provider: {"chatgpt_oauth": "not a dict"},
    )
    assert _load_oauth() is None


# ---------------------------------------------------------------------------
# has_openai_chatgpt_oauth
# ---------------------------------------------------------------------------


def test_has_oauth_none(fake_store):
    assert has_openai_chatgpt_oauth() is False


def test_has_oauth_missing_tokens(monkeypatch):
    monkeypatch.setattr(
        mod,
        "load_provider_auth",
        lambda provider: {"chatgpt_oauth": {"access_token": "", "refresh_token": ""}},
    )
    assert has_openai_chatgpt_oauth() is False


def test_has_oauth_valid(monkeypatch):
    monkeypatch.setattr(
        mod,
        "load_provider_auth",
        lambda provider: {
            "chatgpt_oauth": {"access_token": "a", "refresh_token": "r"}
        },
    )
    assert has_openai_chatgpt_oauth() is True


# ---------------------------------------------------------------------------
# get_valid_openai_chatgpt_oauth
# ---------------------------------------------------------------------------


def test_get_valid_oauth_none(fake_store):
    assert get_valid_openai_chatgpt_oauth() is None


def test_get_valid_oauth_not_expired(monkeypatch):
    far_future = int(time.time() * 1000) + 3_600_000
    oauth = {
        "access_token": "a",
        "refresh_token": "r",
        "expires_at_ms": far_future,
        "account_id": "acct",
    }
    monkeypatch.setattr(
        mod, "load_provider_auth", lambda provider: {"chatgpt_oauth": oauth}
    )
    assert get_valid_openai_chatgpt_oauth() == oauth


def test_get_valid_oauth_expired_refreshes(monkeypatch):
    old = {
        "access_token": "old",
        "refresh_token": "old-rt",
        "expires_at_ms": 0,
    }
    saved: dict[str, Any] = {}

    def fake_load(provider: str) -> dict[str, Any]:
        return {"chatgpt_oauth": old}

    def fake_save(provider: str, auth: dict[str, Any]) -> None:
        saved[provider] = auth

    monkeypatch.setattr(mod, "load_provider_auth", fake_load)
    monkeypatch.setattr(mod, "save_provider_auth", fake_save)
    _install_fake_client(monkeypatch, response=_FakeResponse(200, _good_token_body()))

    refreshed = get_valid_openai_chatgpt_oauth()
    assert refreshed is not None
    assert refreshed["refresh_token"] == "refresh-abc"
    assert "chatgpt_oauth" in saved["openai"]


def test_get_valid_oauth_expired_missing_refresh_token(monkeypatch):
    monkeypatch.setattr(
        mod,
        "load_provider_auth",
        lambda provider: {
            "chatgpt_oauth": {"access_token": "a", "refresh_token": "", "expires_at_ms": 0}
        },
    )
    assert get_valid_openai_chatgpt_oauth() is None


def test_get_valid_oauth_expired_non_string_refresh(monkeypatch):
    monkeypatch.setattr(
        mod,
        "load_provider_auth",
        lambda provider: {
            "chatgpt_oauth": {
                "access_token": "a",
                "refresh_token": 123,
                "expires_at_ms": 0,
            }
        },
    )
    assert get_valid_openai_chatgpt_oauth() is None


def test_get_valid_oauth_refresh_returns_none(monkeypatch):
    monkeypatch.setattr(
        mod,
        "load_provider_auth",
        lambda provider: {
            "chatgpt_oauth": {
                "access_token": "a",
                "refresh_token": "rt",
                "expires_at_ms": 0,
            }
        },
    )
    _install_fake_client(monkeypatch, response=_FakeResponse(401, {}))
    assert get_valid_openai_chatgpt_oauth() is None
