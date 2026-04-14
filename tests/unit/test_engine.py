"""Tests for duh.kernel.engine — session lifecycle wrapper."""

import asyncio
from typing import Any, AsyncGenerator

import pytest

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.security.trifecta import Capability, LethalTrifectaError


async def simple_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
    yield {"type": "assistant", "message": Message(
        role="assistant",
        content=[{"type": "text", "text": "Hello!"}],
    )}


class TestEngine:
    async def test_create(self):
        deps = Deps(call_model=simple_model)
        engine = Engine(deps=deps)
        assert engine.session_id  # auto-generated
        assert engine.turn_count == 0
        assert engine.messages == []

    async def test_run_single_turn(self):
        deps = Deps(call_model=simple_model)
        engine = Engine(deps=deps)

        events = []
        async for e in engine.run("hello"):
            events.append(e)

        assert engine.turn_count == 1
        assert len(engine.messages) >= 1  # at least user message

        types = [e["type"] for e in events]
        assert "session" in types
        assert "assistant" in types
        assert "done" in types

    async def test_session_event(self):
        deps = Deps(call_model=simple_model)
        engine = Engine(deps=deps)

        events = []
        async for e in engine.run("hi"):
            events.append(e)

        session_events = [e for e in events if e["type"] == "session"]
        assert len(session_events) == 1
        assert session_events[0]["session_id"] == engine.session_id
        assert session_events[0]["turn"] == 1

    async def test_multi_turn(self):
        deps = Deps(call_model=simple_model)
        engine = Engine(deps=deps)

        async for _ in engine.run("first"):
            pass
        async for _ in engine.run("second"):
            pass

        assert engine.turn_count == 2

    async def test_config(self):
        config = EngineConfig(
            model="claude-opus-4-6",
            system_prompt="You are helpful",
            max_turns=5,
            cwd="/tmp",
        )
        deps = Deps(call_model=simple_model)
        engine = Engine(deps=deps, config=config)

        # Config should be stored
        assert engine._config.model == "claude-opus-4-6"
        assert engine._config.max_turns == 5

    async def test_kwargs_config(self):
        deps = Deps(call_model=simple_model)
        engine = Engine(deps=deps, model="test-model", max_turns=3)
        assert engine._config.model == "test-model"
        assert engine._config.max_turns == 3

    async def test_unique_session_ids(self):
        deps = Deps(call_model=simple_model)
        e1 = Engine(deps=deps)
        e2 = Engine(deps=deps)
        assert e1.session_id != e2.session_id


def _make_engine(*, trifecta_acknowledged: bool = True, tools: list | None = None) -> Engine:
    """Test helper — create a minimal Engine with a fake model."""
    deps = Deps(call_model=simple_model)
    return Engine(
        deps=deps,
        config=EngineConfig(
            tools=tools or [],
            trifecta_acknowledged=trifecta_acknowledged,
        ),
    )


def test_engine_creates_session_key_and_minter() -> None:
    """Engine must generate a 32-byte session key and expose a ConfirmationMinter."""
    from duh.kernel.confirmation import ConfirmationMinter

    engine = _make_engine()
    assert hasattr(engine, "_confirmation_minter")
    assert isinstance(engine._confirmation_minter, ConfirmationMinter)
    # The key is random — just verify it's 32 bytes
    assert len(engine._confirmation_minter._key) == 32


# ---------------------------------------------------------------------------
# Task 7.3.5: Trifecta check at SESSION_START
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass
class _TriFakeTool:
    """Minimal fake tool for trifecta testing."""
    name: str
    capabilities: Capability


_TRIFECTA_TOOLS = [
    _TriFakeTool(name="Read", capabilities=Capability.READ_PRIVATE),
    _TriFakeTool(
        name="WebFetch",
        capabilities=Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS,
    ),
]


def test_engine_refuses_session_with_lethal_trifecta() -> None:
    """Default tool set triggers trifecta — session must refuse."""
    with pytest.raises(LethalTrifectaError):
        _make_engine(tools=_TRIFECTA_TOOLS, trifecta_acknowledged=False)


def test_engine_starts_with_trifecta_acknowledged() -> None:
    engine = _make_engine(tools=_TRIFECTA_TOOLS, trifecta_acknowledged=True)
    assert engine is not None


def test_engine_starts_no_trifecta_tools() -> None:
    """Engine with no trifecta tools starts fine without ack."""
    tools = [_TriFakeTool(name="Read", capabilities=Capability.READ_PRIVATE)]
    engine = _make_engine(tools=tools, trifecta_acknowledged=False)
    assert engine is not None
