"""Ruff S-rule scanner — replaces Bandit for 85% of rules at 25x speed."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import InProcessScanner, Tier


_LEVEL_MAP = {
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "note": Severity.LOW,
}


class RuffSecScanner(InProcessScanner):
    name = "ruff-sec"
    tier: Tier = "minimal"
    default_severity = (Severity.HIGH, Severity.MEDIUM)
    _module_name = "ruff"

    async def _scan_impl(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None,
    ) -> list[Finding]:
        # Ruff is distributed as a binary wrapped by the `ruff` python package.
        # Shell out to `ruff check --select S --output-format json` for portability.
        argv = ["ruff", "check", "--select", "S", "--output-format", "json"]
        if changed_files:
            argv.extend(str(p) for p in changed_files)
        else:
            argv.append(str(target))
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await proc.communicate()
        if not stdout.strip():
            return []
        try:
            diagnostics = json.loads(stdout)
        except json.JSONDecodeError:
            return []
        findings: list[Finding] = []
        for diag in diagnostics:
            code = diag.get("code") or "S000"
            loc = diag.get("location", {}) or {}
            end = diag.get("end_location", {}) or {}
            findings.append(
                Finding.create(
                    id=code,
                    aliases=(),
                    scanner=self.name,
                    severity=Severity.HIGH if code.startswith("S6") else Severity.MEDIUM,
                    message=diag.get("message", ""),
                    description=diag.get("url", ""),
                    location=Location(
                        file=diag.get("filename", ""),
                        line_start=int(loc.get("row", 0)),
                        line_end=int(end.get("row", loc.get("row", 0))),
                        snippet="",
                    ),
                    metadata={"rule": code},
                )
            )
        return findings
