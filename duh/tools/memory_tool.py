"""MemoryStore and MemoryRecall tools -- persistent cross-session facts.

MemoryStoreTool saves a fact about the codebase for future sessions.
MemoryRecallTool searches saved facts by keyword.

Facts are stored per-project at ~/.config/duh/memory/<project-hash>/facts.jsonl.
"""

from __future__ import annotations

from typing import Any

from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability


class MemoryStoreTool:
    """Save a fact about the codebase for future sessions."""

    name = "MemoryStore"
    capabilities = Capability.NONE
    description = (
        "Save a key learning or fact about the codebase. "
        "Persists across sessions so future conversations start informed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": (
                    "Short identifier for the fact (e.g. 'auth-pattern', "
                    "'db-schema-version'). Used for deduplication."
                ),
            },
            "value": {
                "type": "string",
                "description": "The fact or learning to remember.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for categorization (e.g. ['auth', 'security']).",
            },
        },
        "required": ["key", "value"],
    }

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        key = input.get("key", "").strip()
        value = input.get("value", "").strip()
        tags = input.get("tags", [])

        if not key:
            return ToolResult(output="key is required", is_error=True)
        if not value:
            return ToolResult(output="value is required", is_error=True)

        from duh.adapters.memory_store import FileMemoryStore

        store = FileMemoryStore(cwd=context.cwd)
        entry = store.store_fact(key=key, value=value, tags=tags)
        return ToolResult(
            output=f"Stored fact '{key}' for future sessions.",
            metadata={"entry": entry},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}


class MemoryRecallTool:
    """Search saved facts about the codebase by keyword."""

    name = "MemoryRecall"
    capabilities = Capability.READ_PRIVATE
    description = (
        "Search previously saved facts and learnings about the codebase. "
        "Use to recall patterns, decisions, or context from past sessions."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keyword or phrase to search for in saved facts.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results to return (default: 10).",
                "minimum": 1,
                "maximum": 50,
            },
        },
        "required": ["query"],
    }

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        query = input.get("query", "").strip()
        limit = input.get("limit", 10)

        if not query:
            return ToolResult(output="query is required", is_error=True)

        from duh.adapters.memory_store import FileMemoryStore

        store = FileMemoryStore(cwd=context.cwd)
        results = store.recall_facts(query=query, limit=limit)

        if not results:
            return ToolResult(
                output=f"No saved facts matching '{query}'.",
                metadata={"match_count": 0},
            )

        lines: list[str] = []
        for fact in results:
            key = fact.get("key", "?")
            value = fact.get("value", "")
            tags = fact.get("tags", [])
            ts = fact.get("timestamp", "")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            lines.append(f"- {key}: {value}{tag_str}")
            if ts:
                lines.append(f"  (saved: {ts})")

        return ToolResult(
            output="\n".join(lines),
            metadata={"match_count": len(results)},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
