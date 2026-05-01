"""Ed25519 sign / verify for ``.duhwave`` bundles — ADR-032 §B.

Signatures are **detached**: a 64-byte raw Ed25519 signature lives in
``<bundle>.sig`` next to the ``.duhwave`` archive. Detached lets a
bundle be re-signed without rewriting its bytes.

This module import-guards :mod:`cryptography` so importing
:mod:`duh.duhwave.bundle` does not pull a hard dependency on it; the
clear failure surfaces only when sign/verify is actually invoked.
"""
from __future__ import annotations

from pathlib import Path

from . import BUNDLE_EXT


class BundleSignatureError(Exception):
    """Bundle signature verification failed, or signing inputs are invalid."""


_CRYPTOGRAPHY_HINT = (
    "duhwave signing requires the 'cryptography' package. "
    "Install with: pip install cryptography"
)


def _require_crypto() -> tuple[object, object, object, object, object, object]:
    """Lazy-import cryptography. Raise :class:`BundleSignatureError` if missing."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as e:  # pragma: no cover - exercised only when missing
        raise BundleSignatureError(_CRYPTOGRAPHY_HINT) from e
    return (
        InvalidSignature,
        serialization,
        Ed25519PrivateKey,
        Ed25519PublicKey,
        serialization.Encoding,
        serialization.PrivateFormat,
    )


def generate_keypair(path: Path | str) -> tuple[Path, Path]:
    """Write ``<path>.priv`` and ``<path>.pub`` PEM files. Return their paths.

    ``path`` is the *base* path; the suffixes ``.priv`` and ``.pub`` are
    appended. The directory must already exist.
    """
    (
        _InvalidSignature,
        serialization,
        Ed25519PrivateKey,
        _Ed25519PublicKey,
        Encoding,
        PrivateFormat,
    ) = _require_crypto()

    base = Path(path)
    parent = base.parent if str(base.parent) else Path(".")
    if not parent.exists():
        raise BundleSignatureError(f"keypair parent directory does not exist: {parent}")

    # Always append suffixes to the full base name — keeps behaviour
    # uniform whether or not the caller passed an extension.
    priv_path = parent / (base.name + ".priv")
    pub_path = parent / (base.name + ".pub")

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    priv_bytes = private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = public_key.public_bytes(
        encoding=Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path.write_bytes(priv_bytes)
    priv_path.chmod(0o600)
    pub_path.write_bytes(pub_bytes)
    return priv_path, pub_path


def _load_private_key(path: Path) -> object:
    (
        _InvalidSignature,
        serialization,
        Ed25519PrivateKey,
        _Ed25519PublicKey,
        _Encoding,
        _PrivateFormat,
    ) = _require_crypto()
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError as e:
        raise BundleSignatureError(f"private key not found: {path}") from e
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except Exception as e:  # cryptography raises a few subtypes
        raise BundleSignatureError(f"private key unreadable: {path}: {e}") from e
    if not isinstance(key, Ed25519PrivateKey):
        raise BundleSignatureError("private key is not Ed25519")
    return key


def _load_public_key(path: Path) -> object:
    (
        _InvalidSignature,
        serialization,
        _Ed25519PrivateKey,
        Ed25519PublicKey,
        _Encoding,
        _PrivateFormat,
    ) = _require_crypto()
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError as e:
        raise BundleSignatureError(f"public key not found: {path}") from e
    try:
        key = serialization.load_pem_public_key(data)
    except Exception as e:
        raise BundleSignatureError(f"public key unreadable: {path}: {e}") from e
    if not isinstance(key, Ed25519PublicKey):
        raise BundleSignatureError("public key is not Ed25519")
    return key


def sign_bundle(bundle_path: Path | str, private_key_path: Path | str) -> Path:
    """Sign ``bundle_path`` with the Ed25519 key at ``private_key_path``.

    Writes a detached 64-byte raw signature to ``<bundle_path>.sig``.
    Returns the signature path.
    """
    bundle = Path(bundle_path)
    if not bundle.exists():
        raise BundleSignatureError(f"bundle not found: {bundle}")

    private_key = _load_private_key(Path(private_key_path))

    payload = bundle.read_bytes()
    sig = private_key.sign(payload)  # type: ignore[attr-defined]
    if len(sig) != 64:
        raise BundleSignatureError(f"unexpected ed25519 signature length: {len(sig)}")

    sig_path = bundle.with_name(bundle.name + ".sig")
    sig_path.write_bytes(sig)
    return sig_path


def verify_bundle(
    bundle_path: Path | str,
    public_key_path: Path | str,
    sig_path: Path | str | None = None,
) -> bool:
    """Verify ``bundle_path`` against ``public_key_path``.

    ``sig_path`` defaults to ``<bundle_path>.sig`` (i.e.
    ``<bundle>.duhwave.sig``).

    Returns True iff the signature matches. Raises
    :class:`BundleSignatureError` if files are missing or the signature
    is malformed; returns False (does *not* raise) on a clean
    bytes-mismatch — the caller chooses how loud to be.
    """
    (
        InvalidSignature,
        _serialization,
        _Ed25519PrivateKey,
        _Ed25519PublicKey,
        _Encoding,
        _PrivateFormat,
    ) = _require_crypto()

    bundle = Path(bundle_path)
    if not bundle.exists():
        raise BundleSignatureError(f"bundle not found: {bundle}")

    if sig_path is None:
        # Default: append ".sig" to the full bundle name (handles any extension).
        sig = bundle.with_name(bundle.name + ".sig")
    else:
        sig = Path(sig_path)

    if not sig.exists():
        raise BundleSignatureError(f"signature not found: {sig}")

    public_key = _load_public_key(Path(public_key_path))
    payload = bundle.read_bytes()
    sig_bytes = sig.read_bytes()
    if len(sig_bytes) != 64:
        raise BundleSignatureError(f"signature is not 64 bytes (got {len(sig_bytes)})")

    try:
        public_key.verify(sig_bytes, payload)  # type: ignore[attr-defined]
    except InvalidSignature:  # type: ignore[misc]
        return False
    return True


__all__ = [
    "BUNDLE_EXT",
    "BundleSignatureError",
    "generate_keypair",
    "sign_bundle",
    "verify_bundle",
]
