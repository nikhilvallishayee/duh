# ADR-033: QueryGuard State Machine

**Status**: Accepted  
**Date**: 2026-04-08  
**Implemented**: 2026-04-08  
**Note**: QueryGuard implemented but not wired into the REPL or engine loop. cancel_on_new option not implemented.

## Context

The engine loop has no protection against concurrent query dispatch. If two user inputs arrive in rapid succession (e.g., double-click on submit, programmatic API calls), the engine can dispatch two provider calls simultaneously. This causes race conditions: interleaved tool executions, duplicated messages in history, and corrupted session state.

The reference TS harness uses a generation-tracked state machine to serialize queries and reject stale responses. This is especially important for the TUI where keypresses can queue faster than the model responds.

## Decision

Introduce a `QueryGuard` finite state machine that gates all provider calls:

### States

```
idle → dispatching → running → idle
         ↓
       (stale generation → rejected)
```

### Generation Tracking

Each query is assigned a monotonically increasing generation number. When a new query is dispatched, the generation increments. If a response arrives for a previous generation, it is silently discarded.

```python
class QueryGuard:
    def __init__(self):
        self._state = "idle"
        self._generation = 0
        self._lock = asyncio.Lock()

    async def dispatch(self) -> int:
        async with self._lock:
            if self._state != "idle":
                raise QueryInProgress("A query is already running")
            self._generation += 1
            self._state = "dispatching"
            return self._generation

    async def start_running(self, generation: int) -> None:
        async with self._lock:
            if generation != self._generation:
                raise StaleGeneration(generation, self._generation)
            self._state = "running"

    async def complete(self, generation: int) -> None:
        async with self._lock:
            if generation != self._generation:
                return  # Silently discard stale completion
            self._state = "idle"

    async def abort(self) -> None:
        async with self._lock:
            self._state = "idle"
```

### Integration

The engine loop wraps every provider call cycle:

1. `generation = await guard.dispatch()` — claim the slot
2. `await guard.start_running(generation)` — mark active
3. Execute provider call + tool loop
4. `await guard.complete(generation)` — release

If the user cancels (Ctrl+C), `guard.abort()` resets to idle without waiting for the provider.

### Cancellation

When a new query arrives while one is running, the guard can optionally cancel the in-flight query (via provider cancellation token) before starting the new one. This is opt-in behavior controlled by `cancel_on_new: bool`.

## Consequences

### Positive
- Eliminates race conditions from concurrent queries
- Stale responses cannot corrupt session state
- Clean cancellation support for interactive use
- Generation tracking is simple and debuggable

### Negative
- Adds a serialization point — no parallel queries (by design)
- Cancel-on-new requires provider support for cancellation tokens

### Risks
- Lock contention under rapid input — mitigated by the lock only being held for state transitions, never during provider calls
- Abort without cleanup could leak provider connections — mitigated by provider adapter's own connection management
