"""duh-mcp-pin — CVE-2025-54136 MCPoison defense.

On first connect to a server, SHA256-pins (schema + command + args + env) for
every tool. On subsequent connects, any change requires re-approval.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import InProcessScanner, Tier


def _tool_hash(server: dict, tool: dict) -> str:
    payload = json.dumps(
        {
            "command": server.get("command", ""),
            "args": server.get("args", []),
            "env": server.get("env", {}),
            "tool": tool,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MCPPinScanner(InProcessScanner):
    name = "duh-mcp-pin"
    tier: Tier = "minimal"
    default_severity = (Severity.HIGH, Severity.CRITICAL)
    _module_name = "json"

    def __init__(self, *, trust_file: Path | None = None) -> None:
        self._trust_file = trust_file or (Path.home() / ".duh" / "mcp_trust.json")

    def _load_trust(self) -> dict:
        if not self._trust_file.exists():
            return {}
        try:
            return json.loads(self._trust_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_trust(self, data: dict) -> None:
        self._trust_file.parent.mkdir(parents=True, exist_ok=True)
        self._trust_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    async def _scan_impl(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None,
    ) -> list[Finding]:
        mcp_file = target / ".duh" / "mcp.json"
        if not mcp_file.is_file():
            return []
        try:
            doc = json.loads(mcp_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        trust = self._load_trust()
        findings: list[Finding] = []
        mutated = False
        for server in doc.get("servers", []):
            name = server.get("name", "")
            if not name:
                continue
            known = trust.setdefault(name, {"tools": {}})
            for tool in server.get("tools", []):
                tname = tool.get("name", "")
                h = _tool_hash(server, tool)
                pinned = known["tools"].get(tname)
                if pinned is None:
                    known["tools"][tname] = {"hash": h}
                    mutated = True
                elif pinned["hash"] != h:
                    findings.append(
                        Finding.create(
                            id="DUH-MCP-PIN",
                            aliases=("CVE-2025-54136",),
                            scanner=self.name,
                            severity=Severity.HIGH,
                            message=f"MCP tool {name}:{tname} changed since trust",
                            description=(
                                "Tool schema/command/args/env hash drift. "
                                "Re-approve or disable the tool."
                            ),
                            location=Location(
                                file=str(mcp_file),
                                line_start=0,
                                line_end=0,
                                snippet=f"{name}:{tname}",
                            ),
                            metadata={"pinned": pinned["hash"], "current": h},
                        )
                    )
        if mutated:
            self._save_trust(trust)
        return findings
