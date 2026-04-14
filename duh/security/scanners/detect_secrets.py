"""detect-secrets scanner — Minimal tier, baseline-delta native."""

from __future__ import annotations

from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import InProcessScanner, Tier


class DetectSecretsScanner(InProcessScanner):
    name = "detect-secrets"
    tier: Tier = "minimal"
    default_severity = (Severity.HIGH, Severity.CRITICAL)
    _module_name = "detect_secrets"

    async def _scan_impl(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None,
    ) -> list[Finding]:
        from detect_secrets.core.scan import scan_file
        from detect_secrets.settings import default_settings

        files = list(changed_files) if changed_files else list(target.rglob("*.py"))
        findings: list[Finding] = []
        with default_settings():
            for path in files:
                if not path.is_file():
                    continue
                for secret in scan_file(str(path)):
                    findings.append(
                        Finding.create(
                            id="DETECT-SECRETS",
                            aliases=(),
                            scanner=self.name,
                            severity=Severity.HIGH,
                            message=f"potential secret: {secret.type}",
                            description=f"detect-secrets flagged {secret.type}",
                            location=Location(
                                file=str(path),
                                line_start=secret.line_number,
                                line_end=secret.line_number,
                                snippet="",
                            ),
                            metadata={"type": secret.type},
                        )
                    )
        return findings
