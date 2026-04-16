"""FileMemoryStore adapter -- file-based per-project memory.

See ADR-016 for the full rationale.

Stores memory at ~/.config/duh/projects/<sanitized-cwd>/memory/.
Each project gets its own namespace based on a sanitized cwd path.

Persistent cross-session facts are stored in:
    ~/.config/duh/memory/<project-hash>/facts.jsonl

Each fact entry: {"key": str, "value": str, "timestamp": str, "tags": [str]}

    store = FileMemoryStore(cwd="/Users/alice/Code/my-project")
    store.write_file("project_setup.md", "---\\nname: Setup\\n...")
    headers = store.list_files()

    # Persistent facts
    store.store_fact("auth-pattern", "Uses JWT with refresh tokens", ["auth"])
    results = store.recall_facts("auth")
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from duh.config import config_dir
from duh.ports.memory import MemoryHeader

logger = logging.getLogger(__name__)

INDEX_FILENAME = "MEMORY.md"
INDEX_LINE_CAP = 200
FACTS_FILENAME = "facts.jsonl"
FACTS_LINE_CAP = 500  # max entries before oldest are pruned


def _project_hash(cwd: str) -> str:
    """Compute a stable hash for the project root.

    Uses the git root if available, otherwise the resolved *cwd*.
    Returns the first 12 hex chars of the SHA-256.
    """
    from duh.config import _find_git_root

    root = _find_git_root(cwd)
    key = str(root) if root else str(Path(cwd).resolve())
    return hashlib.sha256(key.encode()).hexdigest()[:12]


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
        self._facts_dir = (
            config_dir() / "memory" / _project_hash(cwd)
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
    # Persistent cross-session facts
    # ------------------------------------------------------------------

    def get_facts_dir(self) -> Path:
        """Return the facts directory path."""
        return self._facts_dir

    def _facts_path(self) -> Path:
        return self._facts_dir / FACTS_FILENAME

    def _ensure_facts_dir(self) -> None:
        self._facts_dir.mkdir(parents=True, exist_ok=True)

    def store_fact(
        self,
        key: str,
        value: str,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Persist a fact to facts.jsonl. Returns the stored entry.

        Fast path: if no fact with ``key`` already exists, append a single
        line to the file (O(1) write, no full-file rewrite).

        Slow path: if a fact with ``key`` exists, do a read-filter-rewrite
        so the old entry is removed.

        In both paths we also enforce ``FACTS_LINE_CAP`` (oldest-first
        eviction) by falling back to a full rewrite when the cap would be
        exceeded.

        Disk-full (ENOSPC/EDQUOT) errors are logged and swallowed so the
        caller keeps the fact in memory rather than crashing the session.
        """
        entry: dict[str, Any] = {
            "key": key,
            "value": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tags": tags or [],
        }

        try:
            self._ensure_facts_dir()
            path = self._facts_path()

            # Fast path — only when we know the key is brand-new AND we're
            # under the line cap.  Both checks require touching the file,
            # but key-existence is a streaming scan that stops at the first
            # match, and line counting is also cheap.
            if not path.exists():
                self._append_fact_line(entry)
                return entry

            if not self._key_exists(key) and self._line_count() < FACTS_LINE_CAP:
                self._append_fact_line(entry)
                return entry

            # Slow path: existing key OR cap reached → read-filter-rewrite.
            existing = self._read_all_facts()
            existing = [e for e in existing if e.get("key") != key]
            existing.append(entry)
            if len(existing) > FACTS_LINE_CAP:
                existing = existing[-FACTS_LINE_CAP:]
            self._write_all_facts(existing)
            return entry
        except OSError as exc:
            if exc.errno in (errno.ENOSPC, errno.EDQUOT):
                logger.warning(
                    "store_fact(%s): disk full (%s); fact not persisted.",
                    key, exc.strerror or exc,
                )
                return entry
            raise

    def _append_fact_line(self, entry: dict[str, Any]) -> None:
        """Append a single JSON line to facts.jsonl atomically.

        Uses ``open(..., 'a')`` which on POSIX is an atomic append for
        writes smaller than PIPE_BUF.  Adequate for single-process use;
        for multi-process callers we rely on the kernel's append-mode
        guarantee plus our own newline-terminated payload.
        """
        path = self._facts_path()
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(line)

    def _key_exists(self, key: str) -> bool:
        """Streaming check: does *any* line in facts.jsonl have this key?

        Returns at the first match without parsing the rest of the file.
        """
        path = self._facts_path()
        if not path.exists():
            return False
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if e.get("key") == key:
                        return True
        except Exception as exc:
            logger.warning("Failed to scan %s: %s", path, exc)
        return False

    def _line_count(self) -> int:
        """Return the number of non-empty lines in facts.jsonl."""
        path = self._facts_path()
        if not path.exists():
            return 0
        try:
            with path.open("r", encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    def recall_facts(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search facts by keyword. Matches against key, value, and tags.

        Returns up to *limit* results, newest first.
        Also records access tracking (last_accessed, access_count) for
        memory decay scoring (ADR-069 P2).

        PERF-8: Avoids building a concatenated lowercase haystack string per
        fact. Instead, we casefold each field on the fly and short-circuit
        on the first field that contains the query. ``str.casefold()`` is
        used (superset of ``.lower()``) for correct Unicode matching.
        """
        all_facts = self._read_all_facts()
        query_cf = query.casefold()
        matched: list[dict[str, Any]] = []
        matched_keys: set[str] = set()
        for fact in reversed(all_facts):  # newest first
            # Short-circuit: check key, then value, then tags individually.
            # No temporary concatenated string is built.
            key_val = fact.get("key", "")
            if key_val and query_cf in key_val.casefold():
                matched.append(fact)
                matched_keys.add(key_val)
                if len(matched) >= limit:
                    break
                continue

            value_val = fact.get("value", "")
            if value_val and query_cf in value_val.casefold():
                matched.append(fact)
                matched_keys.add(key_val)
                if len(matched) >= limit:
                    break
                continue

            tags = fact.get("tags", [])
            if tags and any(query_cf in t.casefold() for t in tags):
                matched.append(fact)
                matched_keys.add(key_val)
                if len(matched) >= limit:
                    break

        # Update access tracking for matched facts
        if matched_keys:
            self._record_access(all_facts, matched_keys)

        return matched

    def _record_access(
        self,
        all_facts: list[dict[str, Any]],
        accessed_keys: set[str],
    ) -> None:
        """Bump last_accessed and access_count for the given keys."""
        now_iso = datetime.now(timezone.utc).isoformat()
        changed = False
        for fact in all_facts:
            if fact.get("key", "") in accessed_keys:
                fact["last_accessed"] = now_iso
                fact["access_count"] = fact.get("access_count", 0) + 1
                changed = True
        if changed:
            self._write_all_facts(all_facts)

    def list_facts(self) -> list[dict[str, Any]]:
        """Return all stored facts, oldest first."""
        return self._read_all_facts()

    def delete_fact(self, key: str) -> bool:
        """Delete a fact by key. Returns True if found and deleted."""
        existing = self._read_all_facts()
        filtered = [e for e in existing if e.get("key") != key]
        if len(filtered) == len(existing):
            return False
        self._write_all_facts(filtered)
        return True

    def _read_all_facts(self) -> list[dict[str, Any]]:
        """Read all facts from facts.jsonl."""
        path = self._facts_path()
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed facts.jsonl line: %s", line[:80])
        except Exception as exc:
            logger.warning("Failed to read %s: %s", path, exc)
        return entries

    def _write_all_facts(self, entries: list[dict[str, Any]]) -> None:
        """Write all facts to facts.jsonl (overwrite)."""
        self._ensure_facts_dir()
        path = self._facts_path()
        lines = [json.dumps(e, ensure_ascii=False) for e in entries]
        path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_dir(self) -> None:
        """Create the memory directory if it doesn't exist."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
