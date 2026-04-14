"""Tests for duh.auth.anthropic_oauth — Anthropic PKCE OAuth flow.

Covers PKCE generation, token exchange, token refresh, has/get helpers,
and adapter OAuth wiring. Uses tmp_path + monkeypatch; nothing touches
the real ~/.config/duh.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from duh.auth import anthropic_oauth as oauth_mod
from duh.auth import store as store_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect config_dir() used by store.py to a tmp directory."""
    target = tmp_path / "duh"
    monkeypatch.setattr(store_mod, "config_dir", lambda: target)
    return target


def _make_token_response(
    *,
    access: str = "at-123",
    refresh: str = "rt-456",
    expires_in: int = 3600,
    email: str = "user@example.com",
    account_uuid: str = "acc-uuid",
    org_uuid: str = "org-uuid",
) -> dict[str, Any]:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": expires_in,
        "scope": "user:inference user:profile",
        "account": {"uuid": account_uuid, "email_address": email},
        "organization": {"uuid": org_uuid},
    }


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


# ---------------------------------------------------------------------------
# PKCE generation
# ---------------------------------------------------------------------------


class TestPKCE:
    def test_b64url_no_pad_has_no_padding(self) -> None:
        result = oauth_mod._b64url_no_pad(b"\x00" * 32)
        assert "=" not in result

    def test_pkce_pair_verifier_is_43_chars(self) -> None:
        verifier, _challenge = oauth_mod._pkce_pair()
        assert len(verifier) == 43

    def test_pkce_pair_challenge_is_sha256_of_verifier(self) -> None:
        verifier, challenge = oauth_mod._pkce_pair()
        expected_hash = hashlib.sha256(verifier.encode("utf-8")).digest()
        expected_challenge = (
            base64.urlsafe_b64encode(expected_hash).decode("ascii").rstrip("=")
        )
        assert challenge == expected_challenge

    def test_pkce_pair_is_unique(self) -> None:
        pairs = {oauth_mod._pkce_pair() for _ in range(10)}
        assert len(pairs) == 10


# ---------------------------------------------------------------------------
# Token exchange (_exchange_code_for_tokens)
# ---------------------------------------------------------------------------


class TestExchangeCode:
    def test_successful_exchange(self) -> None:
        body = _make_token_response()
        fake_resp = _FakeResponse(200, body)
        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.post.return_value = fake_resp

        with patch("duh.auth.anthropic_oauth.httpx.Client", return_value=fake_client):
            result = oauth_mod._exchange_code_for_tokens("code-1", "verifier-1", "state-1")

        assert result is not None
        assert result["access_token"] == "at-123"
        assert result["refresh_token"] == "rt-456"
        assert result["email"] == "user@example.com"
        assert result["account_uuid"] == "acc-uuid"
        assert result["organization_uuid"] == "org-uuid"
        assert result["expires_at_ms"] > 0

    def test_exchange_http_error(self) -> None:
        fake_resp = _FakeResponse(400, {})
        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.post.return_value = fake_resp

        with patch("duh.auth.anthropic_oauth.httpx.Client", return_value=fake_client):
            result = oauth_mod._exchange_code_for_tokens("code", "v", "s")

        assert result is None

    def test_exchange_network_exception(self) -> None:
        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.post.side_effect = ConnectionError("no network")

        with patch("duh.auth.anthropic_oauth.httpx.Client", return_value=fake_client):
            result = oauth_mod._exchange_code_for_tokens("code", "v", "s")

        assert result is None

    def test_exchange_missing_access_token(self) -> None:
        body = _make_token_response(access="")
        fake_resp = _FakeResponse(200, body)
        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.post.return_value = fake_resp

        with patch("duh.auth.anthropic_oauth.httpx.Client", return_value=fake_client):
            result = oauth_mod._exchange_code_for_tokens("code", "v", "s")

        assert result is None

    def test_exchange_non_int_expires_in(self) -> None:
        body = _make_token_response()
        body["expires_in"] = "not-a-number"
        fake_resp = _FakeResponse(200, body)
        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.post.return_value = fake_resp

        with patch("duh.auth.anthropic_oauth.httpx.Client", return_value=fake_client):
            result = oauth_mod._exchange_code_for_tokens("code", "v", "s")

        assert result is None


# ---------------------------------------------------------------------------
# Token refresh (_refresh_tokens)
# ---------------------------------------------------------------------------


class TestRefreshTokens:
    def test_successful_refresh(self) -> None:
        body = _make_token_response(access="at-new", refresh="rt-new")
        fake_resp = _FakeResponse(200, body)
        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.post.return_value = fake_resp

        with patch("duh.auth.anthropic_oauth.httpx.Client", return_value=fake_client):
            result = oauth_mod._refresh_tokens("rt-old")

        assert result is not None
        assert result["access_token"] == "at-new"
        assert result["refresh_token"] == "rt-new"

    def test_refresh_http_error(self) -> None:
        fake_resp = _FakeResponse(401, {})
        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.post.return_value = fake_resp

        with patch("duh.auth.anthropic_oauth.httpx.Client", return_value=fake_client):
            result = oauth_mod._refresh_tokens("rt-old")

        assert result is None

    def test_refresh_network_exception(self) -> None:
        fake_client = MagicMock()
        fake_client.__enter__ = MagicMock(return_value=fake_client)
        fake_client.__exit__ = MagicMock(return_value=False)
        fake_client.post.side_effect = ConnectionError("nope")

        with patch("duh.auth.anthropic_oauth.httpx.Client", return_value=fake_client):
            result = oauth_mod._refresh_tokens("rt-old")

        assert result is None


# ---------------------------------------------------------------------------
# has_anthropic_oauth
# ---------------------------------------------------------------------------


class TestHasAnthropicOAuth:
    def test_no_store(self, fake_config_dir: Path) -> None:
        assert oauth_mod.has_anthropic_oauth() is False

    def test_empty_oauth(self, fake_config_dir: Path) -> None:
        store_mod.save_provider_auth("anthropic", {"oauth": {}})
        assert oauth_mod.has_anthropic_oauth() is False

    def test_non_dict_oauth(self, fake_config_dir: Path) -> None:
        store_mod.save_provider_auth("anthropic", {"oauth": "bad"})
        assert oauth_mod.has_anthropic_oauth() is False

    def test_has_access_token(self, fake_config_dir: Path) -> None:
        store_mod.save_provider_auth(
            "anthropic",
            {"oauth": {"access_token": "at-xyz", "refresh_token": "rt-abc"}},
        )
        assert oauth_mod.has_anthropic_oauth() is True

    def test_missing_access_token(self, fake_config_dir: Path) -> None:
        store_mod.save_provider_auth(
            "anthropic",
            {"oauth": {"refresh_token": "rt-abc"}},
        )
        assert oauth_mod.has_anthropic_oauth() is False


# ---------------------------------------------------------------------------
# get_valid_anthropic_oauth
# ---------------------------------------------------------------------------


class TestGetValidAnthropicOAuth:
    def test_no_store(self, fake_config_dir: Path) -> None:
        assert oauth_mod.get_valid_anthropic_oauth() is None

    def test_no_access_token(self, fake_config_dir: Path) -> None:
        store_mod.save_provider_auth("anthropic", {"oauth": {"refresh_token": "rt"}})
        assert oauth_mod.get_valid_anthropic_oauth() is None

    def test_valid_non_expired(self, fake_config_dir: Path) -> None:
        future = int(time.time() * 1000) + 600_000
        oauth_data = {
            "access_token": "at-good",
            "refresh_token": "rt-good",
            "expires_at_ms": future,
        }
        store_mod.save_provider_auth("anthropic", {"oauth": oauth_data})
        result = oauth_mod.get_valid_anthropic_oauth()
        assert result is not None
        assert result["access_token"] == "at-good"

    def test_env_supplied_never_expires(self, fake_config_dir: Path) -> None:
        """expires_at_ms == 0 signals env-supplied token, never expires."""
        oauth_data = {
            "access_token": "at-env",
            "refresh_token": "",
            "expires_at_ms": 0,
        }
        store_mod.save_provider_auth("anthropic", {"oauth": oauth_data})
        result = oauth_mod.get_valid_anthropic_oauth()
        assert result is not None
        assert result["access_token"] == "at-env"

    def test_expired_triggers_refresh(self, fake_config_dir: Path) -> None:
        past = int(time.time() * 1000) - 10_000
        oauth_data = {
            "access_token": "at-old",
            "refresh_token": "rt-old",
            "expires_at_ms": past,
        }
        store_mod.save_provider_auth("anthropic", {"oauth": oauth_data})

        refreshed = {
            "access_token": "at-new",
            "refresh_token": "rt-new",
            "expires_at_ms": int(time.time() * 1000) + 600_000,
            "account_uuid": "",
            "email": "",
            "organization_uuid": "",
        }
        with patch.object(oauth_mod, "_refresh_tokens", return_value=refreshed):
            result = oauth_mod.get_valid_anthropic_oauth()

        assert result is not None
        assert result["access_token"] == "at-new"
        # Verify persisted
        on_disk = store_mod.load_provider_auth("anthropic")
        assert on_disk["oauth"]["access_token"] == "at-new"

    def test_expired_refresh_fails(self, fake_config_dir: Path) -> None:
        past = int(time.time() * 1000) - 10_000
        oauth_data = {
            "access_token": "at-old",
            "refresh_token": "rt-old",
            "expires_at_ms": past,
        }
        store_mod.save_provider_auth("anthropic", {"oauth": oauth_data})

        with patch.object(oauth_mod, "_refresh_tokens", return_value=None):
            result = oauth_mod.get_valid_anthropic_oauth()

        assert result is None

    def test_expired_no_refresh_token(self, fake_config_dir: Path) -> None:
        past = int(time.time() * 1000) - 10_000
        oauth_data = {
            "access_token": "at-old",
            "refresh_token": "",
            "expires_at_ms": past,
        }
        store_mod.save_provider_auth("anthropic", {"oauth": oauth_data})
        result = oauth_mod.get_valid_anthropic_oauth()
        assert result is None

    def test_expired_non_string_refresh_token(self, fake_config_dir: Path) -> None:
        past = int(time.time() * 1000) - 10_000
        oauth_data = {
            "access_token": "at-old",
            "refresh_token": 12345,
            "expires_at_ms": past,
        }
        store_mod.save_provider_auth("anthropic", {"oauth": oauth_data})
        result = oauth_mod.get_valid_anthropic_oauth()
        assert result is None


# ---------------------------------------------------------------------------
# refresh_oauth_token (public wrapper)
# ---------------------------------------------------------------------------


class TestRefreshOAuthToken:
    def test_delegates_to_internal(self) -> None:
        with patch.object(
            oauth_mod, "_refresh_tokens", return_value={"access_token": "new"}
        ) as mock:
            result = oauth_mod.refresh_oauth_token("rt-old")
        mock.assert_called_once_with("rt-old")
        assert result == {"access_token": "new"}


# ---------------------------------------------------------------------------
# run_oauth_flow — env var shortcut
# ---------------------------------------------------------------------------


class TestRunOAuthFlowEnvVar:
    def test_env_var_shortcut(
        self, fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "env-token")
        ok, msg = oauth_mod.run_oauth_flow()
        assert ok is True
        assert "ANTHROPIC_AUTH_TOKEN" in msg
        on_disk = store_mod.load_provider_auth("anthropic")
        assert on_disk["oauth"]["access_token"] == "env-token"
        assert on_disk["oauth"]["expires_at_ms"] == 0


# ---------------------------------------------------------------------------
# run_oauth_flow — browser flow (no actual browser)
# ---------------------------------------------------------------------------


def _no_server(*args: Any, **kwargs: Any) -> None:
    """Raise OSError to prevent HTTPServer from binding a real port."""
    raise OSError("mock: no real server")


class TestRunOAuthFlowBrowser:
    def test_cancelled(
        self, fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        # Prevent real server from binding a port
        with patch("duh.auth.anthropic_oauth.HTTPServer", side_effect=_no_server), \
             patch("duh.auth.anthropic_oauth.webbrowser.open"):
            ok, msg = oauth_mod.run_oauth_flow(
                input_fn=lambda _prompt: "",
                output_fn=lambda _t: None,
            )
        assert ok is False
        assert "Cancelled" in msg

    def test_exchange_failure(
        self, fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        with patch("duh.auth.anthropic_oauth.HTTPServer", side_effect=_no_server), \
             patch("duh.auth.anthropic_oauth.webbrowser.open"), \
             patch.object(oauth_mod, "_exchange_code_for_tokens", return_value=None):
            ok, msg = oauth_mod.run_oauth_flow(
                input_fn=lambda _prompt: "some-code",
                output_fn=lambda _t: None,
            )
        assert ok is False
        assert "exchange failed" in msg.lower()

    def test_pasted_url_extraction(
        self, fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When user pastes a full redirect URL, code is extracted from query string."""
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        tokens = {
            "access_token": "at-pasted",
            "refresh_token": "rt-pasted",
            "expires_at_ms": int(time.time() * 1000) + 600_000,
            "account_uuid": "",
            "email": "test@example.com",
            "organization_uuid": "",
        }
        with patch("duh.auth.anthropic_oauth.HTTPServer", side_effect=_no_server), \
             patch("duh.auth.anthropic_oauth.webbrowser.open"), \
             patch.object(oauth_mod, "_exchange_code_for_tokens", return_value=tokens):
            ok, msg = oauth_mod.run_oauth_flow(
                input_fn=lambda _prompt: "http://localhost:1456/callback?code=xyz&state=abc",
                output_fn=lambda _t: None,
            )
        assert ok is True
        assert "connected" in msg.lower()
        assert "test@example.com" in msg

    def test_pasted_raw_code(
        self, fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When user pastes raw code (no URL), it's used directly."""
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        tokens = {
            "access_token": "at-raw",
            "refresh_token": "rt-raw",
            "expires_at_ms": int(time.time() * 1000) + 600_000,
            "account_uuid": "",
            "email": "",
            "organization_uuid": "",
        }
        with patch("duh.auth.anthropic_oauth.HTTPServer", side_effect=_no_server), \
             patch("duh.auth.anthropic_oauth.webbrowser.open"), \
             patch.object(oauth_mod, "_exchange_code_for_tokens", return_value=tokens):
            ok, msg = oauth_mod.run_oauth_flow(
                input_fn=lambda _prompt: "raw-code-value",
                output_fn=lambda _t: None,
            )
        assert ok is True
        assert "connected" in msg.lower()


# ---------------------------------------------------------------------------
# connect_anthropic_oauth (anthropic.py wrapper)
# ---------------------------------------------------------------------------


class TestConnectAnthropicOAuth:
    def test_delegates_to_run_oauth_flow(self, fake_config_dir: Path) -> None:
        from duh.auth.anthropic import connect_anthropic_oauth

        with patch.object(
            oauth_mod, "run_oauth_flow", return_value=(True, "OK")
        ) as mock:
            ok, msg = connect_anthropic_oauth(
                input_fn=lambda _p: "", output_fn=lambda _t: None
            )
        assert ok is True
        assert msg == "OK"
        mock.assert_called_once()


# ---------------------------------------------------------------------------
# Adapter OAuth wiring
# ---------------------------------------------------------------------------


class TestAdapterOAuthWiring:
    def test_adapter_accepts_oauth_token(self) -> None:
        """AnthropicProvider can be constructed with oauth_token param."""
        from duh.adapters.anthropic import AnthropicProvider

        # Just verify construction doesn't raise — actual API calls need mocking
        provider = AnthropicProvider(oauth_token="test-token", model="claude-sonnet-4-6")
        assert provider._default_model == "claude-sonnet-4-6"

    def test_adapter_uses_bearer_header(self) -> None:
        """When oauth_token is provided, the client should have Authorization header."""
        from duh.adapters.anthropic import AnthropicProvider

        provider = AnthropicProvider(oauth_token="my-token", model="claude-sonnet-4-6")
        # The anthropic SDK stores default headers; verify our header is set
        default_headers = provider._client._custom_headers
        assert default_headers.get("Authorization") == "Bearer my-token"


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


class TestConstants:
    def test_client_id(self) -> None:
        assert oauth_mod.CLIENT_ID == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

    def test_redirect_port(self) -> None:
        assert oauth_mod.REDIRECT_PORT == 1456

    def test_scopes(self) -> None:
        assert "user:inference" in oauth_mod.SCOPES
        assert "user:profile" in oauth_mod.SCOPES

    def test_authorize_url(self) -> None:
        assert "platform.claude.com" in oauth_mod.AUTHORIZE_URL

    def test_token_url(self) -> None:
        assert "platform.claude.com" in oauth_mod.TOKEN_URL


# ---------------------------------------------------------------------------
# _build_authorize_url
# ---------------------------------------------------------------------------


class TestBuildAuthorizeUrl:
    def test_contains_required_params(self) -> None:
        url = oauth_mod._build_authorize_url("test-state", "test-challenge")
        assert "response_type=code" in url
        assert f"client_id={oauth_mod.CLIENT_ID}" in url
        assert "code_challenge=test-challenge" in url
        assert "code_challenge_method=S256" in url
        assert "state=test-state" in url
        assert "scope=" in url
