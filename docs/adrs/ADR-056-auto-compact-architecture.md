# ADR-056: Auto-Compact Architecture

**Status:** Accepted -- implemented 2026-04-15
**Date:** 2026-04-14
**Prerequisite:** [ADR-035](ADR-035-compaction-pipeline.md), [ADR-046](ADR-046-model-call-compaction.md)

## Context

Sessions hit `400 Bad Request` when context grows beyond the model's window.
D.U.H.'s current compaction fires at 80% threshold via `SimpleCompactor` (a
4-stage pipeline: dedup, image strip, tail window, summarize) with PTL retry
at 70/50/30%. Two problems remain:

1. **Not enough compression.** SimpleCompactor's tail-window approach may not
   shed enough tokens before hitting the model again, especially when large
   tool results (Read, Bash) dominate the context.
2. **Missing microcompact tier.** There is no cheap pre-pass that clears
   stale tool results *before* heavier compaction runs. Leading agent CLIs clear
   old Read/Bash/Grep outputs incrementally (time-based, keeping only the
   last N results), which prevents the need for expensive compaction in most
   sessions.
3. **No circuit breaker.** If compaction itself fails repeatedly (e.g., the
   summarization model is overloaded), the engine retries forever.
4. **No post-compact restoration.** After aggressive compaction drops
   messages, recently accessed files and active context are lost.

Research into leading agent CLI architectures reveals a multi-tier approach:

- **Microcompact** — incremental tool-result clearing (<1ms, no model call)
- **Session memory compaction** — extract key decisions into summary
- **Full compaction** — aggressive tail-window + model summarization
- **Circuit breaker** — max 3 consecutive compact failures, then stop
- **Post-compact restoration** — re-inject recently accessed files (max 5,
  50K token budget)
- **Reserved output buffer** — subtract 20K from context limit for output

## Decision

Implement a 4-tier pluggable compaction system with an `AdaptiveCompactor`
orchestrator that runs tiers in order until context fits within budget.

### Tier 0: Microcompact (no model call, <1ms)

Clear old tool results from the conversation. Tool-bearing messages (Read,
Bash, Grep, Glob, WebFetch, WebSearch) older than the last N tool results
(default 3) have their content replaced with a short placeholder:
`"[tool result cleared for context management]"`. Time-based: if the gap
since the last assistant message exceeds 5 minutes, also clear older results.

**Cost:** Zero. No model call. String replacement only.

### Tier 1: Dedup + Image Strip (no model call, deterministic)

- Remove duplicate file reads (same file read multiple times -> keep latest).
- Remove redundant tool results (same tool + same input -> keep latest).
- Strip image blocks from messages older than `keep_recent` (default 3).

This is equivalent to SimpleCompactor's Stage 0 + Stage 1.

**Cost:** Zero. Deterministic string/block operations.

### Tier 2: Tail-Window + Model Summary (one model call)

- Tail-window: keep N most recent messages that fit within budget.
- Summarize discarded messages using model (if available) or mechanical
  concatenation (SimpleCompactor fallback).
- Post-restoration: re-inject recently accessed files (max 5, 50K token
  budget) so the model retains awareness of the working set.

**Cost:** One model call for summarization (optional; mechanical fallback).

### Tier 3: Aggressive Compression (reserved for future)

- Background agent compaction (model rewrites entire context).
- Multi-pass summarization with importance ranking.
- Not implemented in this ADR; reserved as an extension point.

### Pluggable Interface

```python
class CompactionStrategy(Protocol):
    async def compact(
        self,
        messages: list[Any],
        token_limit: int = 0,
    ) -> list[Any]: ...

    def estimate_tokens(self, messages: list[Any]) -> int: ...
```

All existing compactors (SimpleCompactor, ModelCompactor) already satisfy
this protocol. New tier implementations also implement it.

### AdaptiveCompactor Orchestrator

```python
class AdaptiveCompactor:
    def __init__(self, strategies, token_estimator, output_buffer=20_000):
        ...

    async def compact(self, messages, token_limit=0) -> CompactionResult:
        # Reserved output buffer
        effective_limit = token_limit - self.output_buffer

        for strategy in self.strategies:
            result = await strategy.compact(messages, effective_limit)
            if self.estimate_tokens(result) <= effective_limit:
                return result  # early exit
            messages = result  # feed into next tier

        return messages  # best effort
```

Circuit breaker: after 3 consecutive compaction failures (exceptions), the
orchestrator stops retrying and returns the best result so far.

Hooks: fires `PRE_COMPACT` before the first tier and `POST_COMPACT` after
the last tier completes.

### Engine Integration

`engine.py` uses `deps.compact` as the compaction callable. The
`AdaptiveCompactor` is wired as the default when no explicit `compact`
is provided in `Deps`. Backward compatibility: if `deps.compact` is set
to a bare function (old interface), it is used directly.

## Consequences

- **Sessions survive longer.** Microcompact prevents the need for expensive
  compaction in 80%+ of sessions by clearing stale tool results early.
- **Graceful degradation.** If summarization fails, the circuit breaker
  prevents infinite retry loops.
- **Pluggable.** Users can replace any tier or add custom strategies.
- **Backward compatible.** Existing SimpleCompactor/ModelCompactor continue
  to work unchanged as pluggable strategies.
- **Output buffer.** The 20K reserved buffer prevents the model's response
  from pushing the context over the limit.
