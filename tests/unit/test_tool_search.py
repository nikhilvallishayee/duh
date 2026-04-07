"""Tests for the ToolSearch progressive disclosure tool (ADR-018).

Covers:
- DeferredTool creation
- Keyword search (query mode)
- Exact selection (select mode)
- Edge cases: no match, empty input, select: prefix
"""

from __future__ import annotations

import json

import pytest

from duh.kernel.tool import ToolContext
from duh.tools.tool_search import DeferredTool, ToolSearchTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ctx() -> ToolContext:
    return ToolContext(cwd=".")


def _sample_tools() -> list[DeferredTool]:
    return [
        DeferredTool(
            name="mcp__fs__read",
            description="Read a file from the MCP filesystem server",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
            source="mcp",
        ),
        DeferredTool(
            name="mcp__fs__write",
            description="Write content to a file via MCP",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            source="mcp",
        ),
        DeferredTool(
            name="mcp__github__create_pr",
            description="Create a pull request on GitHub",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["title"],
            },
            source="mcp",
        ),
        DeferredTool(
            name="plugin__lint",
            description="Run linter on source files",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
            source="plugin",
        ),
    ]


# ===========================================================================
# DeferredTool
# ===========================================================================


class TestDeferredTool:
    def test_basic_creation(self):
        dt = DeferredTool(name="test", description="A test tool")
        assert dt.name == "test"
        assert dt.description == "A test tool"
        assert dt.input_schema == {}
        assert dt.source == ""

    def test_full_creation(self):
        dt = DeferredTool(
            name="x",
            description="X tool",
            input_schema={"type": "object", "properties": {}},
            source="mcp",
        )
        assert dt.source == "mcp"
        assert dt.input_schema["type"] == "object"


# ===========================================================================
# ToolSearchTool — search mode
# ===========================================================================


class TestToolSearchQuery:
    def _tool(self) -> ToolSearchTool:
        return ToolSearchTool(deferred_tools=_sample_tools())

    async def test_search_single_keyword(self):
        tool = self._tool()
        result = await tool.call({"query": "file"}, ctx())
        assert result.is_error is False
        assert "mcp__fs__read" in result.output
        assert "mcp__fs__write" in result.output

    async def test_search_github(self):
        tool = self._tool()
        result = await tool.call({"query": "github"}, ctx())
        assert result.is_error is False
        assert "mcp__github__create_pr" in result.output

    async def test_search_no_match(self):
        tool = self._tool()
        result = await tool.call({"query": "nonexistent_xyz"}, ctx())
        assert result.is_error is False
        assert "no tools found" in result.output.lower()
        assert result.metadata["match_count"] == 0

    async def test_search_multiple_keywords(self):
        tool = self._tool()
        result = await tool.call({"query": "read file"}, ctx())
        assert result.is_error is False
        # "read" and "file" both match mcp__fs__read
        assert "mcp__fs__read" in result.output

    async def test_search_case_insensitive(self):
        tool = self._tool()
        result = await tool.call({"query": "GITHUB"}, ctx())
        assert result.is_error is False
        assert "mcp__github__create_pr" in result.output

    async def test_search_respects_max_results(self):
        tool = self._tool()
        result = await tool.call({"query": "mcp", "max_results": 2}, ctx())
        # Should limit to 2 results
        lines = [l for l in result.output.splitlines() if l.startswith("- ")]
        assert len(lines) <= 2

    async def test_search_lint(self):
        tool = self._tool()
        result = await tool.call({"query": "lint"}, ctx())
        assert "plugin__lint" in result.output


# ===========================================================================
# ToolSearchTool — select mode
# ===========================================================================


class TestToolSearchSelect:
    def _tool(self) -> ToolSearchTool:
        return ToolSearchTool(deferred_tools=_sample_tools())

    async def test_select_single_tool(self):
        tool = self._tool()
        result = await tool.call({"select": "mcp__fs__read"}, ctx())
        assert result.is_error is False
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["name"] == "mcp__fs__read"
        assert "properties" in data[0]["input_schema"]

    async def test_select_multiple_tools(self):
        tool = self._tool()
        result = await tool.call({"select": "mcp__fs__read,mcp__github__create_pr"}, ctx())
        assert result.is_error is False
        data = json.loads(result.output)
        assert len(data) == 2
        names = {d["name"] for d in data}
        assert names == {"mcp__fs__read", "mcp__github__create_pr"}

    async def test_select_not_found(self):
        tool = self._tool()
        result = await tool.call({"select": "nonexistent"}, ctx())
        assert "not found" in result.output.lower()
        assert result.metadata["not_found"] == ["nonexistent"]

    async def test_select_partial_match(self):
        tool = self._tool()
        result = await tool.call({"select": "mcp__fs__read,nonexistent"}, ctx())
        # Output has JSON array then "Not found: ..." on last line
        assert result.metadata["found"] == 1
        assert result.metadata["not_found"] == ["nonexistent"]
        assert "mcp__fs__read" in result.output
        assert "not found" in result.output.lower()

    async def test_select_prefix_in_query(self):
        """query='select:ToolName' should trigger select mode."""
        tool = self._tool()
        result = await tool.call({"query": "select:mcp__fs__read"}, ctx())
        assert result.is_error is False
        data = json.loads(result.output)
        assert data[0]["name"] == "mcp__fs__read"

    async def test_select_empty_string(self):
        tool = self._tool()
        result = await tool.call({"select": ""}, ctx())
        assert result.is_error is True

    async def test_select_metadata(self):
        tool = self._tool()
        result = await tool.call({"select": "mcp__fs__read"}, ctx())
        assert result.metadata["found"] == 1
        assert result.metadata["not_found"] == []


# ===========================================================================
# ToolSearchTool — edge cases
# ===========================================================================


class TestToolSearchEdgeCases:
    async def test_no_input(self):
        tool = ToolSearchTool(deferred_tools=_sample_tools())
        result = await tool.call({}, ctx())
        assert result.is_error is True

    async def test_empty_query(self):
        tool = ToolSearchTool(deferred_tools=_sample_tools())
        result = await tool.call({"query": ""}, ctx())
        assert result.is_error is True

    async def test_no_deferred_tools(self):
        tool = ToolSearchTool(deferred_tools=[])
        result = await tool.call({"query": "anything"}, ctx())
        assert "no tools found" in result.output.lower()

    async def test_is_read_only(self):
        tool = ToolSearchTool()
        assert tool.is_read_only is True
        assert tool.is_destructive is False

    async def test_check_permissions(self):
        tool = ToolSearchTool()
        perm = await tool.check_permissions({"query": "x"}, ctx())
        assert perm["allowed"] is True

    def test_schema_structure(self):
        tool = ToolSearchTool()
        assert tool.name == "ToolSearch"
        assert isinstance(tool.description, str)
        assert tool.input_schema["type"] == "object"
        assert "query" in tool.input_schema["properties"]
        assert "select" in tool.input_schema["properties"]

    async def test_add_tool(self):
        tool = ToolSearchTool()
        assert len(tool.deferred_tools) == 0
        tool.add_tool(DeferredTool(name="new", description="New tool"))
        assert len(tool.deferred_tools) == 1
        result = await tool.call({"query": "new"}, ctx())
        assert "new" in result.output.lower()

    async def test_deferred_tools_property(self):
        tools = _sample_tools()
        tool = ToolSearchTool(deferred_tools=tools)
        assert len(tool.deferred_tools) == 4
