"""TodoWrite tool — structured checklist management.

Allows the model to create and update a todo list with status tracking.
This is the tool equivalent of Claude Code's TodoWrite — it gives the
model a way to maintain structured task state.

    tool = TodoWriteTool()
    result = await tool.call({
        "todos": [
            {"id": "1", "text": "Fix the bug", "status": "pending"},
            {"id": "2", "text": "Write tests", "status": "done"},
        ]
    }, context)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult

_VALID_STATUSES = frozenset({"pending", "in_progress", "done", "blocked", "cancelled"})


@dataclass
class TodoItem:
    """A single todo item."""
    id: str
    text: str
    status: str = "pending"


class TodoWriteTool:
    """Structured checklist management tool."""

    name = "TodoWrite"
    description = (
        "Create or update a structured todo checklist. "
        "Each todo has an id, text, and status "
        "(pending | in_progress | done | blocked | cancelled)."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "List of todo items to create or update.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Unique todo identifier"},
                        "text": {"type": "string", "description": "Todo description"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done", "blocked", "cancelled"],
                            "description": "Current status",
                        },
                    },
                    "required": ["id", "text", "status"],
                },
            },
        },
        "required": ["todos"],
    }

    def __init__(self) -> None:
        self._todos: dict[str, TodoItem] = {}

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        todos = input.get("todos", [])

        for item in todos:
            item_id = item.get("id", "")
            text = item.get("text", "")
            status = item.get("status", "pending")

            if status not in _VALID_STATUSES:
                return ToolResult(
                    output=f"Invalid status '{status}' for todo '{item_id}'. "
                           f"Valid: {', '.join(sorted(_VALID_STATUSES))}",
                    is_error=True,
                )

            self._todos[item_id] = TodoItem(id=item_id, text=text, status=status)

        # Build summary
        total = len(self._todos)
        done = sum(1 for t in self._todos.values() if t.status == "done")
        pending = sum(1 for t in self._todos.values() if t.status == "pending")
        in_progress = sum(1 for t in self._todos.values() if t.status == "in_progress")

        lines = [f"Updated {len(todos)} todos ({total} total)."]
        if total > 0:
            lines.append(f"  Done: {done} | In progress: {in_progress} | Pending: {pending}")
        for t in self._todos.values():
            marker = {"done": "[x]", "in_progress": "[~]", "pending": "[ ]",
                       "blocked": "[!]", "cancelled": "[-]"}.get(t.status, "[ ]")
            lines.append(f"  {marker} {t.id}: {t.text}")

        return ToolResult(output="\n".join(lines))

    def summary(self) -> str:
        """Return a text summary of all todos (for /tasks command)."""
        if not self._todos:
            return "No tasks."
        lines: list[str] = []
        for t in self._todos.values():
            marker = {"done": "[x]", "in_progress": "[~]", "pending": "[ ]",
                       "blocked": "[!]", "cancelled": "[-]"}.get(t.status, "[ ]")
            lines.append(f"  {marker} {t.id}: {t.text}")
        done = sum(1 for t in self._todos.values() if t.status == "done")
        lines.append(f"  ({done}/{len(self._todos)} done)")
        return "\n".join(lines)

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
