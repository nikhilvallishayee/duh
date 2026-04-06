"""Tests for duh.ports — verify protocol contracts."""

import asyncio
from typing import Any, AsyncGenerator

from duh.ports import (
    ApprovalGate,
    ContextManager,
    ModelProvider,
    SessionStore,
    ToolExecutor,
)


# --- Fake implementations to verify protocols ---

class FakeProvider:
    async def stream(self, *, messages, **kwargs) -> AsyncGenerator[dict, None]:
        yield {"type": "assistant", "message": {"content": "hi"}}

class FakeExecutor:
    async def run(self, tool_name, input, **kwargs):
        return f"ran {tool_name}"

class FakeApprover:
    async def check(self, tool_name, input):
        return {"allowed": True}

class FakeStore:
    def __init__(self):
        self._data = {}

    async def save(self, session_id, messages):
        self._data[session_id] = messages

    async def load(self, session_id):
        return self._data.get(session_id)

    async def list_sessions(self):
        return [{"id": k} for k in self._data]

    async def delete(self, session_id):
        if session_id in self._data:
            del self._data[session_id]
            return True
        return False

class FakeContextManager:
    async def compact(self, messages, token_limit):
        return messages[-5:]  # keep last 5

    def estimate_tokens(self, messages):
        return sum(len(str(m)) for m in messages) // 4


class TestModelProvider:
    def test_satisfies_protocol(self):
        assert isinstance(FakeProvider(), ModelProvider)

    async def test_stream_yields_events(self):
        p = FakeProvider()
        events = [e async for e in p.stream(messages=[])]
        assert len(events) == 1
        assert events[0]["type"] == "assistant"


class TestToolExecutor:
    def test_satisfies_protocol(self):
        assert isinstance(FakeExecutor(), ToolExecutor)

    async def test_run_returns_result(self):
        e = FakeExecutor()
        result = await e.run("Read", {"path": "/tmp"})
        assert result == "ran Read"


class TestApprovalGate:
    def test_satisfies_protocol(self):
        assert isinstance(FakeApprover(), ApprovalGate)

    async def test_check_returns_allowed(self):
        a = FakeApprover()
        result = await a.check("Bash", {"command": "ls"})
        assert result["allowed"] is True


class TestSessionStore:
    def test_satisfies_protocol(self):
        assert isinstance(FakeStore(), SessionStore)

    async def test_save_and_load(self):
        s = FakeStore()
        await s.save("s1", [{"role": "user", "content": "hi"}])
        loaded = await s.load("s1")
        assert loaded is not None
        assert len(loaded) == 1

    async def test_load_missing(self):
        s = FakeStore()
        assert await s.load("nonexistent") is None

    async def test_list_sessions(self):
        s = FakeStore()
        await s.save("s1", [])
        await s.save("s2", [])
        sessions = await s.list_sessions()
        assert len(sessions) == 2

    async def test_delete(self):
        s = FakeStore()
        await s.save("s1", [])
        assert await s.delete("s1") is True
        assert await s.load("s1") is None
        assert await s.delete("s1") is False  # already deleted


class TestContextManager:
    def test_satisfies_protocol(self):
        assert isinstance(FakeContextManager(), ContextManager)

    async def test_compact_trims(self):
        cm = FakeContextManager()
        msgs = list(range(10))
        result = await cm.compact(msgs, 1000)
        assert len(result) == 5  # keeps last 5

    def test_estimate_tokens(self):
        cm = FakeContextManager()
        tokens = cm.estimate_tokens(["hello world"])
        assert tokens > 0
