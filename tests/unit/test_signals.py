"""Tests for graceful shutdown signal handling."""
import asyncio

import pytest

from duh.kernel.signals import ShutdownHandler


@pytest.mark.asyncio
async def test_shutdown_handler_not_triggered_by_default():
    handler = ShutdownHandler()
    assert not handler.shutting_down


@pytest.mark.asyncio
async def test_shutdown_handler_trigger():
    handler = ShutdownHandler()
    handler.trigger()
    assert handler.shutting_down


@pytest.mark.asyncio
async def test_shutdown_runs_callbacks():
    results = []

    async def cleanup1():
        results.append("c1")

    async def cleanup2():
        results.append("c2")

    handler = ShutdownHandler(timeout=5.0)
    handler.on_shutdown(cleanup1)
    handler.on_shutdown(cleanup2)
    await handler.run_cleanup()
    assert results == ["c1", "c2"]


@pytest.mark.asyncio
async def test_shutdown_callback_timeout():
    """Callbacks that exceed timeout should not block shutdown."""
    async def slow():
        await asyncio.sleep(100)

    handler = ShutdownHandler(timeout=0.1)
    handler.on_shutdown(slow)
    await handler.run_cleanup()  # Should complete within ~0.1s, not hang


@pytest.mark.asyncio
async def test_shutdown_callback_error_isolation():
    """One failing callback should not prevent others from running."""
    results = []

    async def fail():
        raise RuntimeError("boom")

    async def succeed():
        results.append("ok")

    handler = ShutdownHandler(timeout=5.0)
    handler.on_shutdown(fail)
    handler.on_shutdown(succeed)
    await handler.run_cleanup()
    assert results == ["ok"]
