# ADR-032: MCP Session Management

**Status:** Accepted — partial (session-expiry detection and single-retry reconnect
work; consecutive-failure circuit breaker does not mark servers as `degraded` or remove
their tools from the active schema)
**Date**: 2026-04-08

## Context

MCP (Model Context Protocol) servers maintain stateful sessions. These sessions can expire due to server restarts, timeouts, or crashes. When a session expires mid-conversation, duh currently receives a 404 "Session not found" error and propagates it as a tool failure to the model. The model then typically retries the same call, getting the same error, wasting tokens in a failure loop.

The reference TS harness detects session expiry and transparently reconnects. Since MCP is becoming the standard for tool extensibility, robust session handling is essential.

## Decision

Add session lifecycle management to the MCP client:

### Session Expiry Detection

```python
MCP_SESSION_ERRORS = [
    (404, "session not found"),
    (404, "unknown session"),
    (410, "session expired"),
]

def is_session_expired(status: int, body: str) -> bool:
    body_lower = body.lower()
    return any(s == status and msg in body_lower for s, msg in MCP_SESSION_ERRORS)
```

### Auto-Reconnect Flow

1. **Detect** session expiry on any MCP tool call
2. **Reconnect** by re-initializing the MCP session (new `initialize` handshake)
3. **Retry** the failed tool call exactly once with the new session
4. **Track** consecutive reconnection failures per server

### Circuit Breaker

If a server fails 3 consecutive reconnections, mark it as `degraded` and stop attempting calls. Notify the user: `"MCP server '{name}' is unreachable after 3 reconnection attempts. Excluding its tools until manually restarted."` The server's tools are removed from the active schema until the user explicitly reconnects.

```python
class MCPSession:
    def __init__(self, server_config):
        self.config = server_config
        self.consecutive_failures = 0
        self.degraded = False

    async def call_tool(self, name: str, args: dict) -> dict:
        try:
            result = await self._raw_call(name, args)
            self.consecutive_failures = 0
            return result
        except MCPError as e:
            if is_session_expired(e.status, e.body):
                return await self._reconnect_and_retry(name, args)
            raise
```

### Reconnection Backoff

Reconnection attempts use a simple backoff: 0s, 1s, 2s. This prevents hammering a struggling server while keeping recovery fast for transient failures.

## Consequences

### Positive
- Transparent recovery from the most common MCP failure mode
- Circuit breaker prevents infinite reconnection loops
- Model never sees session errors — they're handled at the transport layer

### Negative
- Reconnection loses server-side session state (if any beyond the session ID)
- Tools from degraded servers disappear mid-conversation

### Risks
- Some MCP servers may have side effects on re-initialization — mitigated by only reconnecting on confirmed session errors, not on arbitrary failures

## Implementation Notes

- `duh/adapters/mcp_executor.py` — session-expiry detection + auto-reconnect + single
  retry per failed call. Degraded-state bookkeeping (removing tools from the active
  schema after 3 consecutive failures) is not implemented.

Related: ADR-010 (MCP integration), ADR-040 (multi-transport).
