"""``.duhwave`` bundle format — ADR-032 §B.

A ZIP archive containing a swarm topology + skills + prompts +
permissions manifest, optionally signed (Ed25519). Installed bundles
land in ``~/.duh/waves/<name>/`` with isolated state.

Public surface:

- :class:`BundleManifest` / :class:`BundlePermissions` — parsed TOML files.
- :func:`pack_bundle` — directory → ``.duhwave`` ZIP.
- :func:`sign_bundle` / :func:`verify_bundle` — Ed25519 detached sigs.
- :class:`BundleInstaller` / :class:`InstallResult` — install/uninstall/list.
- :class:`BundleSignatureError` — raised on signature trouble.
"""
from __future__ import annotations

__all__ = [
    "BUNDLE_EXT",
    "BUNDLE_FORMAT_VERSION",
    "BundleManifest",
    "BundlePermissions",
    "BundleInstaller",
    "InstallResult",
    "pack_bundle",
    "sign_bundle",
    "verify_bundle",
    "BundleSignatureError",
]


BUNDLE_EXT = ".duhwave"
BUNDLE_FORMAT_VERSION = 1


# Imports kept at module bottom so the constants above are visible to
# the submodules during their own import (they reference BUNDLE_EXT /
# BUNDLE_FORMAT_VERSION via ``from . import …``).
from .manifest import BundleManifest  # noqa: E402
from .permissions import BundlePermissions  # noqa: E402
from .signing import (  # noqa: E402
    BundleSignatureError,
    sign_bundle,
    verify_bundle,
)
from .packer import pack_bundle  # noqa: E402
from .installer import BundleInstaller, InstallResult  # noqa: E402
