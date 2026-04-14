"""duh-repo — project-file RCE defense (CVE-2025-59536 class).

Refuses auto-loading of repo-local config/hooks/env files unless the cwd is on
an explicit trusted_paths allowlist. Emits a finding for every violation.
"""

from __future__ import annotations

import json
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import InProcessScanner, Tier


_AUTOLOAD_TARGETS = (
    ".duh/hooks",
    ".duh/mcp.json",
    ".duh/settings.json",
    ".env",
    ".envrc",
    ".tool-versions",
)

_BASE_URL_ENV_KEYS = (
    "DUH_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "OPENAI_BASE_URL",
)


class RepoScanner(InProcessScanner):
    name = "duh-repo"
    tier: Tier = "minimal"
    default_severity = (Severity.HIGH, Severity.CRITICAL)
    _module_name = "json"

    def __init__(self, *, trusted_paths_file: Path | None = None) -> None:
        self._trusted_paths_file = trusted_paths_file or (
            Path.home() / ".duh" / "trusted_paths.json"
        )

    def _is_trusted(self, target: Path) -> bool:
        if not self._trusted_paths_file.exists():
            return False
        try:
            data = json.loads(self._trusted_paths_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        paths = [Path(p).resolve() for p in data.get("paths", [])]
        t = target.resolve()
        return any(t == p or p in t.parents for p in paths)

    async def _scan_impl(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        trusted = self._is_trusted(target)

        # 1. Auto-load targets trigger DUH-REPO-UNTRUSTED on untrusted repos.
        if not trusted:
            for rel in _AUTOLOAD_TARGETS:
                p = target / rel
                if p.exists():
                    findings.append(
                        Finding.create(
                            id="DUH-REPO-UNTRUSTED",
                            aliases=("CVE-2025-59536",),
                            scanner=self.name,
                            severity=Severity.HIGH,
                            message=f"untrusted repo contains auto-load target: {rel}",
                            description=(
                                "Project-local file would be auto-loaded by D.U.H. "
                                "Requires TOFU approval via `duh security trust`."
                            ),
                            location=Location(
                                file=str(p),
                                line_start=0,
                                line_end=0,
                                snippet=rel,
                            ),
                        )
                    )

        # 2. Repo-local env overrides for base URLs are always rejected.
        env_file = target / ".env"
        if env_file.is_file():
            try:
                for lineno, line in enumerate(env_file.read_text(encoding="utf-8").splitlines(), 1):
                    for key in _BASE_URL_ENV_KEYS:
                        if line.startswith(f"{key}="):
                            findings.append(
                                Finding.create(
                                    id="DUH-REPO-BASE-URL",
                                    aliases=(),
                                    scanner=self.name,
                                    severity=Severity.CRITICAL,
                                    message=f"repo-local {key} override rejected",
                                    description=(
                                        "Base URL overrides must come from shell or "
                                        "user config, never repo-local env files."
                                    ),
                                    location=Location(
                                        file=str(env_file),
                                        line_start=lineno,
                                        line_end=lineno,
                                        snippet=line,
                                    ),
                                )
                            )
            except OSError:
                pass

        # 3. Symlinks inside .duh/hooks are rejected.
        hooks_dir = target / ".duh" / "hooks"
        if hooks_dir.is_dir():
            for child in hooks_dir.iterdir():
                if child.is_symlink():
                    findings.append(
                        Finding.create(
                            id="DUH-REPO-SYMLINK",
                            aliases=(),
                            scanner=self.name,
                            severity=Severity.HIGH,
                            message=f"symlink in .duh/hooks refused: {child.name}",
                            description="Hooks directory refuses symlinks.",
                            location=Location(
                                file=str(child),
                                line_start=0,
                                line_end=0,
                                snippet="",
                            ),
                        )
                    )

        return findings
