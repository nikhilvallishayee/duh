"""Extended coverage tests for duh.providers.registry.

Targets the lines not yet covered by test_provider_utils.py:
  - connected_providers (all combos)
  - _cache_get expired
  - _openai_model_sort_key for o1/o3/default
  - _discover_openai_models_api_key (no chat models in response, bad status)
  - _discover_openai_models_chatgpt (no oauth, missing access/account, HTTP
    400+, data list parsing)
  - build_model_backend (all provider + auth combinations + unknown)
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from duh.providers.registry import (
    _MODEL_CACHE,
    _cache_get,
    _cache_put,
    _discover_openai_models_api_key,
    _discover_openai_models_chatgpt,
    _filter_chat_models,
    _merge_current_model,
    _openai_model_sort_key,
    available_models_for_provider,
    build_model_backend,
    connected_providers,
    get_anthropic_api_key,
    get_openai_api_key,
    has_anthropic_available,
    has_openai_available,
    infer_provider_from_model,
    resolve_openai_auth_mode,
    resolve_provider_name,
    ProviderBackend,
)


@pytest.fixture(autouse=True)
def _clear_cache_and_env(monkeypatch):
    _MODEL_CACHE.clear()
    # Neutral env for every test unless overridden. DUH_STUB_PROVIDER
    # is cleared so registry behaviour is tested directly without the
    # short-circuit that the stub provider installs at module level —
    # otherwise build_model_backend always returns "stub".
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
    yield
    _MODEL_CACHE.clear()


# ============================================================================
# connected_providers
# ============================================================================


class TestConnectedProviders:
    def test_empty_when_nothing_connected(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_anthropic_api_key", lambda: ""
        )
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_openai_api_key", lambda: ""
        )
        monkeypatch.setattr(
            "duh.providers.registry.has_openai_chatgpt_oauth", lambda: False
        )
        out = connected_providers(check_ollama=lambda: False)
        assert out == []

    def test_only_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_openai_api_key", lambda: ""
        )
        monkeypatch.setattr(
            "duh.providers.registry.has_openai_chatgpt_oauth", lambda: False
        )
        out = connected_providers(check_ollama=lambda: False)
        assert out == ["anthropic"]

    def test_only_openai_api_key(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_anthropic_api_key", lambda: ""
        )
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        monkeypatch.setattr(
            "duh.providers.registry.has_openai_chatgpt_oauth", lambda: False
        )
        out = connected_providers(check_ollama=lambda: False)
        assert out == ["openai"]

    def test_only_openai_oauth(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_anthropic_api_key", lambda: ""
        )
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_openai_api_key", lambda: ""
        )
        monkeypatch.setattr(
            "duh.providers.registry.has_openai_chatgpt_oauth", lambda: True
        )
        out = connected_providers(check_ollama=lambda: False)
        assert out == ["openai"]

    def test_only_ollama(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_anthropic_api_key", lambda: ""
        )
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_openai_api_key", lambda: ""
        )
        monkeypatch.setattr(
            "duh.providers.registry.has_openai_chatgpt_oauth", lambda: False
        )
        out = connected_providers(check_ollama=lambda: True)
        assert out == ["ollama"]

    def test_all_three(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        monkeypatch.setattr(
            "duh.providers.registry.has_openai_chatgpt_oauth", lambda: False
        )
        out = connected_providers(check_ollama=lambda: True)
        assert out == ["anthropic", "openai", "ollama"]


# ============================================================================
# _cache_get expiry
# ============================================================================


class TestCache:
    def test_cache_get_miss(self):
        assert _cache_get("nonexistent") is None

    def test_cache_get_expired(self, monkeypatch):
        _MODEL_CACHE["k"] = (time.time() - 10_000, ["gpt-4o"])
        assert _cache_get("k") is None

    def test_cache_put_dedupes(self):
        got = _cache_put("dup", ["a", "a", "b"])
        assert got == ["a", "b"]


# ============================================================================
# _openai_model_sort_key
# ============================================================================


class TestSortKey:
    def test_codex(self):
        k = _openai_model_sort_key("codex-32b")
        assert k[0] == 0

    def test_gpt5(self):
        k = _openai_model_sort_key("gpt-5-mini")
        assert k[0] == 1

    def test_gpt4(self):
        k = _openai_model_sort_key("gpt-4o")
        assert k[0] == 2

    def test_o1(self):
        k = _openai_model_sort_key("o1-preview")
        assert k[0] == 3

    def test_o3(self):
        k = _openai_model_sort_key("o3-mini")
        assert k[0] == 3

    def test_unknown_default(self):
        k = _openai_model_sort_key("weird-model")
        assert k[0] == 9


# ============================================================================
# _filter_chat_models
# ============================================================================


class TestFilterChatModels:
    def test_filters_embeddings_and_tts(self):
        ids = ["gpt-4o", "text-embedding-3-large", "tts-1", "o1-preview", "codex-7b"]
        out = _filter_chat_models(ids)
        assert "gpt-4o" in out
        assert "o1-preview" in out
        assert "codex-7b" in out
        assert "text-embedding-3-large" not in out
        assert "tts-1" not in out

    def test_sorted_by_key(self):
        ids = ["gpt-4o", "codex-7b", "o1-preview", "gpt-5-mini"]
        out = _filter_chat_models(ids)
        # codex-7b comes first (tier 0), then gpt-5 (1), gpt-4 (2), o1 (3)
        assert out[0] == "codex-7b"


# ============================================================================
# _discover_openai_models_api_key
# ============================================================================


class TestDiscoverApiKeyModels:
    def test_no_key_returns_fallback(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_openai_api_key", lambda: ""
        )
        models = _discover_openai_models_api_key()
        assert "gpt-4o" in models

    def test_bad_status_falls_back(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        fake = SimpleNamespace(status_code=500, json=lambda: {})

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def get(self, *a, **k):
                return fake

        monkeypatch.setattr("duh.providers.registry.httpx.Client", _Client)
        models = _discover_openai_models_api_key()
        assert "gpt-4o" in models  # fallback catalog

    def test_empty_filtered_models_falls_back(self, monkeypatch):
        """Response parses but yields no chat models → fallback."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        fake = SimpleNamespace(
            status_code=200,
            json=lambda: {"data": [{"id": "text-embedding-3-large"}]},
        )

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def get(self, *a, **k):
                return fake

        monkeypatch.setattr("duh.providers.registry.httpx.Client", _Client)
        models = _discover_openai_models_api_key()
        assert "gpt-4o" in models  # fallback catalog


# ============================================================================
# _discover_openai_models_chatgpt
# ============================================================================


class TestDiscoverChatGPTModels:
    def test_no_oauth_catalog(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_valid_openai_chatgpt_oauth", lambda: None
        )
        models = _discover_openai_models_chatgpt()
        assert "gpt-5.2-codex" in models

    def test_missing_access_token_catalog(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_valid_openai_chatgpt_oauth",
            lambda: {"access_token": "", "account_id": "acct"},
        )
        models = _discover_openai_models_chatgpt()
        assert "gpt-5.2-codex" in models

    def test_missing_account_id_catalog(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_valid_openai_chatgpt_oauth",
            lambda: {"access_token": "tok", "account_id": ""},
        )
        models = _discover_openai_models_chatgpt()
        assert "gpt-5.2-codex" in models

    def test_cached_returns_cached(self, monkeypatch):
        _MODEL_CACHE["openai:chatgpt"] = (time.time(), ["cached-model"])
        models = _discover_openai_models_chatgpt()
        assert "cached-model" in models

    def test_first_url_fails_second_url_succeeds(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_valid_openai_chatgpt_oauth",
            lambda: {"access_token": "tok", "account_id": "acct"},
        )

        calls = {"n": 0}

        def make_resp(status, payload):
            return SimpleNamespace(status_code=status, json=lambda: payload)

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def get(self, url, **k):
                calls["n"] += 1
                if calls["n"] == 1:
                    return make_resp(500, {})
                return make_resp(200, {"models": [{"id": "gpt-5.2-codex"}]})

        monkeypatch.setattr("duh.providers.registry.httpx.Client", _Client)
        models = _discover_openai_models_chatgpt()
        assert "gpt-5.2-codex" in models
        assert calls["n"] == 2

    def test_data_list_parsing(self, monkeypatch):
        """ChatGPT endpoint returns ``data`` list instead of ``models``."""
        monkeypatch.setattr(
            "duh.providers.registry.get_valid_openai_chatgpt_oauth",
            lambda: {"access_token": "tok", "account_id": "acct"},
        )

        fake = SimpleNamespace(
            status_code=200,
            json=lambda: {"data": [{"id": "gpt-5.2-codex"}, {"id": "codex-9"}]},
        )

        class _Client:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def get(self, *a, **k):
                return fake

        monkeypatch.setattr("duh.providers.registry.httpx.Client", _Client)
        models = _discover_openai_models_chatgpt()
        assert "gpt-5.2-codex" in models

    def test_all_urls_fail_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_valid_openai_chatgpt_oauth",
            lambda: {"access_token": "tok", "account_id": "acct"},
        )

        class _BadClient:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def get(self, *a, **k):
                raise RuntimeError("boom")

        monkeypatch.setattr("duh.providers.registry.httpx.Client", _BadClient)
        models = _discover_openai_models_chatgpt()
        assert "gpt-5.2-codex" in models  # catalog fallback


# ============================================================================
# build_model_backend
# ============================================================================


class TestBuildModelBackend:
    def test_stub_provider(self, monkeypatch):
        monkeypatch.setenv("DUH_STUB_PROVIDER", "1")
        backend = build_model_backend("stub", "test-model")
        assert backend.provider == "stub"
        assert backend.ok
        assert backend.auth_mode == "stub"

    def test_stub_provider_via_env(self, monkeypatch):
        """Even if provider name is not 'stub', env var forces stub path."""
        monkeypatch.setenv("DUH_STUB_PROVIDER", "1")
        backend = build_model_backend("anthropic", "anything")
        assert backend.provider == "stub"
        assert backend.ok

    def test_stub_default_model(self, monkeypatch):
        monkeypatch.setenv("DUH_STUB_PROVIDER", "1")
        backend = build_model_backend("stub", None)
        assert backend.model == "stub-model"

    def test_anthropic_no_key(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_anthropic_api_key", lambda: ""
        )
        monkeypatch.setattr(
            "duh.providers.registry.get_valid_anthropic_oauth", lambda: None
        )
        backend = build_model_backend("anthropic", "claude-sonnet-4-6")
        assert not backend.ok
        assert "not configured" in backend.error

    def test_anthropic_with_key_default_model(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        fake_provider = MagicMock()
        fake_provider.stream = lambda **kw: None
        backend = build_model_backend(
            "anthropic",
            None,
            provider_factories={"anthropic": lambda m: fake_provider},
        )
        assert backend.provider == "anthropic"
        assert backend.model == "claude-sonnet-4-6"
        assert backend.auth_mode == "api_key"

    def test_anthropic_default_factory_path(self, monkeypatch):
        """When no factory is supplied, it falls back to the real provider."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")

        fake = MagicMock()
        fake.stream = MagicMock()

        with patch(
            "duh.adapters.anthropic.AnthropicProvider", return_value=fake
        ):
            backend = build_model_backend("anthropic", "claude-haiku-4-5")
        assert backend.ok
        assert backend.provider == "anthropic"

    def test_openai_chatgpt_mode(self, monkeypatch):
        """OpenAI + oauth → chatgpt backend."""
        monkeypatch.setattr(
            "duh.providers.registry.resolve_openai_auth_mode",
            lambda m: "chatgpt",
        )
        fake = MagicMock()
        fake.stream = MagicMock()
        backend = build_model_backend(
            "openai",
            None,
            provider_factories={"openai_chatgpt": lambda m: fake},
        )
        assert backend.provider == "openai"
        assert backend.auth_mode == "chatgpt"
        assert backend.model == "gpt-5.2-codex"
        assert backend.ok

    def test_openai_chatgpt_default_factory(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.resolve_openai_auth_mode",
            lambda m: "chatgpt",
        )
        fake = MagicMock()
        fake.stream = MagicMock()
        with patch(
            "duh.adapters.openai_chatgpt.OpenAIChatGPTProvider",
            return_value=fake,
        ):
            backend = build_model_backend("openai", "gpt-5.2-codex")
        assert backend.ok
        assert backend.auth_mode == "chatgpt"

    def test_openai_api_key_mode(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.resolve_openai_auth_mode",
            lambda m: "api_key",
        )
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        fake = MagicMock()
        fake.stream = MagicMock()
        backend = build_model_backend(
            "openai",
            None,
            provider_factories={"openai_api": lambda m: fake},
        )
        assert backend.provider == "openai"
        assert backend.auth_mode == "api_key"
        assert backend.model == "gpt-4o"
        assert backend.ok

    def test_openai_api_key_default_factory(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.resolve_openai_auth_mode",
            lambda m: "api_key",
        )
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")

        fake = MagicMock()
        fake.stream = MagicMock()
        with patch(
            "duh.adapters.openai.OpenAIProvider", return_value=fake
        ):
            backend = build_model_backend("openai", "gpt-4o")
        assert backend.ok
        assert backend.auth_mode == "api_key"

    def test_openai_not_configured(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.resolve_openai_auth_mode",
            lambda m: "none",
        )
        backend = build_model_backend("openai", None)
        assert not backend.ok
        assert "OpenAI not configured" in backend.error

    def test_ollama_with_factory(self, monkeypatch):
        fake = MagicMock()
        fake.stream = MagicMock()
        backend = build_model_backend(
            "ollama",
            None,
            provider_factories={"ollama": lambda m: fake},
        )
        assert backend.provider == "ollama"
        assert backend.auth_mode == "local"
        assert backend.model == "qwen2.5-coder:1.5b"
        assert backend.ok

    def test_ollama_default_factory(self, monkeypatch):
        fake = MagicMock()
        fake.stream = MagicMock()
        with patch(
            "duh.adapters.ollama.OllamaProvider", return_value=fake
        ):
            backend = build_model_backend("ollama", "qwen2.5-coder:1.5b")
        assert backend.ok

    def test_unknown_provider(self):
        backend = build_model_backend("martian", "anything")
        assert not backend.ok
        assert "Unknown provider" in backend.error


# ============================================================================
# resolve_provider_name
# ============================================================================


class TestResolveProviderName:
    def test_stub_shortcircuits(self, monkeypatch):
        monkeypatch.setenv("DUH_STUB_PROVIDER", "1")
        out = resolve_provider_name(
            explicit_provider="anthropic",
            model="claude-sonnet-4-6",
            check_ollama=lambda: False,
        )
        assert out == "stub"

    def test_explicit_wins(self, monkeypatch):
        monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
        out = resolve_provider_name(
            explicit_provider="openai",
            model=None,
            check_ollama=lambda: False,
        )
        assert out == "openai"

    def test_model_inference(self, monkeypatch):
        monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
        out = resolve_provider_name(
            explicit_provider=None,
            model="claude-sonnet-4-6",
            check_ollama=lambda: False,
        )
        assert out == "anthropic"

    def test_anthropic_env(self, monkeypatch):
        monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        out = resolve_provider_name(
            explicit_provider=None,
            model=None,
            check_ollama=lambda: False,
        )
        assert out == "anthropic"

    def test_openai_env(self, monkeypatch):
        monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        out = resolve_provider_name(
            explicit_provider=None,
            model=None,
            check_ollama=lambda: False,
        )
        assert out == "openai"

    def test_ollama_fallback(self, monkeypatch):
        monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
        out = resolve_provider_name(
            explicit_provider=None,
            model=None,
            check_ollama=lambda: True,
        )
        assert out == "ollama"

    def test_none_when_nothing(self, monkeypatch):
        monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
        out = resolve_provider_name(
            explicit_provider=None,
            model=None,
            check_ollama=lambda: False,
        )
        assert out is None


# ============================================================================
# Simple helpers
# ============================================================================


class TestHelpers:
    def test_provider_backend_ok(self):
        pb = ProviderBackend(provider="x", model="m", call_model=lambda: None)
        assert pb.ok

    def test_provider_backend_error_not_ok(self):
        pb = ProviderBackend(
            provider="x", model="m", call_model=None, error="oops"
        )
        assert not pb.ok

    def test_has_anthropic_available_true(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
        assert has_anthropic_available()

    def test_has_anthropic_available_false(self, monkeypatch):
        monkeypatch.setattr(
            "duh.providers.registry.get_saved_anthropic_api_key", lambda: ""
        )
        assert not has_anthropic_available()

    def test_has_openai_available(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk")
        assert has_openai_available()

    def test_merge_current_model_prepends(self):
        out = _merge_current_model(["a", "b"], "c")
        assert out == ["c", "a", "b"]

    def test_merge_current_model_noop(self):
        out = _merge_current_model(["a", "b"], "a")
        assert out == ["a", "b"]

    def test_merge_current_model_none(self):
        out = _merge_current_model(["a", "b"], None)
        assert out == ["a", "b"]
