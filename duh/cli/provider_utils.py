"""Backward-compatible provider utility exports.

Canonical implementations now live in ``duh.providers.registry``.
"""

from __future__ import annotations

from duh.providers.registry import (
    _MODEL_CACHE,
    _discover_openai_models_api_key,
    _discover_openai_models_chatgpt,
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
)
from duh.providers import registry as _registry

httpx = _registry.httpx
get_saved_openai_api_key = _registry.get_saved_openai_api_key
get_valid_openai_chatgpt_oauth = _registry.get_valid_openai_chatgpt_oauth
