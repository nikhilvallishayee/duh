"""TOFU (Trust On First Use) store for plugin signatures (ADR-054, 7.7)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

__all__ = ["TrustStore", "VerifyResult"]


@dataclass
class VerifyResult:
    """Result of a trust-store verification check."""

    status: str  # "trusted", "first_use", "revoked", "signature_mismatch"
    known: str = ""
    provided: str = ""
    reason: str = ""


class TrustStore:
    """Persists known plugin signature hashes with TOFU semantics.

    On first encounter of a plugin, ``verify`` returns ``"first_use"``.
    After ``add``, subsequent calls with the same hash return ``"trusted"``.
    Changed hashes return ``"signature_mismatch"``.
    Revoked entries return ``"revoked"``.
    """

    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._entries: dict[str, dict] = {}
        if self._path.exists():
            self._entries = json.loads(self._path.read_text())

    def verify(self, plugin_name: str, sig_hash: str) -> VerifyResult:
        """Check the trust status of a plugin."""
        entry = self._entries.get(plugin_name)
        if entry is None:
            return VerifyResult(status="first_use")
        if entry.get("revoked"):
            return VerifyResult(
                status="revoked", reason=entry.get("revoke_reason", "")
            )
        if entry["sig_hash"] != sig_hash:
            return VerifyResult(
                status="signature_mismatch",
                known=entry["sig_hash"],
                provided=sig_hash,
            )
        return VerifyResult(status="trusted")

    def add(self, plugin_name: str, sig_hash: str) -> None:
        """Trust a plugin with the given signature hash (TOFU first-use)."""
        self._entries[plugin_name] = {
            "sig_hash": sig_hash,
            "revoked": False,
            "revoke_reason": "",
        }
        self.save()

    def revoke(self, plugin_name: str, *, reason: str = "") -> None:
        """Mark a plugin as revoked (key compromise etc.)."""
        if plugin_name in self._entries:
            self._entries[plugin_name]["revoked"] = True
            self._entries[plugin_name]["revoke_reason"] = reason
            self.save()

    def save(self) -> None:
        """Persist the trust store to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._entries, indent=2))
