# ADR-043: Wire QueryGuard into the REPL

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-11
**Depends on**: ADR-033 (QueryGuard State Machine)

## Context

ADR-033 introduced the `QueryGuard` finite state machine to prevent concurrent queries from corrupting session state. The implementation lives in `duh/kernel/query_guard.py` and has full test coverage. However, it was never wired into the REPL loop or the engine — it sits unused.

Without the guard, two scenarios can corrupt state: (1) a user submitting a new prompt while a previous query is still streaming (possible in future TUI or programmatic API use), and (2) a Ctrl-C abort leaving the engine in an ambiguous state where stale provider callbacks could still modify message history.

## Decision

Wire the existing `QueryGuard` into the REPL loop in `duh/cli/repl.py`:

1. **Instantiate** a `QueryGuard` once per `run_repl()` session, alongside the engine.
2. **Reserve** (`guard.reserve()`) immediately before dispatching `engine.run()`. If the guard is not idle (concurrent query attempt), display an error and skip the prompt.
3. **Start** (`guard.try_start(gen)`) once the engine begins streaming. If the generation is stale, skip the turn.
4. **End** (`guard.end(gen)`) in a `finally` block after the streaming loop completes normally.
5. **Force-end** (`guard.force_end()`) on `KeyboardInterrupt` / `EOFError` during streaming, which bumps the generation so any in-flight callbacks from the cancelled query are rejected.

The guard is not placed inside the engine itself. The REPL owns the user-interaction lifecycle; the engine owns the model-interaction lifecycle. The guard belongs at the boundary between user input and engine dispatch — which is the REPL loop.

## Consequences

### Positive
- Concurrent query corruption is impossible — the guard serializes access
- Ctrl-C abort cleanly invalidates stale callbacks via generation bump
- Zero overhead — the guard is a single integer comparison, no locks, no async
- Existing `QueryGuard` tests continue to pass without modification

### Negative
- Only the REPL is protected; the SDK runner and programmatic API are not (they can add their own guard in future)
- The guard does not cancel in-flight provider requests — it only rejects stale responses

### Risks
- None significant. The guard is a thin state machine with no I/O.

## Implementation Notes

- `duh/cli/repl.py` — imports `QueryGuard` from `duh/kernel/query_guard.py`,
  instantiates one per `run_repl()` session, and calls
  `reserve()` / `try_start()` / `end()` / `force_end()` around each
  `engine.run()` dispatch.
- `duh/kernel/query_guard.py` — synchronous FSM (no `asyncio.Lock`), generation
  counter bumped on both `reserve()` and `force_end()`.

Related: ADR-033.
