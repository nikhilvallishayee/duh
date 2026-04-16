"""UndoStack — tracks file modifications for /undo support.

Stores (file_path, original_content) before each Write or Edit so the
most recent change can be reversed.  Uses a fixed-size ring buffer
(default 20 entries) to bound memory usage.

PERF-15: Additionally caps the total bytes held across all snapshots.
Individual snapshots larger than ``per_entry_max_bytes`` are spilled to
a temp file on disk and the in-memory entry keeps a pointer to the spill
file.  This lets ``undo`` handle very large files without pinning
hundreds of MB in RAM across the ring buffer.

    from duh.kernel.undo import UndoStack

    stack = UndoStack()
    stack.push("/tmp/foo.py", "original content")  # before overwrite
    stack.push("/tmp/new.py", None)                 # before creating new file
    path, msg = stack.undo()                        # restores or deletes
"""

from __future__ import annotations

import os
import tempfile
from collections import deque
from pathlib import Path
from typing import Any


# Sentinel for "file did not exist before the write".
_NEW_FILE: None = None

# (file_path, original_content_or_None) — legacy alias kept for external callers.
UndoEntry = tuple[str, str | None]

# Defaults: any single snapshot >1 MiB gets spilled to a temp file.
_DEFAULT_PER_ENTRY_MAX_BYTES = 1 * 1024 * 1024
# Cap on total in-memory snapshot bytes.  Oldest entries are spilled to
# disk when this threshold would be exceeded.
_DEFAULT_TOTAL_MAX_BYTES = 8 * 1024 * 1024


class _Snapshot:
    """One undo snapshot.

    Either holds ``content`` in memory OR a ``spill_path`` pointing at a
    temp file, but never both.  ``is_new_file`` is True when the target
    did not exist prior to the write (undo = delete).
    """

    __slots__ = ("file_path", "is_new_file", "content", "spill_path", "byte_size")

    def __init__(
        self,
        file_path: str,
        *,
        is_new_file: bool,
        content: str | None = None,
        spill_path: str | None = None,
        byte_size: int = 0,
    ) -> None:
        self.file_path = file_path
        self.is_new_file = is_new_file
        self.content = content
        self.spill_path = spill_path
        self.byte_size = byte_size

    def load(self) -> str | None:
        """Return the original content, reading from spill if necessary.

        Returns ``None`` for new-file snapshots (undo = delete).
        """
        if self.is_new_file:
            return None
        if self.spill_path is not None:
            try:
                return Path(self.spill_path).read_text(encoding="utf-8")
            except OSError:
                # Spill file vanished — best-effort: signal empty restore.
                return ""
        return self.content or ""

    def release(self) -> None:
        """Remove any on-disk spill file.  Idempotent."""
        if self.spill_path is not None:
            try:
                os.remove(self.spill_path)
            except OSError:
                pass
            self.spill_path = None


class UndoStack:
    """Fixed-size stack of file snapshots that supports undo.

    Memory is bounded on two axes:
      * ``maxlen`` — max number of snapshots retained.
      * ``total_max_bytes`` — total bytes held in memory across all
        snapshots.  When adding a new snapshot would exceed this cap,
        oldest in-memory snapshots are spilled to temp files on disk
        until we're back under budget.  Entries larger than
        ``per_entry_max_bytes`` are spilled immediately on push().
    """

    def __init__(
        self,
        maxlen: int = 20,
        *,
        per_entry_max_bytes: int = _DEFAULT_PER_ENTRY_MAX_BYTES,
        total_max_bytes: int = _DEFAULT_TOTAL_MAX_BYTES,
    ) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        if per_entry_max_bytes < 1:
            raise ValueError("per_entry_max_bytes must be >= 1")
        if total_max_bytes < 1:
            raise ValueError("total_max_bytes must be >= 1")
        self._stack: deque[_Snapshot] = deque(maxlen=maxlen)
        self._per_entry_max_bytes = per_entry_max_bytes
        self._total_max_bytes = total_max_bytes
        self._in_memory_bytes = 0  # running sum of in-memory byte_size

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
        # If the ring buffer is full, the entry that will be evicted
        # might be on disk — release its spill file.
        if len(self._stack) == self._stack.maxlen:
            oldest = self._stack[0]
            self._release(oldest)

        if original_content is None:
            snap = _Snapshot(file_path, is_new_file=True)
        else:
            byte_size = len(original_content.encode("utf-8"))
            if byte_size > self._per_entry_max_bytes:
                # Spill to disk immediately — the entry itself is too big.
                spill = self._write_spill(original_content)
                snap = _Snapshot(
                    file_path,
                    is_new_file=False,
                    spill_path=spill,
                    byte_size=byte_size,
                )
            else:
                snap = _Snapshot(
                    file_path,
                    is_new_file=False,
                    content=original_content,
                    byte_size=byte_size,
                )
                self._in_memory_bytes += byte_size
                # If this new entry pushed us over total budget, spill
                # oldest in-memory entries until we're back under cap.
                self._enforce_total_budget()

        self._stack.append(snap)

    def undo(self) -> tuple[str, str]:
        """Undo the most recent file modification.

        Returns:
            A ``(file_path, message)`` tuple describing what was done.

        Raises:
            IndexError: If the stack is empty.
        """
        if not self._stack:
            raise IndexError("Nothing to undo.")

        snap = self._stack.pop()
        file_path = snap.file_path
        original = snap.load()
        # Release any spill file and byte accounting now that we've
        # materialised the content (or confirmed it was a new-file undo).
        if snap.content is not None:
            self._in_memory_bytes -= snap.byte_size
            snap.content = None
        snap.release()

        if snap.is_new_file:
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
                Path(file_path).write_text(original or "", encoding="utf-8")
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

    @property
    def in_memory_bytes(self) -> int:
        """Total bytes of snapshot content currently held in memory.

        Content spilled to disk does NOT count towards this total.
        """
        return self._in_memory_bytes

    def peek(self) -> UndoEntry | None:
        """Return the top entry as (file_path, content_or_None).

        Returns ``None`` if empty.  Loads spilled snapshots from disk
        so the returned tuple matches the legacy ``(path, content)``
        shape expected by existing callers.
        """
        if not self._stack:
            return None
        snap = self._stack[-1]
        if snap.is_new_file:
            return (snap.file_path, None)
        return (snap.file_path, snap.load())

    def clear(self) -> None:
        """Discard all entries and remove any spill files."""
        while self._stack:
            snap = self._stack.pop()
            if snap.content is not None:
                self._in_memory_bytes -= snap.byte_size
            snap.release()
        # Defensive: ensure the counter can't drift below zero if
        # accounting is ever slightly off.
        self._in_memory_bytes = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_spill(self, content: str) -> str:
        """Write *content* to a temp file and return its path."""
        fd, path = tempfile.mkstemp(prefix="duh-undo-", suffix=".snap")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        return path

    def _release(self, snap: _Snapshot) -> None:
        """Remove *snap*'s spill file and update byte accounting."""
        if snap.content is not None:
            self._in_memory_bytes -= snap.byte_size
            snap.content = None
        snap.release()

    def _enforce_total_budget(self) -> None:
        """Spill oldest in-memory snapshots until under total byte cap.

        Walks the deque from oldest to newest; each in-memory snapshot
        encountered is moved to a spill file and its bytes are
        subtracted from the running total.  Stops once the total is at
        or below ``self._total_max_bytes``.  New-file snapshots hold no
        content so they are skipped.
        """
        if self._in_memory_bytes <= self._total_max_bytes:
            return
        # Iterate oldest-first.  We mutate the snapshot objects in place
        # so deque ordering is preserved.
        for snap in self._stack:
            if self._in_memory_bytes <= self._total_max_bytes:
                return
            if snap.content is None:
                continue  # already spilled or new-file
            try:
                snap.spill_path = self._write_spill(snap.content)
            except OSError:
                # Can't spill — drop the content entirely; undo for this
                # entry will restore empty content rather than the
                # original.  Better than crashing at push time.
                snap.spill_path = None
            self._in_memory_bytes -= snap.byte_size
            snap.content = None
