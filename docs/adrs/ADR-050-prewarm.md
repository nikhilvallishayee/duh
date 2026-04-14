# ADR-050: Connection Pre-warming

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-11  

## Context

The first query in a REPL session is noticeably slower than subsequent ones. This is because the first HTTP request to the model provider must:
1. Resolve DNS for the API endpoint
2. Establish a TCP connection
3. Complete the TLS handshake
4. Negotiate HTTP/2 (for Anthropic's API)

These steps add 200-800ms of latency that subsequent requests avoid because the HTTP client caches the connection. The user notices this as a "cold start" delay on their first prompt.

The reference TS harness fires a lightweight model ping at startup to warm the connection pool before the user types anything.

## Decision

Add a `prewarm_connection()` coroutine in `duh/cli/prewarm.py` that:

1. **Sends a minimal prompt** — a single user message (`"hi"`) with a short system prompt (`"Reply with a single word."`) to the configured provider.
2. **Discards the response** — the pre-warm only cares about establishing the HTTP connection, not the model's reply.
3. **Runs in the background** — the REPL launches it via `asyncio.ensure_future()` immediately after provider setup, before the readline loop starts. The user is never blocked.
4. **Never raises** — all exceptions are caught and logged at DEBUG level. A failed pre-warm is invisible to the user.

### PrewarmResult

The coroutine returns a `PrewarmResult` dataclass for observability:

```python
@dataclass
class PrewarmResult:
    success: bool
    latency_ms: float = 0.0
    error: str = ""
```

This can be logged or displayed in `/health` output in future.

### Cost

The pre-warm prompt generates ~1-2 output tokens. At Anthropic's pricing, this costs approximately $0.000015 per session start — negligible.

## Consequences

### Positive
- First-turn latency reduced by 200-800ms (the TLS + HTTP/2 setup time)
- Background execution means zero impact on REPL startup time
- Silent failure — if the API is down, the user discovers it on their first real prompt, same as today

### Negative
- One extra API call per session — adds ~$0.000015 cost
- If the provider rate-limits during pre-warm, the first real query could also be rate-limited (unlikely — pre-warm finishes in <1s)

### Risks
- Pre-warming a model that is not the one the user ends up using (if they `/model` switch before their first prompt). This wastes one connection warm-up but causes no harm — the HTTP client maintains connections per-host, so switching between Anthropic models still benefits from the warm Anthropic connection.

## Implementation Notes

- `duh/cli/prewarm.py` — `prewarm_connection(call_model)` coroutine and
  `PrewarmResult` dataclass. Never raises — all exceptions are caught and logged at
  DEBUG.
- Launched in `duh/cli/repl.py` via `asyncio.ensure_future(prewarm_connection(...))`
  immediately after provider setup.
