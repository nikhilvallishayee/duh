"""QueryGuard — concurrent query state machine.

Prevents race conditions where multiple queries run simultaneously.
Implements the QueryGuard concurrent query prevention pattern.

State transitions:
    IDLE → DISPATCHING (reserve / async_reserve)
    DISPATCHING → RUNNING (try_start / async_try_start)
    RUNNING → IDLE (end / async_end)
    ANY → IDLE (force_end / async_force_end)

Each transition is generation-tracked. Stale generations are rejected,
preventing callbacks from a cancelled query from affecting a new one.

The synchronous ``reserve`` / ``try_start`` / ``end`` / ``force_end``
methods remain for backwards compatibility with the existing REPL wiring.
The async variants (``async_reserve`` etc.) use an ``asyncio.Lock`` for
full coroutine safety, as required by ADR-033.

Optional ``cancel_on_new`` flag: when True, a call to ``async_reserve``
while the guard is already RUNNING will cancel the registered in-flight
``asyncio.Task`` (if any) and abort to IDLE before accepting the new query.
Register the task via ``set_current_task()``.

Usage (sync — backward compat):
    guard = QueryGuard()
    gen = guard.reserve()           # IDLE → DISPATCHING
    if guard.try_start(gen):        # DISPATCHING → RUNNING
        try:
            await do_query()
        finally:
            guard.end(gen)          # RUNNING → IDLE

Usage (async):
    guard = QueryGuard()
    gen = await guard.async_reserve()
    if await guard.async_try_start(gen):
        task = asyncio.current_task()
        guard.set_current_task(task)
        try:
            await do_query()
        finally:
            await guard.async_end(gen)
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Optional


class QueryState(str, Enum):
    IDLE = "idle"
    DISPATCHING = "dispatching"
    RUNNING = "running"


class QueryGuard:
    """Thread-safe state machine for concurrent query prevention.

    Parameters
    ----------
    cancel_on_new:
        When *True*, calling ``async_reserve`` while a query is RUNNING will
        cancel any registered ``asyncio.Task`` and force the guard to IDLE
        before claiming the slot for the new query.  Default is *False*
        (legacy behaviour: raise RuntimeError if not idle).
    """

    def __init__(self, *, cancel_on_new: bool = False) -> None:
        self._state = QueryState.IDLE
        self._generation = 0
        self._lock = asyncio.Lock()
        self.cancel_on_new: bool = cancel_on_new
        self._current_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def state(self) -> QueryState:
        return self._state

    @property
    def generation(self) -> int:
        return self._generation

    # ------------------------------------------------------------------
    # Synchronous API (backward compat — no lock held)
    # ------------------------------------------------------------------

    def reserve(self) -> int:
        """Reserve a slot for a new query. Returns generation number.

        Raises RuntimeError if not idle.
        """
        if self._state != QueryState.IDLE:
            raise RuntimeError(
                f"Cannot reserve: state is {self._state.value}, not idle"
            )
        self._generation += 1
        self._state = QueryState.DISPATCHING
        return self._generation

    def try_start(self, gen: int) -> int | None:
        """Transition from dispatching to running.

        Returns gen if successful, None if generation is stale.
        """
        if gen != self._generation:
            return None
        if self._state != QueryState.DISPATCHING:
            return None
        self._state = QueryState.RUNNING
        return gen

    def end(self, gen: int) -> bool:
        """Transition from running to idle.

        Returns True if successful, False if generation is stale.
        """
        if gen != self._generation:
            return False
        self._state = QueryState.IDLE
        self._current_task = None
        return True

    def force_end(self) -> None:
        """Force transition to idle regardless of current state.

        Bumps generation to invalidate any in-flight callbacks.
        """
        self._generation += 1
        self._state = QueryState.IDLE
        self._current_task = None

    # ------------------------------------------------------------------
    # Async API (uses asyncio.Lock for coroutine safety — ADR-033)
    # ------------------------------------------------------------------

    async def async_reserve(self) -> int:
        """Async-safe reserve. Returns new generation number.

        If ``cancel_on_new`` is True and the guard is currently RUNNING,
        the registered in-flight task is cancelled and the guard is forced
        to IDLE before claiming the new slot.

        Raises RuntimeError if not idle (and cancel_on_new is False).
        """
        async with self._lock:
            if self._state != QueryState.IDLE:
                if self.cancel_on_new and self._state == QueryState.RUNNING:
                    # Cancel the in-flight task if registered
                    if self._current_task is not None and not self._current_task.done():
                        self._current_task.cancel()
                        self._current_task = None
                    # Force to idle (bump generation to invalidate old callbacks)
                    self._generation += 1
                    self._state = QueryState.IDLE
                else:
                    raise RuntimeError(
                        f"Cannot reserve: state is {self._state.value}, not idle"
                    )
            self._generation += 1
            self._state = QueryState.DISPATCHING
            return self._generation

    async def async_try_start(self, gen: int) -> int | None:
        """Async-safe transition from dispatching to running.

        Returns gen if successful, None if generation is stale.
        """
        async with self._lock:
            if gen != self._generation:
                return None
            if self._state != QueryState.DISPATCHING:
                return None
            self._state = QueryState.RUNNING
            return gen

    async def async_end(self, gen: int) -> bool:
        """Async-safe transition from running to idle.

        Returns True if successful, False if generation is stale.
        """
        async with self._lock:
            if gen != self._generation:
                return False
            self._state = QueryState.IDLE
            self._current_task = None
            return True

    async def async_force_end(self) -> None:
        """Async-safe force to idle regardless of current state."""
        async with self._lock:
            self._generation += 1
            self._state = QueryState.IDLE
            self._current_task = None

    # ------------------------------------------------------------------
    # Task registration (for cancel_on_new)
    # ------------------------------------------------------------------

    def set_current_task(self, task: "asyncio.Task[object]") -> None:  # type: ignore[type-arg]
        """Register the current in-flight asyncio.Task.

        When ``cancel_on_new`` is True and a new query arrives, this task
        will be cancelled.  Should be called immediately after
        ``async_try_start`` succeeds.
        """
        self._current_task = task
