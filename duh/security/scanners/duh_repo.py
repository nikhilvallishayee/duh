"""Placeholder — full implementation in Phase 2."""
from duh.security.scanners import InProcessScanner, Tier

class RepoScanner(InProcessScanner):
    name = "duh-repo"
    tier: Tier = "custom"
    _module_name = "json"

    async def _scan_impl(self, target, cfg, *, changed_files):
        return []
