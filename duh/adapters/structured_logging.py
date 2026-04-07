"""Structured JSON logging adapter for D.U.H.

Emits one JSON object per line (JSONL) to a rotating log file.
Each entry carries a timestamp, log level, event name, session ID,
and arbitrary extra fields.

Events logged:
    tool_call, tool_result, model_request, model_response,
    error, session_start, session_end

Enable via ``--log-json`` CLI flag or ``DUH_LOG_JSON=1`` env var.

Log file: ``~/.config/duh/logs/duh.jsonl`` (append mode, rotates at 10 MB).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LOG_DIR = Path.home() / ".config" / "duh" / "logs"
DEFAULT_LOG_FILE = "duh.jsonl"
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB


class StructuredLogger:
    """Append-only JSONL logger with size-based rotation.

    Parameters
    ----------
    log_dir:
        Directory for the log file.  Created if it does not exist.
    log_file:
        Filename inside *log_dir*.
    max_bytes:
        Rotate when the file exceeds this size (default 10 MB).
    session_id:
        Session identifier stamped onto every entry.
    """

    def __init__(
        self,
        *,
        log_dir: Path | str | None = None,
        log_file: str = DEFAULT_LOG_FILE,
        max_bytes: int = MAX_LOG_SIZE,
        session_id: str = "",
    ) -> None:
        self._log_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
        self._log_file = log_file
        self._max_bytes = max_bytes
        self._session_id = session_id
        self._path = self._log_dir / self._log_file
        self._handle: Any = None  # opened lazily

    # -- public API ----------------------------------------------------------

    @property
    def path(self) -> Path:
        """Return the resolved log file path."""
        return self._path

    @property
    def session_id(self) -> str:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self._session_id = value

    def log(
        self,
        event: str,
        *,
        level: str = "info",
        **extra: Any,
    ) -> dict[str, Any]:
        """Write one structured log entry. Returns the entry dict."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event": event,
            "session_id": self._session_id,
            **extra,
        }
        self._write(entry)
        return entry

    def close(self) -> None:
        """Flush and close the underlying file handle."""
        if self._handle and not self._handle.closed:
            self._handle.flush()
            self._handle.close()
            self._handle = None

    # -- convenience methods for each event type ----------------------------

    def tool_call(self, name: str, input: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        return self.log("tool_call", tool_name=name, tool_input=input or {}, **kw)

    def tool_result(
        self,
        name: str,
        output: str = "",
        is_error: bool = False,
        **kw: Any,
    ) -> dict[str, Any]:
        return self.log(
            "tool_result",
            tool_name=name,
            tool_output=output[:2000],  # cap to avoid huge entries
            is_error=is_error,
            **kw,
        )

    def model_request(self, model: str = "", **kw: Any) -> dict[str, Any]:
        return self.log("model_request", model=model, **kw)

    def model_response(self, model: str = "", **kw: Any) -> dict[str, Any]:
        return self.log("model_response", model=model, **kw)

    def error(self, error: str, **kw: Any) -> dict[str, Any]:
        return self.log("error", level="error", error_text=error, **kw)

    def session_start(self, **kw: Any) -> dict[str, Any]:
        return self.log("session_start", **kw)

    def session_end(self, **kw: Any) -> dict[str, Any]:
        return self.log("session_end", **kw)

    # -- internals -----------------------------------------------------------

    def _ensure_open(self) -> Any:
        """Lazily open (or re-open) the log file."""
        if self._handle and not self._handle.closed:
            return self._handle
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._handle = open(self._path, "a", encoding="utf-8")
        return self._handle

    def _rotate_if_needed(self) -> None:
        """Rotate the log file when it exceeds *max_bytes*."""
        try:
            if self._path.exists() and self._path.stat().st_size >= self._max_bytes:
                self.close()
                rotated = self._path.with_suffix(".jsonl.1")
                shutil.move(str(self._path), str(rotated))
                # re-open fresh file
                self._ensure_open()
        except OSError:
            logger.debug("Log rotation failed", exc_info=True)

    def _write(self, entry: dict[str, Any]) -> None:
        """Serialize *entry* as JSON and append to the log file."""
        self._rotate_if_needed()
        try:
            fh = self._ensure_open()
            line = json.dumps(entry, default=str, separators=(",", ":"))
            fh.write(line + "\n")
            fh.flush()
        except OSError:
            logger.debug("Structured log write failed", exc_info=True)
