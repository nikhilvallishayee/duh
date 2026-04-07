"""UndoStack — tracks file modifications for /undo support.

Stores (file_path, original_content) before each Write or Edit so the
most recent change can be reversed.  Uses a fixed-size ring buffer
(default 20 entries) to bound memory usage.

    from duh.kernel.undo import UndoStack

    stack = UndoStack()
    stack.push("/tmp/foo.py", "original content")  # before overwrite
    stack.push("/tmp/new.py", None)                 # before creating new file
    path, msg = stack.undo()                        # restores or deletes
"""

from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from typing import Any


# Sentinel for "file did not exist before the write".
_NEW_FILE: None = None

# (file_path, original_content_or_None)
UndoEntry = tuple[str, str | None]


class UndoStack:
    """Fixed-size stack of file snapshots that supports undo."""

    def __init__(self, maxlen: int = 20) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        self._stack: deque[UndoEntry] = deque(maxlen=maxlen)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, file_path: str, original_content: str | None) -> None:
        """Record the state of *file_path* before a mutation.

        Args:
            file_path: Absolute path to the file about to be changed.
            original_content: The file's content before the change, or
                ``None`` if the file did not previously exist (i.e. a
                brand-new Write).
        """
        self._stack.append((file_path, original_content))

    def undo(self) -> tuple[str, str]:
        """Undo the most recent file modification.

        Returns:
            A ``(file_path, message)`` tuple describing what was done.

        Raises:
            IndexError: If the stack is empty.
        """
        if not self._stack:
            raise IndexError("Nothing to undo.")

        file_path, original = self._stack.pop()

        if original is None:
            # File was newly created — delete it.
            try:
                os.remove(file_path)
                return file_path, f"Deleted {file_path} (was newly created)."
            except FileNotFoundError:
                return file_path, f"{file_path} already removed."
            except OSError as exc:
                return file_path, f"Failed to delete {file_path}: {exc}"
        else:
            # Restore previous content.
            try:
                Path(file_path).write_text(original, encoding="utf-8")
                return file_path, f"Restored {file_path} to previous content."
            except OSError as exc:
                return file_path, f"Failed to restore {file_path}: {exc}"

    @property
    def depth(self) -> int:
        """Number of entries currently on the stack."""
        return len(self._stack)

    @property
    def maxlen(self) -> int:
        """Maximum number of entries the stack can hold."""
        assert self._stack.maxlen is not None
        return self._stack.maxlen

    def peek(self) -> UndoEntry | None:
        """Return the top entry without popping, or ``None`` if empty."""
        if not self._stack:
            return None
        return self._stack[-1]

    def clear(self) -> None:
        """Discard all entries."""
        self._stack.clear()
