"""Tests for session auto-save — Engine saves after every turn.

Verifies:
- Engine auto-saves when session_store is provided
- Engine skips save when session_store is None
- Auto-save is called with correct session_id and messages
- Auto-save errors are swallowed (don't crash the engine)
- Multi-turn conversations accumulate correctly
- --continue restores history into a new engine
- --resume with specific session_id works
- REPL crash recovery: messages persist across engine instances
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from duh.adapters.file_store import FileStore
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _simple_model(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
    """Fake model that returns a single assistant message."""
    yield {"type": "assistant", "message": Message(
        role="assistant",
        content=[{"type": "text", "text": "Hello!"}],
    )}


async def _multi_tool_model(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
    """Fake model that uses a tool then responds."""
    # First: assistant with tool_use
    yield {"type": "assistant", "message": Message(
        role="assistant",
        content=[
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo hi"}},
        ],
    )}


def _make_deps(**overrides: Any) -> Deps:
    return Deps(
        call_model=overrides.get("call_model", _simple_model),
        run_tool=overrides.get("run_tool"),
    )


class _FakeStore:
    """In-memory fake that satisfies the SessionStore protocol."""

    def __init__(self) -> None:
        self._data: dict[str, list[Any]] = {}
        self.save_count = 0

    async def save(self, session_id: str, messages: list[Any]) -> None:
        self._data[session_id] = list(messages)
        self.save_count += 1

    async def load(self, session_id: str) -> list[dict[str, Any]] | None:
        msgs = self._data.get(session_id)
        if msgs is None:
            return None
        result: list[dict[str, Any]] = []
        for m in msgs:
            if isinstance(m, Message):
                result.append({"role": m.role, "content": m.content})
            else:
                result.append(m)
        return result

    async def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {"session_id": sid, "modified": "", "message_count": len(msgs)}
            for sid, msgs in self._data.items()
        ]

    async def delete(self, session_id: str) -> bool:
        return self._data.pop(session_id, None) is not None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAutoSaveBasic:
    """Engine auto-saves after each turn when session_store is set."""

    async def test_autosave_called_after_turn(self):
        store = _FakeStore()
        engine = Engine(deps=_make_deps(), session_store=store)

        async for _ in engine.run("hello"):
            pass

        assert store.save_count == 1
        saved = store._data[engine.session_id]
        # Should have at least user + assistant messages
        assert len(saved) >= 2
        assert saved[0].role == "user"
        assert saved[1].role == "assistant"

    async def test_no_autosave_without_store(self):
        """Engine works fine without a session_store (no crash)."""
        engine = Engine(deps=_make_deps())

        events = []
        async for e in engine.run("hello"):
            events.append(e)

        types = [e["type"] for e in events]
        assert "done" in types
        assert engine.turn_count == 1

    async def test_autosave_uses_correct_session_id(self):
        store = _FakeStore()
        engine = Engine(deps=_make_deps(), session_store=store)

        async for _ in engine.run("test"):
            pass

        assert engine.session_id in store._data


class TestAutoSaveMultiTurn:
    """Multi-turn auto-save accumulates messages correctly."""

    async def test_two_turns_two_saves(self):
        store = _FakeStore()
        engine = Engine(deps=_make_deps(), session_store=store)

        async for _ in engine.run("first"):
            pass
        async for _ in engine.run("second"):
            pass

        assert store.save_count == 2
        saved = store._data[engine.session_id]
        # 2 user + 2 assistant = 4 messages minimum
        assert len(saved) >= 4

    async def test_messages_accumulate(self):
        store = _FakeStore()
        engine = Engine(deps=_make_deps(), session_store=store)

        async for _ in engine.run("first"):
            pass
        count_after_first = len(store._data[engine.session_id])

        async for _ in engine.run("second"):
            pass
        count_after_second = len(store._data[engine.session_id])

        assert count_after_second > count_after_first


class TestAutoSaveErrorResilience:
    """Auto-save errors must not crash the engine."""

    async def test_save_error_swallowed(self):
        """If store.save() raises, engine still completes normally."""

        class _BrokenStore:
            async def save(self, session_id: str, messages: list[Any]) -> None:
                raise IOError("disk full")

            async def load(self, session_id: str) -> list[Any] | None:
                return None

            async def list_sessions(self) -> list[dict[str, Any]]:
                return []

            async def delete(self, session_id: str) -> bool:
                return False

        engine = Engine(deps=_make_deps(), session_store=_BrokenStore())

        events = []
        async for e in engine.run("hello"):
            events.append(e)

        types = [e["type"] for e in events]
        assert "done" in types
        assert engine.turn_count == 1

    async def test_save_error_does_not_lose_messages(self):
        """Even when save fails, engine._messages is intact."""

        class _FlakeyStore:
            async def save(self, session_id: str, messages: list[Any]) -> None:
                raise RuntimeError("transient error")

            async def load(self, session_id: str) -> list[Any] | None:
                return None

            async def list_sessions(self) -> list[dict[str, Any]]:
                return []

            async def delete(self, session_id: str) -> bool:
                return False

        engine = Engine(deps=_make_deps(), session_store=_FlakeyStore())

        async for _ in engine.run("hello"):
            pass

        assert len(engine.messages) >= 2  # user + assistant


class TestCrashRecovery:
    """Simulate crash recovery: save with one engine, restore with another."""

    async def test_crash_recovery_via_store(self):
        """Messages saved by engine 1 can be loaded into engine 2."""
        store = _FakeStore()

        # Engine 1: run a turn, auto-save kicks in
        engine1 = Engine(deps=_make_deps(), session_store=store)
        async for _ in engine1.run("remember this"):
            pass

        # Simulate crash — engine1 is gone. Load from store.
        saved = await store.load(engine1.session_id)
        assert saved is not None
        assert len(saved) >= 2

        # Engine 2: restore messages
        engine2 = Engine(deps=_make_deps(), session_store=store)
        for m in saved:
            role = m.get("role", "user") if isinstance(m, dict) else m.role
            content = m.get("content", "") if isinstance(m, dict) else m.content
            engine2._messages.append(Message(role=role, content=content))

        # Engine 2 has the full history
        assert len(engine2.messages) == len(saved)
        assert engine2.messages[0].role == "user"

    async def test_continue_latest_session(self):
        """--continue semantics: load the most recent session."""
        store = _FakeStore()

        # Run two separate engines
        engine1 = Engine(deps=_make_deps(), session_store=store)
        async for _ in engine1.run("session one"):
            pass

        engine2 = Engine(deps=_make_deps(), session_store=store)
        async for _ in engine2.run("session two"):
            pass

        # Both sessions exist
        sessions = await store.list_sessions()
        assert len(sessions) == 2

        # Load both and verify they are distinct
        data1 = await store.load(engine1.session_id)
        data2 = await store.load(engine2.session_id)
        assert data1 is not None
        assert data2 is not None
        assert data1 != data2


class TestFileStoreIntegration:
    """Integration: Engine + real FileStore on disk."""

    async def test_autosave_with_real_file_store(self, tmp_path):
        store = FileStore(base_dir=tmp_path / "sessions")
        engine = Engine(deps=_make_deps(), session_store=store)

        async for _ in engine.run("hello world"):
            pass

        # Verify file was written
        session_file = tmp_path / "sessions" / f"{engine.session_id}.jsonl"
        assert session_file.exists()

        # Verify we can load it back
        loaded = await store.load(engine.session_id)
        assert loaded is not None
        assert len(loaded) >= 2
        assert loaded[0]["role"] == "user"

    async def test_multi_turn_file_store(self, tmp_path):
        store = FileStore(base_dir=tmp_path / "sessions")
        engine = Engine(deps=_make_deps(), session_store=store)

        async for _ in engine.run("turn one"):
            pass
        async for _ in engine.run("turn two"):
            pass

        loaded = await store.load(engine.session_id)
        assert loaded is not None
        # At least 4 messages: 2 user + 2 assistant
        assert len(loaded) >= 4

    async def test_resume_from_file_store(self, tmp_path):
        """Full round-trip: save via auto-save, reload into new engine."""
        store = FileStore(base_dir=tmp_path / "sessions")

        # Engine 1 runs and auto-saves
        engine1 = Engine(deps=_make_deps(), session_store=store)
        async for _ in engine1.run("important context"):
            pass
        sid = engine1.session_id

        # Engine 2 restores from the same session
        engine2 = Engine(deps=_make_deps(), session_store=store)
        prev = await store.load(sid)
        assert prev is not None
        for m in prev:
            engine2._messages.append(
                Message(role=m["role"], content=m["content"])
            )

        # Verify engine2 has the full history
        assert len(engine2.messages) >= 2
        assert engine2.messages[0].role == "user"
        assert engine2.messages[0].content == "important context"
