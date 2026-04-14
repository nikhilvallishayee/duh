"""Tests for the provider registry (duh.providers.registry).

Originally these tests targeted a ``duh.cli.provider_utils`` shim that
re-exported registry symbols.  The shim added no value, coupled the REPL CLI
module to provider auth concerns, and caused patches to land in the wrong
module — so it was removed in favour of importing from the canonical
location directly.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from duh.providers.registry import (
    _MODEL_CACHE,
    available_models_for_provider,
    infer_provider_from_model,
    resolve_openai_auth_mode,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _MODEL_CACHE.clear()
    yield
    _MODEL_CACHE.clear()


def test_infer_provider_from_codex_model():
    assert infer_provider_from_model("gpt-5.2-codex") == "openai"


def test_infer_provider_returns_none_for_empty():
    assert infer_provider_from_model("") is None
    assert infer_provider_from_model(None) is None


def test_infer_provider_unknown_model():
    assert infer_provider_from_model("llama-3") is None


def test_available_models_openai_includes_codex():
    with patch("duh.providers.registry.resolve_openai_auth_mode", return_value="api_key"), \
         patch("duh.providers.registry._discover_openai_models_api_key", return_value=["gpt-4o", "gpt-5.2-codex"]):
        models = available_models_for_provider("openai")
        assert "gpt-5.2-codex" in models
        assert "gpt-4o" in models


def test_available_models_unknown_provider_returns_empty():
    assert available_models_for_provider("weird") == []


def test_available_models_anthropic():
    models = available_models_for_provider("anthropic")
    assert "claude-sonnet-4-6" in models
    assert "claude-opus-4-6" in models
    assert "claude-haiku-4-5" in models


def test_available_models_anthropic_merges_current_model():
    models = available_models_for_provider("anthropic", current_model="claude-next-3")
    assert models[0] == "claude-next-3"


def test_available_models_ollama_merges_current_model():
    models = available_models_for_provider("ollama", current_model="qwen2.5")
    assert models[0] == "qwen2.5"


def test_resolve_openai_auth_mode_prefers_chatgpt_for_codex():
    with patch("duh.providers.registry.get_valid_openai_chatgpt_oauth", return_value={"access_token": "x"}), \
         patch("duh.providers.registry.get_saved_openai_api_key", return_value="sk-test"), \
         patch("os.environ.get", side_effect=lambda k, d=None: d):
        assert resolve_openai_auth_mode("gpt-5.2-codex") == "chatgpt"


def test_resolve_openai_auth_mode_prefers_api_key_for_non_codex():
    with patch("duh.providers.registry.get_valid_openai_chatgpt_oauth", return_value={"access_token": "x"}), \
         patch("duh.providers.registry.get_saved_openai_api_key", return_value="sk-test"), \
         patch("os.environ.get", side_effect=lambda k, d=None: "sk-env" if k == "OPENAI_API_KEY" else d):
        assert resolve_openai_auth_mode("gpt-4o") == "api_key"


def test_resolve_openai_auth_mode_none_when_unconfigured():
    with patch("duh.providers.registry.get_valid_openai_chatgpt_oauth", return_value=None), \
         patch("duh.providers.registry.get_saved_openai_api_key", return_value=""), \
         patch("os.environ.get", side_effect=lambda k, d=None: d):
        assert resolve_openai_auth_mode("gpt-4o") == "none"


def test_openai_api_key_model_discovery_from_v1_models():
    fake_response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "data": [
                {"id": "gpt-4o"},
                {"id": "gpt-5.2-codex"},
                {"id": "text-embedding-3-large"},
            ]
        },
    )

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            return fake_response

    with patch("duh.providers.registry.resolve_openai_auth_mode", return_value="api_key"), \
         patch("duh.providers.registry.httpx.Client", _FakeClient), \
         patch("duh.providers.registry.get_saved_openai_api_key", return_value="sk-test"), \
         patch("os.environ.get", side_effect=lambda k, d=None: d):
        models = available_models_for_provider("openai")

    assert "gpt-4o" in models
    assert "gpt-5.2-codex" in models
    assert "text-embedding-3-large" not in models


def test_openai_api_key_discovery_error_fallback():
    class _BadClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            raise RuntimeError("boom")

    with patch("duh.providers.registry.resolve_openai_auth_mode", return_value="api_key"), \
         patch("duh.providers.registry.httpx.Client", _BadClient), \
         patch("duh.providers.registry.get_saved_openai_api_key", return_value="sk-test"), \
         patch("os.environ.get", side_effect=lambda k, d=None: d):
        models = available_models_for_provider("openai")
    assert "gpt-4o" in models  # fallback catalog


def test_openai_api_key_discovery_cached():
    fake_response = SimpleNamespace(
        status_code=200,
        json=lambda: {"data": [{"id": "gpt-4o"}]},
    )
    calls = {"n": 0}

    class _CountingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            calls["n"] += 1
            return fake_response

    with patch("duh.providers.registry.resolve_openai_auth_mode", return_value="api_key"), \
         patch("duh.providers.registry.httpx.Client", _CountingClient), \
         patch("duh.providers.registry.get_saved_openai_api_key", return_value="sk-test"), \
         patch("os.environ.get", side_effect=lambda k, d=None: d):
        available_models_for_provider("openai")
        available_models_for_provider("openai")
    assert calls["n"] == 1  # second call is cached


def test_openai_chatgpt_discovery_falls_back_to_codex_catalog():
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            raise RuntimeError("no model endpoint")

    oauth = {"access_token": "tok", "account_id": "acct"}
    with patch("duh.providers.registry.resolve_openai_auth_mode", return_value="chatgpt"), \
         patch("duh.providers.registry.httpx.Client", _FakeClient), \
         patch("duh.providers.registry.get_valid_openai_chatgpt_oauth", return_value=oauth):
        models = available_models_for_provider("openai")

    assert "gpt-5.2-codex" in models


def test_openai_chatgpt_discovery_parses_models_payload():
    fake_response = SimpleNamespace(
        status_code=200,
        json=lambda: {"models": [{"id": "gpt-5.2-codex"}, {"id": "gpt-5.1-codex"}, "gpt-5.1"]},
    )

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            return fake_response

    oauth = {"access_token": "tok", "account_id": "acct"}
    with patch("duh.providers.registry.resolve_openai_auth_mode", return_value="chatgpt"), \
         patch("duh.providers.registry.httpx.Client", _FakeClient), \
         patch("duh.providers.registry.get_valid_openai_chatgpt_oauth", return_value=oauth):
        models = available_models_for_provider("openai")
    assert "gpt-5.2-codex" in models


def test_openai_chatgpt_discovery_without_oauth_uses_catalog():
    with patch("duh.providers.registry.resolve_openai_auth_mode", return_value="chatgpt"), \
         patch("duh.providers.registry.get_valid_openai_chatgpt_oauth", return_value=None):
        models = available_models_for_provider("openai")
    assert "gpt-5.2-codex" in models
