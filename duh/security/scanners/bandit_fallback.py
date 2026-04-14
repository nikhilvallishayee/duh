"""Bandit scanner — Extended tier, Python SAST (fallback to ruff-sec)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import SubprocessScanner, Tier


_SEVERITY_MAP = {
    "LOW": Severity.LOW,
    "MEDIUM": Severity.MEDIUM,
    "HIGH": Severity.HIGH,
}

_CONFIDENCE_MAP = {
    "LOW": Severity.LOW,
    "MEDIUM": Severity.MEDIUM,
    "HIGH": Severity.HIGH,
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
        test_id: str = result.get("test_id", "B000")
        test_name: str = result.get("test_name", "")
        issue_text: str = result.get("issue_text", "")
        sev_str: str = (result.get("issue_severity") or "MEDIUM").upper()
        severity = _SEVERITY_MAP.get(sev_str, Severity.MEDIUM)
        file_path: str = result.get("filename", "")
        line_start: int = int(result.get("line_number") or result.get("line_range", [0])[0] or 0)
        line_range = result.get("line_range", []) or []
        line_end: int = int(line_range[-1]) if line_range else line_start
        code: str = (result.get("code") or "").strip()
        cwe_node = result.get("issue_cwe", {}) or {}
        cwe_id: int | None = None
        raw_cwe = cwe_node.get("id")
        if raw_cwe is not None:
            try:
                cwe_id = int(raw_cwe)
            except (ValueError, TypeError):
                pass
        more_info: str = result.get("more_info", "") or ""
        findings.append(
            Finding.create(
                id=test_id,
                aliases=(),
                scanner="bandit",
                severity=severity,
                message=issue_text[:200],
                description=more_info or issue_text,
                location=Location(
                    file=file_path,
                    line_start=line_start,
                    line_end=line_end,
                    snippet=code[:200],
                ),
                cwe=tuple([cwe_id] if cwe_id is not None else []),
                metadata={"rule": test_name, "test_id": test_id},
            )
        )
    return findings


class BanditScanner(SubprocessScanner):
    name = "bandit"
    tier: Tier = "extended"
    default_severity = (Severity.HIGH, Severity.MEDIUM)
    _binary = "bandit"

    async def scan(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None = None,
    ) -> list[Finding]:
        argv = ["bandit", "-f", "json"]
        if changed_files:
            argv.extend(str(p) for p in changed_files)
        else:
            argv.extend(["-r", str(target)])
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(target) if target.is_dir() else None,
        )
        stdout, _stderr = await proc.communicate()
        return _parse(stdout)
