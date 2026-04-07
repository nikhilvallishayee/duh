# ADR-006: Context Engineering

**Status**: Accepted  
**Date**: 2026-04-07

## Context

LLMs have finite context windows. As conversations grow, they exceed the limit and the model either degrades or rejects the request. Claude Code handles this with a multi-layered compaction system spanning 16 files (`services/compact/`): auto-compact thresholds, micro-compaction of tool results, session memory compaction, reactive compact on prompt-too-long errors, time-based clearing, and context collapse. The total compaction subsystem is ~4,000 LOC.

D.U.H. needs context management as a first-class concern, but we start simple and grow only when real usage demands it.

### Legacy Behavior (Claude Code)

Key patterns from `tengu-legacy/src/services/`:

1. **Token estimation** (`tokenEstimation.ts`):
   - `roughTokenCountEstimation(content, bytesPerToken=4)` — `Math.round(content.length / bytesPerToken)` (chars / 4 for text, chars / 2 for JSON)
   - API-based exact counting via `countTokens` endpoint as the source of truth
   - File-type-aware estimation (JSON gets 2 bytes/token, everything else 4)

2. **Auto-compact trigger** (`autoCompact.ts`):
   - `getEffectiveContextWindowSize(model)` = context window - reserved output tokens (20K)
   - `getAutoCompactThreshold(model)` = effective window - 13K buffer
   - Fires when `tokenCountWithEstimation(messages) >= threshold`
   - Circuit breaker: stops after 3 consecutive failures
   - Disabled for sub-agents (compact, session_memory, marble_origami)

3. **Compaction strategy** (`compact.ts`):
   - Sends entire conversation to a forked agent with a "summarize" prompt
   - Produces a compact boundary marker + summary as the new conversation start
   - Keeps recent messages after the boundary
   - Strips images, reinjected attachments, and redacted thinking before compaction
   - Retries with head-truncation if compact itself hits prompt-too-long

4. **Micro-compaction** (`microCompact.ts`):
   - Clears old tool result content blocks (Read, Bash, Grep outputs) in-place
   - Keeps the N most recent compactable results
   - Time-based variant clears when gap since last loop iteration exceeds threshold

## Decision

Implement the `ContextManager` port (defined in ADR-003) with a `SimpleCompactor` adapter that covers the 80% case: rough token estimation and tail-window truncation.

### Token Estimation

```python
def estimate_tokens(self, messages: list[Any]) -> int:
    """chars / 4 rough estimate — matches Claude Code's roughTokenCountEstimation."""
    return len(str(messages)) // 4
```

This mirrors `roughTokenCountEstimation(content, bytesPerToken=4)` from legacy. The `str()` serialization captures all content including tool inputs/outputs.

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

This is simpler than Claude Code's "summarize via forked agent" approach but achieves the same goal: keep recent context, drop old context. The legacy approach is better (summaries preserve information), but requires calling the model recursively. We start with truncation and add summarization when we need it.

### Configurable Parameters

| Parameter | Default | Legacy equivalent |
|-----------|---------|-------------------|
| `default_limit` | 100,000 | `getEffectiveContextWindowSize()` - buffer |
| `bytes_per_token` | 4 | `roughTokenCountEstimation(content, 4)` |
| `min_keep` | 2 | No equivalent (always keep at least 2 recent messages) |

### Future Enhancements

When the simple compactor proves insufficient:
- **Summarization compactor**: call the model to summarize old turns (matches legacy)
- **File-type-aware estimation**: 2 bytes/token for JSON (matches `bytesPerTokenForFileType`)
- **Auto-compact trigger**: fire compaction before model call when threshold exceeded
- **Micro-compaction**: clear old tool results in-place (matches `microCompact.ts`)
- **API-based counting**: use `countTokens` endpoint for exact counts

## Consequences

- Context management works out of the box with zero configuration
- Token estimation is fast (no API calls, no dependencies)
- The `ContextManager` port allows swapping in a smarter compactor later
- System messages are never lost during compaction
- The most recent context is always preserved (recency bias matches human conversation)
