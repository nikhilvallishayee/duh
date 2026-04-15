"""FileStore adapter — JSONL-based session persistence.

Stores each session as a .jsonl file under ~/.config/duh/sessions/.
One JSON object per line = one message. Atomic writes via
temp-file-then-rename for thread safety.

    store = FileStore(cwd="/path/to/project")
    await store.save("abc-123", messages)
    history = await store.load("abc-123")
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from duh.kernel.messages import Message

logger = logging.getLogger(__name__)

# Maximum session file size (64 MB). Industry standard cap.
MAX_SESSION_BYTES = 64 * 1024 * 1024


def _default_base_dir() -> Path:
    return Path.home() / ".config" / "duh" / "sessions"


def _project_sessions_dir(cwd: str | None = None) -> Path:
    """Return a project-scoped sessions directory.

    Sessions are stored under ``~/.config/duh/sessions/<hash>/`` where
    ``<hash>`` is derived from the git root (or cwd if not in a repo).
    This follows the industry-standard per-project session scoping pattern.
    """
    import hashlib
    project_root = cwd or "."
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=project_root, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            project_root = result.stdout.strip()
    except Exception:
        pass
    project_root = str(Path(project_root).resolve())
    h = hashlib.sha256(project_root.encode()).hexdigest()[:16]
    return _default_base_dir() / h


class FileStore:
    """JSONL file-backed SessionStore implementation."""

    def __init__(self, base_dir: Path | str | None = None, cwd: str | None = None):
        if base_dir:
            self._base_dir = Path(base_dir)
        elif cwd:
            self._base_dir = _project_sessions_dir(cwd)
        else:
            self._base_dir = _default_base_dir()

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

        ADR-057: On load, detects and migrates sessions with broken role
        alternation (consecutive same-role messages). The migration applies
        ``validate_alternation()`` once and persists the corrected version
        on the next save.
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

        # ADR-057: Migrate broken sessions with consecutive same-role messages
        if _needs_alternation_fix(messages):
            logger.warning("Migrating session %s: fixing message alternation", session_id)
            messages = _migrate_alternation(messages)

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


# ---------------------------------------------------------------------------
# ADR-057: Session migration helpers
# ---------------------------------------------------------------------------

def _needs_alternation_fix(messages: list[dict[str, Any]]) -> bool:
    """Return True if consecutive assistant messages are detected.

    The ADR-057 bug specifically caused missing tool_result user messages
    between assistant messages. We only migrate that pattern — consecutive
    user messages are left alone (they can occur legitimately in tests or
    manual session construction).
    """
    for i in range(len(messages) - 1):
        if (messages[i].get("role") == "assistant"
                and messages[i + 1].get("role") == "assistant"):
            return True
    return False


def _migrate_alternation(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fix broken alternation by converting to Message objects, applying
    validate_alternation, then converting back to dicts."""
    from dataclasses import asdict as _asdict
    from duh.kernel.messages import Message as Msg, validate_alternation

    msg_objs = [Msg(role=m.get("role", "user"), content=m.get("content", "")) for m in messages]
    fixed = validate_alternation(msg_objs)
    return [_asdict(m) for m in fixed]
