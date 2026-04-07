"""T1 integration test: D.U.H. MCPExecutor <-> Playwright MCP server.

Proves that MCPExecutor.from_config can:
  1. Parse a Playwright MCP server configuration
  2. Spawn the @playwright/mcp server via stdio transport
  3. Initialize the MCP session handshake
  4. Discover the Playwright tool catalogue
  5. Find expected navigation tools (e.g. browser_navigate)

Requires:
  - ``mcp`` Python package (pip install mcp)
  - ``npx @playwright/mcp`` available on PATH

If either is missing the test is skipped rather than failing.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

_has_npx = shutil.which("npx") is not None

try:
    from mcp import ClientSession, StdioServerParameters  # noqa: F401
    from mcp.client.stdio import stdio_client  # noqa: F401

    _has_mcp_sdk = True
except ImportError:
    _has_mcp_sdk = False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(not _has_mcp_sdk, reason="mcp Python package not installed"),
    pytest.mark.skipif(not _has_npx, reason="npx not found on PATH"),
]

# Import MCPExecutor *after* the skipif guards so we don't blow up at
# collection time when the mcp package is missing.
if _has_mcp_sdk:
    from duh.adapters.mcp_executor import MCPExecutor, MCPServerConfig


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PLAYWRIGHT_CONFIG: dict = {
    "mcpServers": {
        "playwright": {
            "command": "npx",
            "args": ["@playwright/mcp", "--headless"],
        }
    }
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_playwright_available() -> bool:
    """Quick smoke-test: can npx resolve @playwright/mcp?"""
    try:
        result = subprocess.run(
            ["npx", "@playwright/mcp", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ---------------------------------------------------------------------------
# Config parsing tests (always run when mcp SDK is present)
# ---------------------------------------------------------------------------


class TestMCPExecutorFromConfig:
    """Verify MCPExecutor.from_config parses the Playwright entry correctly."""

    def test_from_config_parses_playwright(self):
        """from_config should produce an executor with the right server config."""
        executor = MCPExecutor.from_config(PLAYWRIGHT_CONFIG)

        assert "playwright" in executor._servers
        config = executor._servers["playwright"]
        assert isinstance(config, MCPServerConfig)
        assert config.command == "npx"
        assert "@playwright/mcp" in config.args
        assert "--headless" in config.args

    def test_from_config_empty(self):
        """from_config with no mcpServers produces an executor with no servers."""
        executor = MCPExecutor.from_config({})
        assert len(executor._servers) == 0
        assert executor.tool_names == []

    def test_from_config_multiple_servers(self):
        """Multiple server entries are all parsed."""
        config = {
            "mcpServers": {
                "playwright": {"command": "npx", "args": ["@playwright/mcp"]},
                "filesystem": {
                    "command": "npx",
                    "args": ["@anthropic-ai/mcp-server-filesystem"],
                },
            }
        }
        executor = MCPExecutor.from_config(config)
        assert "playwright" in executor._servers
        assert "filesystem" in executor._servers


# ---------------------------------------------------------------------------
# Live connection tests (require Playwright MCP via npx)
# ---------------------------------------------------------------------------


class TestMCPPlaywrightConnection:
    """Verify MCPExecutor can connect to the Playwright MCP server.

    These tests spawn a real @playwright/mcp subprocess and exercise
    the full MCP handshake: initialize -> list_tools -> disconnect.
    """

    @pytest.fixture
    def executor(self) -> MCPExecutor:
        return MCPExecutor.from_config(PLAYWRIGHT_CONFIG)

    async def test_connect_discovers_tools(self, executor: MCPExecutor):
        """Connect to Playwright MCP and verify that tools are discovered."""
        if not _check_playwright_available():
            pytest.skip("@playwright/mcp not resolvable via npx")

        try:
            tools_by_server = await asyncio.wait_for(
                executor.connect_all(),
                timeout=60,
            )
        except Exception as exc:
            pytest.fail(f"Could not connect to Playwright MCP server: {exc}")

        try:
            assert "playwright" in tools_by_server, (
                "Expected 'playwright' key in connect_all results"
            )

            tools = tools_by_server["playwright"]
            assert len(tools) > 0, (
                "Playwright MCP server should expose at least one tool"
            )

            tool_names = [t.name for t in tools]
            navigate_candidates = [
                "browser_navigate",
                "playwright_navigate",
                "navigate",
            ]
            found_navigate = any(name in tool_names for name in navigate_candidates)
            assert found_navigate, (
                f"Expected a navigation tool among {navigate_candidates}, "
                f"but discovered tools are: {tool_names}"
            )
        finally:
            await executor.disconnect_all()

    async def test_tool_names_are_qualified(self, executor: MCPExecutor):
        """Discovered tools should use the mcp__<server>__<tool> naming."""
        if not _check_playwright_available():
            pytest.skip("@playwright/mcp not resolvable via npx")

        try:
            await asyncio.wait_for(
                executor.connect_all(),
                timeout=60,
            )
        except Exception as exc:
            pytest.fail(f"Could not connect to Playwright MCP server: {exc}")

        try:
            qualified_names = executor.tool_names
            assert len(qualified_names) > 0

            for qname in qualified_names:
                assert qname.startswith("mcp__playwright__"), (
                    f"Qualified tool name should start with 'mcp__playwright__', "
                    f"got: {qname}"
                )
        finally:
            await executor.disconnect_all()

    async def test_tool_info_has_schema(self, executor: MCPExecutor):
        """Each discovered tool should carry name, description, and input_schema."""
        if not _check_playwright_available():
            pytest.skip("@playwright/mcp not resolvable via npx")

        try:
            await asyncio.wait_for(
                executor.connect_all(),
                timeout=60,
            )
        except Exception as exc:
            pytest.fail(f"Could not connect to Playwright MCP server: {exc}")

        try:
            tools = executor.list_tools()
            assert len(tools) > 0

            for tool in tools:
                assert tool.name, "Tool must have a non-empty name"
                assert tool.server_name == "playwright"
                assert isinstance(tool.description, str)
                assert isinstance(tool.input_schema, dict)
        finally:
            await executor.disconnect_all()
