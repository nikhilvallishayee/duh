"""Ghost snapshot mode -- fork engine state for speculative execution.

Two execution layers are provided:

1. ReadOnlyExecutor  (legacy "snapshot" mode)
   Blocks all mutating tools.  Read-only exploration only.

2. GhostExecutor  (ghost mode — ADR-039)
   Intercepts write calls into an in-memory dict overlay.  Reads are
   served from the overlay first, falling back to the real executor.
   The overlay is capped at GHOST_MAX_FILES files / GHOST_MAX_BYTES bytes.
   Writes that would breach the cap raise OverflowError.

   Call ``ghost_executor.merge_to_disk()`` to flush the overlay to the
   real filesystem.  The overlay is cleared after a successful merge.

GhostSnapshot tracks conversation state (a deep copy of messages at the
fork point) alongside the fs overlay and turn count.

Usage in REPL:
    /snapshot         -- enter legacy read-only snapshot mode
    /snapshot apply   -- merge snapshot messages back into main session
    /snapshot discard -- discard snapshot and return to main session

    /ghost            -- enter ghost mode (speculative writes)
    /ghost merge      -- apply overlay writes + ghost messages to main session
    /ghost discard    -- drop overlay and ghost messages, return to main session

Usage programmatically:
    # Legacy read-only mode
    executor = ReadOnlyExecutor(real_executor.run)
    snapshot = SnapshotSession(engine.messages)

    # Ghost mode
    overlay: dict[str, str] = {}
    ghost_exec = GhostExecutor(real_executor, overlay)
    ghost_snap = GhostSnapshot(
        id=str(uuid.uuid4()),
        parent_messages=copy.deepcopy(engine.messages),
        fs_overlay=overlay,
        created_at=time.time(),
        label="my exploration",
    )
    # ... run agent turns ...
    ghost_snap.increment_turn()  # call once per agent turn
    if keep:
        ghost_exec.merge_to_disk()
        engine._messages.extend(ghost_snap.get_new_messages())
    else:
        ghost_snap.discard()
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from duh.kernel.messages import Message
from duh.kernel.tool_categories import READ_TOOLS, MUTATING_TOOLS

# Tools that are safe to run in snapshot mode (read-only)
_SNAPSHOT_ALLOWED_TOOLS = READ_TOOLS

# Tools that are explicitly blocked in snapshot mode (mutating)
_SNAPSHOT_BLOCKED_TOOLS = MUTATING_TOOLS

# ---------------------------------------------------------------------------
# Ghost mode limits (ADR-039)
# ---------------------------------------------------------------------------

#: Maximum number of files the overlay may contain.
GHOST_MAX_FILES: int = 20

#: Maximum total byte size of all content in the overlay.
GHOST_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB

#: Turn count at which a warning is emitted.
GHOST_WARN_TURNS: int = 40

#: Turn count at which ghost mode expires automatically.
GHOST_MAX_TURNS: int = 50


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


# ---------------------------------------------------------------------------
# Ghost Snapshot (ADR-039) — speculative write overlay
# ---------------------------------------------------------------------------


@dataclass
class GhostSnapshot:
    """Metadata for an active ghost session.

    ``fs_overlay`` is a shared reference to the same dict held by the
    ``GhostExecutor``.  Merging or discarding is done via the executor;
    this object tracks conversation state and turn count.

    Attributes:
        id:              Unique identifier for this ghost session.
        parent_messages: Frozen copy of conversation at fork point.
        fs_overlay:      path → content for virtual writes (shared ref).
        created_at:      Unix timestamp of fork.
        label:           User-provided description for this exploration.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_messages: list[Message] = field(default_factory=list)
    fs_overlay: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    label: str = ""

    # Internal turn counter — not included in equality comparisons
    _turn_count: int = field(default=0, init=False, repr=False, compare=False)

    def increment_turn(self) -> None:
        """Advance the turn counter and check limits.

        Raises:
            RuntimeError: if ``GHOST_MAX_TURNS`` has been reached.

        Emits a ``UserWarning`` when the turn count hits ``GHOST_WARN_TURNS``.
        """
        self._turn_count += 1
        if self._turn_count == GHOST_WARN_TURNS:
            import warnings
            warnings.warn(
                f"Ghost mode turn {self._turn_count}: approaching the {GHOST_MAX_TURNS}-turn limit. "
                f"Use /ghost merge or /ghost discard soon.",
                UserWarning,
                stacklevel=2,
            )
        if self._turn_count > GHOST_MAX_TURNS:
            raise RuntimeError(
                f"Ghost mode expired: exceeded {GHOST_MAX_TURNS} turns. "
                f"Use /ghost discard to exit ghost mode."
            )

    @property
    def turn_count(self) -> int:
        """Current number of turns in this ghost session."""
        return self._turn_count

    def get_new_messages(self, original_count: int) -> list[Message]:
        """Return messages added after *original_count* in parent_messages.

        This is a convenience for callers who track the pre-fork message count.
        """
        return self.parent_messages[original_count:]

    def discard(self) -> None:
        """Clear the overlay and mark this snapshot as discarded."""
        self.fs_overlay.clear()
        self.parent_messages.clear()


class GhostExecutor:
    """Tool executor that intercepts writes into an in-memory overlay.

    Reads are served from the overlay first, falling back to the real executor.
    Write operations go to the overlay, never touching the real filesystem,
    until ``merge_to_disk()`` is called.

    Overlay caps (ADR-039):
        - Maximum ``GHOST_MAX_FILES`` distinct paths.
        - Maximum ``GHOST_MAX_BYTES`` total content bytes.

    Exceeding either cap raises ``OverflowError``.

    ``execute_write`` / ``execute_read`` are the primary public interface.
    ``run()`` dispatches tool calls from the engine using the standard
    ``(tool_name, input, **kwargs)`` signature and delegates everything
    except ``Write`` / ``Edit`` / ``MultiEdit`` / ``Read`` to the real executor.
    Bash and other command tools are blocked in ghost mode.
    """

    # Tools whose write path is intercepted into the overlay
    _OVERLAY_WRITE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "MultiEdit"})

    # Tools whose read path checks the overlay first
    _OVERLAY_READ_TOOLS: frozenset[str] = frozenset({"Read"})

    def __init__(
        self,
        real_executor: Any,
        overlay: dict[str, str],
    ) -> None:
        """
        Args:
            real_executor: The real executor (must have a ``run`` coroutine).
            overlay:       Shared dict of path → content for virtual writes.
                           Pass the same dict held by a ``GhostSnapshot``.
        """
        self._real = real_executor
        self.overlay = overlay

    # ------------------------------------------------------------------
    # Cap helpers
    # ------------------------------------------------------------------

    def _overlay_byte_size(self) -> int:
        return sum(len(v.encode("utf-8")) for v in self.overlay.values())

    def _check_caps(self, path: str, content: str) -> None:
        """Raise OverflowError if adding *content* at *path* would breach caps."""
        new_file_count = len(self.overlay) + (0 if path in self.overlay else 1)
        if new_file_count > GHOST_MAX_FILES:
            raise OverflowError(
                f"Ghost overlay cap exceeded: max {GHOST_MAX_FILES} files. "
                f"Use /ghost merge or /ghost discard to continue."
            )
        # Size cap: subtract existing content for this path (it's being replaced)
        existing_bytes = len(self.overlay.get(path, "").encode("utf-8"))
        new_total = self._overlay_byte_size() - existing_bytes + len(content.encode("utf-8"))
        if new_total > GHOST_MAX_BYTES:
            raise OverflowError(
                f"Ghost overlay size cap exceeded: max {GHOST_MAX_BYTES // (1024 * 1024)} MB. "
                f"Use /ghost merge or /ghost discard to continue."
            )

    # ------------------------------------------------------------------
    # Primary write / read interface
    # ------------------------------------------------------------------

    async def execute_write(self, path: str, content: str) -> dict[str, Any]:
        """Intercept a write into the overlay without touching disk.

        Returns a tool-result-like dict with ``output`` and ``ghost`` keys.
        """
        self._check_caps(path, content)
        self.overlay[path] = content
        return {
            "output": f"[ghost] Would write {len(content.encode('utf-8'))} bytes to {path}",
            "ghost": True,
        }

    async def execute_read(self, path: str) -> dict[str, Any]:
        """Read *path*, preferring the overlay over the real filesystem.

        Returns a tool-result-like dict with ``output`` and ``ghost`` keys.
        The ``ghost`` key is True if the content came from the overlay.
        """
        if path in self.overlay:
            return {"output": self.overlay[path], "ghost": True}
        result = await self._real.run("Read", {"file_path": path})
        return {"output": result, "ghost": False}

    # ------------------------------------------------------------------
    # Engine dispatch interface
    # ------------------------------------------------------------------

    async def run(
        self,
        tool_name: str,
        input: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        """Dispatch a tool call.

        - ``Write`` / ``Edit`` / ``MultiEdit``: intercepted into overlay.
        - ``Read``: served from overlay if path present, else real executor.
        - ``Bash`` and other command tools: blocked in ghost mode.
        - Everything else: forwarded to real executor.
        """
        if tool_name in self._OVERLAY_WRITE_TOOLS:
            path = input.get("file_path") or input.get("path", "")
            content = input.get("content", input.get("new_string", ""))
            result = await self.execute_write(path, content)
            return result["output"]

        if tool_name in self._OVERLAY_READ_TOOLS:
            path = input.get("file_path") or input.get("path", "")
            result = await self.execute_read(path)
            return result["output"]

        if tool_name == "Bash":
            raise PermissionError(
                "Ghost mode: Bash commands are blocked. "
                "Subprocess side-effects cannot be intercepted into the overlay. "
                "Use /ghost merge or /ghost discard to return to normal mode."
            )

        # All other tools (reads, searches, etc.) go to the real executor
        return await self._real.run(tool_name, input, **kwargs)

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge_to_disk(self) -> list[str]:
        """Write all overlay entries to the real filesystem.

        Returns the list of paths written.  The overlay is cleared after
        a successful merge so this object can be reused or discarded.

        Raises:
            IOError: if any write fails (overlay is NOT cleared in this case).
        """
        written: list[str] = []
        for path, content in list(self.overlay.items()):
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(path)
        # Clear only after all writes succeed
        self.overlay.clear()
        return written

    def __repr__(self) -> str:
        return (
            f"GhostExecutor("
            f"files={len(self.overlay)}, "
            f"bytes={self._overlay_byte_size()}"
            f")"
        )
