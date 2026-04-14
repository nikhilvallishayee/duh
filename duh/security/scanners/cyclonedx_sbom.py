"""CycloneDX SBOM emitter — Minimal tier, informational."""

from __future__ import annotations

import asyncio
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding
from duh.security.scanners import InProcessScanner, Tier


class CycloneDXScanner(InProcessScanner):
    name = "cyclonedx-sbom"
    tier: Tier = "minimal"
    _module_name = "cyclonedx_py"

    async def _scan_impl(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None,
    ) -> list[Finding]:
        argv = [
            "cyclonedx-py",
            "environment",
            "--output-format", "JSON",
            "--schema-version", "1.7",
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(target) if target.is_dir() else None,
        )
        stdout, _stderr = await proc.communicate()
        if stdout.strip():
            (target / "sbom.cdx.json").write_bytes(stdout)
        return []
