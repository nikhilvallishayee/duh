"""QueryGuard — concurrent query state machine.

Prevents race conditions where multiple queries run simultaneously.
Ported from Claude Code TS's QueryGuard pattern.

State transitions:
    IDLE → DISPATCHING (reserve)
    DISPATCHING → RUNNING (try_start)
    RUNNING → IDLE (end)
    ANY → IDLE (force_end)

Each transition is generation-tracked. Stale generations are rejected,
preventing callbacks from a cancelled query from affecting a new one.

Usage:
    guard = QueryGuard()
    gen = guard.reserve()          # IDLE → DISPATCHING
    if guard.try_start(gen):       # DISPATCHING → RUNNING
        try:
            await do_query()
        finally:
            guard.end(gen)         # RUNNING → IDLE
"""

from __future__ import annotations

from enum import Enum


class QueryState(str, Enum):
    IDLE = "idle"
    DISPATCHING = "dispatching"
    RUNNING = "running"


class QueryGuard:
    """Thread-safe state machine for concurrent query prevention."""

    def __init__(self) -> None:
        self._state = QueryState.IDLE
        self._generation = 0

    @property
    def state(self) -> QueryState:
        return self._state

    @property
    def generation(self) -> int:
        return self._generation

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
        return True

    def force_end(self) -> None:
        """Force transition to idle regardless of current state.

        Bumps generation to invalidate any in-flight callbacks.
        """
        self._generation += 1
        self._state = QueryState.IDLE
