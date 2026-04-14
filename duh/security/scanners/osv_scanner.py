"""Placeholder — full implementation in Phase 5."""
from duh.security.scanners import SubprocessScanner, Tier

class OSVScanner(SubprocessScanner):
    name = "osv-scanner"
    tier: Tier = "extended"
    _binary = "osv-scanner"

    async def _scan_impl(self, target, cfg, *, changed_files):
        return []
