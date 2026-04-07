"""MemoryStore port -- how D.U.H. persists per-project memory.

See ADR-016 for the full rationale.

Memory is file-based, stored at ~/.config/duh/projects/<sanitized-cwd>/memory/.
A MEMORY.md index contains one-line pointers to topic files. The index is
loaded into the system prompt every conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class MemoryHeader:
    """Metadata parsed from a memory topic file's frontmatter."""

    filename: str
    name: str
    description: str
    type: str  # user | feedback | project | reference


@runtime_checkable
class MemoryStore(Protocol):
    """Abstract interface for per-project memory persistence."""

    def get_memory_dir(self) -> Path:
        """Return the directory where memory files are stored."""
        ...

    def read_index(self) -> str:
        """Read the MEMORY.md index file. Returns empty string if missing."""
        ...

    def write_index(self, content: str) -> None:
        """Write the MEMORY.md index file, truncating to 200 lines."""
        ...

    def read_file(self, name: str) -> str:
        """Read a topic file by name. Returns empty string if missing."""
        ...

    def write_file(self, name: str, content: str) -> None:
        """Write a topic file. Creates the memory directory if needed."""
        ...

    def list_files(self) -> list[MemoryHeader]:
        """List all topic files with their parsed frontmatter headers."""
        ...

    def delete_file(self, name: str) -> None:
        """Delete a topic file. No-op if it doesn't exist."""
        ...
