# ADR-061: Prompt Cache Optimization

**Status:** Proposed — 2026-04-16
**Date:** 2026-04-16
**Related:** ADR-059 (context collapse), ADR-060 (snip), ADR-006 (context engineering)

## Context

Anthropic's API supports prompt caching: the first request pays `cache_creation` tokens, subsequent requests with the same prefix pay only `cache_read` tokens at 90% discount. Leading agent CLIs implement sophisticated cache optimization:

1. **Cache break detection** — detects when cache read rate drops unexpectedly and flags potential breaks
2. **Cached microcompact** — uses `cache_edits` API to delete old tool results from the cached prefix without invalidating it
3. **Prompt cache sharing** — forked agents (compact, session memory) reuse the main conversation's cache prefix

D.U.H. currently has no prompt cache awareness. Every API call creates a new cache. This wastes ~80% of potential savings on multi-turn conversations.

## Decision

### Phase 1: System Prompt Caching (Quick Win)

The system prompt is identical across all turns. Mark it with `cache_control` to enable automatic caching:

```python
# In the Anthropic adapter
system=[{
    "type": "text",
    "text": system_prompt,
    "cache_control": {"type": "ephemeral"}
}]
```

**Expected savings**: ~90% reduction on system prompt tokens (typically 2K-10K per turn).

### Phase 2: Message Prefix Caching

Mark the boundary between old messages (stable prefix) and new messages:

```python
# Last message before the new user input gets cache_control
messages[-2]["content"][-1]["cache_control"] = {"type": "ephemeral"}
```

This tells the API: "everything up to this point is the same as last time."

**Expected savings**: ~90% on all prior conversation context.

### Phase 3: Cache Break Detection

Track cache read rates across turns. If cache reads drop unexpectedly:
- Log a warning
- Check if compaction/snip invalidated the prefix
- Reset the cache baseline after compaction

### Phase 4: Cache-Aware Microcompact

Instead of clearing tool result content (which changes the prefix and breaks cache), use the `cache_edits` API to remove tool results from the server-side cache without changing the local messages. This is the cached microcompact approach used by leading agent CLIs.

## Implementation Plan

- [ ] Phase 1: Add `cache_control` to system prompt in Anthropic adapter
- [ ] Phase 2: Add `cache_control` to message prefix boundary
- [ ] Phase 3: Track cache hit rates, detect breaks
- [ ] Phase 4: Cache-edits API for microcompact

## Consequences

### Positive
- ~80% cost reduction on multi-turn conversations
- ~90% reduction on system prompt repeated each turn
- Cache-aware microcompact preserves cache while freeing context
- Direct cost savings visible in token usage

### Negative
- Cache has 5-minute TTL — breaks on idle sessions
- Compaction invalidates cache (prefix changes)
- Cache-edits API is newer, may not be available to all users
- Must coordinate cache markers with compaction boundaries
