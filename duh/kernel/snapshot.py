"""Ghost snapshot mode -- fork engine state for read-only exploration.

Allows the model to explore "what-if" scenarios without committing changes.
The snapshot forks the message history and wraps tool execution in a
read-only layer that blocks all mutating operations.

Usage in REPL:
    /snapshot         -- enter snapshot mode
    /snapshot apply   -- merge snapshot messages back into main session
    /snapshot discard -- discard snapshot and return to main session

Usage programmatically:
    executor = ReadOnlyExecutor(real_executor.run)
    snapshot = SnapshotSession(engine.messages)
    # ... run queries against snapshot ...
    if keep:
        new_messages = snapshot.get_new_messages()
        engine._messages.extend(new_messages)
    else:
        snapshot.discard()
"""

from __future__ import annotations

import copy
from typing import Any, Awaitable, Callable

from duh.kernel.messages import Message


# Tools that are safe to run in snapshot mode (read-only)
_SNAPSHOT_ALLOWED_TOOLS = frozenset({
    "Read", "Glob", "Grep", "ToolSearch", "WebSearch",
    "MemoryRecall", "Skill",
})

# Tools that are explicitly blocked in snapshot mode (mutating)
_SNAPSHOT_BLOCKED_TOOLS = frozenset({
    "Write", "Edit", "MultiEdit", "Bash", "NotebookEdit",
    "WebFetch", "HTTP", "Database", "Docker", "GitHub",
    "Task", "EnterWorktree", "ExitWorktree", "MemoryStore",
})


class ReadOnlyExecutor:
    """Wraps a tool executor to block all mutating operations.

    Only read-only tools (Read, Glob, Grep, ToolSearch, WebSearch) are
    allowed. Everything else raises PermissionError.
    """

    def __init__(self, inner_run: Callable[..., Awaitable[Any]]):
        self._inner_run = inner_run

    async def run(
        self,
        tool_name: str,
        input: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        """Execute a tool if it's read-only, otherwise raise PermissionError."""
        if tool_name in _SNAPSHOT_ALLOWED_TOOLS:
            return await self._inner_run(tool_name, input, **kwargs)

        raise PermissionError(
            f"Snapshot mode: tool '{tool_name}' is blocked. "
            f"Only read-only tools are allowed in snapshot mode. "
            f"Use /snapshot apply to return to normal mode and execute writes."
        )


class SnapshotSession:
    """A forked conversation state for read-only exploration.

    Deep-copies the message history so changes to the snapshot don't
    affect the original session. New messages added during snapshot
    exploration can be merged back (apply) or thrown away (discard).
    """

    def __init__(self, messages: list[Message]):
        self._original_count = len(messages)
        self._messages: list[Message] = copy.deepcopy(messages)
        self._is_discarded = False

    @property
    def messages(self) -> list[Message]:
        """Return the snapshot's message list."""
        return self._messages

    @property
    def is_discarded(self) -> bool:
        """True if the snapshot has been discarded."""
        return self._is_discarded

    def add_message(self, message: Message) -> None:
        """Add a message to the snapshot."""
        if self._is_discarded:
            raise RuntimeError("Cannot add messages to a discarded snapshot")
        self._messages.append(message)

    def get_new_messages(self) -> list[Message]:
        """Return only the messages added after the snapshot was created.

        Returns an empty list if the snapshot has been discarded.
        """
        if self._is_discarded:
            return []
        return self._messages[self._original_count:]

    def discard(self) -> None:
        """Discard the snapshot. Clears all messages."""
        self._messages.clear()
        self._is_discarded = True

    def __str__(self) -> str:
        status = "discarded" if self._is_discarded else "active"
        total = len(self._messages)
        new = len(self.get_new_messages())
        return f"Snapshot({status}, {total} messages, {new} new)"
