"""Graceful shutdown — signal handling and cleanup coordination.

Registers SIGTERM/SIGINT handlers that trigger cleanup callbacks before exit.
Callbacks run with a timeout to prevent hanging on exit.

Usage:
    handler = ShutdownHandler()
    handler.on_shutdown(my_cleanup_fn)
    handler.install()  # registers signal handlers
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

# Default timeout for cleanup callbacks (matches Claude Code's 1.5s session end)
DEFAULT_SHUTDOWN_TIMEOUT = 1.5


class ShutdownHandler:
    """Coordinates graceful shutdown with timeout-bounded cleanup."""

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
        """Register a cleanup callback. Runs in registration order."""
        self._callbacks.append(callback)

    async def run_cleanup(self) -> None:
        """Run all cleanup callbacks with timeout. Errors are isolated."""
        self._shutting_down = True
        for cb in self._callbacks:
            try:
                await asyncio.wait_for(cb(), timeout=self._timeout)
            except asyncio.TimeoutError:
                logger.warning("Shutdown callback %s timed out", cb.__name__)
            except Exception:
                logger.warning("Shutdown callback %s failed", cb.__name__, exc_info=True)

    def install(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Install SIGTERM and SIGINT handlers on the event loop.

        Safe to call from within a running loop. On non-Unix systems
        (Windows), falls back to signal.signal().
        """
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # No loop running — skip signal installation

        def _handle_signal(sig: signal.Signals) -> None:
            logger.info("Received %s, shutting down...", sig.name)
            self.trigger()
            # Schedule cleanup as a task so it runs in the event loop
            loop.create_task(self._shutdown_and_exit(sig))

        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            # Windows: add_signal_handler not supported
            pass

    async def _shutdown_and_exit(self, sig: signal.Signals) -> None:
        """Run cleanup then exit."""
        await self.run_cleanup()
        raise SystemExit(128 + sig.value)
