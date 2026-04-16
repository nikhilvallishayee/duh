"""FileTracker — records file operations during a session.

Tracks which files the agent reads, writes, and edits so users
can see what changed at a glance.

    tracker = FileTracker()
    tracker.track("/foo/bar.py", "read")
    tracker.track("/foo/bar.py", "edit")
    print(await tracker.diff_summary())
"""

from __future__ import annotations

import asyncio
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

    def _modified_paths(self) -> list[str]:
        """Return deduplicated list of paths that were written or edited."""
        seen: set[str] = set()
        paths: list[str] = []
        for op in self._ops:
            if op.operation in ("write", "edit") and op.path not in seen:
                seen.add(op.path)
                paths.append(op.path)
        return paths

    async def diff_summary(self, *, cwd: str = ".") -> str:
        """Return git-style diff info for files that were written or edited.

        Runs a single batched ``git diff --stat -- file1 file2 ...`` command
        using ``asyncio.create_subprocess_exec`` so the event loop is never
        blocked.  Files not under git control are listed as "untracked".
        If git is unavailable the method falls back to a simple list.
        """
        modified_paths = self._modified_paths()
        if not modified_paths:
            return "No files modified."

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--stat", "--", *modified_paths,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                stdout, _stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=5
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                return "\n".join(
                    f" {p} (git timed out)" for p in modified_paths
                )
            stat = stdout.decode("utf-8", errors="replace").strip()
            if stat:
                return stat
            # All files are new/untracked (git diff produces no output)
            return "\n".join(
                f" {p} (new/untracked)" for p in modified_paths
            )
        except (FileNotFoundError, OSError):
            return "\n".join(
                f" {p} (git unavailable)" for p in modified_paths
            )

    def diff_summary_sync(self, *, cwd: str = ".") -> str:
        """Synchronous fallback for ``diff_summary``.

        .. deprecated::
            Legacy synchronous helper.  Prefer the async :meth:`diff_summary`
            to avoid blocking the event loop.  Uses a single batched
            ``git diff --stat`` call (same as the async version).
        """
        modified_paths = self._modified_paths()
        if not modified_paths:
            return "No files modified."

        try:
            result = subprocess.run(
                ["git", "diff", "--stat", "--", *modified_paths],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=cwd,
            )
            stat = result.stdout.strip()
            if stat:
                return stat
            return "\n".join(
                f" {p} (new/untracked)" for p in modified_paths
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return "\n".join(
                f" {p} (git unavailable)" for p in modified_paths
            )
