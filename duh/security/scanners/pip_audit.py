"""pip-audit scanner — Minimal tier, OSV-backed Python dependency scanner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import InProcessScanner, Tier


class PipAuditScanner(InProcessScanner):
    name = "pip-audit"
    tier: Tier = "minimal"
    default_severity = (Severity.HIGH, Severity.CRITICAL)
    _module_name = "pip_audit"

    async def _scan_impl(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None,
    ) -> list[Finding]:
        argv = ["pip-audit", "--format", "json", "--progress-spinner", "off"]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(target) if target.is_dir() else None,
        )
        stdout, _stderr = await proc.communicate()
        if not stdout.strip():
            return []
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return []
        findings: list[Finding] = []
        for dep in data.get("dependencies", []):
            pkg = dep.get("name", "")
            ver = dep.get("version", "")
            for vuln in dep.get("vulns", []) or []:
                aliases = tuple(vuln.get("aliases", []) or [])
                vid = vuln.get("id", "")
                primary = aliases[0] if aliases and aliases[0].startswith("CVE-") else vid
                fix_versions = vuln.get("fix_versions", []) or []
                findings.append(
                    Finding.create(
                        id=primary,
                        aliases=tuple([vid] + list(aliases)) if vid not in aliases else aliases,
                        scanner=self.name,
                        severity=Severity.HIGH,
                        message=vuln.get("description", "")[:200],
                        description=vuln.get("description", ""),
                        location=Location(
                            file="pyproject.toml",
                            line_start=0,
                            line_end=0,
                            snippet=f"{pkg}=={ver}",
                        ),
                        package=pkg,
                        version=ver,
                        fixed_in=fix_versions[0] if fix_versions else None,
                    )
                )
        return findings
