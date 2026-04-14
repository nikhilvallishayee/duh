"""Tests for MCP subprocess sandboxing (ADR-054, 7.6)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from duh.adapters.mcp_executor import (
    _build_sandboxed_command,
    _compute_mcp_sandbox_policy,
    _sandbox_available,
)
from duh.adapters.mcp_manifest import MCPManifest


# ---------------------------------------------------------------------------
# Task 7.6.4: _compute_mcp_sandbox_policy
# ---------------------------------------------------------------------------


def test_default_manifest_denies_network() -> None:
    manifest = MCPManifest()  # default — no network
    policy = _compute_mcp_sandbox_policy(manifest)
    assert policy is not None
    assert policy.network_allowed is False


def test_network_manifest_allows_network() -> None:
    manifest = MCPManifest(network_allowed=True)
    policy = _compute_mcp_sandbox_policy(manifest)
    assert policy.network_allowed is True


def test_writable_paths_propagated() -> None:
    manifest = MCPManifest(writable_paths=frozenset({Path("/tmp/mcp")}))
    policy = _compute_mcp_sandbox_policy(manifest)
    assert "/tmp/mcp" in policy.writable_paths


def test_readable_paths_propagated() -> None:
    manifest = MCPManifest(readable_paths=frozenset({Path("/home/user/data")}))
    policy = _compute_mcp_sandbox_policy(manifest)
    assert "/home/user/data" in policy.readable_paths


def test_empty_paths_give_empty_policy_lists() -> None:
    manifest = MCPManifest()
    policy = _compute_mcp_sandbox_policy(manifest)
    assert policy.writable_paths == []
    assert policy.readable_paths == []


# ---------------------------------------------------------------------------
# Task 7.6.5: _build_sandboxed_command
# ---------------------------------------------------------------------------


def test_build_sandboxed_command_wraps_argv() -> None:
    """When sandbox is available, _build_sandboxed_command wraps the command."""
    manifest = MCPManifest()  # restrictive default
    result = _build_sandboxed_command("node", ["server.js"], manifest)
    if result is None:
        pytest.skip("No sandbox available on this platform")
    # Result should be a list with the sandbox wrapper + original args
    assert isinstance(result, list)
    assert len(result) > 2  # at minimum: sandbox + original command + args
    # Original args must appear somewhere in the combined argv
    assert "server.js" in " ".join(result)


def test_build_sandboxed_command_none_when_no_sandbox_available() -> None:
    """On platforms without sandbox support, return None."""
    with patch("duh.adapters.mcp_executor._sandbox_available", return_value=False):
        result = _build_sandboxed_command("node", ["server.js"], MCPManifest())
        assert result is None


def test_build_sandboxed_command_returns_list_or_none() -> None:
    """_build_sandboxed_command always returns list[str] | None."""
    result = _build_sandboxed_command("python", ["runner.py", "--port", "8080"], MCPManifest())
    assert result is None or isinstance(result, list)


def test_build_sandboxed_command_network_manifest() -> None:
    """Network-allowed manifest is accepted without error."""
    manifest = MCPManifest(network_allowed=True)
    result = _build_sandboxed_command("uvx", ["mcp-server-git"], manifest)
    # Should not raise; result is either a list or None
    assert result is None or isinstance(result, list)


# ---------------------------------------------------------------------------
# Task 7.6.6: _sandbox_available
# ---------------------------------------------------------------------------


def test_sandbox_available_returns_bool() -> None:
    """_sandbox_available always returns a bool."""
    result = _sandbox_available()
    assert isinstance(result, bool)


def test_sandbox_available_false_when_detect_raises() -> None:
    """If detect_sandbox_type raises, _sandbox_available returns False."""
    with patch(
        "duh.adapters.mcp_executor.detect_sandbox_type",
        side_effect=OSError("no sandbox"),
    ):
        assert _sandbox_available() is False
