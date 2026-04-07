"""Task management — in-memory task tracking for a session.

The model uses TaskManager during a session to create, update, and
display tasks as a checklist. No persistence — tasks live only for
the duration of the session.

    mgr = TaskManager()
    t = mgr.create("Refactor auth module")
    mgr.update(t.id, "in_progress")
    print(mgr.summary())
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

Status = Literal["pending", "in_progress", "completed"]

VALID_STATUSES: set[str] = {"pending", "in_progress", "completed"}

# Status display glyphs
_GLYPHS: dict[str, str] = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "completed": "[x]",
}


@dataclass
class Task:
    """A single tracked task."""

    id: str
    description: str
    status: Status = "pending"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TaskManager:
    """In-memory task tracker.

    Thread-safe enough for a single-session REPL (no locking needed).
    """

    def __init__(self) -> None:
        self._tasks: list[Task] = []
        self._index: dict[str, Task] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create(self, description: str) -> Task:
        """Create a new pending task. Returns the Task."""
        task = Task(
            id=uuid.uuid4().hex[:8],
            description=description,
        )
        self._tasks.append(task)
        self._index[task.id] = task
        return task

    def update(self, task_id: str, status: str) -> Task:
        """Update a task's status. Raises KeyError / ValueError on bad input."""
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status {status!r}. Must be one of: {', '.join(sorted(VALID_STATUSES))}"
            )
        task = self._index.get(task_id)
        if task is None:
            raise KeyError(f"No task with id {task_id!r}")
        task.status = status  # type: ignore[assignment]
        return task

    def get(self, task_id: str) -> Task | None:
        """Look up a task by id. Returns None if not found."""
        return self._index.get(task_id)

    def list_all(self) -> list[Task]:
        """Return all tasks in creation order."""
        return list(self._tasks)

    def summary(self) -> str:
        """Return a formatted checklist string.

        Example output::

            Tasks (1/3 completed):
              [x] abc12345 — Write unit tests
              [~] def67890 — Refactor module
              [ ] 0a1b2c3d — Update docs
        """
        if not self._tasks:
            return "No tasks."

        completed = sum(1 for t in self._tasks if t.status == "completed")
        lines = [f"Tasks ({completed}/{len(self._tasks)} completed):"]
        for t in self._tasks:
            glyph = _GLYPHS.get(t.status, "[ ]")
            lines.append(f"  {glyph} {t.id} — {t.description}")
        return "\n".join(lines)
