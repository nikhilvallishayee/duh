"""Every registered tool must declare a capabilities attribute (ADR-054, 7.3.4)."""

from __future__ import annotations

import pytest

from duh.security.trifecta import Capability
from duh.tools.registry import get_all_tools

EXPECTED_CAPS: dict[str, Capability] = {
    "Bash": Capability.EXEC | Capability.NETWORK_EGRESS | Capability.FS_WRITE,
    "Read": Capability.READ_PRIVATE,
    "Write": Capability.FS_WRITE,
    "Edit": Capability.FS_WRITE,
    "MultiEdit": Capability.FS_WRITE,
    "NotebookEdit": Capability.FS_WRITE | Capability.EXEC,
    "WebFetch": Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS,
    "WebSearch": Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS,
    "Grep": Capability.READ_PRIVATE,
    "Glob": Capability.READ_PRIVATE,
    "HTTP": Capability.NETWORK_EGRESS,
    "Docker": Capability.EXEC | Capability.NETWORK_EGRESS,
    "MCP": Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS,
    "Database": Capability.READ_PRIVATE,
    "MemoryRecall": Capability.READ_PRIVATE,
    "Agent": Capability.EXEC,
    "Skill": Capability.EXEC,
    "Task": Capability.EXEC,
    "GitHub": Capability.NETWORK_EGRESS,
    "LSP": Capability.READ_PRIVATE,
    "AskUser": Capability.NONE,
    "Todo": Capability.NONE,
    "ToolSearch": Capability.NONE,
    "TestImpact": Capability.READ_PRIVATE,
    "Worktree": Capability.FS_WRITE | Capability.EXEC,
}


def test_all_tools_have_capabilities() -> None:
    for tool in get_all_tools():
        assert hasattr(tool, "capabilities"), (
            f"Tool {tool.name} missing 'capabilities' attribute"
        )
        assert isinstance(tool.capabilities, Capability), (
            f"Tool {tool.name}.capabilities is not a Capability flag"
        )


def test_known_tools_match_expected_caps() -> None:
    tools_by_name = {t.name: t for t in get_all_tools()}
    for name, expected in EXPECTED_CAPS.items():
        if name in tools_by_name:
            assert tools_by_name[name].capabilities == expected, (
                f"Tool {name} capabilities mismatch: "
                f"got {tools_by_name[name].capabilities}, expected {expected}"
            )
