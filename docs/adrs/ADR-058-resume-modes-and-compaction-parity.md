# ADR-058: Resume Modes and CC-Parity Compaction

**Status:** Proposed — 2026-04-15
**Date:** 2026-04-15
**Supersedes:** None
**Related:** ADR-057 (message flow unification), ADR-031 (progressive compaction), ADR-056 (adaptive compactor)

## Context

Claude Code TS offers a single resume mode: load ALL messages as-is, let auto-compact handle context limits on the next query. This works because:
1. CC tracks messages in JSONL transcript files with UUIDs and parent chains
2. CC's compaction is multi-tier (microcompact → session memory → full summary → reactive)
3. CC rebuilds post-compact state (file cache, plan mode, skills, deferred tools, MCP instructions)

D.U.H. currently has a simpler setup:
- `--continue` loads messages and force-compacts on resume (context loss!)
- Single-tier compaction (SimpleCompactor or AdaptiveCompactor)
- No post-compact state rebuild
- No compact boundary markers

With ADR-057 fixing message flow, sessions now save correctly. But we need:
1. **Two resume modes** — full context (for 1M models) and summarized (for smaller contexts)
2. **Multi-tier compaction** matching CC's sophistication
3. **Post-compact state rebuild** to restore working context after summarization

## Decision

### Resume Modes

#### Mode 1: Resume As-Is (default for `--continue`)
Load all messages into `engine._messages`. No compaction on resume. Auto-compact in `engine.run()` handles context limits when the next query fires.

This is what CC does, and it's the right default for 1M context models.

```
duh --continue                    # resume most recent, full context
duh --resume <session-id>         # resume specific session, full context
```

#### Mode 2: Resume With Summary (`--continue --summarize`)
Load messages, immediately summarize older ones, keep recent N turns. For users on smaller-context models or who want a clean start.

```
duh --continue --summarize        # resume with summarized history
duh --resume <id> --summarize     # resume specific with summary
```

### Multi-Tier Compaction (CC Parity)

| Tier | Name | Trigger | Model Call | Cost | D.U.H. Status |
|------|------|---------|-----------|------|---------------|
| 0 | Microcompact | Every turn | No | ~0 | Done (MicroCompactor) |
| 1 | Model Summary | Auto at 80% context | Yes (forked) | ~$0.01 | Upgrade needed |
| 2 | Reactive | Prompt-too-long error | Yes | ~$0.01 | Done (PTL retry) |

#### Tier 0: Microcompact (already implemented)
- `MicroCompactor` clears old tool_result content with `[tool result cleared for context management]`
- Time-based: clears if gap between messages > 5 minutes (cache is cold anyway)
- Count-based: keeps last N tool results
- **Enhancement**: Add CC's `COMPACTABLE_TOOLS` set — only clear Read, Bash, Grep, Glob, WebFetch, WebSearch, Edit, Write

#### Tier 1: Model-Based Summary Compaction (upgrade needed)
CC's `compactConversation()` pattern:
1. Send all messages to a forked model call with a summary prompt
2. Model returns a conversation summary
3. Build post-compact messages: `[boundary_marker, summary, recent_messages, attachments]`
4. Replace `engine._messages` with the compacted set
5. Rebuild state: file cache, plan mode, skills

**Compact Boundary Marker**: A system message inserted at the compaction point:
```python
Message(
    role="user",
    content="[Conversation compacted. Summary of prior context follows.]",
    metadata={"subtype": "compact_boundary", "pre_compact_count": N, "timestamp": "..."}
)
```

**Post-Compact Rebuild**: After summarizing, restore:
- Files recently read (re-attach as context)
- Active plan (if plan mode is active)
- Tool schemas that were discovered mid-session

**Auto-Compact Trigger**: `effective_context_window - buffer_tokens`
- CC uses 13K buffer. D.U.H. currently uses 20% threshold (80% of context).
- Keep 80% threshold but add circuit breaker: max 3 consecutive failures.

#### Tier 2: Reactive Compact (already implemented)
PTL retry in `engine.py` already handles prompt-too-long with progressive compaction (70% → 50% → 30%).

### Session Persistence for Resume

ADR-057 fixed the core issue (tool_result messages now saved). Additional changes:
1. `file_store.py load()` migrates broken sessions (consecutive assistant messages)
2. `file_store.py save()` preserves compact boundary markers
3. Session files are always correctly alternating, so resume loads cleanly

### SDK Mode Support

The SDK runner (`duh/cli/runner.py`) should support the same resume flags:
```
duh -p "continue working" --continue          # SDK resume as-is
duh -p "continue working" --continue --summarize  # SDK resume with summary
```

## Consequences

### Positive
- Full context resume for 1M models (no context loss)
- Optional summarized resume for cost-conscious users
- CC-parity compaction sophistication
- Post-compact state rebuild prevents "amnesia" after compaction
- Compact boundary markers make compaction visible in session history

### Negative
- Model-based summary adds ~2-5s and ~$0.01 per compaction
- Post-compact rebuild requires tracking file state (new engine state)
- Two resume modes means more code paths to test

## Implementation Plan

### Phase 1: Resume as-is (mostly done)
- [x] ADR-057: fix message flow so sessions save correctly
- [x] Remove TUI force-compact on resume
- [ ] Remove REPL force-compact on resume (if any)
- [ ] Verify SDK runner resume works without compaction

### Phase 2: Model-based summary compaction
- [ ] Implement `SummaryCompactor` — forked model call to summarize
- [ ] Add compact boundary marker message type
- [ ] Implement post-compact file state rebuild
- [ ] Add circuit breaker (max 3 consecutive failures)
- [ ] Wire into `AdaptiveCompactor` as Tier 1

### Phase 3: Resume with summary
- [ ] Add `--summarize` flag to CLI parser
- [ ] Implement summarize-on-resume in REPL runner
- [ ] Implement summarize-on-resume in TUI runner
- [ ] Implement summarize-on-resume in SDK runner

### Phase 4: CC-parity enhancements
- [ ] Time-based microcompact trigger (gap > 5 minutes)
- [ ] Post-compact plan restoration
- [ ] Post-compact skill/tool restoration
- [ ] Compact analytics (token savings, frequency)
