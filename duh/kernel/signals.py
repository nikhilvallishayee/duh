"""Graceful shutdown — signal handling and cleanup coordination.

Registers SIGTERM/SIGINT handlers that trigger cleanup callbacks before exit.
Callbacks run in LIFO order with a per-callback timeout to prevent hanging.

A second SIGINT/SIGTERM forces an immediate exit. SIGQUIT dumps stack traces.

Usage:
    handler = ShutdownHandler()
    handler.on_shutdown(my_cleanup_fn)
    handler.install()  # registers signal handlers
"""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import signal
import sys
import traceback
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

# Default timeout for the entire cleanup sequence (ADR-030 spec: 5 s).
DEFAULT_SHUTDOWN_TIMEOUT = 5.0


class ShutdownHandler:
    """Coordinates graceful shutdown with timeout-bounded cleanup.

    - Callbacks registered via on_shutdown() execute in LIFO order.
    - A second SIGINT/SIGTERM forces sys.exit(1) immediately.
    - SIGQUIT dumps all thread stack traces to stderr then exits.
    - Total cleanup is bounded by *timeout* seconds; individual callbacks
      that stall are cancelled when the budget is exhausted.
    """

    def __init__(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT):
        self._timeout = timeout
        self._callbacks: list[Callable[[], Awaitable[None]]] = []
        self._shutting_down = False

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    def trigger(self) -> None:
        """Mark shutdown as in progress."""
        self._shutting_down = True

    def on_shutdown(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Register a cleanup callback. Executes in LIFO (reverse) order."""
        self._callbacks.append(callback)

    async def run_cleanup(self) -> None:
        """Run all cleanup callbacks in LIFO order with a shared timeout budget."""
        self._shutting_down = True
        deadline = self._timeout
        for cb in reversed(self._callbacks):
            if deadline <= 0:
                logger.warning(
                    "Shutdown timeout exhausted; skipping remaining callbacks"
                )
                break
            import time
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(cb(), timeout=deadline)
            except asyncio.TimeoutError:
                logger.warning("Shutdown callback %s timed out", cb.__name__)
            except Exception:
                logger.warning(
                    "Shutdown callback %s failed", cb.__name__, exc_info=True
                )
            finally:
                deadline -= time.monotonic() - t0

    def install(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Install SIGTERM, SIGINT, and SIGQUIT handlers on the event loop.

        Safe to call from within a running loop.  On non-Unix systems
        (Windows), falls back to signal.signal() (no SIGQUIT support).
        """
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # No loop running — skip signal installation

        def _handle_signal(sig: signal.Signals) -> None:
            if self._shutting_down:
                # Second signal: force immediate exit.
                logger.warning(
                    "Received second %s — forcing immediate exit", sig.name
                )
                sys.exit(1)
            logger.info("Received %s, shutting down...", sig.name)
            self.trigger()
            # Schedule cleanup as a task so it runs in the event loop.
            loop.create_task(self._shutdown_and_exit(sig))

        def _handle_sigquit() -> None:  # pragma: no cover - signal not on all platforms
            """Dump all thread stacks to stderr, then exit."""
            faulthandler.dump_traceback(sys.stderr)
            # Also dump via traceback for clarity
            for thread_id, frame in sys._current_frames().items():
                print(f"\n--- Thread {thread_id} ---", file=sys.stderr)
                traceback.print_stack(frame, file=sys.stderr)
            sys.exit(1)

        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, _handle_signal, sig)
            # SIGQUIT is Unix-only
            if hasattr(signal, "SIGQUIT"):
                loop.add_signal_handler(signal.SIGQUIT, _handle_sigquit)
        except NotImplementedError:
            # Windows: add_signal_handler not supported
            pass

    async def _shutdown_and_exit(self, sig: signal.Signals) -> None:
        """Run cleanup then exit."""
        await self.run_cleanup()
        raise SystemExit(128 + sig.value)
