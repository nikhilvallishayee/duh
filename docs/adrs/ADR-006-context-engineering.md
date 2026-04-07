# ADR-006: Context Engineering

**Status**: Accepted  
**Date**: 2026-04-07

## Context

LLMs have finite context windows. As conversations grow, they exceed the limit and the model either degrades or rejects the request. Production harnesses handle this with multi-layered compaction systems: auto-compact thresholds, micro-compaction of tool results, session memory compaction, reactive compact on prompt-too-long errors, time-based clearing, and context collapse.

D.U.H. needs context management as a first-class concern, but we start simple and grow only when real usage demands it.

### Patterns from production harnesses

1. **Token estimation**: Rough char-based estimation (chars / 4 for text, chars / 2 for JSON), with API-based exact counting as the source of truth. File-type-aware estimation.

2. **Auto-compact trigger**: Fires when estimated token count exceeds a threshold (effective context window minus a buffer). Circuit breakers stop after consecutive failures.

3. **Compaction strategy**: Summarize old conversation via a forked agent, produce a compact boundary marker, keep recent messages after the boundary.

4. **Micro-compaction**: Clear old tool result content blocks in-place, keeping the N most recent compactable results.

## Decision

Implement the `ContextManager` port (defined in ADR-003) with a `SimpleCompactor` adapter that covers the 80% case: rough token estimation and tail-window truncation.

### Token Estimation

```python
def estimate_tokens(self, messages: list[Any]) -> int:
    """chars / 4 rough estimate."""
    return len(str(messages)) // 4
```

The `str()` serialization captures all content including tool inputs/outputs.

### Compaction Strategy

```python
async def compact(self, messages: list[Any], token_limit: int) -> list[Any]:
    """Keep system message + most recent messages that fit within limit."""
```

Algorithm:
1. Separate system messages (role="system") from conversation messages
2. System messages are always kept (they anchor the agent's identity)
3. Walk backward from the most recent message, accumulating estimated tokens
4. Stop when adding the next message would exceed the token limit
5. Return system messages + the tail window of conversation messages

This is simpler than a "summarize via forked agent" approach but achieves the same goal: keep recent context, drop old context. Summarization preserves more information but requires calling the model recursively. We start with truncation and add summarization when we need it.

### Configurable Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `default_limit` | 100,000 | Effective context window minus buffer |
| `bytes_per_token` | 4 | Rough chars-per-token ratio |
| `min_keep` | 2 | Always keep at least 2 recent messages |

### Future Enhancements

When the simple compactor proves insufficient:
- **Summarization compactor**: call the model to summarize old turns
- **File-type-aware estimation**: 2 bytes/token for JSON
- **Auto-compact trigger**: fire compaction before model call when threshold exceeded
- **Micro-compaction**: clear old tool results in-place
- **API-based counting**: use `countTokens` endpoint for exact counts

## Consequences

- Context management works out of the box with zero configuration
- Token estimation is fast (no API calls, no dependencies)
- The `ContextManager` port allows swapping in a smarter compactor later
- System messages are never lost during compaction
- The most recent context is always preserved (recency bias matches human conversation)
