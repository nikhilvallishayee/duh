"""Bundle installer — ADR-032 §B.

Installs ``.duhwave`` bundles into ``~/.duh/waves/<name>/<version>/``,
with a top-level ``index.json`` listing what's installed. Verifies
detached signatures, surfaces a permissions diff against any prior
version, and downgrades unsigned bundles to "ask every time".

The install root defaults to ``~/.duh/waves/`` but the constructor
accepts an override so tests (and the smoke script) can install into a
tempdir without touching the user's home.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ..spec.parser import SwarmSpecError, parse_swarm
from . import BUNDLE_EXT
from .manifest import BundleManifest, ManifestError
from .permissions import BundlePermissions, PermissionsError
from .signing import BundleSignatureError, verify_bundle

logger = logging.getLogger(__name__)


# Trust levels surfaced to the caller / kernel permission gate.
TRUST_TRUSTED = "trusted"        # signed + verified against provided pubkey
TRUST_UNTRUSTED = "untrusted"    # signed but no pubkey provided to verify
TRUST_UNSIGNED = "unsigned"      # no .sig at all → ask-every-time mode


# Bundle layout invariants from ADR-032 §B.
_REQUIRED_ENTRIES = frozenset({"manifest.toml", "swarm.toml", "permissions.toml"})
_KNOWN_ROOTS = frozenset({"manifest.toml", "swarm.toml", "permissions.toml", "README.md"})
_KNOWN_DIRS = frozenset({"skills/", "prompts/"})


@dataclass(slots=True)
class InstallResult:
    """Outcome of one ``BundleInstaller.install`` call."""

    name: str
    version: str
    path: str
    trust_level: str
    permissions_changed: bool
    signed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class BundleInstallError(Exception):
    """Install failed (bad layout, signature mismatch, refused upgrade)."""


# A confirmation callback returns True to proceed, False to abort. The
# default is "yes" — interactive prompting is the CLI's job, not the
# installer's. Tests pass `lambda _: True`.
ConfirmFn = Callable[[str], bool]


def _default_confirm(_diff: str) -> bool:  # pragma: no cover - trivial
    return True


class BundleInstaller:
    """Install / uninstall / list ``.duhwave`` bundles under a root dir."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        confirm: ConfirmFn | None = None,
    ) -> None:
        if root is None:
            root = Path.home() / ".duh" / "waves"
        self.root = Path(root)
        self.confirm = confirm or _default_confirm
        self.index_path = self.root / "index.json"

    # ── public API ───────────────────────────────────────────────────

    def install(
        self,
        bundle_path: Path | str,
        *,
        public_key_path: Path | str | None = None,
        force: bool = False,
    ) -> InstallResult:
        """Install one bundle. See module docstring for the full flow."""
        bundle = Path(bundle_path)
        if not bundle.is_file():
            raise BundleInstallError(f"bundle not found: {bundle}")

        # 1. Validate layout + parse all three structural files.
        self._validate_zip_layout(bundle)
        manifest, spec_ok, permissions = self._read_structural(bundle)
        if not spec_ok:
            raise BundleInstallError("swarm.toml inside bundle failed to parse")

        # 2. Signature handling.
        sig_path = bundle.with_name(bundle.name + ".sig")
        signed = sig_path.exists()
        if signed and public_key_path is not None:
            ok = verify_bundle(bundle, public_key_path, sig_path)
            if not ok:
                raise BundleSignatureError(
                    f"bundle signature does not match provided public key: {bundle}"
                )
            trust_level = TRUST_TRUSTED
        elif signed and public_key_path is None:
            logger.warning(
                "bundle %s is signed but no public_key_path provided; treating as untrusted",
                bundle.name,
            )
            trust_level = TRUST_UNTRUSTED
        else:
            logger.warning(
                "bundle %s is unsigned; permissions downgraded to ask-every-time",
                bundle.name,
            )
            trust_level = TRUST_UNSIGNED

        # 3. Permissions diff vs. prior install of same name.
        prior = self._lookup_prior(manifest.name)
        if prior is not None:
            try:
                prior_perms = BundlePermissions.from_toml(
                    Path(prior["path"]) / "permissions.toml"
                )
            except (PermissionsError, FileNotFoundError):
                prior_perms = BundlePermissions()
            diff = permissions.diff(prior_perms)
        else:
            diff = permissions.diff(BundlePermissions())

        permissions_changed = bool(diff)

        if permissions_changed and not force:
            if not self.confirm(diff):
                raise BundleInstallError("install aborted: permissions diff not approved")

        # 4. Extract to <root>/<name>/<version>/.
        target_dir = self.root / manifest.name / manifest.version
        if target_dir.exists():
            if not force:
                raise BundleInstallError(
                    f"already installed: {manifest.name} {manifest.version} "
                    f"(use force=True to replace)"
                )
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Safe extraction: refuse path-traversal entries.
        with zipfile.ZipFile(bundle) as zf:
            for info in zf.infolist():
                self._safe_extract(zf, info, target_dir)

        # 5. Update the install index.
        self._update_index(
            InstallResult(
                name=manifest.name,
                version=manifest.version,
                path=str(target_dir),
                trust_level=trust_level,
                permissions_changed=permissions_changed,
                signed=signed,
            )
        )

        return InstallResult(
            name=manifest.name,
            version=manifest.version,
            path=str(target_dir),
            trust_level=trust_level,
            permissions_changed=permissions_changed,
            signed=signed,
        )

    def uninstall(self, name: str) -> bool:
        """Remove the bundle dir and its index entry. Return True if found."""
        index = self._load_index()
        entry = index.get(name)
        if entry is None:
            return False
        # Remove the whole `~/.duh/waves/<name>/` tree (all versions).
        bundle_root = self.root / name
        if bundle_root.exists():
            shutil.rmtree(bundle_root)
        del index[name]
        self._write_index(index)
        return True

    def list_installed(self) -> list[InstallResult]:
        """Return installed bundles, ordered by name."""
        index = self._load_index()
        out: list[InstallResult] = []
        for name in sorted(index.keys()):
            entry = index[name]
            out.append(
                InstallResult(
                    name=entry["name"],
                    version=entry["version"],
                    path=entry["path"],
                    trust_level=entry.get("trust_level", TRUST_UNSIGNED),
                    permissions_changed=bool(entry.get("permissions_changed", False)),
                    signed=bool(entry.get("signed", False)),
                )
            )
        return out

    # ── internals ────────────────────────────────────────────────────

    def _validate_zip_layout(self, bundle: Path) -> None:
        try:
            with zipfile.ZipFile(bundle) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile as e:
            raise BundleInstallError(f"not a valid ZIP: {bundle}") from e

        missing = _REQUIRED_ENTRIES - names
        if missing:
            raise BundleInstallError(
                f"bundle missing required entries: {sorted(missing)}"
            )

        # Reject unexpected top-level entries — keeps malicious bundles
        # honest. Subpaths under known dirs are fine.
        for n in names:
            if n in _KNOWN_ROOTS:
                continue
            if any(n.startswith(d) for d in _KNOWN_DIRS):
                continue
            # Allow directory entries themselves.
            if n.endswith("/"):
                continue
            raise BundleInstallError(f"bundle has unexpected entry: {n}")

    def _read_structural(
        self, bundle: Path
    ) -> tuple[BundleManifest, bool, BundlePermissions]:
        """Extract the three TOML files to a tempdir and parse them."""
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            with zipfile.ZipFile(bundle) as zf:
                for name in _REQUIRED_ENTRIES:
                    zf.extract(name, tdp)
            try:
                manifest = BundleManifest.from_toml(tdp / "manifest.toml")
            except ManifestError as e:
                raise BundleInstallError(f"manifest.toml: {e}") from e
            try:
                permissions = BundlePermissions.from_toml(tdp / "permissions.toml")
            except PermissionsError as e:
                raise BundleInstallError(f"permissions.toml: {e}") from e
            try:
                parse_swarm(tdp / "swarm.toml")
                spec_ok = True
            except SwarmSpecError as e:
                raise BundleInstallError(f"swarm.toml: {e}") from e
        return manifest, spec_ok, permissions

    def _safe_extract(
        self,
        zf: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        target_dir: Path,
    ) -> None:
        """Extract one zip entry, refusing any path-traversal."""
        # Normalise: forbid absolute paths and any ``..`` segment.
        name = info.filename
        if name.startswith("/") or os.path.isabs(name):
            raise BundleInstallError(f"bundle has absolute path entry: {name}")
        norm = os.path.normpath(name)
        if norm.startswith("..") or "/../" in norm.replace(os.sep, "/"):
            raise BundleInstallError(f"bundle has path-traversal entry: {name}")
        zf.extract(info, target_dir)

    def _lookup_prior(self, name: str) -> dict | None:
        return self._load_index().get(name)

    def _load_index(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text())
        except json.JSONDecodeError:
            logger.warning("index.json corrupt; treating as empty")
            return {}

    def _write_index(self, index: dict[str, dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(index, indent=2, sort_keys=True))

    def _update_index(self, result: InstallResult) -> None:
        index = self._load_index()
        index[result.name] = {
            **result.to_dict(),
            "installed_at": time.time(),
        }
        self._write_index(index)


__all__ = [
    "BUNDLE_EXT",
    "BundleInstaller",
    "BundleInstallError",
    "InstallResult",
    "TRUST_TRUSTED",
    "TRUST_UNTRUSTED",
    "TRUST_UNSIGNED",
]
