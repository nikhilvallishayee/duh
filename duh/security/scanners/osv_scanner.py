"""OSV-Scanner — Extended tier, dependency vulnerability scanner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import SubprocessScanner, Tier


def _cvss_to_severity(score: float | None) -> Severity:
    if score is None:
        return Severity.MEDIUM
    if score >= 9.0:
        return Severity.CRITICAL
    if score >= 7.0:
        return Severity.HIGH
    if score >= 4.0:
        return Severity.MEDIUM
    if score > 0.0:
        return Severity.LOW
    return Severity.INFO


def _parse(stdout: bytes) -> list[Finding]:
    if not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    findings: list[Finding] = []
    for result in data.get("results", []) or []:
        source = result.get("source", {}) or {}
        source_file: str = source.get("path", "")
        for pkg_entry in result.get("packages", []) or []:
            pkg_info = pkg_entry.get("package", {}) or {}
            pkg_name: str = pkg_info.get("name", "")
            pkg_version: str = pkg_info.get("version", "")
            for vuln in pkg_entry.get("vulnerabilities", []) or []:
                vid: str = vuln.get("id", "")
                aliases = tuple(vuln.get("aliases", []) or [])
                # Derive primary CVE id if available
                cve_aliases = [a for a in aliases if a.startswith("CVE-")]
                primary_id = cve_aliases[0] if cve_aliases else vid
                summary: str = vuln.get("summary", "") or ""
                details: str = vuln.get("details", "") or summary
                # Extract CVSS score from severity array
                severities = vuln.get("severity", []) or []
                cvss_score: float | None = None
                for sev_entry in severities:
                    score_str = sev_entry.get("score", "")
                    try:
                        cvss_score = float(score_str)
                        break
                    except (ValueError, TypeError):
                        pass
                severity = _cvss_to_severity(cvss_score)
                # Find fixed version from affected ranges
                fixed_in: str | None = None
                for affected in vuln.get("affected", []) or []:
                    for rng in affected.get("ranges", []) or []:
                        for ev in rng.get("events", []) or []:
                            fv = ev.get("fixed")
                            if fv:
                                fixed_in = fv
                                break
                        if fixed_in:
                            break
                    if fixed_in:
                        break
                findings.append(
                    Finding.create(
                        id=primary_id,
                        aliases=tuple(set([vid] + list(aliases)) - {primary_id}),
                        scanner="osv-scanner",
                        severity=severity,
                        message=summary[:200],
                        description=details,
                        location=Location(
                            file=source_file or "pyproject.toml",
                            line_start=0,
                            line_end=0,
                            snippet=f"{pkg_name}=={pkg_version}",
                        ),
                        package=pkg_name,
                        version=pkg_version,
                        fixed_in=fixed_in,
                    )
                )
    return findings


class OSVScanner(SubprocessScanner):
    name = "osv-scanner"
    tier: Tier = "extended"
    default_severity = (Severity.HIGH, Severity.CRITICAL)
    _binary = "osv-scanner"

    async def scan(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None = None,
    ) -> list[Finding]:
        argv = ["osv-scanner", "--json", str(target)]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(target) if target.is_dir() else None,
        )
        stdout, _stderr = await proc.communicate()
        return _parse(stdout)
