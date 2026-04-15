"""Tests for structured audit logging (ADR-072 P1).

Covers:
- Log entry written to file
- Secrets are redacted
- Large inputs are truncated
- Multiple entries append correctly
- Works with tmp_path (never writes to real audit.jsonl)
- read_entries returns most recent entries
- Loop integration: audit_logger on Deps is called
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from duh.security.audit import AuditLogger


# ---------------------------------------------------------------------------
# Basic write
# ---------------------------------------------------------------------------


def test_log_entry_written_to_file(tmp_path: Path) -> None:
    """A single log_tool_call writes one JSON line to the file."""
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(path=log_file)

    logger.log_tool_call(
        session_id="sess-1",
        tool_name="Bash",
        tool_input={"command": "ls"},
        result_status="ok",
        duration_ms=42,
    )

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["sid"] == "sess-1"
    assert entry["tool"] == "Bash"
    assert entry["input"] == {"command": "ls"}
    assert entry["status"] == "ok"
    assert entry["ms"] == 42
    assert "ts" in entry


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_secrets_are_redacted(tmp_path: Path) -> None:
    """Fields with 'key', 'token', 'secret', 'password' in name are redacted."""
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(path=log_file)

    logger.log_tool_call(
        session_id="s",
        tool_name="Connect",
        tool_input={
            "api_key": "sk-ant-secret123",
            "auth_token": "tok_abc",
            "password": "hunter2",
            "secret_value": "shh",
            "credential": "cred123",
            "normal_field": "visible",
        },
        result_status="ok",
    )

    entry = json.loads(log_file.read_text().strip())
    assert entry["input"]["api_key"] == "[REDACTED]"
    assert entry["input"]["auth_token"] == "[REDACTED]"
    assert entry["input"]["password"] == "[REDACTED]"
    assert entry["input"]["secret_value"] == "[REDACTED]"
    assert entry["input"]["normal_field"] == "visible"


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def test_large_inputs_are_truncated(tmp_path: Path) -> None:
    """String values longer than 500 chars are truncated."""
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(path=log_file)

    big_value = "x" * 1000
    logger.log_tool_call(
        session_id="s",
        tool_name="Write",
        tool_input={"content": big_value},
        result_status="ok",
    )

    entry = json.loads(log_file.read_text().strip())
    assert entry["input"]["content"].endswith("...[truncated]")
    # First 100 chars preserved + "...[truncated]"
    assert entry["input"]["content"].startswith("x" * 100)
    assert len(entry["input"]["content"]) < 200


# ---------------------------------------------------------------------------
# Append behavior
# ---------------------------------------------------------------------------


def test_multiple_entries_append_correctly(tmp_path: Path) -> None:
    """Multiple log_tool_call invocations append to the same file."""
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(path=log_file)

    for i in range(5):
        logger.log_tool_call(
            session_id="s",
            tool_name=f"Tool{i}",
            tool_input={"i": i},
            result_status="ok",
            duration_ms=i * 10,
        )

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) == 5
    for i, line in enumerate(lines):
        entry = json.loads(line)
        assert entry["tool"] == f"Tool{i}"
        assert entry["ms"] == i * 10


# ---------------------------------------------------------------------------
# read_entries
# ---------------------------------------------------------------------------


def test_read_entries_returns_most_recent(tmp_path: Path) -> None:
    """read_entries(limit=3) returns only the last 3 entries."""
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(path=log_file)

    for i in range(10):
        logger.log_tool_call(
            session_id="s",
            tool_name=f"T{i}",
            tool_input={},
            result_status="ok",
        )

    entries = logger.read_entries(limit=3)
    assert len(entries) == 3
    assert entries[0]["tool"] == "T7"
    assert entries[1]["tool"] == "T8"
    assert entries[2]["tool"] == "T9"


def test_read_entries_empty_file(tmp_path: Path) -> None:
    """read_entries returns empty list if file does not exist."""
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(path=log_file)
    assert logger.read_entries() == []


# ---------------------------------------------------------------------------
# tmp_path isolation (never writes to real audit.jsonl)
# ---------------------------------------------------------------------------


def test_uses_custom_path(tmp_path: Path) -> None:
    """AuditLogger uses the provided path, not the default."""
    custom = tmp_path / "custom" / "dir" / "audit.jsonl"
    logger = AuditLogger(path=custom)
    assert logger.path == custom

    logger.log_tool_call(
        session_id="s",
        tool_name="X",
        tool_input={},
        result_status="ok",
    )
    assert custom.exists()


# ---------------------------------------------------------------------------
# Return value
# ---------------------------------------------------------------------------


def test_log_tool_call_returns_entry_dict(tmp_path: Path) -> None:
    """log_tool_call returns the serialized entry dict."""
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(path=log_file)

    result = logger.log_tool_call(
        session_id="s",
        tool_name="Read",
        tool_input={"file": "/tmp/x.py"},
        result_status="error",
        duration_ms=5,
    )
    assert result["tool"] == "Read"
    assert result["status"] == "error"
    assert result["ms"] == 5


# ---------------------------------------------------------------------------
# Non-string values pass through
# ---------------------------------------------------------------------------


def test_non_string_values_not_redacted(tmp_path: Path) -> None:
    """Non-string values are not affected by redaction."""
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(path=log_file)

    logger.log_tool_call(
        session_id="s",
        tool_name="X",
        tool_input={"api_key": 12345, "count": 99},
        result_status="ok",
    )

    entry = json.loads(log_file.read_text().strip())
    # api_key is an int, not a string — redaction only applies to strings
    assert entry["input"]["api_key"] == 12345
    assert entry["input"]["count"] == 99


# ---------------------------------------------------------------------------
# Deps integration
# ---------------------------------------------------------------------------


def test_deps_has_audit_logger_field() -> None:
    """Deps dataclass accepts an audit_logger field."""
    from duh.kernel.deps import Deps

    logger = AuditLogger(path=Path("/tmp/fake.jsonl"))
    deps = Deps(audit_logger=logger)
    assert deps.audit_logger is logger


def test_deps_has_session_id_field() -> None:
    """Deps dataclass accepts a session_id field."""
    from duh.kernel.deps import Deps

    deps = Deps(session_id="abc-123")
    assert deps.session_id == "abc-123"


# ---------------------------------------------------------------------------
# Loop integration (audit_logger called during tool execution)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_calls_audit_logger_on_tool_ok(tmp_path: Path) -> None:
    """The query loop logs successful tool executions to the audit logger."""
    from duh.kernel.deps import Deps
    from duh.kernel.loop import query
    from duh.kernel.messages import Message

    log_file = tmp_path / "audit.jsonl"
    audit = AuditLogger(path=log_file)

    # Model returns one tool_use then stops
    call_count = 0

    async def fake_call_model(**kw: Any):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{
                        "type": "tool_use",
                        "id": "tu-1",
                        "name": "Bash",
                        "input": {"command": "echo hi"},
                    }],
                ),
            }
        else:
            yield {
                "type": "assistant",
                "message": Message(role="assistant", content="Done"),
            }

    async def fake_run_tool(name: str, inp: dict) -> str:
        return "hi"

    deps = Deps(
        call_model=fake_call_model,
        run_tool=fake_run_tool,
        session_id="test-sess",
        audit_logger=audit,
    )

    events = []
    async for ev in query(
        messages=[Message(role="user", content="run echo")],
        deps=deps,
    ):
        events.append(ev)

    # Verify audit entry was written
    entries = audit.read_entries()
    assert len(entries) == 1
    assert entries[0]["tool"] == "Bash"
    assert entries[0]["status"] == "ok"
    assert entries[0]["sid"] == "test-sess"


@pytest.mark.asyncio
async def test_loop_calls_audit_logger_on_tool_error(tmp_path: Path) -> None:
    """The query loop logs tool errors to the audit logger."""
    from duh.kernel.deps import Deps
    from duh.kernel.loop import query
    from duh.kernel.messages import Message

    log_file = tmp_path / "audit.jsonl"
    audit = AuditLogger(path=log_file)

    call_count = 0

    async def fake_call_model(**kw: Any):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{
                        "type": "tool_use",
                        "id": "tu-1",
                        "name": "Bash",
                        "input": {"command": "fail"},
                    }],
                ),
            }
        else:
            yield {
                "type": "assistant",
                "message": Message(role="assistant", content="Done"),
            }

    async def fake_run_tool(name: str, inp: dict) -> str:
        raise RuntimeError("command failed")

    deps = Deps(
        call_model=fake_call_model,
        run_tool=fake_run_tool,
        session_id="test-sess",
        audit_logger=audit,
    )

    events = []
    async for ev in query(
        messages=[Message(role="user", content="run fail")],
        deps=deps,
    ):
        events.append(ev)

    entries = audit.read_entries()
    assert len(entries) == 1
    assert entries[0]["status"] == "error"


@pytest.mark.asyncio
async def test_loop_calls_audit_logger_on_denied(tmp_path: Path) -> None:
    """The query loop logs denied tool calls to the audit logger."""
    from duh.kernel.deps import Deps
    from duh.kernel.loop import query
    from duh.kernel.messages import Message

    log_file = tmp_path / "audit.jsonl"
    audit = AuditLogger(path=log_file)

    call_count = 0

    async def fake_call_model(**kw: Any):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{
                        "type": "tool_use",
                        "id": "tu-1",
                        "name": "Bash",
                        "input": {"command": "rm -rf /"},
                    }],
                ),
            }
        else:
            yield {
                "type": "assistant",
                "message": Message(role="assistant", content="Denied"),
            }

    async def fake_approve(name: str, inp: dict) -> dict:
        return {"allowed": False, "reason": "Too dangerous"}

    deps = Deps(
        call_model=fake_call_model,
        run_tool=None,
        approve=fake_approve,
        session_id="test-sess",
        audit_logger=audit,
    )

    events = []
    async for ev in query(
        messages=[Message(role="user", content="delete everything")],
        deps=deps,
    ):
        events.append(ev)

    entries = audit.read_entries()
    assert len(entries) == 1
    assert entries[0]["status"] == "denied"
    assert entries[0]["tool"] == "Bash"


# ---------------------------------------------------------------------------
# CLI subcommand (duh audit)
# ---------------------------------------------------------------------------


def test_cli_audit_command_no_entries(tmp_path: Path, monkeypatch: Any) -> None:
    """duh audit with no entries prints a message."""
    monkeypatch.setattr(
        "duh.security.audit.AuditLogger.DEFAULT_PATH",
        tmp_path / "audit.jsonl",
    )
    from duh.cli.main import main
    rc = main(["audit"])
    assert rc == 0


def test_cli_audit_command_with_entries(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    """duh audit prints formatted entries."""
    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(path=log_file)
    logger.log_tool_call(
        session_id="abcd1234-5678",
        tool_name="Bash",
        tool_input={"command": "ls"},
        result_status="ok",
        duration_ms=10,
    )

    monkeypatch.setattr(
        "duh.security.audit.AuditLogger.DEFAULT_PATH",
        log_file,
    )
    from duh.cli.main import main
    rc = main(["audit"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Bash" in out
    assert "ok" in out
