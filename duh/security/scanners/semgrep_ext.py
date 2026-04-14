"""Semgrep scanner — Extended tier, SAST via semgrep rules."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import SubprocessScanner, Tier


_SEVERITY_MAP = {
    "ERROR": Severity.HIGH,
    "WARNING": Severity.MEDIUM,
    "INFO": Severity.LOW,
    "INVENTORY": Severity.INFO,
    "EXPERIMENT": Severity.INFO,
}


def _parse(stdout: bytes) -> list[Finding]:
    if not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    findings: list[Finding] = []
    for result in data.get("results", []) or []:
        check_id: str = result.get("check_id", "semgrep-unknown")
        extra = result.get("extra", {}) or {}
        sev_str: str = (extra.get("severity") or "WARNING").upper()
        severity = _SEVERITY_MAP.get(sev_str, Severity.MEDIUM)
        message: str = extra.get("message", "") or ""
        meta = extra.get("metadata", {}) or {}
        cwe_raw = meta.get("cwe", [])
        if isinstance(cwe_raw, str):
            cwe_raw = [cwe_raw]
        cwe: list[int] = []
        for c in cwe_raw:
            # entries look like "CWE-79: ..." or just "79"
            c_str = str(c).split(":")[0].replace("CWE-", "").strip()
            if c_str.isdigit():
                cwe.append(int(c_str))
        loc_data = result.get("start", {}) or {}
        end_data = result.get("end", {}) or {}
        file_path: str = result.get("path", "")
        snippet: str = (extra.get("lines") or "").strip()
        findings.append(
            Finding.create(
                id=check_id,
                aliases=(),
                scanner="semgrep",
                severity=severity,
                message=message[:200],
                description=message,
                location=Location(
                    file=file_path,
                    line_start=int(loc_data.get("line", 0)),
                    line_end=int(end_data.get("line", loc_data.get("line", 0))),
                    snippet=snippet,
                ),
                cwe=tuple(cwe),
                metadata={"rule": check_id},
            )
        )
    return findings


class SemgrepScanner(SubprocessScanner):
    name = "semgrep"
    tier: Tier = "extended"
    default_severity = (Severity.HIGH, Severity.MEDIUM)
    _binary = "semgrep"

    async def scan(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None = None,
    ) -> list[Finding]:
        argv = ["semgrep", "scan", "--json", "--config", "p/security-audit"]
        if changed_files:
            argv.extend(str(p) for p in changed_files)
        else:
            argv.append(str(target))
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(target) if target.is_dir() else None,
        )
        stdout, _stderr = await proc.communicate()
        return _parse(stdout)
