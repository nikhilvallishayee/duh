"""OpenAI ChatGPT subscription OAuth auth for Codex models."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx

from duh.auth.store import load_provider_auth, save_provider_auth

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
# Redirect URI template -- the port is filled in at runtime with an
# OS-assigned ephemeral port (SEC-MEDIUM-3).
_REDIRECT_URI_TEMPLATE = "http://localhost:{port}/auth/callback"
SCOPE = "openid profile email offline_access"
JWT_AUTH_CLAIM = "https://api.openai.com/auth"

OPENAI_CHATGPT_MODELS = [
    "gpt-5.2-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5.2",
    "gpt-5.1",
]


def _b64url_no_pad(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url_no_pad(secrets.token_bytes(48))
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


def _decode_jwt_noverify(token: str) -> dict[str, Any] | None:
    """Decode a JWT **without** signature verification.

    .. warning::
        This function does NOT verify the JWT signature.  OpenAI's auth
        endpoint does not publish a public JWKS URI, so cryptographic
        verification is not feasible here.  The token is only used to
        extract ``chatgpt_account_id`` after a successful OAuth token
        exchange over TLS -- the token itself was delivered directly by
        OpenAI's token endpoint, not supplied by an untrusted party.

        Structural validation (3 dot-separated base64url segments, valid
        JSON payload) is enforced to reject obviously malformed input.
    """
    if not isinstance(token, str) or not token:
        return None
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        # Structural validation: every segment must be valid base64url.
        for part in parts:
            if not part:
                return None
            padded = part + "=" * (-len(part) % 4)
            base64.urlsafe_b64decode(padded)
        # Decode payload (middle segment).
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        result = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(result, dict):
            return None
        return result
    except Exception:
        return None


def _extract_account_id(access_token: str) -> str:
    """Extract ``chatgpt_account_id`` from an access-token JWT.

    .. warning::
        The JWT is decoded **without** signature verification -- see
        :func:`_decode_jwt_noverify` for rationale.  The value extracted
        here should only be treated as a *hint* (e.g. for caching) and
        must not be used for authorization decisions.
    """
    payload = _decode_jwt_noverify(access_token) or {}
    claim = payload.get(JWT_AUTH_CLAIM, {})
    if isinstance(claim, dict):
        value = claim.get("chatgpt_account_id", "")
        if isinstance(value, str):
            return value
    return ""


@dataclass
class TokenExchangeResult:
    """Outcome of an OAuth token exchange.

    ``tokens`` is set on success; otherwise ``error`` carries an
    operator-friendly description and ``status`` carries the HTTP status
    code (or ``None`` if the call never reached the server).
    """

    tokens: dict[str, Any] | None = None
    status: int | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.tokens is not None


def _exchange_code_for_tokens(
    code: str, verifier: str, redirect_uri: str = ""
) -> dict[str, Any] | None:
    """Backwards-compatible wrapper -- returns just the tokens dict or ``None``."""
    return _exchange_code_for_tokens_detailed(code, verifier, redirect_uri).tokens


def _exchange_code_for_tokens_detailed(
    code: str, verifier: str, redirect_uri: str = ""
) -> TokenExchangeResult:
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri or _REDIRECT_URI_TEMPLATE.format(port=0),
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        status = resp.status_code
        if status >= 400:
            return TokenExchangeResult(status=status, error=f"HTTP {status}")
        body = resp.json()
    except Exception as exc:
        return TokenExchangeResult(status=None, error=f"network error: {exc}")

    access = body.get("access_token", "")
    refresh = body.get("refresh_token", "")
    expires_in = body.get("expires_in", 0)
    if not access or not refresh or not isinstance(expires_in, int):
        return TokenExchangeResult(status=status, error="malformed token response")
    return TokenExchangeResult(
        status=status,
        tokens={
            "access_token": access,
            "refresh_token": refresh,
            "expires_at_ms": int(time.time() * 1000) + expires_in * 1000,
            "account_id": _extract_account_id(access),
        },
    )


def _refresh_tokens(refresh_token: str) -> dict[str, Any] | None:
    data = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code >= 400:
            return None
        body = resp.json()
    except Exception:
        return None

    access = body.get("access_token", "")
    refresh = body.get("refresh_token", "")
    expires_in = body.get("expires_in", 0)
    if not access or not refresh or not isinstance(expires_in, int):
        return None
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at_ms": int(time.time() * 1000) + expires_in * 1000,
        "account_id": _extract_account_id(access),
    }


@dataclass
class _OAuthWaitState:
    expected_state: str
    code: str = ""
    ready: threading.Event | None = None


def _make_oauth_handler(wait_state: _OAuthWaitState) -> type:
    """Create a request-handler class bound to a specific *wait_state*.

    This avoids storing ``wait_state`` as a **class attribute** on the
    handler, which would be shared across concurrent OAuth flows
    (SEC-HIGH-1).  Each call produces a fresh subclass whose instances
    all close over the same ``wait_state`` instance.
    """

    class _BoundHandler(BaseHTTPRequestHandler):

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            params = urllib.parse.parse_qs(parsed.query)
            state = params.get("state", [""])[0]
            code = params.get("code", [""])[0]

            if state != wait_state.expected_state or not code:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid OAuth callback")
                return

            wait_state.code = code
            if wait_state.ready:
                wait_state.ready.set()

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h3>Authentication complete.</h3>"
                b"<p>You can return to D.U.H.</p></body></html>"
            )

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    return _BoundHandler


def _build_authorize_url(
    state: str, challenge: str, redirect_uri: str = ""
) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri or _REDIRECT_URI_TEMPLATE.format(port=0),
            "scope": SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "codex_cli_rs",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def connect_openai_chatgpt_subscription(
    *,
    input_fn: Any = input,
    output_fn: Any = print,
) -> tuple[bool, str]:
    """Run interactive OAuth flow and persist tokens in auth store."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_hex(16)
    wait_state = _OAuthWaitState(expected_state=state, ready=threading.Event())
    # SEC-HIGH-1: Each flow gets its own handler class bound to *this*
    # wait_state -- no shared class attribute.
    handler_cls = _make_oauth_handler(wait_state)

    server: HTTPServer | None = None
    thread: threading.Thread | None = None
    server_ready = False
    redirect_uri = ""
    try:
        # SEC-MEDIUM-3: Use port 0 so the OS assigns an ephemeral port.
        server = HTTPServer(("127.0.0.1", 0), handler_cls)
        assigned_port = server.server_address[1]
        redirect_uri = _REDIRECT_URI_TEMPLATE.format(port=assigned_port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server_ready = True
    except Exception:
        server = None
        thread = None

    url = _build_authorize_url(state, challenge, redirect_uri)
    output_fn("  Opening browser for ChatGPT Plus/Pro login...")
    output_fn(f"  If it doesn't open, use this URL:\n  {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    code = ""
    if server_ready and wait_state.ready:
        wait_state.ready.wait(timeout=240)
        code = wait_state.code

    if not code:
        pasted = input_fn(
            "  Paste redirect URL (or raw code), or press Enter to cancel: "
        ).strip()
        if not pasted:
            return False, "Cancelled."
        try:
            parsed = urllib.parse.urlparse(pasted)
            params = urllib.parse.parse_qs(parsed.query)
            code = params.get("code", [""])[0]
            if not code:
                code = pasted
        except Exception:
            code = pasted

    # Try the legacy entry point first so existing monkeypatches in tests
    # (which target ``_exchange_code_for_tokens``) keep working.  When it
    # returns ``None`` we re-issue the request via the detailed variant to
    # capture the HTTP status / network error for the operator (QX-5).
    tokens = _exchange_code_for_tokens(code, verifier, redirect_uri)
    if not tokens:
        result = _exchange_code_for_tokens_detailed(code, verifier, redirect_uri)
        status = f" (HTTP {result.status})" if result.status is not None else ""
        detail = f": {result.error}" if result.error else ""
        return False, (
            f"OAuth token exchange failed{status}{detail}. "
            "Run `duh doctor` to check auth, or try `/connect openai` again."
        )
    if not tokens.get("account_id"):
        return False, (
            "Authenticated but could not extract chatgpt_account_id. "
            "Run `duh doctor` to verify your ChatGPT subscription."
        )

    provider = load_provider_auth("openai")
    if not isinstance(provider, dict):
        provider = {}
    provider["chatgpt_oauth"] = tokens
    save_provider_auth("openai", provider)
    return True, "OpenAI ChatGPT subscription connected."


def connect_openai_api_key(
    *,
    input_fn: Any = input,
) -> tuple[bool, str]:
    key = input_fn("  Enter OpenAI API key: ").strip()
    if not key:
        return False, "No key entered."
    provider = load_provider_auth("openai")
    if not isinstance(provider, dict):
        provider = {}
    provider["api_key"] = key
    save_provider_auth("openai", provider)
    return True, "OpenAI API key saved."


def get_saved_openai_api_key() -> str:
    provider = load_provider_auth("openai")
    value = provider.get("api_key", "")
    return value if isinstance(value, str) else ""


def _load_oauth() -> dict[str, Any] | None:
    provider = load_provider_auth("openai")
    oauth = provider.get("chatgpt_oauth")
    return oauth if isinstance(oauth, dict) else None


def has_openai_chatgpt_oauth() -> bool:
    oauth = _load_oauth()
    return bool(oauth and oauth.get("access_token") and oauth.get("refresh_token"))


def get_valid_openai_chatgpt_oauth() -> dict[str, Any] | None:
    oauth = _load_oauth()
    if not oauth:
        return None
    now_ms = int(time.time() * 1000)
    expires_at = int(oauth.get("expires_at_ms", 0))
    if expires_at > now_ms + 60_000:
        return oauth

    refresh_token = oauth.get("refresh_token", "")
    if not isinstance(refresh_token, str) or not refresh_token:
        return None
    refreshed = _refresh_tokens(refresh_token)
    if not refreshed:
        return None
    provider = load_provider_auth("openai")
    provider["chatgpt_oauth"] = refreshed
    save_provider_auth("openai", provider)
    return refreshed
