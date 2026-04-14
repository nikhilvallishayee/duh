"""Placeholder — full implementation in Phase 2."""
from duh.security.scanners import InProcessScanner, Tier

class MCPSchemaScanner(InProcessScanner):
    name = "duh-mcp-schema"
    tier: Tier = "custom"
    _module_name = "json"

    async def _scan_impl(self, target, cfg, *, changed_files):
        return []
