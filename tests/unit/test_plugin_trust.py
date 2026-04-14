"""Tests for TOFU trust store and plugin verification (ADR-054, 7.7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from duh.plugins.trust_store import TrustStore, VerifyResult


@pytest.fixture()
def store(tmp_path) -> TrustStore:
    return TrustStore(store_path=tmp_path / "trust.json")


# ---------------------------------------------------------------------------
# Task 7.7.3: TrustStore TOFU semantics
# ---------------------------------------------------------------------------

def test_first_use_returns_first_use(store) -> None:
    result = store.verify("test-plugin", "sig-hash-abc")
    assert result.status == "first_use"


def test_after_add_returns_trusted(store) -> None:
    store.add("test-plugin", "sig-hash-abc")
    result = store.verify("test-plugin", "sig-hash-abc")
    assert result.status == "trusted"


def test_different_sig_returns_mismatch(store) -> None:
    store.add("test-plugin", "sig-hash-abc")
    result = store.verify("test-plugin", "sig-hash-DIFFERENT")
    assert result.status == "signature_mismatch"
    assert result.known == "sig-hash-abc"
    assert result.provided == "sig-hash-DIFFERENT"


def test_revoked_plugin(store) -> None:
    store.add("test-plugin", "sig-hash-abc")
    store.revoke("test-plugin", reason="compromised key")
    result = store.verify("test-plugin", "sig-hash-abc")
    assert result.status == "revoked"
    assert "compromised" in result.reason


def test_store_persists_to_disk(tmp_path) -> None:
    store_path = tmp_path / "trust.json"
    s1 = TrustStore(store_path=store_path)
    s1.add("plugin-a", "hash-1")
    s1.save()

    s2 = TrustStore(store_path=store_path)
    result = s2.verify("plugin-a", "hash-1")
    assert result.status == "trusted"


# ---------------------------------------------------------------------------
# Task 7.7.6: load_verified_plugin — first use flow
# ---------------------------------------------------------------------------

from duh.plugins import load_verified_plugin


def test_load_verified_plugin_first_use_accepted(store, tmp_path) -> None:
    """First use with user confirmation adds to trust store."""
    manifest_data = {
        "plugin_name": "new-plugin",
        "version": "1.0.0",
        "author": "alice@example.com",
        "capabilities": {"hook_events": [], "can_observe_tools": False,
                         "fs_read_paths": [], "fs_write_paths": [],
                         "network_egress": False},
        "signature": {"method": "none", "bundle_b64": ""},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data))

    result = load_verified_plugin(manifest_path, store, confirm_tofu=lambda _: True)
    assert result.plugin_name == "new-plugin"
    # Now trusted
    assert store.verify("new-plugin", result._sig_hash).status == "trusted"


# ---------------------------------------------------------------------------
# Task 7.7.7: Refuse plugin on TOFU rejection
# ---------------------------------------------------------------------------

from duh.plugins import PluginError


def test_load_verified_plugin_first_use_rejected(store, tmp_path) -> None:
    manifest_data = {
        "plugin_name": "suspicious-plugin",
        "version": "1.0.0",
        "author": "evil@example.com",
        "capabilities": {"hook_events": [], "can_observe_tools": False,
                         "fs_read_paths": [], "fs_write_paths": [],
                         "network_egress": True},
        "signature": {"method": "none", "bundle_b64": ""},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data))

    with pytest.raises(PluginError, match="refused TOFU"):
        load_verified_plugin(manifest_path, store, confirm_tofu=lambda _: False)


# ---------------------------------------------------------------------------
# Task 7.7.8: Refuse plugin on signature mismatch
# ---------------------------------------------------------------------------

def test_load_verified_plugin_signature_mismatch(store, tmp_path) -> None:
    # First, trust with one hash
    store.add("tampered-plugin", "original-hash")

    # Now try to load with a different manifest (different hash)
    manifest_data = {
        "plugin_name": "tampered-plugin",
        "version": "1.0.0-TAMPERED",
        "author": "alice@example.com",
        "capabilities": {"hook_events": [], "can_observe_tools": False,
                         "fs_read_paths": [], "fs_write_paths": [],
                         "network_egress": False},
        "signature": {"method": "none", "bundle_b64": ""},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data))

    with pytest.raises(PluginError, match="signature invalid"):
        load_verified_plugin(manifest_path, store, confirm_tofu=lambda _: True)


# ---------------------------------------------------------------------------
# Task 7.7.9: Refuse plugin on revocation
# ---------------------------------------------------------------------------

def test_load_verified_plugin_revoked(store, tmp_path) -> None:
    manifest_data = {
        "plugin_name": "revoked-plugin",
        "version": "1.0.0",
        "author": "alice@example.com",
        "capabilities": {"hook_events": [], "can_observe_tools": False,
                         "fs_read_paths": [], "fs_write_paths": [],
                         "network_egress": False},
        "signature": {"method": "none", "bundle_b64": ""},
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_data))

    # Load once to trust
    load_verified_plugin(manifest_path, store, confirm_tofu=lambda _: True)

    # Revoke
    store.revoke("revoked-plugin", reason="key compromised")

    # Reload should fail
    with pytest.raises(PluginError, match="revoked"):
        load_verified_plugin(manifest_path, store, confirm_tofu=lambda _: True)


# ---------------------------------------------------------------------------
# Task 7.7.10: Refuse plugin without manifest.json
# ---------------------------------------------------------------------------

def test_load_verified_plugin_no_manifest(store, tmp_path) -> None:
    missing_path = tmp_path / "nonexistent" / "manifest.json"
    with pytest.raises(FileNotFoundError):
        load_verified_plugin(missing_path, store, confirm_tofu=lambda _: True)
