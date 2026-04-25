"""Tests for the new native provider adapters (ADR-027).

Covers DeepSeek, Mistral, Qwen, Together — the four OpenAI-compatible
native adapters added when OpenRouter and LiteLLM were removed.

Each adapter is a thin subclass of OpenAIProvider that customises:

- ``base_url`` to the provider's native endpoint
- ``api_key`` lookup via the provider's own env var
- model-prefix strip on construction and per-call

The streaming / tool-call / message-shape logic is inherited unchanged
and tested in ``test_openai_adapter.py`` — these tests focus on the
provider-specific bits.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from duh.adapters.deepseek import DeepSeekProvider
from duh.adapters.mistral import MistralProvider
from duh.adapters.qwen import QwenProvider
from duh.adapters.together import TogetherProvider


# ---- DeepSeek -------------------------------------------------------

def test_deepseek_uses_native_base_url():
    p = DeepSeekProvider(api_key="sk-test", model="deepseek-chat")
    assert "api.deepseek.com" in str(p._client.base_url)


def test_deepseek_strips_provider_prefix_in_default_model():
    p = DeepSeekProvider(api_key="sk-test", model="deepseek/deepseek-v4-pro")
    assert p._default_model == "deepseek-v4-pro"


def test_deepseek_reads_env_api_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    p = DeepSeekProvider(model="deepseek-chat")
    # OpenAI SDK exposes the configured key — accept either ``api_key`` or
    # the lower-cased internal attribute, depending on SDK version.
    key = getattr(p._client, "api_key", None)
    assert key == "sk-from-env"


# ---- Mistral --------------------------------------------------------

def test_mistral_uses_native_base_url():
    p = MistralProvider(api_key="sk-test", model="mistral-large-2512")
    assert "api.mistral.ai" in str(p._client.base_url)


def test_mistral_strips_provider_prefix():
    p = MistralProvider(api_key="sk-test", model="mistral/mistral-large-2512")
    assert p._default_model == "mistral-large-2512"


def test_mistral_reads_env_api_key(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "sk-mistral-env")
    p = MistralProvider(model="mistral-medium-2505")
    assert getattr(p._client, "api_key", None) == "sk-mistral-env"


# ---- Qwen / DashScope ----------------------------------------------

def test_qwen_uses_dashscope_compat_base_url():
    p = QwenProvider(api_key="sk-test", model="qwen3-max")
    assert "dashscope" in str(p._client.base_url)
    assert "compatible-mode" in str(p._client.base_url)


def test_qwen_strips_provider_prefix():
    p = QwenProvider(api_key="sk-test", model="qwen/qwen3-max-thinking")
    assert p._default_model == "qwen3-max-thinking"


def test_qwen_reads_dashscope_env_first(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-from-dashscope")
    monkeypatch.setenv("ALIBABA_API_KEY", "sk-from-alibaba")
    p = QwenProvider(model="qwen3-max")
    # DashScope wins when both are set.
    assert getattr(p._client, "api_key", None) == "sk-from-dashscope"


def test_qwen_falls_back_to_alibaba_env(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("ALIBABA_API_KEY", "sk-from-alibaba")
    p = QwenProvider(model="qwen3-max")
    assert getattr(p._client, "api_key", None) == "sk-from-alibaba"


# ---- Together --------------------------------------------------------

def test_together_uses_native_base_url():
    p = TogetherProvider(api_key="sk-test",
                          model="meta-llama/Llama-4-Scout-17B-16E-Instruct")
    assert "api.together.xyz" in str(p._client.base_url)


def test_together_strips_provider_prefix():
    p = TogetherProvider(
        api_key="sk-test",
        model="together/meta-llama/Llama-4-Scout-17B-16E-Instruct",
    )
    # The "together/" prefix is stripped, leaving the upstream's full
    # vendor/model id intact.
    assert p._default_model == "meta-llama/Llama-4-Scout-17B-16E-Instruct"


def test_together_reads_env_api_key(monkeypatch):
    monkeypatch.setenv("TOGETHER_API_KEY", "sk-together-env")
    p = TogetherProvider(model="meta-llama/Llama-3.3-70B-Instruct-Turbo")
    assert getattr(p._client, "api_key", None) == "sk-together-env"


# ---- registry routing -----------------------------------------------

@pytest.mark.parametrize("model_id, expected_provider", [
    ("deepseek/deepseek-v4-pro",                    "deepseek"),
    ("mistral/mistral-large-2512",                  "mistral"),
    ("qwen/qwen3-max-thinking",                     "qwen"),
    ("together/meta-llama/Llama-4-Scout-17B-16E-Instruct", "together"),
])
def test_registry_routes_model_prefix_to_native_provider(
    model_id, expected_provider, monkeypatch,
):
    """Model prefixes must dispatch to their native adapter.

    No more falling through to OpenRouter or LiteLLM.
    """
    # Provide the matching API key so build_model_backend.ok holds —
    # otherwise the absence of a key would mask a routing bug.
    keys = {
        "deepseek": "DEEPSEEK_API_KEY",
        "mistral":  "MISTRAL_API_KEY",
        "qwen":     "DASHSCOPE_API_KEY",
        "together": "TOGETHER_API_KEY",
    }
    monkeypatch.setenv(keys[expected_provider], "sk-test")

    from duh.providers.registry import (
        build_model_backend, infer_provider_from_model,
    )
    assert infer_provider_from_model(model_id) == expected_provider
    backend = build_model_backend(expected_provider, model_id)
    assert backend.ok, backend.error
    assert backend.provider == expected_provider


def test_registry_no_longer_routes_openrouter_or_litellm():
    """Old prefixes / provider names must surface as unknown."""
    from duh.providers.registry import (
        build_model_backend, infer_provider_from_model,
    )
    # No prefix lookup for openrouter/* anymore — falls through to the
    # keyword-based heuristics which won't match.
    assert infer_provider_from_model("openrouter/anything") is None
    # No provider branch for "litellm" or "openrouter" anymore.
    backend = build_model_backend("litellm", "anything")
    assert not backend.ok
    assert "Unknown provider" in backend.error
    backend = build_model_backend("openrouter", "anything")
    assert not backend.ok
    assert "Unknown provider" in backend.error
