"""Tests for duh.auth.store and duh.auth.anthropic.

Brings both modules to 100% line coverage. Uses tmp_path + monkeypatch
so nothing touches the real ~/.config/duh.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

import duh.auth  # noqa: F401  (ensure package import is covered)
from duh.auth import anthropic as anth_mod
from duh.auth import store as store_mod


@pytest.fixture
def fake_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect config_dir() used by store.py to a tmp directory."""
    target = tmp_path / "duh"
    monkeypatch.setattr(store_mod, "config_dir", lambda: target)
    return target


# ---------------------------------------------------------------------------
# _auth_path
# ---------------------------------------------------------------------------


def test_auth_path_returns_config_dir_auth_json(fake_config_dir: Path) -> None:
    assert store_mod._auth_path() == fake_config_dir / "auth.json"


# ---------------------------------------------------------------------------
# _load_store
# ---------------------------------------------------------------------------


def test_load_store_missing_file(fake_config_dir: Path) -> None:
    # No file created — FileNotFoundError branch.
    assert store_mod._load_store() == {}


def test_load_store_empty_file(fake_config_dir: Path) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    (fake_config_dir / "auth.json").write_text("", encoding="utf-8")
    # Empty string triggers JSONDecodeError → generic Exception branch.
    assert store_mod._load_store() == {}


def test_load_store_invalid_json(fake_config_dir: Path) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    (fake_config_dir / "auth.json").write_text("{not json", encoding="utf-8")
    assert store_mod._load_store() == {}


def test_load_store_valid_dict(fake_config_dir: Path) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    payload = {"providers": {"anthropic": {"api_key": "sk-xxx"}}}
    (fake_config_dir / "auth.json").write_text(json.dumps(payload), encoding="utf-8")
    assert store_mod._load_store() == payload


def test_load_store_non_dict_root(fake_config_dir: Path) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    # Valid JSON but the root is a list — isinstance(..., dict) is False, hits
    # the trailing `return {}`.
    (fake_config_dir / "auth.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert store_mod._load_store() == {}


# ---------------------------------------------------------------------------
# _save_store
# ---------------------------------------------------------------------------


def test_save_store_creates_parent_and_writes_json(fake_config_dir: Path) -> None:
    assert not fake_config_dir.exists()
    data = {"providers": {"anthropic": {"api_key": "sk-xxx"}}}
    store_mod._save_store(data)

    auth_file = fake_config_dir / "auth.json"
    assert auth_file.exists()
    assert json.loads(auth_file.read_text(encoding="utf-8")) == data


@pytest.mark.skipif(sys.platform == "win32", reason="chmod semantics differ on Windows")
def test_save_store_sets_0600_perms(fake_config_dir: Path) -> None:
    store_mod._save_store({"providers": {}})
    mode = (fake_config_dir / "auth.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_save_store_chmod_failure_is_swallowed(
    fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `try/except` around chmod must swallow errors silently."""

    original_chmod = Path.chmod

    def boom(self: Path, mode: int) -> None:  # pragma: no cover - signature
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "chmod", boom)
    try:
        store_mod._save_store({"providers": {"x": {}}})
    finally:
        monkeypatch.setattr(Path, "chmod", original_chmod)

    # Data should still be on disk despite chmod failing.
    assert (fake_config_dir / "auth.json").exists()


# ---------------------------------------------------------------------------
# load_provider_auth
# ---------------------------------------------------------------------------


def test_load_provider_auth_empty_store(fake_config_dir: Path) -> None:
    assert store_mod.load_provider_auth("anthropic") == {}


def test_load_provider_auth_missing_provider(fake_config_dir: Path) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    (fake_config_dir / "auth.json").write_text(
        json.dumps({"providers": {"openai": {"api_key": "k"}}}),
        encoding="utf-8",
    )
    assert store_mod.load_provider_auth("anthropic") == {}


def test_load_provider_auth_non_dict_provider_value(fake_config_dir: Path) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    (fake_config_dir / "auth.json").write_text(
        json.dumps({"providers": {"anthropic": "not-a-dict"}}),
        encoding="utf-8",
    )
    assert store_mod.load_provider_auth("anthropic") == {}


def test_load_provider_auth_non_dict_providers_root(fake_config_dir: Path) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    (fake_config_dir / "auth.json").write_text(
        json.dumps({"providers": ["oops"]}),
        encoding="utf-8",
    )
    assert store_mod.load_provider_auth("anthropic") == {}


def test_load_provider_auth_valid_value(fake_config_dir: Path) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    (fake_config_dir / "auth.json").write_text(
        json.dumps({"providers": {"anthropic": {"api_key": "sk-xxx"}}}),
        encoding="utf-8",
    )
    assert store_mod.load_provider_auth("anthropic") == {"api_key": "sk-xxx"}


# ---------------------------------------------------------------------------
# save_provider_auth
# ---------------------------------------------------------------------------


def test_save_provider_auth_empty_store(fake_config_dir: Path) -> None:
    store_mod.save_provider_auth("anthropic", {"api_key": "sk-xxx"})
    on_disk = json.loads((fake_config_dir / "auth.json").read_text(encoding="utf-8"))
    assert on_disk == {"providers": {"anthropic": {"api_key": "sk-xxx"}}}


def test_save_provider_auth_preserves_other_providers(fake_config_dir: Path) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    (fake_config_dir / "auth.json").write_text(
        json.dumps({"providers": {"openai": {"api_key": "oa"}}}),
        encoding="utf-8",
    )
    store_mod.save_provider_auth("anthropic", {"api_key": "an"})
    on_disk = json.loads((fake_config_dir / "auth.json").read_text(encoding="utf-8"))
    assert on_disk == {
        "providers": {
            "openai": {"api_key": "oa"},
            "anthropic": {"api_key": "an"},
        }
    }


def test_save_provider_auth_replaces_existing(fake_config_dir: Path) -> None:
    store_mod.save_provider_auth("anthropic", {"api_key": "old"})
    store_mod.save_provider_auth("anthropic", {"api_key": "new"})
    on_disk = json.loads((fake_config_dir / "auth.json").read_text(encoding="utf-8"))
    assert on_disk == {"providers": {"anthropic": {"api_key": "new"}}}


def test_save_provider_auth_resets_non_dict_providers_root(
    fake_config_dir: Path,
) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    # Root exists but `providers` is not a dict — must be reset.
    (fake_config_dir / "auth.json").write_text(
        json.dumps({"providers": ["broken"]}),
        encoding="utf-8",
    )
    store_mod.save_provider_auth("anthropic", {"api_key": "sk"})
    on_disk = json.loads((fake_config_dir / "auth.json").read_text(encoding="utf-8"))
    assert on_disk == {"providers": {"anthropic": {"api_key": "sk"}}}


# ---------------------------------------------------------------------------
# connect_anthropic_api_key
# ---------------------------------------------------------------------------


def test_connect_anthropic_api_key_empty_input_fails(fake_config_dir: Path) -> None:
    ok, msg = anth_mod.connect_anthropic_api_key(input_fn=lambda _prompt: "   ")
    assert ok is False
    assert "No key" in msg
    # Nothing written.
    assert not (fake_config_dir / "auth.json").exists()


def test_connect_anthropic_api_key_valid_input_saves(fake_config_dir: Path) -> None:
    ok, msg = anth_mod.connect_anthropic_api_key(input_fn=lambda _prompt: "  sk-xyz  ")
    assert ok is True
    assert "saved" in msg.lower()
    on_disk = json.loads((fake_config_dir / "auth.json").read_text(encoding="utf-8"))
    assert on_disk == {"providers": {"anthropic": {"api_key": "sk-xyz"}}}


def test_connect_anthropic_api_key_resets_broken_provider_value(
    fake_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If load_provider_auth returns something non-dict-ish, the function
    should still store a clean dict. load_provider_auth normally coerces
    non-dicts to {}, so we stub it here to return a string and exercise
    the `if not isinstance(provider, dict)` branch inside the function."""

    monkeypatch.setattr(
        anth_mod, "load_provider_auth", lambda _p: "not-a-dict"  # type: ignore[return-value]
    )
    ok, msg = anth_mod.connect_anthropic_api_key(input_fn=lambda _prompt: "sk-xyz")
    assert ok is True
    assert "saved" in msg.lower()
    on_disk = json.loads((fake_config_dir / "auth.json").read_text(encoding="utf-8"))
    assert on_disk == {"providers": {"anthropic": {"api_key": "sk-xyz"}}}


# ---------------------------------------------------------------------------
# get_saved_anthropic_api_key
# ---------------------------------------------------------------------------


def test_get_saved_anthropic_api_key_empty_store(fake_config_dir: Path) -> None:
    assert anth_mod.get_saved_anthropic_api_key() == ""


def test_get_saved_anthropic_api_key_non_string_value(
    fake_config_dir: Path,
) -> None:
    fake_config_dir.mkdir(parents=True, exist_ok=True)
    (fake_config_dir / "auth.json").write_text(
        json.dumps({"providers": {"anthropic": {"api_key": 12345}}}),
        encoding="utf-8",
    )
    assert anth_mod.get_saved_anthropic_api_key() == ""


def test_get_saved_anthropic_api_key_valid_value(fake_config_dir: Path) -> None:
    store_mod.save_provider_auth("anthropic", {"api_key": "sk-xyz"})
    assert anth_mod.get_saved_anthropic_api_key() == "sk-xyz"


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_auth_package_importable() -> None:
    import duh.auth as pkg

    assert pkg.__doc__ is not None


# Silence unused-import warning from pytest collection
_ = stat
