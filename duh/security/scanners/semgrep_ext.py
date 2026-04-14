"""Placeholder — full implementation in Phase 5."""
from duh.security.scanners import SubprocessScanner, Tier

class SemgrepScanner(SubprocessScanner):
    name = "semgrep"
    tier: Tier = "extended"
    _binary = "semgrep"

    async def _scan_impl(self, target, cfg, *, changed_files):
        return []
