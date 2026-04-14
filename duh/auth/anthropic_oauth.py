"""Anthropic OAuth (PKCE) auth for Claude models via platform.claude.com."""

from __future__ import annotations

import base64
import hashlib
import os
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://platform.claude.com/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
REDIRECT_PORT = 1456
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPES = "user:inference user:profile"

# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def _b64url_no_pad(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE (verifier, challenge) pair using S256."""
    verifier = _b64url_no_pad(secrets.token_bytes(32))
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("utf-8")).digest())
    return verifier, challenge


# ---------------------------------------------------------------------------
# Token exchange / refresh
# ---------------------------------------------------------------------------


def _exchange_code_for_tokens(
    code: str, verifier: str, state: str
) -> dict[str, Any] | None:
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
        "state": state,
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
    if not access or not isinstance(expires_in, int):
        return None

    account = body.get("account", {})
    organization = body.get("organization", {})

    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at_ms": int(time.time() * 1000) + expires_in * 1000,
        "account_uuid": account.get("uuid", "") if isinstance(account, dict) else "",
        "email": account.get("email_address", "") if isinstance(account, dict) else "",
        "organization_uuid": (
            organization.get("uuid", "") if isinstance(organization, dict) else ""
        ),
    }


def _refresh_tokens(refresh_token: str) -> dict[str, Any] | None:
    data = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
        "scope": SCOPES,
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
    if not access or not isinstance(expires_in, int):
        return None

    account = body.get("account", {})
    organization = body.get("organization", {})

    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at_ms": int(time.time() * 1000) + expires_in * 1000,
        "account_uuid": account.get("uuid", "") if isinstance(account, dict) else "",
        "email": account.get("email_address", "") if isinstance(account, dict) else "",
        "organization_uuid": (
            organization.get("uuid", "") if isinstance(organization, dict) else ""
        ),
    }


# ---------------------------------------------------------------------------
# Local callback server
# ---------------------------------------------------------------------------


@dataclass
class _OAuthWaitState:
    expected_state: str
    code: str = ""
    ready: threading.Event | None = None


class _OAuthHandler(BaseHTTPRequestHandler):
    wait_state: _OAuthWaitState

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
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


# ---------------------------------------------------------------------------
# Authorize URL builder
# ---------------------------------------------------------------------------


def _build_authorize_url(state: str, challenge: str) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_oauth_flow(
    *,
    input_fn: Any = input,
    output_fn: Any = print,
) -> tuple[bool, str]:
    """Run interactive Anthropic OAuth PKCE flow and persist tokens."""
    # Check env override first
    env_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if env_token:
        provider = load_provider_auth("anthropic")
        if not isinstance(provider, dict):
            provider = {}
        provider["oauth"] = {
            "access_token": env_token,
            "refresh_token": "",
            "expires_at_ms": 0,  # never expires (env-supplied)
            "account_uuid": "",
            "email": "",
            "organization_uuid": "",
        }
        save_provider_auth("anthropic", provider)
        return True, "Anthropic OAuth token set from ANTHROPIC_AUTH_TOKEN."

    verifier, challenge = _pkce_pair()
    state = _b64url_no_pad(secrets.token_bytes(32))
    wait_state = _OAuthWaitState(expected_state=state, ready=threading.Event())
    _OAuthHandler.wait_state = wait_state

    server: HTTPServer | None = None
    thread: threading.Thread | None = None
    server_ready = False
    try:
        server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _OAuthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        server_ready = True
    except Exception:
        server = None
        thread = None

    url = _build_authorize_url(state, challenge)
    output_fn("  Opening browser for Anthropic login...")
    output_fn(f"  If it doesn't open, use this URL:\n  {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    code = ""
    if server_ready and wait_state.ready:
        wait_state.ready.wait(timeout=240)
        code = wait_state.code

    if server:
        server.shutdown()

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

    tokens = _exchange_code_for_tokens(code, verifier, state)
    if not tokens:
        return False, "OAuth token exchange failed."

    provider = load_provider_auth("anthropic")
    if not isinstance(provider, dict):
        provider = {}
    provider["oauth"] = tokens
    save_provider_auth("anthropic", provider)
    email = tokens.get("email", "")
    suffix = f" ({email})" if email else ""
    return True, f"Anthropic OAuth connected.{suffix}"


def refresh_oauth_token(refresh_token: str) -> dict[str, Any] | None:
    """Refresh an Anthropic OAuth token. Returns new token dict or None."""
    return _refresh_tokens(refresh_token)


def _load_oauth() -> dict[str, Any] | None:
    provider = load_provider_auth("anthropic")
    oauth = provider.get("oauth")
    return oauth if isinstance(oauth, dict) else None


def has_anthropic_oauth() -> bool:
    """Check if valid Anthropic OAuth tokens exist in the auth store."""
    oauth = _load_oauth()
    return bool(oauth and oauth.get("access_token"))


def get_valid_anthropic_oauth() -> dict[str, Any] | None:
    """Get a valid Anthropic OAuth token, refreshing if expired.

    Returns the token dict with at least ``access_token``, or *None*.
    """
    oauth = _load_oauth()
    if not oauth:
        return None

    access = oauth.get("access_token", "")
    if not access:
        return None

    # Env-supplied tokens (expires_at_ms == 0) never expire
    expires_at = int(oauth.get("expires_at_ms", 0))
    if expires_at == 0:
        return oauth

    now_ms = int(time.time() * 1000)
    if expires_at > now_ms + 60_000:
        return oauth

    # Try refresh
    refresh_token = oauth.get("refresh_token", "")
    if not isinstance(refresh_token, str) or not refresh_token:
        return None
    refreshed = _refresh_tokens(refresh_token)
    if not refreshed:
        return None
    provider = load_provider_auth("anthropic")
    provider["oauth"] = refreshed
    save_provider_auth("anthropic", provider)
    return refreshed
