"""Pack a project directory into a ``.duhwave`` ZIP — ADR-032 §B.

Bundles are *deterministic* ZIPs: sorted entries and a fixed mtime so
re-packing identical inputs produces byte-identical output, which is
what makes detached signatures meaningful.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from .manifest import BundleManifest, ManifestError
from .permissions import BundlePermissions, PermissionsError
from .signing import sign_bundle

# A fixed mtime keeps bundles reproducible. 2020-01-01 00:00:00 UTC,
# packed in zip's DOS-time tuple form. Using an obviously-not-now
# timestamp also makes it visible in `unzip -l` output.
_FIXED_MTIME = (2020, 1, 1, 0, 0, 0)

# Canonical entries that must appear in every bundle. Optional dirs
# (``skills/``, ``prompts/``) and ``README.md`` are added when present.
_REQUIRED_FILES = ("manifest.toml", "swarm.toml", "permissions.toml")
_OPTIONAL_FILES = ("README.md",)
_OPTIONAL_DIRS = ("skills", "prompts")


class BundlePackError(ValueError):
    """Source directory is missing required files or is otherwise unpackable."""


def pack_bundle(
    spec_dir: Path | str,
    out_path: Path | str,
    *,
    sign_with: Path | str | None = None,
) -> Path:
    """Pack ``spec_dir`` into a ``.duhwave`` archive at ``out_path``.

    The source directory must contain at least ``manifest.toml``,
    ``swarm.toml``, ``permissions.toml``. ``skills/``, ``prompts/`` and
    ``README.md`` are included if present. Manifest and permissions
    files are *parsed* during packing as a sanity check — a malformed
    bundle never reaches disk.

    If ``sign_with`` is given, the resulting archive is also signed with
    that Ed25519 private key; the detached ``.sig`` lands next to
    ``out_path``.

    Returns the bundle path.
    """
    src = Path(spec_dir)
    if not src.is_dir():
        raise BundlePackError(f"spec_dir is not a directory: {src}")

    for name in _REQUIRED_FILES:
        if not (src / name).is_file():
            raise BundlePackError(f"missing required file: {name}")

    # Parse-validate the structural files so a broken bundle never ships.
    try:
        BundleManifest.from_toml(src / "manifest.toml")
    except ManifestError as e:
        raise BundlePackError(f"manifest.toml invalid: {e}") from e
    try:
        BundlePermissions.from_toml(src / "permissions.toml")
    except PermissionsError as e:
        raise BundlePackError(f"permissions.toml invalid: {e}") from e

    out = Path(out_path)
    # Any extension is permitted — convention is .duhwave but we don't refuse.
    out.parent.mkdir(parents=True, exist_ok=True)

    entries = _collect_entries(src)

    # Write deterministically: sorted by archive path, fixed mtime,
    # store mode (no compression-time variability across zlib versions).
    # We do still use ZIP_DEFLATED for size — deflate is deterministic
    # given the same input bytes and level.
    with zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for arc_name, abs_path in entries:
            info = zipfile.ZipInfo(filename=arc_name, date_time=_FIXED_MTIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            # 0644 file mode in the high bits; rest left at zero.
            info.external_attr = (0o644 & 0xFFFF) << 16
            with abs_path.open("rb") as f:
                zf.writestr(info, f.read())

    if sign_with is not None:
        sign_bundle(out, sign_with)

    return out


def _collect_entries(src: Path) -> list[tuple[str, Path]]:
    """Return sorted (archive-path, absolute-path) pairs for a bundle source."""
    entries: list[tuple[str, Path]] = []

    for name in _REQUIRED_FILES:
        entries.append((name, src / name))

    for name in _OPTIONAL_FILES:
        p = src / name
        if p.is_file():
            entries.append((name, p))

    for d in _OPTIONAL_DIRS:
        dpath = src / d
        if not dpath.is_dir():
            continue
        for f in sorted(dpath.rglob("*")):
            if f.is_file():
                arc = f.relative_to(src).as_posix()
                entries.append((arc, f))

    # Final sort by archive path to make ordering independent of disk
    # walk order.
    entries.sort(key=lambda kv: kv[0])
    return entries


__all__ = ["BundlePackError", "pack_bundle"]
