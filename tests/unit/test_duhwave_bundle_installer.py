"""End-to-end tests for ``duh.duhwave.bundle.installer.BundleInstaller``.

We pack a tiny but real bundle in ``tmp_path`` and exercise the full
install / list / uninstall lifecycle. Every test runs against an
isolated ``waves_root`` so nothing touches the user's home.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from duh.duhwave.bundle import (
    BUNDLE_EXT,
    BundleInstaller,
    BundleSignatureError,
    InstallResult,
    pack_bundle,
)
from duh.duhwave.bundle.installer import (
    TRUST_TRUSTED,
    TRUST_UNSIGNED,
    TRUST_UNTRUSTED,
    BundleInstallError,
)


def _build_bundle_src(src: Path, *, name: str = "demo", version: str = "0.1.0") -> Path:
    """Write a minimal but valid bundle source directory."""
    src.mkdir(parents=True, exist_ok=True)
    (src / "manifest.toml").write_text(
        f"""
[bundle]
name = "{name}"
version = "{version}"
description = "test fixture"
author = "tests"
format_version = 1
created_at = 1700000000.0

[signing]
signed = false
""".strip()
        + "\n"
    )
    (src / "swarm.toml").write_text(
        f"""
[swarm]
name = "{name}"
version = "{version}"
description = "test swarm"
format_version = 1

[[agents]]
id = "solo"
role = "researcher"
model = "sonnet"
""".strip()
        + "\n"
    )
    (src / "permissions.toml").write_text(
        """
[filesystem]
read = ["/repos/**"]

network = []

tools = ["Read"]
""".strip()
        + "\n"
    )
    return src


def _pack(tmp_path: Path, *, name: str = "demo", version: str = "0.1.0") -> Path:
    src = _build_bundle_src(tmp_path / f"src-{name}-{version}", name=name, version=version)
    out = tmp_path / f"{name}-{version}{BUNDLE_EXT}"
    return pack_bundle(src, out)


# ---------------------------------------------------------------------------
# install — happy paths
# ---------------------------------------------------------------------------


class TestInstall:
    def test_install_unsigned_bundle(self, tmp_path: Path):
        bundle = _pack(tmp_path)
        waves_root = tmp_path / "waves"
        installer = BundleInstaller(root=waves_root)

        result = installer.install(bundle, force=True)
        assert isinstance(result, InstallResult)
        assert result.name == "demo"
        assert result.version == "0.1.0"
        # Unsigned → ask-every-time downgrade.
        assert result.trust_level == TRUST_UNSIGNED
        assert result.signed is False
        assert Path(result.path).is_dir()
        # Required files extracted into the install dir.
        assert (Path(result.path) / "manifest.toml").is_file()
        assert (Path(result.path) / "swarm.toml").is_file()
        assert (Path(result.path) / "permissions.toml").is_file()

    def test_install_signed_with_pubkey_is_trusted(self, tmp_path: Path):
        cryptography = pytest.importorskip(
            "cryptography",
            reason="cryptography not installed — signed-install test skipped",
        )
        from duh.duhwave.bundle.signing import generate_keypair

        priv, pub = generate_keypair(tmp_path / "k")
        src = _build_bundle_src(tmp_path / "src-trusted")
        bundle = pack_bundle(
            src, tmp_path / f"trusted{BUNDLE_EXT}", sign_with=priv
        )
        installer = BundleInstaller(root=tmp_path / "waves")
        result = installer.install(bundle, public_key_path=pub, force=True)
        assert result.trust_level == TRUST_TRUSTED
        assert result.signed is True

    def test_install_signed_without_pubkey_is_untrusted(self, tmp_path: Path):
        cryptography = pytest.importorskip(
            "cryptography",
            reason="cryptography not installed — signed-install test skipped",
        )
        from duh.duhwave.bundle.signing import generate_keypair

        priv, _pub = generate_keypair(tmp_path / "k")
        src = _build_bundle_src(tmp_path / "src-untrusted")
        bundle = pack_bundle(
            src, tmp_path / f"untrusted{BUNDLE_EXT}", sign_with=priv
        )
        installer = BundleInstaller(root=tmp_path / "waves")
        result = installer.install(bundle, public_key_path=None, force=True)
        assert result.trust_level == TRUST_UNTRUSTED
        assert result.signed is True

    def test_tampered_signed_bundle_raises_signature_error(self, tmp_path: Path):
        cryptography = pytest.importorskip(
            "cryptography",
            reason="cryptography not installed — tampered-install test skipped",
        )
        from duh.duhwave.bundle.signing import generate_keypair

        priv, pub = generate_keypair(tmp_path / "k")
        src = _build_bundle_src(tmp_path / "src-tamper")
        bundle = pack_bundle(
            src, tmp_path / f"tamper{BUNDLE_EXT}", sign_with=priv
        )
        # Mutate one byte inside the .duhwave AFTER signing.
        data = bytearray(bundle.read_bytes())
        # Find an offset that won't break the ZIP header structure too
        # badly to read the layout — flip a byte in the deflate payload.
        # A simple flip near the end of the file is enough.
        data[-100] ^= 0xFF
        bundle.write_bytes(bytes(data))

        installer = BundleInstaller(root=tmp_path / "waves")
        with pytest.raises(BundleSignatureError):
            installer.install(bundle, public_key_path=pub, force=True)


# ---------------------------------------------------------------------------
# list_installed / uninstall
# ---------------------------------------------------------------------------


class TestListAndUninstall:
    def test_list_after_install(self, tmp_path: Path):
        bundle = _pack(tmp_path)
        waves_root = tmp_path / "waves"
        installer = BundleInstaller(root=waves_root)
        installer.install(bundle, force=True)

        listed = installer.list_installed()
        assert len(listed) == 1
        assert listed[0].name == "demo"
        assert listed[0].version == "0.1.0"

    def test_uninstall_removes_dir_and_index_entry(self, tmp_path: Path):
        bundle = _pack(tmp_path)
        waves_root = tmp_path / "waves"
        installer = BundleInstaller(root=waves_root)
        result = installer.install(bundle, force=True)

        assert installer.uninstall("demo") is True
        # Tree is gone.
        assert not Path(result.path).exists()
        assert not (waves_root / "demo").exists()
        # Index no longer references it.
        assert installer.list_installed() == []

    def test_uninstall_unknown_returns_false(self, tmp_path: Path):
        installer = BundleInstaller(root=tmp_path / "waves")
        assert installer.uninstall("never-installed") is False


# ---------------------------------------------------------------------------
# ZIP layout validation
# ---------------------------------------------------------------------------


class TestZipLayout:
    def test_missing_manifest_rejected(self, tmp_path: Path):
        # Build a malformed bundle: no manifest.toml.
        bundle = tmp_path / f"broken{BUNDLE_EXT}"
        with zipfile.ZipFile(bundle, "w") as zf:
            zf.writestr("swarm.toml", '[swarm]\nname="x"\nversion="0"\n')
            zf.writestr("permissions.toml", "")
        installer = BundleInstaller(root=tmp_path / "waves")
        with pytest.raises(BundleInstallError, match="missing required entries"):
            installer.install(bundle, force=True)

    def test_path_traversal_entry_rejected(self, tmp_path: Path):
        # Build a bundle that has the three required entries plus a
        # traversal entry. Layout validation passes (traversal entry
        # would also fail the "unexpected entry" check, so we put the
        # traversal under a known dir to bypass that).
        bundle = tmp_path / f"evil{BUNDLE_EXT}"
        with zipfile.ZipFile(bundle, "w") as zf:
            zf.writestr(
                "manifest.toml",
                '[bundle]\nname="evil"\nversion="0.1.0"\ncreated_at=0\n[signing]\nsigned=false\n',
            )
            zf.writestr(
                "swarm.toml",
                '[swarm]\nname="evil"\nversion="0.1.0"\ndescription=""\nformat_version=1\n\n[[agents]]\nid="a"\nrole="researcher"\nmodel="sonnet"\n',
            )
            zf.writestr("permissions.toml", "")
            # Path-traversal entry, dressed up as a "skill" so the
            # known-dir check accepts it past layout validation.
            zf.writestr("skills/../../../etc/passwd", "evil")

        installer = BundleInstaller(root=tmp_path / "waves")
        with pytest.raises(BundleInstallError, match="path-traversal"):
            installer.install(bundle, force=True)

    def test_unexpected_top_level_entry_rejected(self, tmp_path: Path):
        bundle = tmp_path / f"surprise{BUNDLE_EXT}"
        with zipfile.ZipFile(bundle, "w") as zf:
            zf.writestr(
                "manifest.toml",
                '[bundle]\nname="x"\nversion="0.1.0"\ncreated_at=0\n[signing]\nsigned=false\n',
            )
            zf.writestr(
                "swarm.toml",
                '[swarm]\nname="x"\nversion="0.1.0"\ndescription=""\nformat_version=1\n\n[[agents]]\nid="a"\nrole="researcher"\nmodel="sonnet"\n',
            )
            zf.writestr("permissions.toml", "")
            zf.writestr("MALWARE.exe", b"haha")

        installer = BundleInstaller(root=tmp_path / "waves")
        with pytest.raises(BundleInstallError, match="unexpected entry"):
            installer.install(bundle, force=True)

    def test_install_without_force_refuses_duplicate(self, tmp_path: Path):
        bundle = _pack(tmp_path)
        installer = BundleInstaller(root=tmp_path / "waves")
        installer.install(bundle, force=True)
        # Second install of same name+version without force → error.
        with pytest.raises(BundleInstallError, match="already installed"):
            installer.install(bundle, force=False)
