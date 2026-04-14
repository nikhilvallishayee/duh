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
# Must match OAuth client configuration exactly.
# Codex/OpenCode flows use localhost here (not 127.0.0.1 in the URI).
REDIRECT_URI = "http://localhost:1455/auth/callback"
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
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except Exception:
        return None


def _extract_account_id(access_token: str) -> str:
    payload = _decode_jwt_noverify(access_token) or {}
    claim = payload.get(JWT_AUTH_CLAIM, {})
    if isinstance(claim, dict):
        value = claim.get("chatgpt_account_id", "")
        if isinstance(value, str):
            return value
    return ""


def _exchange_code_for_tokens(code: str, verifier: str) -> dict[str, Any] | None:
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
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


class _OAuthHandler(BaseHTTPRequestHandler):
    wait_state: _OAuthWaitState

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

        if state != self.wait_state.expected_state or not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid OAuth callback")
            return

        self.wait_state.code = code
        if self.wait_state.ready:
            self.wait_state.ready.set()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h3>Authentication complete.</h3>"
            b"<p>You can return to D.U.H.</p></body></html>"
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def _build_authorize_url(state: str, challenge: str) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
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
    _OAuthHandler.wait_state = wait_state

    server: HTTPServer | None = None
    thread: threading.Thread | None = None
    server_ready = False
    try:
        server = HTTPServer(("127.0.0.1", 1455), _OAuthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server_ready = True
    except Exception:
        server = None
        thread = None

    url = _build_authorize_url(state, challenge)
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

    tokens = _exchange_code_for_tokens(code, verifier)
    if not tokens:
        return False, "OAuth token exchange failed."
    if not tokens.get("account_id"):
        return False, "Authenticated but could not extract chatgpt_account_id."

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
