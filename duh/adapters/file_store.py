"""FileStore adapter — JSONL-based session persistence.

Stores each session as a .jsonl file under ~/.config/duh/sessions/.
One JSON object per line = one message. Atomic writes via
temp-file-then-rename for thread safety.

    store = FileStore()
    await store.save("abc-123", messages)
    history = await store.load("abc-123")
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from duh.kernel.messages import Message

# Maximum session file size (64 MB). Matches Claude Code TS MAX_PERSISTED_SIZE.
MAX_SESSION_BYTES = 64 * 1024 * 1024


def _default_base_dir() -> Path:
    return Path.home() / ".config" / "duh" / "sessions"


class FileStore:
    """JSONL file-backed SessionStore implementation."""

    def __init__(self, base_dir: Path | str | None = None):
        self._base_dir = Path(base_dir) if base_dir else _default_base_dir()

    def _session_path(self, session_id: str) -> Path:
        return self._base_dir / f"{session_id}.jsonl"

    def _ensure_dir(self) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # SessionStore protocol
    # ------------------------------------------------------------------

    async def save(self, session_id: str, messages: list[Any]) -> None:
        """Append *new* messages to the session file.

        Messages are serialised with ``dataclasses.asdict`` when they are
        Message dataclass instances; plain dicts pass through as-is.
        Writes are atomic: we write to a temporary file in the same
        directory, then ``os.replace`` into the final path — so a crash
        mid-write never corrupts existing data.
        """
        self._ensure_dir()
        path = self._session_path(session_id)

        # Read existing lines so we only *append* the delta.
        existing_count = 0
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                existing_count = sum(1 for line in f if line.strip())

        new_messages = messages[existing_count:]
        if not new_messages:
            return

        lines: list[str] = []
        for msg in new_messages:
            if isinstance(msg, Message):
                lines.append(json.dumps(asdict(msg), ensure_ascii=False))
            else:
                lines.append(json.dumps(msg, ensure_ascii=False))

        # Check projected size against the session cap before writing.
        existing_size = path.stat().st_size if path.exists() else 0
        new_bytes = sum(len(line.encode("utf-8")) + 1 for line in lines)  # +1 for "\n"
        projected_size = existing_size + new_bytes
        if projected_size > MAX_SESSION_BYTES:
            raise ValueError(
                f"Session state would exceed the {MAX_SESSION_BYTES // 1024 // 1024} MB "
                f"session cap ({projected_size // 1024 // 1024} MB projected). "
                "Compact the session before saving."
            )

        # Atomic write: copy existing content + new lines → temp → rename.
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._base_dir), suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                # Copy existing content
                if path.exists():
                    with open(path, "r", encoding="utf-8") as orig:
                        tmp.write(orig.read())
                # Append new lines
                for line in lines:
                    tmp.write(line + "\n")
            os.replace(tmp_path, str(path))
        except BaseException:
            # Clean up temp file on any error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def load(self, session_id: str) -> list[dict[str, Any]] | None:
        """Load messages for a session, returning dicts (not Message objects).

        Returns ``None`` when the session file does not exist.
        """
        path = self._session_path(session_id)
        if not path.exists():
            return None

        messages: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    messages.append(json.loads(stripped))
        return messages

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Return metadata for every persisted session.

        Each entry contains:
        - ``session_id``
        - ``created``   — ISO-8601 timestamp (file ctime)
        - ``modified``  — ISO-8601 timestamp (file mtime)
        - ``message_count``
        """
        if not self._base_dir.exists():
            return []

        sessions: list[dict[str, Any]] = []
        for entry in sorted(self._base_dir.iterdir()):
            if entry.suffix != ".jsonl" or not entry.is_file():
                continue
            stat = entry.stat()
            with open(entry, "r", encoding="utf-8") as f:
                count = sum(1 for line in f if line.strip())
            sessions.append({
                "session_id": entry.stem,
                "created": datetime.fromtimestamp(
                    stat.st_birthtime if hasattr(stat, "st_birthtime") else stat.st_ctime,
                    tz=timezone.utc,
                ).isoformat(),
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc,
                ).isoformat(),
                "message_count": count,
            })
        return sessions

    async def delete(self, session_id: str) -> bool:
        """Delete a session file. Returns True if it existed."""
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False
