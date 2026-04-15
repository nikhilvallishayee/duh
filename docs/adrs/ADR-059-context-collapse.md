# ADR-059: Context Collapse — Granular Context Management

**Status:** Proposed — 2026-04-16
**Date:** 2026-04-16
**Related:** ADR-056 (adaptive compactor), ADR-058 (resume modes), ADR-035 (advanced compaction)

## Context

CC TS has evolved beyond auto-compact to a newer system called **Context Collapse** (`src/services/contextCollapse/`). Auto-compact summarizes the entire conversation when context hits ~93% — a blunt instrument that destroys granular context. Context Collapse instead:

1. **Commits** conversation segments at 90% usage — saving them to a structured log
2. **Blocks** new requests at 95% — forces the user to wait for compaction
3. **Preserves** recent tool results and file state with higher fidelity than summary

D.U.H. currently has:
- Microcompact (Tier 0): clears old tool results
- Auto-compact at 80% threshold via AdaptiveCompactor
- PTL retry (reactive): fires on prompt-too-long errors

Missing: a granular commit/restore mechanism that preserves structure instead of summarizing.

## Decision

Implement a two-phase context management system:

### Phase 1: Snip Compaction (ADR-060)

Remove completed API rounds (assistant+tool_result pairs) from the oldest end of the conversation while preserving the most recent N rounds intact. No model call needed. Structural preservation instead of summarization.

### Phase 2: Context Collapse

Full CC-parity context management:

**Commit phase (90% context):**
- Save the current conversation segment to a structured transcript
- Replace committed messages with a compact boundary marker + summary
- Keep the most recent assistant message and all pending tool results

**Block phase (95% context):**
- Refuse new queries until compaction completes
- Display a "compacting..." indicator

**Restore phase (on resume):**
- Load transcript segments on demand
- Reconstruct file state from committed segments

### Architecture

```
Token usage →
0%────────50%────────80%──────90%──────95%──100%
          │           │        │        │
          │           │        │        └─ BLOCK: refuse queries
          │           │        └─ COMMIT: save segment, compact
          │           └─ MICROCOMPACT: clear old tool results
          └─ Normal operation
```

### Implementation Order

1. **Snip compaction** — remove old API rounds (no model call, ~50% savings)
2. **Segment commits** — structured transcript with boundary markers
3. **Blocking gate** — refuse queries at 95% with compaction indicator
4. **Transcript restore** — load committed segments on demand

## Consequences

### Positive
- Preserves recent context with much higher fidelity than summary
- Snip compaction is free (no model call)
- Structural preservation: file state, tool results, thinking blocks intact
- Matches CC's evolution beyond auto-compact

### Negative
- More complex than single-threshold auto-compact
- Segment commits require transcript storage
- Blocking gate changes UX (user waits for compaction)
- Must coordinate with existing auto-compact (don't double-fire)
