"""Tests for /compact async handling — QX-2 fix.

Verifies that /compact can be called from within an already-running
async event loop without raising RuntimeError.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from duh.cli.repl import _handle_slash
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig


def _make_engine() -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(model="test-model")
    return Engine(deps=deps, config=config)


class TestCompactAsync:
    """Ensure /compact works correctly inside an async context."""

    def test_compact_returns_sentinel_when_compactor_present(self):
        """_handle_slash returns the compact sentinel for async handling."""
        engine = _make_engine()

        async def fake_compact(messages):
            return messages[:1]

        deps = Deps(
            call_model=AsyncMock(),
            run_tool=AsyncMock(),
            compact=fake_compact,
        )
        keep, model = _handle_slash("/compact", engine, "m", deps)
        assert keep is True
        assert model == "\x00compact\x00"

    def test_compact_no_compactor_prints_message(self, capsys):
        """Without a compactor, /compact prints a message and returns normally."""
        engine = _make_engine()
        deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
        keep, model = _handle_slash("/compact", engine, "m", deps)
        assert keep is True
        assert model == "m"  # model unchanged
        out = capsys.readouterr().out
        assert "No compactor" in out

    @pytest.mark.asyncio
    async def test_compact_callable_from_async_context(self):
        """The compact coroutine can be awaited from within an async context.

        This is the core fix for QX-2: previously, /compact used
        asyncio.get_event_loop().run_until_complete() which crashes
        inside an already-running loop (Python 3.12+).
        """
        engine = _make_engine()
        engine._messages.extend([
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ])
        called = False

        async def fake_compact(messages):
            nonlocal called
            called = True
            # Simulate compaction by keeping only last message
            del messages[:-1]
            return messages

        deps = Deps(
            call_model=AsyncMock(),
            run_tool=AsyncMock(),
            compact=fake_compact,
        )

        # Step 1: _handle_slash returns sentinel (sync call)
        keep, model = _handle_slash("/compact", engine, "m", deps)
        assert keep is True
        assert model == "\x00compact\x00"

        # Step 2: Simulate what run_repl does — await the compact coroutine
        # This is the critical part: we're already inside an async context
        # and this must NOT raise RuntimeError
        await deps.compact(engine._messages)
        assert called is True

    @pytest.mark.asyncio
    async def test_compact_error_in_async_context(self):
        """If compact raises, the error is catchable without loop issues."""
        engine = _make_engine()

        async def failing_compact(messages):
            raise ValueError("compaction failed")

        deps = Deps(
            call_model=AsyncMock(),
            run_tool=AsyncMock(),
            compact=failing_compact,
        )

        keep, model = _handle_slash("/compact", engine, "m", deps)
        assert model == "\x00compact\x00"

        # The await should raise, but it's a normal exception, not a
        # RuntimeError about the event loop
        with pytest.raises(ValueError, match="compaction failed"):
            await deps.compact(engine._messages)
