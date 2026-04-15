# ADR-057: Message Flow Architecture — Engine/Loop Unification

**Status:** Proposed — 2026-04-15
**Date:** 2026-04-15

## Context

The engine (`engine.py`) and the agentic loop (`loop.py`) maintain separate message lists that diverge during execution:

- **Engine** has `self._messages` — the canonical session history, used for persistence and resume
- **Loop** has `current_messages` — a working copy that includes tool_result user messages

When the model calls a tool, the loop creates a tool_result user message (required by Anthropic's API for role alternation) and appends it to `current_messages`. But this message **never flows back** to `engine._messages`. The result:

1. `engine._messages` has consecutive assistant messages (no interleaving tool_result user messages)
2. Saving to disk preserves this broken sequence
3. On resume, `validate_alternation()` must merge/fix these, losing context
4. Each restart cycle progressively loses messages (48 → 33 → 14 observed)

This is the root cause of session context loss in D.U.H.

## Decision

Unify the message flow so ALL messages — user, assistant, tool_result — flow through a single canonical list owned by the engine.

### Architecture

```
User input
    ↓
Engine.run(prompt)
    ↓ appends user Message to self._messages
    ↓ passes self._messages to query()
    ↓
Loop.query(messages=self._messages)
    ↓ calls model with messages
    ↓ yields assistant event → Engine appends to self._messages
    ↓ executes tools
    ↓ yields tool_result event → Engine appends to self._messages  ← NEW
    ↓ loops (model sees updated self._messages directly)
    ↓
Engine auto-saves self._messages to disk
```

### Changes Required

1. **loop.py**: Stop maintaining a separate `current_messages`. Operate directly on the passed `messages` list (which IS `engine._messages`). When tool results are created, append them to the passed list AND yield them as events.

2. **engine.py**: On receiving `tool_result` events, append the tool_result user message to `self._messages`. Remove `validate_alternation()` — it's no longer needed because messages always alternate correctly.

3. **file_store.py**: Session save remains append-only. No special handling needed — the canonical list is always correct.

4. **Auto-compact**: Operates on `self._messages` directly. When it compacts, both the API call and persistence see the compacted version. This is correct behavior — compacted sessions should stay compacted.

5. **Session resume**: Load messages from disk into `engine._messages`. They're already correctly alternating (because they were saved correctly). No merge/fix step needed.

### Message Types in Canonical List

Every message in `self._messages` will be one of:
- `user` (role="user", content=str) — human input
- `assistant` (role="assistant", content=list) — model response with text/tool_use blocks
- `user` (role="user", content=list[tool_result]) — tool results (auto-generated)

### Backward Compatibility

- `query()` signature unchanged — it receives a message list
- `Engine.run()` signature unchanged — it returns an async generator of events
- Session files remain JSONL — format unchanged
- Existing sessions with broken alternation still load via `validate_alternation()` as a migration step (applied once on load, then saved correctly going forward)

## Consequences

### Positive
- Single source of truth for session state
- No progressive context loss on restart
- No need for `validate_alternation()` on every API call
- Auto-compact operates on the real message list
- Session resume preserves full context
- Tool results visible in session history (useful for debugging)

### Negative
- `loop.py` refactor — the most critical file in the codebase
- Must handle the mutation carefully (loop operates on a live reference)
- Tool_result messages increase session file size (they contain tool outputs)
- Need migration path for existing broken sessions

## Implementation Plan

See `/Users/nomind/Code/duh/docs/superpowers/plans/2026-04-15-message-flow.md`
