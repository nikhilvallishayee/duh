"""Tests for CycloneDXScanner — SBOM emission."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.security.config import ScannerConfig
from duh.security.scanners.cyclonedx_sbom import CycloneDXScanner


def test_cyclonedx_name_and_tier() -> None:
    s = CycloneDXScanner()
    assert s.name == "cyclonedx-sbom"
    assert s.tier == "minimal"


def test_cyclonedx_emits_valid_json(tmp_path: Path) -> None:
    s = CycloneDXScanner()

    fake_sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.7",
        "components": [{"name": "requests", "version": "2.31.0", "type": "library"}],
    }

    async def fake_run(*args, **kwargs):
        class _Proc:
            returncode = 0

            async def communicate(self):
                return (json.dumps(fake_sbom).encode(), b"")
        return _Proc()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_run):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    # SBOM is informational — no findings, but side-effect file may be produced
    assert findings == []


def test_cyclonedx_writes_sbom_artifact(tmp_path: Path) -> None:
    s = CycloneDXScanner()
    fake_sbom = {"bomFormat": "CycloneDX", "specVersion": "1.7", "components": []}

    async def fake_run(*args, **kwargs):
        class _Proc:
            returncode = 0

            async def communicate(self):
                return (json.dumps(fake_sbom).encode(), b"")
        return _Proc()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_run):
        cfg = ScannerConfig(enabled=True)
        asyncio.run(s.scan(tmp_path, cfg, changed_files=None))
    assert (tmp_path / "sbom.cdx.json").exists()
    data = json.loads((tmp_path / "sbom.cdx.json").read_text())
    assert data["bomFormat"] == "CycloneDX"
