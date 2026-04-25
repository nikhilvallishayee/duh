"""Unified provider/auth/model registry for CLI and REPL runtimes."""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
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


_MODEL_CACHE_TTL_S = 300
_MODEL_CACHE: dict[str, tuple[float, list[str]]] = {}

_logger = logging.getLogger("duh.providers")

# Single-shot session warnings / info (ADR-075).
_LITELLM_DEPRECATION_WARNED = False
_ADAPTER_STARTUP_LOGGED: set[str] = set()


# ---------------------------------------------------------------------------
# Centralised model-name / provider-prefix / env-var registries
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelAliases:
    """Canonical model-name constants.

    Single source of truth for default model selections across the codebase.
    Adapters, registry fallbacks, CLI slash handlers, and the Codex auth
    module all pull their names from here instead of duplicating literals.
    """

    CHATGPT_CODEX_MODEL: str = "gpt-5.2-codex"
    CHATGPT_CODEX_HINTS: tuple[str, ...] = ("codex", "gpt-5")
    ANTHROPIC_DEFAULT: str = "claude-sonnet-4-6"
    OPENAI_DEFAULT: str = "gpt-4o"
    GEMINI_DEFAULT: str = "gemini-2.5-flash"
    GROQ_DEFAULT: str = "llama-3.3-70b-versatile"
    OLLAMA_DEFAULT: str = "qwen2.5-coder:1.5b"


# Module-level singleton exposing the aliases as attribute access.
# Consumers prefer ``ModelAliases.CHATGPT_CODEX_MODEL`` (class-level access)
# over instance lookups.
_ALIASES = ModelAliases()

# Legacy alias preserved for backwards compatibility with callers that
# already imported ``OPENAI_CODEX_MODEL_HINTS`` from this module.
OPENAI_CODEX_MODEL_HINTS: tuple[str, ...] = ModelAliases.CHATGPT_CODEX_HINTS


# Default model per provider. ``get_default_model()`` is the public accessor;
# keep this dict just below ``ModelAliases`` so additions stay colocated.
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": ModelAliases.ANTHROPIC_DEFAULT,
    "openai": ModelAliases.OPENAI_DEFAULT,
    "gemini": ModelAliases.GEMINI_DEFAULT,
    "groq": ModelAliases.GROQ_DEFAULT,
    "ollama": ModelAliases.OLLAMA_DEFAULT,
    "litellm": f"gemini/{ModelAliases.GEMINI_DEFAULT}",
    "openai_chatgpt": ModelAliases.CHATGPT_CODEX_MODEL,
}


def get_default_model(provider: str) -> str:
    """Return the canonical default model for *provider*.

    Returns an empty string when the provider is unknown. Every call-site
    that previously hardcoded a default model literal (anthropic adapter,
    OpenAI adapter fallback, gemini/groq registry fallback, etc.) resolves
    through this helper so renaming a default only requires editing
    ``ModelAliases``.
    """
    return DEFAULT_MODELS.get(provider, "")


# ---------------------------------------------------------------------------
# Sub-agent model tier resolution
# ---------------------------------------------------------------------------
#
# The AgentTool / SwarmTool schema exposes generic tiers (``small`` /
# ``medium`` / ``large`` / ``inherit``) instead of Anthropic-specific aliases
# (``haiku`` / ``sonnet`` / ``opus``). At invocation time the tier is resolved
# against the parent's *current* provider so the sub-agent stays on a model
# that provider actually serves — no more 404s when the parent runs on
# Gemini/Groq/Ollama and the child asks for ``haiku``.

#
# Last verified 2026-04-19 against live /models endpoints where the
# maintainer had API keys: Gemini + Groq + Ollama are probed-live; Anthropic
# and OpenAI entries are conservative pinned versions (no active keys on
# hand to verify the April 2026 4.7 / 5.4 flagship releases). Upgrade path:
# run ``gh workflow run model-drift-check`` — or just
# ``curl $PROVIDER_MODELS_URL | jq`` — to bump these when newer keys exist.

PROVIDER_TIER_MODELS: dict[str, dict[str, str]] = {
    # CONSERVATIVE — verify claude-opus-4-7 / claude-haiku-4-5 via /v1/models
    # before promoting. 4-6 entries confirmed present throughout 2026.
    "anthropic": {
        "small":  "claude-haiku-4-5",
        "medium": "claude-sonnet-4-6",
        "large":  "claude-opus-4-6",
    },
    # CONSERVATIVE — gpt-5.4 / gpt-5.4-pro released March 2026 per OpenAI
    # announcements but not live-verified here. Upgrade once keys are probed.
    "openai": {
        "small":  "gpt-4o-mini",
        "medium": "gpt-4o",
        "large":  "o1",
    },
    # LIVE-VERIFIED 2026-04-19 via generativelanguage.googleapis.com/v1beta.
    # 3.1-pro-preview reports 1M input tokens and supports generateContent.
    "gemini": {
        "small":  "gemini-2.5-flash",
        "medium": "gemini-2.5-pro",
        "large":  "gemini-3.1-pro-preview",
    },
    # LIVE-VERIFIED 2026-04-19 via api.groq.com/openai/v1/models.
    # gpt-oss-120b is OpenAI's open-weights 120B hosted on Groq infra —
    # the strongest reasoning model Groq serves.
    "groq": {
        "small":  "llama-3.1-8b-instant",
        "medium": "llama-3.3-70b-versatile",
        "large":  "openai/gpt-oss-120b",
    },
    # Local / Ollama. Pull with ``ollama pull <model>`` first.
    "ollama": {
        "small":  "qwen2.5-coder:1.5b",
        "medium": "qwen2.5-coder:7b",
        "large":  "deepseek-coder-v2:lite",
    },
    # litellm fallback keeps the ``gemini/`` namespace prefix.
    "litellm": {
        "small":  "gemini/gemini-2.5-flash",
        "medium": "gemini/gemini-2.5-pro",
        "large":  "gemini/gemini-3.1-pro-preview",
    },
}

TIER_ALIASES: set[str] = {"small", "medium", "large", "inherit"}


def resolve_model_alias(model: str | None) -> str | None:
    """Resolve ``<provider>/<tier>`` CLI aliases to concrete model names.

    Examples::

        gemini/large       -> gemini-3.1-pro-preview
        groq/small         -> llama-3.1-8b-instant
        anthropic/medium   -> claude-sonnet-4-6

    Plain model names (``claude-opus-4-6``, ``gemini/gemini-2.5-pro``) and
    unknown provider/tier combinations pass through unchanged, so this is
    safe to call unconditionally at top-level model-selection entry points.
    """
    if not model or "/" not in model:
        return model
    prefix, _, rest = model.partition("/")
    tier = rest.lower()
    if tier not in TIER_ALIASES or tier == "inherit":
        return model
    tier_map = PROVIDER_TIER_MODELS.get(prefix.lower())
    if tier_map is None or tier not in tier_map:
        return model
    return tier_map[tier]


def resolve_agent_tier(tier: str, parent_model: str) -> str:
    """Map a generic tier to a concrete model based on parent's provider.

    - ``""`` or ``"inherit"`` → return *parent_model* unchanged so the
      sub-agent runs on whatever the parent is currently using.
    - ``"small"`` / ``"medium"`` / ``"large"`` → look up in
      :data:`PROVIDER_TIER_MODELS` for the parent's inferred provider; fall
      back to *parent_model* and log an actionable warning when the provider
      is unknown or the tier is missing.
    - Any other value → treated as a literal model name (backwards compat
      for callers that still pass ``"claude-haiku-4-5"`` or similar).
    """
    if not tier or tier == "inherit":
        return parent_model
    if tier in TIER_ALIASES:
        provider = infer_provider_from_model(parent_model)
        if provider is None:
            _logger.warning(
                "Cannot resolve tier %r for provider 'unknown': no tier "
                "mapping. Using parent model %r.",
                tier,
                parent_model,
            )
            return parent_model
        tier_map = PROVIDER_TIER_MODELS.get(provider)
        if tier_map is None or tier not in tier_map:
            _logger.warning(
                "Cannot resolve tier %r for provider %r: no tier mapping. "
                "Using parent model %r.",
                tier,
                provider,
                parent_model,
            )
            return parent_model
        return tier_map[tier]
    # Literal model name — pass through unchanged.
    return tier


# ---------------------------------------------------------------------------
# Provider-prefix map — authoritative
# ---------------------------------------------------------------------------
#
# Ordered list of ``(prefix, provider)`` tuples. Longer / more specific
# prefixes must come first when two entries could both match a given model.
# ``is_<provider>_model`` predicates and ``infer_provider_from_model`` both
# consult this single table so adding a new provider prefix is a one-liner.

_PROVIDER_PREFIX_MAP: list[tuple[str, str]] = [
    ("gemini/", "gemini"),
    ("gemini-", "gemini"),
    ("groq/", "groq"),
    ("openrouter/", "openrouter"),
    ("deepseek/", "deepseek"),
]


def _lookup_provider_by_prefix(model: str | None) -> str | None:
    """Return the provider for *model* by scanning the prefix map.

    Case-insensitive; returns ``None`` when no prefix matches or *model* is
    falsy.
    """
    if not model:
        return None
    m = model.lower()
    for prefix, provider in _PROVIDER_PREFIX_MAP:
        if m.startswith(prefix):
            return provider
    return None


def strip_provider_prefix(model: str) -> str:
    """Strip any registered provider *namespace* prefix from *model*.

    Native SDKs want bare model names (``gemini-2.5-flash``,
    ``llama-3.3-70b-versatile``) — the registry emits ``gemini/…`` /
    ``groq/…`` for display and this helper gets used before the API call.
    Only slash-terminated namespace prefixes (``gemini/``, ``groq/``) are
    stripped — the bare ``gemini-`` prefix is preserved because it's part
    of the canonical model name for Google's own API.
    """
    if not model:
        return model
    lower = model.lower()
    for prefix, _ in _PROVIDER_PREFIX_MAP:
        if not prefix.endswith("/"):
            continue
        if lower.startswith(prefix):
            return model[len(prefix):]
    return model


# ---------------------------------------------------------------------------
# API-key env var registry
# ---------------------------------------------------------------------------

# Each provider lists its accepted env vars in MRO order — first non-empty
# value wins. ``get_api_key("gemini")`` therefore returns ``GEMINI_API_KEY``
# if set, else falls back to ``GOOGLE_API_KEY``.
PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "groq": ("GROQ_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "cerebras": ("CEREBRAS_API_KEY",),
}


def get_api_key(provider: str) -> str:
    """Return the first non-empty env var for *provider*, or an empty string.

    Providers with saved-auth files (Anthropic/OpenAI) should still fall
    through to their ``get_saved_*_api_key`` helpers — this only checks the
    env. Unknown providers return ``""``.
    """
    for name in PROVIDER_ENV_VARS.get(provider, ()):
        val = os.environ.get(name, "")
        if val:
            return val
    return ""


def _module_importable(name: str) -> bool:
    """True if ``name`` can be imported without actually importing it.

    We check ``sys.modules`` first so tests can simulate "package missing"
    by setting ``sys.modules[name] = None``.
    """
    if name in sys.modules:
        return sys.modules[name] is not None
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _google_genai_available() -> bool:
    return _module_importable("google.genai") or _module_importable("google_genai")


def _groq_sdk_available() -> bool:
    return _module_importable("groq")


def _litellm_available() -> bool:
    return _module_importable("litellm")


def is_gemini_model(model: str | None) -> bool:
    return _lookup_provider_by_prefix(model) == "gemini"


def is_groq_model(model: str | None) -> bool:
    return _lookup_provider_by_prefix(model) == "groq"


def is_openrouter_model(model: str | None) -> bool:
    return _lookup_provider_by_prefix(model) == "openrouter"


def _emit_adapter_startup_log(adapter: str, model: str) -> None:
    """Emit a single log line per (adapter, model) per session."""
    key = f"{adapter}:{model}"
    if key in _ADAPTER_STARTUP_LOGGED:
        return
    _ADAPTER_STARTUP_LOGGED.add(key)
    _logger.info("%s for %s", adapter, model)


def emit_litellm_deprecation_warning() -> None:
    """Emit the deprecation stderr notice once per session."""
    global _LITELLM_DEPRECATION_WARNED
    if _LITELLM_DEPRECATION_WARNED:
        return
    _LITELLM_DEPRECATION_WARNED = True
    sys.stderr.write(
        "[duh] LiteLLM adapter is opt-in fallback. Prefer native providers "
        "when available (ADR-075).\n"
    )


def _reset_session_state_for_tests() -> None:
    """Test helper: clear the one-shot session flags."""
    global _LITELLM_DEPRECATION_WARNED
    _LITELLM_DEPRECATION_WARNED = False
    _ADAPTER_STARTUP_LOGGED.clear()


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
    # ADR-075: gemini/* and groq/* prefer native adapters when the SDK is
    # installed; otherwise they fall through to LiteLLM as the opt-in fallback.
    # openrouter/* uses the openai SDK natively against OpenRouter's
    # OpenAI-compatible endpoint — no LiteLLM hop.
    prefix_provider = _lookup_provider_by_prefix(model)
    if prefix_provider == "gemini":
        return "gemini" if _google_genai_available() else "litellm"
    if prefix_provider == "groq":
        return "groq" if _groq_sdk_available() else "litellm"
    if prefix_provider == "openrouter":
        return "openrouter"
    if prefix_provider == "deepseek":
        return "deepseek" if get_api_key("deepseek") else "openrouter"
    # Everything else with a "/" (e.g. "bedrock/claude-3-haiku",
    # "together_ai/…") is a LiteLLM model string. Check before native
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
    wants_codex = any(k in m for k in ModelAliases.CHATGPT_CODEX_HINTS)

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
    # Auto-detect: env vars and saved keys first, then ollama probe.
    if has_anthropic_available():
        return "anthropic"
    if has_openai_available() or has_openai_chatgpt_oauth():
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
    if _google_genai_available() and get_api_key("gemini"):
        out.append("gemini")
    if _groq_sdk_available() and get_api_key("groq"):
        out.append("groq")
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
        return _merge_current_model([ModelAliases.OLLAMA_DEFAULT], current_model)
    return []


ProviderFactory = Callable[[str], Any]
ProviderFactories = dict[str, ProviderFactory]


def _try_native_gemini(model: str) -> Any | None:
    """Return a GeminiProvider instance if google-genai is importable; else None.

    The adapter module may not exist yet while ADR-075 is rolling out
    (GeminiProvider is implemented concurrently by another agent). Import
    is deferred and wrapped so callers don't crash when the file is absent.
    """
    if not _google_genai_available():
        return None
    try:
        from duh.adapters.gemini import GeminiProvider  # type: ignore[import-not-found]
    except ImportError:
        return None
    # -1 = dynamic thinking budget. On thinking-capable models (2.5-pro,
    # 3.1-pro-preview, …) this makes Gemini stream thought parts, which the
    # TUI renders in the ThinkingWidget. Without this, include_thoughts
    # defaults off and no thinking_delta events ever fire.
    return GeminiProvider(model=model, thinking_budget=-1)


def _try_native_groq(model: str) -> Any | None:
    """Return a GroqProvider instance if the ``groq`` SDK is importable; else None."""
    if not _groq_sdk_available():
        return None
    try:
        from duh.adapters.groq import GroqProvider  # type: ignore[import-not-found]
    except ImportError:
        return None
    return GroqProvider(model=model)


def build_model_backend(
    provider_name: str,
    model: str | None,
    provider_factories: ProviderFactories | None = None,
) -> ProviderBackend:
    provider_factories = provider_factories or {}
    model = resolve_model_alias(model)

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
        default_model = get_default_model("anthropic")
        if not api_key:
            return ProviderBackend("anthropic", model or default_model, None, "ANTHROPIC_API_KEY not set.")
        resolved = model or default_model
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
            resolved = model or ModelAliases.CHATGPT_CODEX_MODEL
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
        openai_default = get_default_model("openai")
        if mode == "api_key":
            api_key = get_openai_api_key()
            resolved = model or openai_default
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
            model or openai_default,
            None,
            "OpenAI not configured. Use /connect openai or set OPENAI_API_KEY.",
        )

    if provider_name == "ollama":
        resolved = model or get_default_model("ollama")
        create = provider_factories.get("ollama")
        if create is None:
            from duh.adapters.ollama import OllamaProvider

            create = lambda m: OllamaProvider(model=m)
        _emit_adapter_startup_log("Using OllamaProvider (native)", resolved)
        return ProviderBackend("ollama", resolved, create(resolved).stream, auth_mode="local")

    if provider_name == "gemini":
        resolved = model or get_default_model("gemini")
        create = provider_factories.get("gemini")
        if create is not None:
            provider = create(resolved)
        else:
            provider = _try_native_gemini(resolved)
        if provider is None:
            return ProviderBackend(
                "gemini",
                resolved,
                None,
                "google-genai is not installed. Install with: pip install 'google-genai'",
            )
        _emit_adapter_startup_log("Using GeminiProvider (native)", resolved)
        return ProviderBackend("gemini", resolved, provider.stream, auth_mode="api_key")

    if provider_name == "deepseek":
        api_key = get_api_key("deepseek")
        resolved = model or "deepseek/deepseek-chat"
        if not api_key:
            return ProviderBackend(
                "deepseek",
                resolved,
                None,
                "DEEPSEEK_API_KEY not set. Get a key at platform.deepseek.com.",
            )
        create = provider_factories.get("deepseek")
        if create is None:
            from duh.adapters.deepseek import DeepSeekProvider
            create = lambda m: DeepSeekProvider(api_key=api_key, model=m)
        _emit_adapter_startup_log("Using DeepSeekProvider (native, OpenAI-shaped)", resolved)
        return ProviderBackend("deepseek", resolved, create(resolved).stream, auth_mode="api_key")

    if provider_name == "openrouter":
        api_key = get_api_key("openrouter")
        resolved = model or "openrouter/deepseek/deepseek-v4-pro"
        if not api_key:
            return ProviderBackend(
                "openrouter",
                resolved,
                None,
                "OPENROUTER_API_KEY not set. Get a key at openrouter.ai.",
            )
        create = provider_factories.get("openrouter")
        if create is None:
            from duh.adapters.openrouter import OpenRouterProvider
            create = lambda m: OpenRouterProvider(api_key=api_key, model=m)
        _emit_adapter_startup_log("Using OpenRouterProvider (native, OpenAI-shaped)", resolved)
        return ProviderBackend("openrouter", resolved, create(resolved).stream, auth_mode="api_key")

    if provider_name == "groq":
        resolved = model or f"groq/{ModelAliases.GROQ_DEFAULT}"
        create = provider_factories.get("groq")
        if create is not None:
            provider = create(resolved)
        else:
            provider = _try_native_groq(resolved)
        if provider is None:
            return ProviderBackend(
                "groq",
                resolved,
                None,
                "groq SDK is not installed. Install with: pip install groq",
            )
        _emit_adapter_startup_log("Using GroqProvider (native)", resolved)
        return ProviderBackend("groq", resolved, provider.stream, auth_mode="api_key")

    if provider_name == "litellm":
        resolved = model or get_default_model("litellm")
        create = provider_factories.get("litellm")
        if create is None:
            # ADR-075: litellm is opt-in. Produce a clear error if it's not
            # installed instead of letting the import explode at stream time.
            if not _litellm_available():
                return ProviderBackend(
                    "litellm",
                    resolved,
                    None,
                    (
                        f"LiteLLM is required for provider {resolved!r}. "
                        "Install with: pip install 'duh-cli[litellm]'"
                    ),
                )
            from duh.adapters.litellm_provider import LiteLLMProvider
            create = lambda m: LiteLLMProvider(model=m)  # noqa: E731
        _emit_adapter_startup_log("Using LiteLLM fallback", resolved)
        return ProviderBackend("litellm", resolved, create(resolved).stream, auth_mode="env_vars")

    return ProviderBackend(provider_name, model or "", None, f"Unknown provider: {provider_name}")
