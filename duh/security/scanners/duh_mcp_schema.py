"""MCP tool-poisoning defense."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import InProcessScanner, Tier


_IMPERATIVE_PATTERNS = [
    re.compile(r"ignore\s+previous", re.IGNORECASE),
    re.compile(r"always\s+also\s+call", re.IGNORECASE),
    re.compile(r"before\s+responding", re.IGNORECASE),
    re.compile(r"system\s*:\s*you\s+are", re.IGNORECASE),
]

_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\ufeff"}
_BIDI = {chr(c) for c in range(0x202A, 0x202F)} | {chr(c) for c in range(0x2066, 0x206A)}
_TAG_RANGE = range(0xE0000, 0xE0080)
_VARIATION_SELECTORS = set(range(0xFE00, 0xFE10)) | set(range(0xE0100, 0xE01F0))

_BASE64_RE = re.compile(r"(?:[A-Za-z0-9+/]{32,}={0,2})")
_EXFIL_RE = re.compile(
    r"(curl\s+|wget\s+|https?://\d{1,3}(?:\.\d{1,3}){3}|\.onion|\.xyz|\.top)",
    re.IGNORECASE,
)


class MCPSchemaScanner(InProcessScanner):
    name = "duh-mcp-schema"
    tier: Tier = "minimal"
    default_severity = (Severity.HIGH, Severity.CRITICAL)
    _module_name = "json"

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
        findings: list[Finding] = []
        for server in doc.get("servers", []):
            for tool in server.get("tools", []):
                desc = tool.get("description", "") or ""
                findings.extend(self._lint_text(mcp_file, tool.get("name", ""), desc))
        return findings

    def _lint_text(self, src: Path, tool_name: str, text: str) -> list[Finding]:
        out: list[Finding] = []
        loc = Location(file=str(src), line_start=0, line_end=0, snippet=tool_name)

        def _add(id: str, sev: Severity, msg: str) -> None:
            out.append(
                Finding.create(
                    id=id, aliases=(), scanner=self.name, severity=sev,
                    message=msg, description=msg, location=loc,
                    metadata={"tool": tool_name},
                )
            )

        for pat in _IMPERATIVE_PATTERNS:
            if pat.search(text):
                _add("DUH-MCP-IMPERATIVE", Severity.HIGH,
                     f"imperative verb targeting model in tool {tool_name!r}")
                break

        # Unicode anomalies
        if any(ch in text for ch in _ZERO_WIDTH):
            _add("DUH-MCP-UNICODE", Severity.CRITICAL,
                 f"zero-width character in tool {tool_name!r}")
        elif any(ch in text for ch in _BIDI):
            _add("DUH-MCP-UNICODE", Severity.CRITICAL,
                 f"bidi override in tool {tool_name!r}")
        elif any(ord(ch) in _TAG_RANGE for ch in text):
            _add("DUH-MCP-UNICODE", Severity.CRITICAL,
                 f"Unicode tag character in tool {tool_name!r}")
        elif any(ord(ch) in _VARIATION_SELECTORS for ch in text):
            _add("DUH-MCP-UNICODE", Severity.HIGH,
                 f"variation selector in tool {tool_name!r}")
        else:
            normalized = unicodedata.normalize("NFKC", text)
            if normalized != text:
                _add("DUH-MCP-UNICODE", Severity.MEDIUM,
                     f"NFKC reshape in tool {tool_name!r}")

        if _BASE64_RE.search(text):
            _add("DUH-MCP-BASE64", Severity.MEDIUM,
                 f"base64 blob in tool {tool_name!r}")

        if _EXFIL_RE.search(text):
            _add("DUH-MCP-EXFIL", Severity.HIGH,
                 f"exfiltration pattern in tool {tool_name!r}")

        return out
