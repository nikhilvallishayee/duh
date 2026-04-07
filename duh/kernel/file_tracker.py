"""FileTracker — records file operations during a session.

Tracks which files the agent reads, writes, and edits so users
can see what changed at a glance.

    tracker = FileTracker()
    tracker.track("/foo/bar.py", "read")
    tracker.track("/foo/bar.py", "edit")
    print(tracker.summary())
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class FileOp:
    """A single recorded file operation."""

    path: str
    operation: str  # "read", "write", "edit"
    timestamp: datetime


class FileTracker:
    """Records file operations and produces summaries."""

    def __init__(self) -> None:
        self._ops: list[FileOp] = []

    def track(self, path: str, operation: str) -> None:
        """Record a file operation.

        Args:
            path: Absolute or relative file path.
            operation: One of "read", "write", "edit".
        """
        self._ops.append(
            FileOp(
                path=path,
                operation=operation,
                timestamp=datetime.now(timezone.utc),
            )
        )

    @property
    def ops(self) -> list[FileOp]:
        """All recorded operations, in order."""
        return list(self._ops)

    def clear(self) -> None:
        """Discard all recorded operations."""
        self._ops.clear()

    def summary(self) -> str:
        """Return a human-readable summary grouped by operation type.

        Example output::

            Reads (2):
              /foo/bar.py
              /foo/baz.py

            Writes (1):
              /foo/new.py

            Edits (1):
              /foo/bar.py
        """
        if not self._ops:
            return "No file operations recorded."

        by_op: dict[str, list[str]] = {}
        for op in self._ops:
            by_op.setdefault(op.operation, []).append(op.path)

        # Deduplicate paths within each group, preserve first-seen order
        sections: list[str] = []
        for op_type in ("read", "write", "edit"):
            paths = by_op.get(op_type, [])
            if not paths:
                continue
            seen: set[str] = set()
            unique: list[str] = []
            for p in paths:
                if p not in seen:
                    seen.add(p)
                    unique.append(p)
            label = op_type.capitalize() + "s"
            lines = [f"{label} ({len(unique)}):"]
            for p in unique:
                lines.append(f"  {p}")
            sections.append("\n".join(lines))

        # Include any unexpected operation types at the end
        for op_type, paths in by_op.items():
            if op_type in ("read", "write", "edit"):
                continue
            seen_extra: set[str] = set()
            unique_extra: list[str] = []
            for p in paths:
                if p not in seen_extra:
                    seen_extra.add(p)
                    unique_extra.append(p)
            label = op_type.capitalize() + "s"
            lines = [f"{label} ({len(unique_extra)}):"]
            for p in unique_extra:
                lines.append(f"  {p}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def diff_summary(self, *, cwd: str = ".") -> str:
        """Return git-style diff info for files that were written or edited.

        Runs ``git diff`` for each modified file and collects the stat lines.
        Files not under git control are listed as "untracked".
        If git is unavailable the method falls back to a simple list.
        """
        modified_paths: list[str] = []
        seen: set[str] = set()
        for op in self._ops:
            if op.operation in ("write", "edit") and op.path not in seen:
                seen.add(op.path)
                modified_paths.append(op.path)

        if not modified_paths:
            return "No files modified."

        lines: list[str] = []
        for path in modified_paths:
            try:
                result = subprocess.run(
                    ["git", "diff", "--stat", "--", path],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=cwd,
                )
                stat = result.stdout.strip()
                if stat:
                    lines.append(stat)
                else:
                    # Might be a new untracked file
                    lines.append(f" {path} (new/untracked)")
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                lines.append(f" {path} (git unavailable)")

        return "\n".join(lines)
