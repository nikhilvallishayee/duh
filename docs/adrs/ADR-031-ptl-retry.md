# ADR-031: Prompt-Too-Long Retry

**Status**: Proposed  
**Date**: 2026-04-08

## Context

When conversation context exceeds the provider's token limit, the API returns a "prompt too long" (PTL) error. Currently, this crashes the session — the user loses their entire conversation with no recovery option.

The reference TS harness handles this by auto-compacting context and retrying. This is one of the most common runtime errors in long coding sessions (large file reads, many tool calls, accumulated history). Users should never see a PTL crash.

## Decision

Add PTL detection and auto-recovery to the engine loop:

### Detection

Match PTL errors across providers:

```python
PTL_PATTERNS = [
    "prompt is too long",
    "maximum context length",
    "context_length_exceeded",
    "max_tokens exceeded",
    "request too large",
]

def is_ptl_error(error: Exception) -> bool:
    msg = str(error).lower()
    return any(p in msg for p in PTL_PATTERNS)
```

### Recovery Strategy

1. **Catch** the PTL error in the engine's `_call_provider()` method
2. **Compact** the conversation to 70% of the provider's context limit using the context compactor
3. **Retry** the same user turn with the compacted history
4. **Limit** retries to 3 attempts, reducing target to 50% then 30% on subsequent failures
5. **Fail gracefully** after 3 retries with a clear message: `"Context too large even after compaction. Start a new session or manually remove large messages."`

### Compaction Target

```python
COMPACTION_TARGETS = [0.70, 0.50, 0.30]  # Progressive reduction

for attempt, target in enumerate(COMPACTION_TARGETS):
    try:
        compacted = compactor.compact(messages, target_ratio=target)
        return await provider.call(compacted)
    except PTLError:
        continue
raise SessionError("Context too large after 3 compaction attempts")
```

### User Notification

On each compaction, display a status message: `"Context compacted to {pct}% ({n} messages removed). Retrying..."` This is informational, not interactive — the retry happens automatically.

## Consequences

### Positive
- Long sessions no longer crash on context overflow
- Matches behavior users expect from production harnesses
- Progressive compaction finds the minimal reduction needed

### Negative
- Auto-compaction may remove context the user considers important
- Three retries add latency on PTL errors (seconds, not minutes)

### Risks
- Compaction might remove tool results the model needs for its next response — mitigated by ADR-035's smart compaction preserving recent tool outputs
- Provider error messages may change format — mitigated by loose substring matching
