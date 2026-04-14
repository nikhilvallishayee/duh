"""Unified provider/auth/model registry for CLI and REPL runtimes."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from duh.adapters.stub_provider import stub_provider_enabled
from duh.auth.anthropic import get_saved_anthropic_api_key
from duh.auth.openai_chatgpt import (
    OPENAI_CHATGPT_MODELS,
    get_saved_openai_api_key,
    get_valid_openai_chatgpt_oauth,
    has_openai_chatgpt_oauth,
)


OPENAI_CODEX_MODEL_HINTS = ("codex", "gpt-5")
_MODEL_CACHE_TTL_S = 300
_MODEL_CACHE: dict[str, tuple[float, list[str]]] = {}


@dataclass
class ProviderBackend:
    provider: str
    model: str
    call_model: Any | None
    error: str = ""
    auth_mode: str = ""

    @property
    def ok(self) -> bool:
        return self.call_model is not None and not self.error


def infer_provider_from_model(model: str | None) -> str | None:
    if not model:
        return None
    # litellm convention: model strings with "/" (e.g. "gemini/gemini-2.5-flash",
    # "bedrock/claude-3-haiku") are litellm model strings.  Check before native
    # providers since a litellm string like "bedrock/claude-3-haiku" would
    # otherwise match the "haiku" keyword for anthropic.
    if "/" in model:
        return "litellm"
    m = model.lower()
    if any(k in m for k in ("claude", "haiku", "sonnet", "opus")):
        return "anthropic"
    if any(k in m for k in ("gpt", "o1", "o3", "davinci", "codex")):
        return "openai"
    return None


def get_anthropic_api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "") or get_saved_anthropic_api_key()


def get_openai_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "") or get_saved_openai_api_key()


def has_anthropic_available() -> bool:
    return bool(get_anthropic_api_key())


def has_openai_available() -> bool:
    return bool(get_openai_api_key())


def resolve_openai_auth_mode(model: str | None) -> str:
    m = (model or "").lower()
    oauth = get_valid_openai_chatgpt_oauth()
    api_key = get_openai_api_key()
    wants_codex = any(k in m for k in OPENAI_CODEX_MODEL_HINTS)

    if wants_codex and oauth:
        return "chatgpt"
    if api_key:
        return "api_key"
    return "none"


def resolve_provider_name(
    *,
    explicit_provider: str | None,
    model: str | None,
    check_ollama: Callable[[], bool],
) -> str | None:
    # Stub provider short-circuits everything for tests / offline runs.
    if stub_provider_enabled():
        return "stub"
    provider_name = explicit_provider or infer_provider_from_model(model)
    if provider_name:
        return provider_name
    # Keep auto-detection conservative and backward-compatible:
    # env vars first, then ollama probe. Saved keys are used once a provider
    # is explicitly chosen or inferred from model.
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if check_ollama():
        return "ollama"
    return None


def connected_providers(check_ollama: Callable[[], bool]) -> list[str]:
    out: list[str] = []
    if has_anthropic_available():
        out.append("anthropic")
    if has_openai_available() or has_openai_chatgpt_oauth():
        out.append("openai")
    if check_ollama():
        out.append("ollama")
    return list(dict.fromkeys(out))


def _cache_get(key: str) -> list[str] | None:
    row = _MODEL_CACHE.get(key)
    if not row:
        return None
    ts, models = row
    if time.time() - ts > _MODEL_CACHE_TTL_S:
        return None
    return list(models)


def _cache_put(key: str, models: list[str]) -> list[str]:
    deduped = list(dict.fromkeys(models))
    _MODEL_CACHE[key] = (time.time(), deduped)
    return deduped


def _merge_current_model(models: list[str], current_model: str | None) -> list[str]:
    if current_model and current_model not in models:
        return [current_model] + models
    return models


def _openai_model_sort_key(mid: str) -> tuple[int, str]:
    m = mid.lower()
    if "codex" in m:
        return (0, m)
    if m.startswith("gpt-5"):
        return (1, m)
    if m.startswith("gpt-4"):
        return (2, m)
    if m.startswith("o1") or m.startswith("o3"):
        return (3, m)
    return (9, m)


def _filter_chat_models(model_ids: list[str]) -> list[str]:
    out: list[str] = []
    for mid in model_ids:
        m = mid.lower()
        if m.startswith("gpt-") or m.startswith("o1") or m.startswith("o3") or "codex" in m:
            out.append(mid)
    return sorted(set(out), key=_openai_model_sort_key)


def _discover_openai_models_api_key(current_model: str | None = None) -> list[str]:
    cache_key = "openai:api_key"
    cached = _cache_get(cache_key)
    if cached:
        return _merge_current_model(cached, current_model)

    api_key = get_openai_api_key()
    if not api_key:
        base = ["gpt-4o", "gpt-4o-mini", "o1", "o3"] + OPENAI_CHATGPT_MODELS
        return _cache_put(cache_key, _merge_current_model(base, current_model))

    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"openai models status {resp.status_code}")
        body = resp.json()
        data = body.get("data", [])
        ids = [x.get("id", "") for x in data if isinstance(x, dict) and x.get("id")]
        models = _filter_chat_models([m for m in ids if isinstance(m, str)])
        if not models:
            raise RuntimeError("no chat models in response")
        return _cache_put(cache_key, _merge_current_model(models, current_model))
    except Exception:
        fallback = ["gpt-4o", "gpt-4o-mini", "o1", "o3"] + OPENAI_CHATGPT_MODELS
        return _cache_put(cache_key, _merge_current_model(fallback, current_model))


def _discover_openai_models_chatgpt(current_model: str | None = None) -> list[str]:
    cache_key = "openai:chatgpt"
    cached = _cache_get(cache_key)
    if cached:
        return _merge_current_model(cached, current_model)

    oauth = get_valid_openai_chatgpt_oauth()
    if not oauth:
        return _cache_put(cache_key, _merge_current_model(list(OPENAI_CHATGPT_MODELS), current_model))

    access = oauth.get("access_token", "")
    account_id = oauth.get("account_id", "")
    if not access or not account_id:
        return _cache_put(cache_key, _merge_current_model(list(OPENAI_CHATGPT_MODELS), current_model))

    headers = {
        "Authorization": f"Bearer {access}",
        "chatgpt-account-id": str(account_id),
        "OpenAI-Beta": "responses=experimental",
        "originator": "codex_cli_rs",
    }
    candidates = [
        "https://chatgpt.com/backend-api/codex/models",
        "https://chatgpt.com/backend-api/codex/model_list",
    ]
    for url in candidates:
        try:
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url, headers=headers)
            if resp.status_code >= 400:
                continue
            body = resp.json()
            ids: list[str] = []
            if isinstance(body, dict):
                if isinstance(body.get("models"), list):
                    for item in body["models"]:
                        if isinstance(item, dict) and isinstance(item.get("id"), str):
                            ids.append(item["id"])
                        elif isinstance(item, str):
                            ids.append(item)
                elif isinstance(body.get("data"), list):
                    for item in body["data"]:
                        if isinstance(item, dict) and isinstance(item.get("id"), str):
                            ids.append(item["id"])
            models = _filter_chat_models(ids)
            if models:
                return _cache_put(cache_key, _merge_current_model(models, current_model))
        except Exception:
            continue
    return _cache_put(cache_key, _merge_current_model(list(OPENAI_CHATGPT_MODELS), current_model))


def available_models_for_provider(provider_name: str, *, current_model: str | None = None) -> list[str]:
    if provider_name == "anthropic":
        return _merge_current_model(
            ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5"],
            current_model,
        )
    if provider_name == "openai":
        mode = resolve_openai_auth_mode(current_model)
        if mode == "chatgpt":
            return _discover_openai_models_chatgpt(current_model)
        return _discover_openai_models_api_key(current_model)
    if provider_name == "ollama":
        return _merge_current_model(["qwen2.5-coder:1.5b"], current_model)
    return []


ProviderFactory = Callable[[str], Any]
ProviderFactories = dict[str, ProviderFactory]


def build_model_backend(
    provider_name: str,
    model: str | None,
    provider_factories: ProviderFactories | None = None,
) -> ProviderBackend:
    provider_factories = provider_factories or {}

    if provider_name == "stub" or stub_provider_enabled():
        from duh.adapters.stub_provider import StubProvider

        resolved = model or "stub-model"
        return ProviderBackend(
            "stub",
            resolved,
            StubProvider(model=resolved).stream,
            auth_mode="stub",
        )

    if provider_name == "anthropic":
        api_key = get_anthropic_api_key()
        if not api_key:
            return ProviderBackend("anthropic", model or "claude-sonnet-4-6", None, "ANTHROPIC_API_KEY not set.")
        resolved = model or "claude-sonnet-4-6"
        create = provider_factories.get("anthropic")
        if create is None:
            from duh.adapters.anthropic import AnthropicProvider

            create = lambda m: AnthropicProvider(api_key=api_key, model=m)
        return ProviderBackend(
            "anthropic",
            resolved,
            create(resolved).stream,
            auth_mode="api_key",
        )

    if provider_name == "openai":
        mode = resolve_openai_auth_mode(model)
        if mode == "chatgpt":
            resolved = model or "gpt-5.2-codex"
            create = provider_factories.get("openai_chatgpt")
            if create is None:
                from duh.adapters.openai_chatgpt import OpenAIChatGPTProvider

                create = lambda m: OpenAIChatGPTProvider(model=m)
            return ProviderBackend(
                "openai",
                resolved,
                create(resolved).stream,
                auth_mode="chatgpt",
            )
        if mode == "api_key":
            api_key = get_openai_api_key()
            resolved = model or "gpt-4o"
            create = provider_factories.get("openai_api")
            if create is None:
                from duh.adapters.openai import OpenAIProvider

                create = lambda m: OpenAIProvider(api_key=api_key, model=m)
            return ProviderBackend(
                "openai",
                resolved,
                create(resolved).stream,
                auth_mode="api_key",
            )
        return ProviderBackend(
            "openai",
            model or "gpt-4o",
            None,
            "OpenAI not configured. Use /connect openai or set OPENAI_API_KEY.",
        )

    if provider_name == "ollama":
        resolved = model or "qwen2.5-coder:1.5b"
        create = provider_factories.get("ollama")
        if create is None:
            from duh.adapters.ollama import OllamaProvider

            create = lambda m: OllamaProvider(model=m)
        return ProviderBackend("ollama", resolved, create(resolved).stream, auth_mode="local")

    if provider_name == "litellm":
        resolved = model or "gemini/gemini-2.5-flash"
        create = provider_factories.get("litellm")
        if create is None:
            from duh.adapters.litellm_provider import LiteLLMProvider
            create = lambda m: LiteLLMProvider(model=m)  # noqa: E731
        return ProviderBackend("litellm", resolved, create(resolved).stream, auth_mode="env_vars")

    return ProviderBackend(provider_name, model or "", None, f"Unknown provider: {provider_name}")
