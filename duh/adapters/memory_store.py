"""FileMemoryStore adapter -- file-based per-project memory.

See ADR-016 for the full rationale.

Stores memory at ~/.config/duh/projects/<sanitized-cwd>/memory/.
Each project gets its own namespace based on a sanitized cwd path.

    store = FileMemoryStore(cwd="/Users/alice/Code/my-project")
    store.write_file("project_setup.md", "---\\nname: Setup\\n...")
    headers = store.list_files()
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from duh.config import config_dir
from duh.ports.memory import MemoryHeader

logger = logging.getLogger(__name__)

INDEX_FILENAME = "MEMORY.md"
INDEX_LINE_CAP = 200


def _sanitize_cwd(cwd: str) -> str:
    """Sanitize a cwd path for use as a directory name.

    Replaces ``/`` with ``-`` and strips the leading ``-``.

    Examples:
        /Users/alice/Code/proj  ->  Users-alice-Code-proj
        /home/bob/work          ->  home-bob-work
    """
    sanitized = cwd.replace("/", "-").replace("\\", "-")
    return sanitized.lstrip("-")


def _parse_frontmatter(text: str, filename: str) -> MemoryHeader:
    """Parse YAML-ish frontmatter from a memory topic file.

    Expects:
        ---
        name: Some Name
        description: One liner
        type: project
        ---
    """
    name = filename
    description = ""
    mem_type = ""

    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if match:
        for line in match.group(1).splitlines():
            line = line.strip()
            if line.startswith("name:"):
                name = line[len("name:"):].strip()
            elif line.startswith("description:"):
                description = line[len("description:"):].strip()
            elif line.startswith("type:"):
                mem_type = line[len("type:"):].strip()

    return MemoryHeader(
        filename=filename,
        name=name,
        description=description,
        type=mem_type,
    )


def _truncate_index(content: str, cap: int = INDEX_LINE_CAP) -> str:
    """Truncate MEMORY.md content to *cap* lines.

    Keeps the first line (header) and the last (cap - 1) content lines,
    dropping the oldest entries from the top.
    """
    lines = content.splitlines()
    if len(lines) <= cap:
        return content

    # Keep first line (header) + last (cap - 1) lines
    header = lines[0]
    kept = lines[-(cap - 1):]
    return "\n".join([header] + kept)


class FileMemoryStore:
    """File-backed MemoryStore implementation.

    Memory directory: ``~/.config/duh/projects/<sanitized-cwd>/memory/``
    """

    def __init__(self, cwd: str | None = None):
        if cwd is None:
            import os
            cwd = os.getcwd()
        self._cwd = cwd
        self._memory_dir = (
            config_dir() / "projects" / _sanitize_cwd(cwd) / "memory"
        )

    # ------------------------------------------------------------------
    # MemoryStore protocol
    # ------------------------------------------------------------------

    def get_memory_dir(self) -> Path:
        """Return the memory directory path."""
        return self._memory_dir

    def read_index(self) -> str:
        """Read MEMORY.md. Returns empty string if missing."""
        index_path = self._memory_dir / INDEX_FILENAME
        if not index_path.exists():
            return ""
        try:
            return index_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read %s: %s", index_path, exc)
            return ""

    def write_index(self, content: str) -> None:
        """Write MEMORY.md, truncating to INDEX_LINE_CAP lines."""
        self._ensure_dir()
        truncated = _truncate_index(content)
        index_path = self._memory_dir / INDEX_FILENAME
        index_path.write_text(truncated, encoding="utf-8")

    def read_file(self, name: str) -> str:
        """Read a topic file. Returns empty string if missing."""
        file_path = self._memory_dir / name
        if not file_path.exists():
            return ""
        try:
            return file_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read %s: %s", file_path, exc)
            return ""

    def write_file(self, name: str, content: str) -> None:
        """Write a topic file. Creates directories on first write."""
        self._ensure_dir()
        file_path = self._memory_dir / name
        file_path.write_text(content, encoding="utf-8")

    def list_files(self) -> list[MemoryHeader]:
        """List all .md topic files (excluding MEMORY.md) with frontmatter."""
        if not self._memory_dir.exists():
            return []

        headers: list[MemoryHeader] = []
        for entry in sorted(self._memory_dir.iterdir()):
            if not entry.is_file():
                continue
            if not entry.name.endswith(".md"):
                continue
            if entry.name == INDEX_FILENAME:
                continue
            try:
                text = entry.read_text(encoding="utf-8")
                headers.append(_parse_frontmatter(text, entry.name))
            except Exception as exc:
                logger.warning("Failed to read %s: %s", entry, exc)
                headers.append(MemoryHeader(
                    filename=entry.name, name=entry.name,
                    description="", type="",
                ))
        return headers

    def delete_file(self, name: str) -> None:
        """Delete a topic file. No-op if it doesn't exist."""
        file_path = self._memory_dir / name
        if file_path.exists():
            file_path.unlink()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> None:
        """Create the memory directory if it doesn't exist."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
