"""Plugin manifest parsing and validation (ADR-054, 7.7)."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "PluginCapabilities",
    "PluginManifest",
    "ManifestError",
    "load_manifest",
    "compute_manifest_hash",
    "verify_signature",
]


class ManifestError(ValueError):
    """Raised when a plugin manifest is invalid."""


@dataclass(frozen=True)
class PluginCapabilities:
    """Declared capabilities of a plugin."""

    hook_events: list[str] = field(default_factory=list)
    can_observe_tools: bool = False
    fs_read_paths: list[str] = field(default_factory=list)
    fs_write_paths: list[str] = field(default_factory=list)
    network_egress: bool = False


@dataclass(frozen=True)
class PluginManifest:
    """Parsed and validated plugin manifest."""

    plugin_name: str
    version: str
    author: str
    capabilities: PluginCapabilities
    signature_method: str = "none"
    signature_bundle: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "PluginManifest":
        """Parse a manifest from a raw dict."""
        caps_data = data.get("capabilities", {})
        caps = PluginCapabilities(
            hook_events=caps_data.get("hook_events", []),
            can_observe_tools=caps_data.get("can_observe_tools", False),
            fs_read_paths=caps_data.get("fs_read_paths", []),
            fs_write_paths=caps_data.get("fs_write_paths", []),
            network_egress=caps_data.get("network_egress", False),
        )
        sig = data.get("signature", {})
        return cls(
            plugin_name=data["plugin_name"],
            version=data["version"],
            author=data["author"],
            capabilities=caps,
            signature_method=sig.get("method", "none"),
            signature_bundle=sig.get("bundle_b64", ""),
        )


def load_manifest(path: Path) -> PluginManifest:
    """Load a plugin manifest from a JSON file.

    Raises:
        FileNotFoundError: If the manifest file does not exist.
        ManifestError: If the manifest is invalid JSON or missing required fields.
    """
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Invalid JSON in manifest {path}: {exc}") from exc
    try:
        return PluginManifest.from_dict(data)
    except KeyError as exc:
        raise ManifestError(f"Manifest {path} missing required field: {exc}") from exc


def compute_manifest_hash(data: dict) -> str:
    """SHA-256 of the JSON-serialized manifest (sorted keys, no whitespace).

    Returns the hex digest (64 characters).
    """
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def verify_signature(method: str, bundle_b64: str, payload: bytes) -> bool:
    """Verify a manifest signature.

    Returns True if the signature is valid (or method is "none").
    Returns False if verification fails or the library is unavailable.
    """
    if method == "none":
        return True
    if method == "sigstore":
        try:
            from sigstore.verify import Verifier  # type: ignore[import]

            bundle_bytes = base64.b64decode(bundle_b64)
            verifier = Verifier.production()
            verifier.verify_artifact(payload, bundle_bytes)
            return True
        except ImportError:
            # sigstore-python not installed — cannot verify
            return False
        except Exception:
            return False
    return False
