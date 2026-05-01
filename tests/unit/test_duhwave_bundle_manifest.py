"""Tests for ``duh.duhwave.bundle.manifest.BundleManifest``.

The manifest is the bundle's identity card. Its TOML serialisation must
be deterministic so detached signatures over the bundle stay valid
across re-packs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from duh.duhwave.bundle.manifest import BundleManifest, ManifestError


def _write(path: Path, body: str) -> Path:
    path.write_text(body.strip() + "\n")
    return path


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_full_round_trip_preserves_fields(self, tmp_path: Path):
        original = BundleManifest(
            name="triage-bot",
            version="1.2.3",
            description="auto-triages github issues",
            author="Nikhil",
            format_version=1,
            signed=True,
            signing_key_id="ed25519:abc123",
            created_at=1700000000.0,
        )
        toml_path = tmp_path / "manifest.toml"
        toml_path.write_text(original.to_toml())

        loaded = BundleManifest.from_toml(toml_path)
        assert loaded.name == original.name
        assert loaded.version == original.version
        assert loaded.description == original.description
        assert loaded.author == original.author
        assert loaded.format_version == original.format_version
        assert loaded.signed == original.signed
        assert loaded.signing_key_id == original.signing_key_id
        assert loaded.created_at == original.created_at

    def test_to_toml_is_deterministic(self):
        m = BundleManifest(
            name="det",
            version="0.1.0",
            description="d",
            author="a",
            format_version=1,
            signed=False,
            created_at=1700000000.0,
        )
        # Same input → same bytes, run after run.
        assert m.to_toml() == m.to_toml()

    def test_to_toml_emits_bundle_section_first(self):
        m = BundleManifest(name="ordered", version="1", created_at=1.0)
        out = m.to_toml()
        # [bundle] header before [signing] header — fixed order.
        i_bundle = out.index("[bundle]")
        i_signing = out.index("[signing]")
        assert i_bundle < i_signing

    def test_round_trip_handles_special_chars(self, tmp_path: Path):
        # Quote, backslash, newline, tab — all the escape cases.
        original = BundleManifest(
            name='quirky "bot"',
            version="0.1.0",
            description="line1\nline2\twith tab \\backslash",
            author='He said "hi"',
            created_at=1700000000.0,
        )
        toml_path = tmp_path / "manifest.toml"
        toml_path.write_text(original.to_toml())
        loaded = BundleManifest.from_toml(toml_path)
        assert loaded.name == original.name
        assert loaded.description == original.description
        assert loaded.author == original.author


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_required_field_raises(self, tmp_path: Path):
        # version omitted — name without version is unusable.
        spec_path = _write(
            tmp_path / "manifest.toml",
            """
[bundle]
name = "incomplete"
""",
        )
        with pytest.raises(ManifestError, match="version"):
            BundleManifest.from_toml(spec_path)

    def test_missing_bundle_section_raises(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "manifest.toml",
            """
[signing]
signed = true
""",
        )
        with pytest.raises(ManifestError, match=r"\[bundle\]"):
            BundleManifest.from_toml(spec_path)

    def test_bad_toml_raises_manifest_error_or_decode_error(self, tmp_path: Path):
        spec_path = _write(
            tmp_path / "manifest.toml",
            "this is not valid = = toml [[[",
        )
        # tomllib.TOMLDecodeError leaks through — that's fine; manifest
        # validation upstream catches it. Either is acceptable contract.
        with pytest.raises(Exception):
            BundleManifest.from_toml(spec_path)

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(ManifestError, match="not found"):
            BundleManifest.from_toml(tmp_path / "does-not-exist.toml")
