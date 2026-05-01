"""Bundle manifest — ADR-032 §B.

Identity for a ``.duhwave`` bundle. Stored as ``manifest.toml`` at the
bundle root. The manifest carries enough to identify the bundle, name
its author, and describe whether it carries a signature; signature
*bytes* live in the detached ``.sig`` file alongside the bundle.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

from . import BUNDLE_FORMAT_VERSION


@dataclass(slots=True)
class BundleManifest:
    """Identity record for one ``.duhwave`` bundle."""

    name: str
    version: str
    description: str = ""
    author: str = ""
    format_version: int = BUNDLE_FORMAT_VERSION
    signed: bool = False
    signing_key_id: str | None = None
    created_at: float = field(default_factory=time.time)

    @classmethod
    def from_toml(cls, path: Path | str) -> BundleManifest:
        """Read a ``manifest.toml`` file. Raises :class:`ManifestError`."""
        p = Path(path)
        try:
            with p.open("rb") as f:
                raw = tomllib.load(f)
        except FileNotFoundError as e:
            raise ManifestError(f"manifest not found: {p}") from e
        except OSError as e:
            raise ManifestError(f"manifest unreadable: {p}: {e}") from e

        bundle = raw.get("bundle")
        if not isinstance(bundle, dict):
            raise ManifestError("manifest missing [bundle] section")

        try:
            name = str(bundle["name"])
            version = str(bundle["version"])
        except KeyError as e:
            raise ManifestError(f"manifest [bundle] missing required key: {e.args[0]}") from e

        signing = raw.get("signing", {})
        signed = bool(signing.get("signed", False))
        signing_key_id = signing.get("public_key_id")
        if signing_key_id is not None:
            signing_key_id = str(signing_key_id)

        return cls(
            name=name,
            version=version,
            description=str(bundle.get("description", "")),
            author=str(bundle.get("author", "")),
            format_version=int(bundle.get("format_version", BUNDLE_FORMAT_VERSION)),
            signed=signed,
            signing_key_id=signing_key_id,
            created_at=float(bundle.get("created_at", time.time())),
        )

    def to_toml(self) -> str:
        """Serialise to a TOML string (deterministic key order)."""
        # Hand-rolled — stdlib has no tomllib *writer* and we keep
        # determinism so signed bundles round-trip.
        lines: list[str] = []
        lines.append("[bundle]")
        lines.append(f'name = "{_esc(self.name)}"')
        lines.append(f'version = "{_esc(self.version)}"')
        lines.append(f'description = "{_esc(self.description)}"')
        lines.append(f'author = "{_esc(self.author)}"')
        lines.append(f"format_version = {int(self.format_version)}")
        lines.append(f"created_at = {float(self.created_at)}")
        lines.append("")
        lines.append("[signing]")
        lines.append(f"signed = {'true' if self.signed else 'false'}")
        if self.signing_key_id is not None:
            lines.append(f'public_key_id = "{_esc(self.signing_key_id)}"')
        lines.append("")
        return "\n".join(lines)


class ManifestError(ValueError):
    """``manifest.toml`` failed to parse or was missing required keys."""


def _esc(s: str) -> str:
    """Minimal TOML basic-string escape (newlines + quotes + backslashes)."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
