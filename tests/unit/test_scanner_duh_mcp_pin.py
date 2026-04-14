"""Tests for MCPPinScanner — CVE-2025-54136 rug-pull defense."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.scanners.duh_mcp_pin import MCPPinScanner


def _write_mcp(dir: Path, servers: list[dict]) -> None:
    (dir / ".duh").mkdir(parents=True, exist_ok=True)
    (dir / ".duh" / "mcp.json").write_text(json.dumps({"servers": servers}))


def test_first_connect_writes_trust_file(tmp_path: Path) -> None:
    trust = tmp_path / "mcp_trust.json"
    _write_mcp(tmp_path, [{
        "name": "srv",
        "command": "node",
        "args": ["srv.js"],
        "tools": [{"name": "make_issue", "description": "create issue"}],
    }])
    s = MCPPinScanner(trust_file=trust)
    findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []
    data = json.loads(trust.read_text())
    assert "srv" in data


def test_schema_change_flagged(tmp_path: Path) -> None:
    trust = tmp_path / "mcp_trust.json"
    _write_mcp(tmp_path, [{
        "name": "srv",
        "command": "node",
        "args": ["srv.js"],
        "tools": [{"name": "t", "description": "old"}],
    }])
    s = MCPPinScanner(trust_file=trust)
    asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    # Now mutate
    _write_mcp(tmp_path, [{
        "name": "srv",
        "command": "node",
        "args": ["srv.js"],
        "tools": [{"name": "t", "description": "new different"}],
    }])
    findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert any(f.id == "DUH-MCP-PIN" for f in findings)


def test_cve_2025_54136_replay(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "security" / "cve_replays" / "CVE-2025-54136"
    trust = tmp_path / "mcp_trust.json"
    # First load baseline
    work = tmp_path / "work"
    work.mkdir()
    (work / ".duh").mkdir()
    (work / ".duh" / "mcp.json").write_text(
        (fixture / "mcp_before.json").read_text()
    )
    s = MCPPinScanner(trust_file=trust)
    asyncio.run(s.scan(work, ScannerConfig(), changed_files=None))
    # Now flip to the poisoned one
    (work / ".duh" / "mcp.json").write_text(
        (fixture / "mcp_after.json").read_text()
    )
    findings = asyncio.run(s.scan(work, ScannerConfig(), changed_files=None))
    assert any(f.id == "DUH-MCP-PIN" for f in findings)
