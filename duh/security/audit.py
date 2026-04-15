"""Structured audit logging for tool invocations (ADR-072 P1).

Writes append-only JSONL to ~/.config/duh/audit.jsonl with:
- timestamp (ISO 8601)
- session_id
- tool_name
- tool_input (redacted if contains secrets)
- result_status (ok/error/denied)
- duration_ms
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    """Append-only JSONL audit logger for security compliance.

    Each tool invocation is recorded as a single JSON line.
    Sensitive fields (keys, tokens, passwords, secrets) are automatically
    redacted.  Large input values are truncated to prevent log bloat.

    Parameters
    ----------
    path:
        Path to the audit log file.  Defaults to
        ``~/.config/duh/audit.jsonl``.  Parent directories are created
        automatically.
    """

    DEFAULT_PATH = Path.home() / ".config" / "duh" / "audit.jsonl"

    # Substrings in key names that trigger redaction
    _SENSITIVE_KEYS = ("key", "token", "secret", "password", "credential", "auth")

    # Maximum length of a single string value before truncation
    _MAX_VALUE_LEN = 500

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or self.DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        """Return the resolved audit log path."""
        return self._path

    def log_tool_call(
        self,
        session_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        result_status: str,  # "ok", "error", "denied"
        duration_ms: int = 0,
    ) -> dict[str, Any]:
        """Append a tool call record to the audit log.

        Returns the entry dict (useful for testing).
        """
        safe_input = self._redact(tool_input)
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z"),
            "sid": session_id,
            "tool": tool_name,
            "input": safe_input,
            "status": result_status,
            "ms": duration_ms,
        }
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read_entries(self, limit: int = 20) -> list[dict[str, Any]]:
        """Read the most recent *limit* entries from the audit log.

        Returns an empty list if the file does not exist.
        """
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").strip().splitlines()
        entries: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _redact(self, input_dict: dict[str, Any]) -> dict[str, Any]:
        """Redact values that look like secrets."""
        redacted: dict[str, Any] = {}
        for k, v in input_dict.items():
            if isinstance(v, str) and any(
                s in k.lower() for s in self._SENSITIVE_KEYS
            ):
                redacted[k] = "[REDACTED]"
            elif isinstance(v, str) and len(v) > self._MAX_VALUE_LEN:
                redacted[k] = v[:100] + "...[truncated]"
            else:
                redacted[k] = v
        return redacted
