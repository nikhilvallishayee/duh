# ADR-030: Graceful Shutdown

**Status**: Accepted  
**Date**: 2026-04-08  
**Implemented**: 2026-04-08  
**Note**: Second-signal force exit and SIGQUIT stack dump not implemented.

## Context

D.U.H. has no signal handling. When a user presses Ctrl+C or the process receives SIGTERM (e.g., from a container orchestrator), the process exits immediately. This can leave:
- Session state unsaved (losing conversation history)
- MCP server processes orphaned
- Temporary files unclean
- Provider streaming connections dangling

The reference TS harness handles SIGINT/SIGTERM with a coordinated shutdown sequence. Production deployments in containers require SIGTERM handling to meet graceful shutdown contracts.

## Decision

Introduce a `ShutdownHandler` that manages ordered cleanup:

```python
class ShutdownHandler:
    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout
        self._callbacks: list[Callable] = []
        self._shutting_down = False

    def register(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Register a cleanup callback. Called in LIFO order."""
        self._callbacks.append(callback)

    async def shutdown(self, signal: int) -> None:
        if self._shutting_down:
            return  # Second signal = force exit
        self._shutting_down = True
        for cb in reversed(self._callbacks):
            try:
                await asyncio.wait_for(cb(), timeout=self.timeout)
            except (asyncio.TimeoutError, Exception):
                pass  # Log but don't block
        sys.exit(0)
```

### Signal Handling

- **First SIGINT/SIGTERM**: Trigger graceful shutdown. Run all callbacks with per-callback timeout.
- **Second signal**: Force immediate exit (for truly stuck shutdowns).
- **SIGQUIT**: Dump stack traces for debugging, then exit.

### Registration Order

Components register in startup order; callbacks execute in reverse (LIFO):

1. Provider streaming connections (cancel in-flight requests)
2. MCP server processes (send shutdown, wait briefly)
3. Session state (persist to disk)
4. Temporary files (cleanup)
5. TUI (restore terminal state)

### Timeout

Default 5 seconds total. Each callback gets the remaining time, not the full timeout. If the total exceeds 5s, remaining callbacks are skipped and we exit.

## Consequences

### Positive
- Session state survives Ctrl+C — no more lost conversations
- MCP servers are cleanly stopped — no orphan processes
- Terminal state is always restored — no broken terminals
- Container orchestrators get clean exits

### Negative
- Adds complexity to startup (each component must register its cleanup)
- Double-signal force exit means some cleanup may be skipped

### Risks
- Cleanup callbacks that block longer than expected delay shutdown — mitigated by per-callback timeout
- Registration order bugs could cause saves before connections close — mitigated by LIFO ordering
