"""ToolSearchTool -- progressive tool disclosure via lazy schema loading.

See ADR-018 for the full rationale.

Deferred tools are listed by name only in the system prompt.
The model calls ToolSearch to load a tool's full schema before
calling it. This keeps the initial prompt small when many tools
are available.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability


# ---------------------------------------------------------------------------
# Deferred tool definition
# ---------------------------------------------------------------------------

@dataclass
class DeferredTool:
    """A tool whose full schema is deferred (not in the initial prompt).

    Attributes:
        name: Tool name (e.g., ``mcp__filesystem__read_file``).
        description: Short description for search and discovery.
        input_schema: Full JSON Schema, held back from the system prompt.
        source: Origin of the tool (``mcp``, ``plugin``, etc.).
    """

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    source: str = ""


# ---------------------------------------------------------------------------
# ToolSearchTool
# ---------------------------------------------------------------------------

class ToolSearchTool:
    """Search for tools and load their full schemas on demand.

    Two modes:

    1. **Search** (``query``): Keyword search across tool names and
       descriptions. Returns matching names with descriptions.

    2. **Select** (``select``): Comma-separated tool names. Returns
       full JSON Schema definitions for the named tools.
    """

    name = "ToolSearch"
    capabilities = Capability.NONE
    description = (
        "Search for available tools by keyword, or select specific tools "
        "by name to load their full schema. Use this to discover deferred "
        "tools before calling them."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Keyword search across tool names and descriptions. "
                    "Returns matching tools with their descriptions."
                ),
            },
            "select": {
                "type": "string",
                "description": (
                    "Comma-separated tool names to load full schemas for. "
                    'E.g., "select:Read,Edit" or just tool names.'
                ),
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of search results (default: 5).",
                "default": 5,
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": [],
    }

    def __init__(self, deferred_tools: list[DeferredTool] | None = None) -> None:
        self._tools: dict[str, DeferredTool] = {}
        if deferred_tools:
            for tool in deferred_tools:
                self._tools[tool.name] = tool

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    @property
    def deferred_tools(self) -> list[DeferredTool]:
        """All registered deferred tools."""
        return list(self._tools.values())

    def add_tool(self, tool: DeferredTool) -> None:
        """Register a deferred tool."""
        self._tools[tool.name] = tool

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        query = input.get("query", "").strip()
        select = input.get("select", "").strip()
        max_results = input.get("max_results", 5)

        # Handle select: prefix in query
        if query.startswith("select:"):
            select = query[len("select:"):]
            query = ""

        if select:
            return self._handle_select(select)

        if query:
            return self._handle_search(query, max_results)

        return ToolResult(
            output="Provide either 'query' (keyword search) or 'select' (tool names) parameter.",
            is_error=True,
        )

    def _handle_select(self, select_str: str) -> ToolResult:
        """Return full schemas for the named tools."""
        names = [n.strip() for n in select_str.split(",") if n.strip()]

        if not names:
            return ToolResult(
                output="No tool names provided in select.",
                is_error=True,
            )

        results: list[dict[str, Any]] = []
        not_found: list[str] = []

        for name in names:
            tool = self._tools.get(name)
            if tool:
                results.append({
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    "source": tool.source,
                })
            else:
                not_found.append(name)

        output_parts: list[str] = []
        if results:
            output_parts.append(json.dumps(results, indent=2))
        if not_found:
            output_parts.append(f"Not found: {', '.join(not_found)}")

        return ToolResult(
            output="\n".join(output_parts),
            metadata={"found": len(results), "not_found": not_found},
        )

    def _handle_search(self, query: str, max_results: int) -> ToolResult:
        """Keyword search across tool names and descriptions."""
        query_lower = query.lower()
        keywords = query_lower.split()

        scored: list[tuple[int, DeferredTool]] = []
        for tool in self._tools.values():
            searchable = f"{tool.name} {tool.description}".lower()
            # Score: count of keywords found in the searchable text
            score = sum(1 for kw in keywords if kw in searchable)
            if score > 0:
                scored.append((score, tool))

        # Sort by score descending, then name ascending
        scored.sort(key=lambda x: (-x[0], x[1].name))
        matches = scored[:max_results]

        if not matches:
            return ToolResult(
                output=f"No tools found matching: {query!r}",
                metadata={"match_count": 0},
            )

        lines: list[str] = []
        for _score, tool in matches:
            lines.append(f"- {tool.name}: {tool.description}")

        return ToolResult(
            output="\n".join(lines),
            metadata={"match_count": len(matches)},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
