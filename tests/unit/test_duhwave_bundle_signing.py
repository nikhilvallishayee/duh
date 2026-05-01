"""Tests for Ed25519 bundle signing — ``duh.duhwave.bundle.signing``.

These exercise the real ``cryptography`` package; if the dep is
missing the suite skips cleanly. We never stub the crypto — fake
signatures wouldn't catch the bug we care about (mismatch between
sign and verify code paths).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip the whole module when cryptography is unavailable.
cryptography = pytest.importorskip(
    "cryptography",
    reason="cryptography not installed — Ed25519 signing tests skipped",
)

from duh.duhwave.bundle.signing import (  # noqa: E402  (import after skip)
    BundleSignatureError,
    generate_keypair,
    sign_bundle,
    verify_bundle,
)


@pytest.fixture
def keypair(tmp_path: Path) -> tuple[Path, Path]:
    """A fresh Ed25519 keypair in tmp_path."""
    base = tmp_path / "k"
    priv, pub = generate_keypair(base)
    return priv, pub


@pytest.fixture
def bundle_file(tmp_path: Path) -> Path:
    """A small fake-bundle byte payload — signatures don't care what's inside."""
    p = tmp_path / "fixture.duhwave"
    p.write_bytes(b"PK\x03\x04" + b"pretend this is a zip" * 20)
    return p


# ---------------------------------------------------------------------------
# generate_keypair
# ---------------------------------------------------------------------------


class TestGenerateKeypair:
    def test_writes_priv_and_pub_pem_files(self, tmp_path: Path):
        priv, pub = generate_keypair(tmp_path / "mykey")
        assert priv.exists()
        assert pub.exists()
        assert priv.suffix == ".priv"
        assert pub.suffix == ".pub"
        # Both should be PEM-encoded.
        assert b"BEGIN PRIVATE KEY" in priv.read_bytes()
        assert b"BEGIN PUBLIC KEY" in pub.read_bytes()

    def test_rejects_missing_parent_dir(self, tmp_path: Path):
        bad = tmp_path / "no" / "such" / "dir" / "k"
        with pytest.raises(BundleSignatureError, match="parent directory"):
            generate_keypair(bad)

    def test_priv_file_is_chmod_600(self, tmp_path: Path):
        priv, _ = generate_keypair(tmp_path / "mykey")
        # Owner-only on POSIX.
        mode = priv.stat().st_mode & 0o777
        assert mode == 0o600


# ---------------------------------------------------------------------------
# sign_bundle / verify_bundle round-trip
# ---------------------------------------------------------------------------


class TestSignAndVerify:
    def test_sign_writes_64_byte_sig(self, bundle_file: Path, keypair):
        priv, _pub = keypair
        sig_path = sign_bundle(bundle_file, priv)
        assert sig_path == bundle_file.with_name(bundle_file.name + ".sig")
        assert sig_path.exists()
        assert sig_path.stat().st_size == 64

    def test_verify_returns_true_for_legit_signature(
        self, bundle_file: Path, keypair
    ):
        priv, pub = keypair
        sign_bundle(bundle_file, priv)
        assert verify_bundle(bundle_file, pub) is True

    def test_verify_returns_false_when_bytes_tampered(
        self, bundle_file: Path, keypair
    ):
        priv, pub = keypair
        sign_bundle(bundle_file, priv)
        # Mutate one byte inside the bundle.
        data = bytearray(bundle_file.read_bytes())
        data[10] ^= 0xFF
        bundle_file.write_bytes(bytes(data))
        # Bytes-mismatch returns False (does not raise).
        assert verify_bundle(bundle_file, pub) is False


# ---------------------------------------------------------------------------
# verify_bundle — error paths
# ---------------------------------------------------------------------------


class TestVerifyErrors:
    def test_missing_sig_file_raises(self, bundle_file: Path, keypair):
        _priv, pub = keypair
        # Never signed this bundle, so .sig is absent.
        with pytest.raises(BundleSignatureError, match="signature not found"):
            verify_bundle(bundle_file, pub)

    def test_malformed_sig_file_raises(self, bundle_file: Path, keypair):
        priv, pub = keypair
        sign_bundle(bundle_file, priv)
        sig_path = bundle_file.with_name(bundle_file.name + ".sig")
        # Truncate to the wrong size.
        sig_path.write_bytes(b"\x00" * 16)
        with pytest.raises(BundleSignatureError, match="not 64 bytes"):
            verify_bundle(bundle_file, pub)

    def test_missing_bundle_raises(self, tmp_path: Path, keypair):
        _priv, pub = keypair
        with pytest.raises(BundleSignatureError, match="bundle not found"):
            verify_bundle(tmp_path / "ghost.duhwave", pub)
