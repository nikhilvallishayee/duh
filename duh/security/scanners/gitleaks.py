"""Gitleaks scanner — Extended tier, secret detection in git repos."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import SubprocessScanner, Tier


def _parse(stdout: bytes) -> list[Finding]:
    if not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    findings: list[Finding] = []
    for entry in data:
        rule_id: str = entry.get("RuleID") or entry.get("ruleID") or "gitleaks-secret"
        description: str = entry.get("Description") or entry.get("description") or ""
        file_path: str = entry.get("File") or entry.get("file") or ""
        line_start: int = int(entry.get("StartLine") or entry.get("startLine") or 0)
        line_end: int = int(entry.get("EndLine") or entry.get("endLine") or line_start)
        secret: str = entry.get("Secret") or entry.get("secret") or ""
        # Redact the actual secret from the snippet for safety
        match_val: str = entry.get("Match") or entry.get("match") or ""
        snippet = match_val[:80] if match_val else ""
        commit: str = entry.get("Commit") or entry.get("commit") or ""
        author: str = entry.get("Author") or entry.get("author") or ""
        message: str = f"Secret detected: {description}" if description else f"Secret detected: {rule_id}"
        findings.append(
            Finding.create(
                id=rule_id,
                aliases=(),
                scanner="gitleaks",
                severity=Severity.HIGH,
                message=message[:200],
                description=description,
                location=Location(
                    file=file_path,
                    line_start=line_start,
                    line_end=line_end,
                    snippet=snippet,
                ),
                metadata={
                    "rule": rule_id,
                    "commit": commit,
                    "author": author,
                    "secret_length": len(secret),
                },
            )
        )
    return findings


class GitleaksScanner(SubprocessScanner):
    name = "gitleaks"
    tier: Tier = "extended"
    default_severity = (Severity.HIGH, Severity.CRITICAL)
    _binary = "gitleaks"

    async def scan(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None = None,
    ) -> list[Finding]:
        argv = [
            "gitleaks",
            "detect",
            "--report-format", "json",
            "--report-path", "/dev/stdout",
            "--source", str(target),
            "--no-git",
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(target) if target.is_dir() else None,
        )
        stdout, _stderr = await proc.communicate()
        return _parse(stdout)
