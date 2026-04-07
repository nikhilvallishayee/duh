"""Tests for duh.adapters.structured_logging — structured JSON logging."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest

from duh.adapters.structured_logging import StructuredLogger, DEFAULT_LOG_FILE, MAX_LOG_SIZE
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger(tmp_path: Path, **kwargs: Any) -> StructuredLogger:
    """Create a StructuredLogger writing into *tmp_path*."""
    return StructuredLogger(log_dir=tmp_path, session_id="test-session", **kwargs)


def _read_entries(tmp_path: Path, filename: str = DEFAULT_LOG_FILE) -> list[dict[str, Any]]:
    """Read all JSONL entries from the log file."""
    path = tmp_path / filename
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


async def _simple_model(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
    yield {"type": "assistant", "message": Message(
        role="assistant",
        content=[{"type": "text", "text": "Hello!"}],
    )}
    yield {"type": "done", "stop_reason": "end_turn", "turns": 1}


async def _tool_model(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
    """Model that requests a tool call."""
    yield {"type": "assistant", "message": Message(
        role="assistant",
        content=[
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
        ],
    )}
    yield {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}
    yield {"type": "tool_result", "tool_use_id": "t1", "output": "file.py", "is_error": False}
    yield {"type": "done", "stop_reason": "end_turn", "turns": 1}


async def _error_model(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
    # Raising inside the model stream triggers the loop's except clause,
    # which yields {"type": "error", "error": "..."}.
    raise RuntimeError("Something went wrong")
    yield  # noqa: unreachable — required to make this a generator


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStructuredLogger:

    def test_log_creates_file_and_directory(self, tmp_path: Path):
        log_dir = tmp_path / "subdir" / "logs"
        slog = StructuredLogger(log_dir=log_dir, session_id="s1")
        slog.log("test_event")
        slog.close()

        assert (log_dir / DEFAULT_LOG_FILE).exists()
        entries = _read_entries(log_dir)
        assert len(entries) == 1
        assert entries[0]["event"] == "test_event"

    def test_log_entry_structure(self, tmp_path: Path):
        slog = _make_logger(tmp_path)
        entry = slog.log("tool_call", level="info", tool_name="Bash")
        slog.close()

        assert entry["event"] == "tool_call"
        assert entry["level"] == "info"
        assert entry["session_id"] == "test-session"
        assert entry["tool_name"] == "Bash"
        assert "timestamp" in entry
        # Verify ISO format
        assert "T" in entry["timestamp"]

    def test_log_persists_to_disk(self, tmp_path: Path):
        slog = _make_logger(tmp_path)
        slog.log("session_start")
        slog.log("model_request", model="opus")
        slog.log("session_end")
        slog.close()

        entries = _read_entries(tmp_path)
        assert len(entries) == 3
        events = [e["event"] for e in entries]
        assert events == ["session_start", "model_request", "session_end"]

    def test_convenience_methods(self, tmp_path: Path):
        slog = _make_logger(tmp_path)

        slog.tool_call("Bash", input={"command": "ls"})
        slog.tool_result("Bash", output="file.py", is_error=False)
        slog.model_request(model="opus")
        slog.model_response(model="opus")
        slog.error(error="bad input")
        slog.session_start()
        slog.session_end(turns=5)
        slog.close()

        entries = _read_entries(tmp_path)
        assert len(entries) == 7
        events = [e["event"] for e in entries]
        assert events == [
            "tool_call", "tool_result", "model_request",
            "model_response", "error", "session_start", "session_end",
        ]
        # Check error level is set
        error_entry = entries[4]
        assert error_entry["level"] == "error"
        assert error_entry["error_text"] == "bad input"

    def test_tool_result_output_capped(self, tmp_path: Path):
        slog = _make_logger(tmp_path)
        long_output = "x" * 5000
        entry = slog.tool_result("Read", output=long_output)
        slog.close()

        assert len(entry["tool_output"]) == 2000

    def test_session_id_setter(self, tmp_path: Path):
        slog = _make_logger(tmp_path)
        assert slog.session_id == "test-session"

        slog.session_id = "new-session"
        entry = slog.log("ping")
        slog.close()

        assert entry["session_id"] == "new-session"

    def test_close_is_idempotent(self, tmp_path: Path):
        slog = _make_logger(tmp_path)
        slog.log("ping")
        slog.close()
        slog.close()  # should not raise

    def test_rotation_at_max_bytes(self, tmp_path: Path):
        slog = StructuredLogger(
            log_dir=tmp_path,
            session_id="s1",
            max_bytes=200,  # tiny threshold for testing
        )
        # Write enough entries to exceed 200 bytes
        for i in range(20):
            slog.log("tick", i=i)
        slog.close()

        # After rotation, the .jsonl.1 file should exist
        rotated = tmp_path / "duh.jsonl.1"
        assert rotated.exists()
        # The current log should still have entries
        entries = _read_entries(tmp_path)
        assert len(entries) > 0

    def test_path_property(self, tmp_path: Path):
        slog = StructuredLogger(log_dir=tmp_path, session_id="s1")
        assert slog.path == tmp_path / DEFAULT_LOG_FILE

    def test_extra_fields_passthrough(self, tmp_path: Path):
        slog = _make_logger(tmp_path)
        entry = slog.log("custom", level="debug", foo="bar", count=42)
        slog.close()

        assert entry["foo"] == "bar"
        assert entry["count"] == 42

    def test_write_after_close_reopens(self, tmp_path: Path):
        slog = _make_logger(tmp_path)
        slog.log("first")
        slog.close()

        # Writing after close should lazily re-open
        slog.log("second")
        slog.close()

        entries = _read_entries(tmp_path)
        assert len(entries) == 2


class TestEngineWithStructuredLogging:
    """Integration: verify engine emits structured log events."""

    async def test_engine_logs_session_start(self, tmp_path: Path):
        slog = StructuredLogger(log_dir=tmp_path, session_id="")
        deps = Deps(call_model=_simple_model)
        engine = Engine(deps=deps, structured_logger=slog, model="test-model")

        async for _ in engine.run("hello"):
            pass
        slog.close()

        entries = _read_entries(tmp_path)
        events = [e["event"] for e in entries]
        assert "session_start" in events

    async def test_engine_logs_model_request_and_response(self, tmp_path: Path):
        slog = StructuredLogger(log_dir=tmp_path, session_id="")
        deps = Deps(call_model=_simple_model)
        engine = Engine(deps=deps, structured_logger=slog, model="test-model")

        async for _ in engine.run("hello"):
            pass
        slog.close()

        entries = _read_entries(tmp_path)
        events = [e["event"] for e in entries]
        assert "model_request" in events
        assert "model_response" in events

        req = next(e for e in entries if e["event"] == "model_request")
        assert req["model"] == "test-model"

    async def test_engine_logs_tool_events(self, tmp_path: Path):
        async def tool_runner(name: str, input: dict) -> str:
            return "output"

        slog = StructuredLogger(log_dir=tmp_path, session_id="")
        deps = Deps(call_model=_tool_model, run_tool=tool_runner)
        engine = Engine(deps=deps, structured_logger=slog, model="test-model")

        async for _ in engine.run("do something"):
            pass
        slog.close()

        entries = _read_entries(tmp_path)
        events = [e["event"] for e in entries]
        assert "tool_call" in events
        assert "tool_result" in events

        tc = next(e for e in entries if e["event"] == "tool_call")
        assert tc["tool_name"] == "Bash"

    async def test_engine_logs_error(self, tmp_path: Path):
        slog = StructuredLogger(log_dir=tmp_path, session_id="")
        deps = Deps(call_model=_error_model)
        engine = Engine(deps=deps, structured_logger=slog, model="test-model")

        async for _ in engine.run("trigger error"):
            pass
        slog.close()

        entries = _read_entries(tmp_path)
        events = [e["event"] for e in entries]
        assert "error" in events

        err = next(e for e in entries if e["event"] == "error")
        assert err["level"] == "error"
        assert "Something went wrong" in err["error_text"]

    async def test_engine_without_logger_works_fine(self, tmp_path: Path):
        """Engine with no structured_logger should not break."""
        deps = Deps(call_model=_simple_model)
        engine = Engine(deps=deps, model="test-model")

        events = []
        async for e in engine.run("hello"):
            events.append(e)

        types = [e["type"] for e in events]
        assert "assistant" in types
        assert "done" in types

    async def test_session_id_propagated_to_logger(self, tmp_path: Path):
        slog = StructuredLogger(log_dir=tmp_path, session_id="will-be-overwritten")
        deps = Deps(call_model=_simple_model)
        engine = Engine(deps=deps, structured_logger=slog)

        async for _ in engine.run("hello"):
            pass
        slog.close()

        entries = _read_entries(tmp_path)
        # All entries should have the engine's session_id
        for entry in entries:
            assert entry["session_id"] == engine.session_id
