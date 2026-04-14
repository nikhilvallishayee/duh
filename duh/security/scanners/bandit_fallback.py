"""Placeholder — full implementation in Phase 5."""
from duh.security.scanners import SubprocessScanner, Tier

class BanditScanner(SubprocessScanner):
    name = "bandit"
    tier: Tier = "extended"
    _binary = "bandit"

    async def _scan_impl(self, target, cfg, *, changed_files):
        return []
