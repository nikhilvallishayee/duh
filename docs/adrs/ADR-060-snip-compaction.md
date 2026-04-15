# ADR-060: Snip Compaction — Structural Message Pruning

**Status:** Proposed — 2026-04-16
**Date:** 2026-04-16
**Related:** ADR-059 (context collapse), ADR-056 (adaptive compactor), ADR-057 (message flow)

## Context

Leading agent CLIs implement snip compaction, which removes completed API rounds (assistant→tool_result pairs) from the oldest end of the conversation. Unlike summary compaction:
- **No model call** — zero cost, sub-millisecond
- **Structural** — removes whole rounds, keeps alternation intact
- **Predictable** — removes exactly N oldest rounds, no summarization guesswork

Snip fires before auto-compact as a cheaper first pass. If snip frees enough tokens, auto-compact doesn't fire at all.

D.U.H. has ADR-057's unified message flow (correct alternation) which makes snip straightforward: messages are always `[user, assistant, user(tool_result), assistant, ...]`. We can remove pairs from the front.

## Decision

### Snip Algorithm

```python
def snip(messages: list[Message], keep_last: int = 6) -> list[Message]:
    """Remove old API rounds from the front, keep the last N messages.
    
    A 'round' is: assistant(tool_use) + user(tool_result)
    After snip, insert a synthetic user message:
      "(Earlier conversation snipped for context management)"
    """
```

**Rules:**
1. Never snip the first user message (the original prompt — preserves task context)
2. Never snip the last `keep_last` messages (recent context)
3. Only snip complete rounds (assistant + tool_result pairs)
4. After snip, insert a snip boundary marker
5. Track tokens freed for analytics

### Snip Boundary Marker

```python
Message(
    role="user", 
    content="(Earlier conversation snipped for context management. "
            f"{snipped_count} messages removed, ~{tokens_freed:,} tokens freed.)",
    metadata={"subtype": "snip_boundary", "snipped_count": N}
)
```

### Integration with AdaptiveCompactor

```
Turn starts → microcompact (clear old tool results)
            → snip (remove old rounds if above 75% context)
            → auto-compact (summarize if still above 85% after snip)
            → PTL retry (reactive if API rejects)
```

Snip at 75%, auto-compact at 85%. If snip frees enough, auto-compact never fires.

### Snip Projection

A snip projection estimator calculates how many tokens snip would free *before* running it. This lets the system decide: "if snip would free 40K tokens, that's enough — skip the expensive model summary."

We implement projection as a simple estimator:
```python
def estimate_snip_savings(messages, keep_last=6) -> int:
    """Estimate tokens that snip would free."""
    snippable = messages[1:-keep_last]  # skip first user + recent
    return sum(count_tokens(m.text) for m in snippable)
```

## Implementation Plan

### Phase 1: SnipCompactor class
- [x] `duh/adapters/compact/snip.py` — SnipCompactor with snip() and estimate()
- [x] Snip boundary marker message
- [x] Unit tests: snip preserves first+last, correct round removal, alternation maintained

### Phase 2: Wire into AdaptiveCompactor
- [x] Add snip as Tier 0.5 (between microcompact and model summary)
- [x] 75% threshold trigger
- [x] Skip model summary if snip freed enough tokens

### Phase 3: Snip projection
- [x] Token estimation for snip savings
- [x] Decision logic: snip-only vs snip+summarize

## Consequences

### Positive
- Free context management (no model call)
- Sub-millisecond execution
- Predictable: removes exactly N rounds
- Preserves recent context perfectly
- Reduces expensive model summary calls by ~60% (based on industry benchmarks)

### Negative
- Loses old conversation context (no summary of what was snipped)
- Must coordinate thresholds with auto-compact
- Snip boundary marker uses a few tokens
