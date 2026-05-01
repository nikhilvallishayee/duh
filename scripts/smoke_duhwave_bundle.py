#!/usr/bin/env python3
"""End-to-end smoke test for the duhwave bundle install/sign/verify flow.

Exercises the full path: keypair → pack → sign → verify (positive +
tampered negative) → install into a tempdir-rooted ``~/.duh/waves/`` →
list → uninstall. Prints "smoke OK" on success.

Run with the project venv:

    /Users/nomind/Code/duh/.venv/bin/python3 scripts/smoke_duhwave_bundle.py
"""
from __future__ import annotations

import sys
import tempfile
import textwrap
import traceback
from pathlib import Path


# Make `import duh.duhwave...` work when running from the repo root.
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(REPO_ROOT))


from duh.duhwave.bundle import (  # noqa: E402
    BUNDLE_EXT,
    BundleInstaller,
    BundleSignatureError,
    pack_bundle,
    sign_bundle,
    verify_bundle,
)
from duh.duhwave.bundle.signing import generate_keypair  # noqa: E402


_SWARM_TOML = """\
[swarm]
name = "smoke-test"
version = "0.1.0"
description = "smoke fixture"
format_version = 1

[[agents]]
id = "researcher"
role = "researcher"
model = "anthropic/claude-haiku-4-5"
tools = ["bash", "search"]

[budget]
max_tokens_per_hour = 100000
max_usd_per_day = 1.0
max_concurrent_tasks = 1
"""

_MANIFEST_TOML = """\
[bundle]
name = "smoke-test"
version = "0.1.0"
description = "smoke fixture"
author = "smoke <smoke@example.com>"
format_version = 1
created_at = 1700000000.0

[signing]
signed = false
"""

_PERMISSIONS_TOML = """\
[filesystem]
read = ["~/repos/*"]
write = ["./tmp/*"]

[network]
allow = ["api.example.com"]

[tools]
require = ["bash", "search"]
"""


def _write_fixture(spec_dir: Path) -> None:
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "swarm.toml").write_text(_SWARM_TOML)
    (spec_dir / "manifest.toml").write_text(_MANIFEST_TOML)
    (spec_dir / "permissions.toml").write_text(_PERMISSIONS_TOML)
    (spec_dir / "README.md").write_text("# smoke-test\n")
    prompts = spec_dir / "prompts"
    prompts.mkdir()
    (prompts / "researcher.md").write_text("you are a smoke researcher.\n")


def _step(n: int, msg: str) -> None:
    print(f"[{n}] {msg}")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        spec_dir = td_path / "spec"
        keys_dir = td_path / "keys"
        out_dir = td_path / "out"
        install_root = td_path / "waves"

        keys_dir.mkdir()
        out_dir.mkdir()

        # 1. Keypair.
        _step(1, "generate keypair")
        priv, pub = generate_keypair(keys_dir / "smoke")
        assert priv.exists() and pub.exists(), "keypair files missing"
        assert priv.read_bytes().startswith(b"-----BEGIN"), "priv not PEM"
        assert pub.read_bytes().startswith(b"-----BEGIN"), "pub not PEM"

        # 2. Pack a tiny bundle.
        _step(2, "pack bundle")
        _write_fixture(spec_dir)
        bundle_path = out_dir / f"smoke-test{BUNDLE_EXT}"
        pack_bundle(spec_dir, bundle_path)
        assert bundle_path.exists(), "bundle not produced"
        size = bundle_path.stat().st_size
        assert size > 0, "bundle is empty"

        # 3. Sign it.
        _step(3, "sign bundle")
        sig_path = sign_bundle(bundle_path, priv)
        assert sig_path.exists() and sig_path.stat().st_size == 64, (
            f"signature wrong size: {sig_path.stat().st_size}"
        )

        # 4. Verify (positive).
        _step(4, "verify (positive)")
        ok = verify_bundle(bundle_path, pub)
        assert ok is True, "expected positive verification to succeed"

        # 5. Tamper + verify (negative).
        _step(5, "tamper bundle bytes; verify (negative)")
        original = bundle_path.read_bytes()
        # Flip one byte in the middle of the archive.
        mid = len(original) // 2
        tampered = bytearray(original)
        tampered[mid] ^= 0xFF
        bundle_path.write_bytes(bytes(tampered))
        try:
            result = verify_bundle(bundle_path, pub)
        except BundleSignatureError as e:
            # Acceptable: some tamper patterns invalidate the sig file
            # interpretation as well. Either path counts as "rejected".
            print(f"    tampered bundle raised BundleSignatureError: {e}")
        else:
            assert result is False, "tampered bundle should NOT verify"
            print("    tampered bundle returned False (rejected) as expected")
        # Restore for install step.
        bundle_path.write_bytes(original)
        # Re-verify positive after restore as a sanity check.
        assert verify_bundle(bundle_path, pub) is True, "post-restore verify failed"

        # 6. Install into tempdir-rooted waves/.
        _step(6, "install bundle into tempdir-rooted ~/.duh/waves/")
        installer = BundleInstaller(root=install_root, confirm=lambda _diff: True)
        result = installer.install(bundle_path, public_key_path=pub)
        assert result.name == "smoke-test", f"unexpected name: {result.name}"
        assert result.version == "0.1.0", f"unexpected version: {result.version}"
        assert result.trust_level == "trusted", (
            f"expected trusted, got {result.trust_level}"
        )
        assert Path(result.path).is_dir(), "install dir missing"
        assert (Path(result.path) / "swarm.toml").is_file(), "swarm.toml not extracted"
        assert (install_root / "index.json").is_file(), "index.json not written"

        # 7. List installed.
        _step(7, "list installed")
        listed = installer.list_installed()
        assert len(listed) == 1, f"expected 1 installed, got {len(listed)}"
        assert listed[0].name == "smoke-test", "listed name wrong"
        print(f"    installed: {listed[0].name} {listed[0].version} "
              f"trust={listed[0].trust_level}")

        # 8. Uninstall.
        _step(8, "uninstall")
        removed = installer.uninstall("smoke-test")
        assert removed is True, "uninstall returned False"
        assert installer.list_installed() == [], "still listed after uninstall"
        assert not (install_root / "smoke-test").exists(), "dir not removed"

    print("\nsmoke OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"\nSMOKE FAILED: assertion: {e}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
    except Exception as e:
        print(f"\nSMOKE FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
