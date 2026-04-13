"""Persistent auth store for provider credentials.

Stored at ~/.config/duh/auth.json with structure:
{
  "providers": {
    "openai": {
      ...
    }
  }
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from duh.config import config_dir


def _auth_path() -> Path:
    return config_dir() / "auth.json"


def _load_store() -> dict[str, Any]:
    path = _auth_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return {}


def _save_store(data: dict[str, Any]) -> None:
    path = _auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass


def load_provider_auth(provider: str) -> dict[str, Any]:
    data = _load_store()
    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        return {}
    value = providers.get(provider, {})
    return value if isinstance(value, dict) else {}


def save_provider_auth(provider: str, auth: dict[str, Any]) -> None:
    data = _load_store()
    providers = data.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        data["providers"] = providers
    providers[provider] = auth
    _save_store(data)

