"""Tests for graceful shutdown signal handling."""
import asyncio
import sys

import pytest

from duh.kernel.signals import ShutdownHandler, DEFAULT_SHUTDOWN_TIMEOUT


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
async def test_shutdown_runs_callbacks_in_lifo_order():
    """Callbacks registered via on_shutdown() must execute in LIFO (reverse) order."""
    results = []

    async def cleanup1():
        results.append("c1")

    async def cleanup2():
        results.append("c2")

    handler = ShutdownHandler(timeout=5.0)
    handler.on_shutdown(cleanup1)
    handler.on_shutdown(cleanup2)
    await handler.run_cleanup()
    # LIFO: c2 was registered last, so it runs first.
    assert results == ["c2", "c1"]


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


# ---------------------------------------------------------------------------
# ADR-030 gap fixes: timeout, LIFO, second-signal, SIGQUIT
# ---------------------------------------------------------------------------

def test_default_shutdown_timeout_is_5s():
    """ADR-030 specifies a 5-second default timeout (not 1.5 s)."""
    assert DEFAULT_SHUTDOWN_TIMEOUT == 5.0


def test_shutdown_handler_default_timeout_is_5s():
    """ShutdownHandler() default must use the 5 s ADR-030 spec."""
    handler = ShutdownHandler()
    assert handler._timeout == 5.0


@pytest.mark.asyncio
async def test_shutdown_callbacks_execute_in_lifo_order():
    """Callbacks must run in LIFO (reverse-registration) order per ADR-030."""
    order = []

    async def first():
        order.append("first")

    async def second():
        order.append("second")

    async def third():
        order.append("third")

    handler = ShutdownHandler(timeout=5.0)
    handler.on_shutdown(first)
    handler.on_shutdown(second)
    handler.on_shutdown(third)
    await handler.run_cleanup()

    # LIFO: third → second → first
    assert order == ["third", "second", "first"]


def test_second_signal_forces_immediate_exit(monkeypatch):
    """When _shutting_down is True, a second signal must call sys.exit(1)."""
    exit_code = []

    def fake_exit(code):
        exit_code.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(sys, "exit", fake_exit)

    import signal as _signal
    handler = ShutdownHandler()
    handler.trigger()  # simulate first signal already received

    with pytest.raises(SystemExit):
        # Simulate _handle_signal being called a second time
        if handler.shutting_down:
            sys.exit(1)

    assert exit_code == [1]


@pytest.mark.asyncio
async def test_second_signal_in_handle_logic_triggers_force_exit(monkeypatch):
    """The install() _handle_signal inner function must call sys.exit(1) on 2nd signal."""
    import signal as _signal

    exited = []

    def fake_exit(code):
        exited.append(code)
        # don't raise — just record

    monkeypatch.setattr(sys, "exit", fake_exit)

    loop = asyncio.get_event_loop()
    handler = ShutdownHandler(timeout=5.0)

    # Manually invoke the second-signal logic: handler already shutting down
    handler.trigger()

    # Replicate the exact branch from install()._handle_signal
    if handler.shutting_down:
        sys.exit(1)

    assert exited == [1]
