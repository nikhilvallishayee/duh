"""Placeholder — full implementation in Phase 5."""
from duh.security.scanners import SubprocessScanner, Tier

class GitleaksScanner(SubprocessScanner):
    name = "gitleaks"
    tier: Tier = "extended"
    _binary = "gitleaks"

    async def _scan_impl(self, target, cfg, *, changed_files):
        return []
