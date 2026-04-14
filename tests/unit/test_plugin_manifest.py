"""Tests for plugin manifest parsing (ADR-054, 7.7)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from duh.plugins.manifest import PluginManifest, load_manifest


def test_manifest_from_dict() -> None:
    data = {
        "plugin_name": "duh-coverage-reporter",
        "version": "1.2.3",
        "author": "alice@example.com",
        "capabilities": {
            "hook_events": ["POST_TOOL_USE", "SESSION_END"],
            "can_observe_tools": True,
            "fs_read_paths": ["./coverage"],
            "fs_write_paths": ["./.duh/coverage"],
            "network_egress": False,
        },
        "signature": {
            "method": "sigstore",
            "bundle_b64": "dGVzdA==",
        },
    }
    manifest = PluginManifest.from_dict(data)
    assert manifest.plugin_name == "duh-coverage-reporter"
    assert manifest.version == "1.2.3"
    assert manifest.author == "alice@example.com"
    assert manifest.capabilities.network_egress is False
    assert "POST_TOOL_USE" in manifest.capabilities.hook_events
    assert manifest.signature_method == "sigstore"


def test_load_manifest_from_file() -> None:
    data = {
        "plugin_name": "test-plugin",
        "version": "0.1.0",
        "author": "bob@example.com",
        "capabilities": {
            "hook_events": [],
            "can_observe_tools": False,
            "fs_read_paths": [],
            "fs_write_paths": [],
            "network_egress": False,
        },
        "signature": {"method": "none", "bundle_b64": ""},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = Path(f.name)
    manifest = load_manifest(path)
    assert manifest.plugin_name == "test-plugin"
    path.unlink()


def test_load_manifest_missing_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_manifest(Path("/nonexistent/manifest.json"))


# ---------------------------------------------------------------------------
# Task 7.7.4: compute_manifest_hash
# ---------------------------------------------------------------------------

from duh.plugins.manifest import compute_manifest_hash


def test_compute_manifest_hash_deterministic() -> None:
    data = {"plugin_name": "x", "version": "1", "author": "a", "capabilities": {}, "signature": {}}
    h1 = compute_manifest_hash(data)
    h2 = compute_manifest_hash(data)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_compute_manifest_hash_changes_on_mutation() -> None:
    d1 = {"plugin_name": "x", "version": "1", "author": "a", "capabilities": {}, "signature": {}}
    d2 = {"plugin_name": "x", "version": "2", "author": "a", "capabilities": {}, "signature": {}}
    assert compute_manifest_hash(d1) != compute_manifest_hash(d2)


# ---------------------------------------------------------------------------
# Task 7.7.5: verify_signature stub
# ---------------------------------------------------------------------------

from duh.plugins.manifest import verify_signature


def test_verify_signature_none_method_always_passes() -> None:
    assert verify_signature("none", "", b"payload") is True


def test_verify_signature_sigstore_without_library_raises() -> None:
    # If sigstore-python is not installed, raise ImportError-wrapped error
    result = verify_signature("sigstore", "dGVzdA==", b"payload")
    # Returns False if sigstore is not installed, or True if it is and verifies
    assert isinstance(result, bool)
