"""TaskTool — create, update, and list tasks during a session.

Equivalent to Claude Code's TodoWrite / TaskCreate functionality.
The model calls this tool to track its own work as a checklist
visible to the user.
"""

from __future__ import annotations

from typing import Any

from duh.kernel.tasks import TaskManager, VALID_STATUSES
from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability


class TaskTool:
    """In-session task/todo management."""

    name = "Task"
    capabilities = Capability.EXEC
    description = (
        "Create, update, or list tasks. Use this to track work during "
        "a session as a visible checklist."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "update", "list"],
                "description": "Action to perform.",
            },
            "description": {
                "type": "string",
                "description": "Task description (required for 'create').",
            },
            "task_id": {
                "type": "string",
                "description": "Task id (required for 'update').",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "completed"],
                "description": "New status (required for 'update').",
            },
        },
        "required": ["action"],
    }

    def __init__(self, task_manager: TaskManager | None = None) -> None:
        self._mgr = task_manager or TaskManager()

    @property
    def task_manager(self) -> TaskManager:
        return self._mgr

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        action = input.get("action", "")

        if action == "create":
            return self._create(input)
        if action == "update":
            return self._update(input)
        if action == "list":
            return self._list()

        return ToolResult(
            output=f"Unknown action {action!r}. Use 'create', 'update', or 'list'.",
            is_error=True,
        )

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _create(self, input: dict[str, Any]) -> ToolResult:
        description = input.get("description", "").strip()
        if not description:
            return ToolResult(
                output="'description' is required for action 'create'.",
                is_error=True,
            )
        task = self._mgr.create(description)
        return ToolResult(
            output=f"Created task {task.id}: {task.description}",
            metadata={"task_id": task.id, "status": task.status},
        )

    def _update(self, input: dict[str, Any]) -> ToolResult:
        task_id = input.get("task_id", "").strip()
        status = input.get("status", "").strip()

        if not task_id:
            return ToolResult(
                output="'task_id' is required for action 'update'.",
                is_error=True,
            )
        if not status:
            return ToolResult(
                output="'status' is required for action 'update'.",
                is_error=True,
            )
        if status not in VALID_STATUSES:
            return ToolResult(
                output=f"Invalid status {status!r}. Must be one of: {', '.join(sorted(VALID_STATUSES))}.",
                is_error=True,
            )

        try:
            task = self._mgr.update(task_id, status)
        except KeyError:
            return ToolResult(
                output=f"No task with id {task_id!r}.",
                is_error=True,
            )

        return ToolResult(
            output=f"Updated task {task.id} -> {task.status}",
            metadata={"task_id": task.id, "status": task.status},
        )

    def _list(self) -> ToolResult:
        summary = self._mgr.summary()
        tasks = self._mgr.list_all()
        return ToolResult(
            output=summary,
            metadata={"count": len(tasks)},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
