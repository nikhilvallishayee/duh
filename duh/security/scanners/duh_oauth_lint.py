"""duh-oauth-lint — localhost OAuth hardening."""

from __future__ import annotations

import re
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import InProcessScanner, Tier


_PATTERNS = [
    ("DUH-OAUTH-BIND",          Severity.HIGH,    re.compile(r'bind\(\s*\(\s*["\']0\.0\.0\.0["\']')),
    ("DUH-OAUTH-REUSEADDR",     Severity.MEDIUM,  re.compile(r'SO_REUSEADDR')),
    ("DUH-OAUTH-LOG-SECRET",    Severity.HIGH,    re.compile(r'Authorization:\s*\{')),
    ("DUH-OAUTH-REDIRECT-PREFIX", Severity.HIGH,  re.compile(r'redirect\w*\.startswith\(')),
    ("DUH-OAUTH-PKCE",          Severity.HIGH,    re.compile(r'code_challenge_method\s*=\s*["\']plain["\']')),
]


class OAuthLintScanner(InProcessScanner):
    name = "duh-oauth-lint"
    tier: Tier = "minimal"
    default_severity = (Severity.HIGH,)
    _module_name = "json"

    async def _scan_impl(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None,
    ) -> list[Finding]:
        files = list(changed_files) if changed_files else list(target.rglob("*.py"))
        out: list[Finding] = []
        for path in files:
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, 1):
                for fid, sev, pat in _PATTERNS:
                    if pat.search(line):
                        out.append(
                            Finding.create(
                                id=fid, aliases=(), scanner=self.name, severity=sev,
                                message=f"{fid} in {path.name}:{lineno}",
                                description=f"OAuth hardening violation: {fid}",
                                location=Location(
                                    file=str(path), line_start=lineno, line_end=lineno,
                                    snippet=line.strip(),
                                ),
                            )
                        )
        return out
