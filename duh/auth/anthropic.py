"""Anthropic auth helpers."""

from __future__ import annotations

from typing import Any

from duh.auth.store import load_provider_auth, save_provider_auth


def connect_anthropic_api_key(*, input_fn: Any = input) -> tuple[bool, str]:
    key = input_fn("  Enter Anthropic API key: ").strip()
    if not key:
        return False, "No key entered."
    provider = load_provider_auth("anthropic")
    if not isinstance(provider, dict):
        provider = {}
    provider["api_key"] = key
    save_provider_auth("anthropic", provider)
    return True, "Anthropic API key saved."


def get_saved_anthropic_api_key() -> str:
    provider = load_provider_auth("anthropic")
    value = provider.get("api_key", "")
    return value if isinstance(value, str) else ""

