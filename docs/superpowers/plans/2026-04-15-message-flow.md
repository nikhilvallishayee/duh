# Message Flow Unification — Implementation Plan

> ADR-057. Fix progressive session context loss.

**Goal:** All messages (user, assistant, tool_result) flow through one canonical list. No context loss on restart.

---

## Phase 1: Loop yields tool_result events (3 tasks)

### Task 1.1: Add tool_result event to loop.py

**Files:** `duh/kernel/loop.py`

- [ ] After tool execution, yield `{"type": "tool_result_message", "message": Message(...)}` containing the tool_result user message that loop.py currently builds internally
- [ ] Keep building the message the same way (role="user", content=list of tool_result blocks)
- [ ] The loop still appends to its own `current_messages` for the next model turn
- [ ] Write test: verify tool_result_message event is yielded after tool execution

### Task 1.2: Engine captures tool_result events

**Files:** `duh/kernel/engine.py`

- [ ] In the event handler loop, on `tool_result_message` event, append the message to `self._messages`
- [ ] This ensures the canonical list has the interleaving messages
- [ ] Write test: verify engine._messages contains tool_result user messages after tool use

### Task 1.3: Remove validate_alternation from hot path

**Files:** `duh/kernel/engine.py`

- [ ] Remove the `validate_alternation(list(self._messages))` call before query
- [ ] Pass `self._messages` directly to query (no copy needed — messages alternate correctly now)
- [ ] Keep `validate_alternation` in messages.py for migration use
- [ ] Write test: verify messages alternate correctly without explicit validation

---

## Phase 2: Session persistence correctness (3 tasks)

### Task 2.1: Verify session save includes tool_results

**Files:** `tests/`

- [ ] Write integration test: run a multi-tool session, save, verify JSONL contains user/assistant/user(tool_result)/assistant sequence
- [ ] Verify no consecutive same-role messages in saved session

### Task 2.2: Migration for existing broken sessions

**Files:** `duh/adapters/file_store.py`

- [ ] On `load()`, if consecutive same-role messages detected, apply `validate_alternation()` once as migration
- [ ] Log a warning: "Migrating session {id}: fixed message alternation"
- [ ] Next save will persist the corrected version

### Task 2.3: Session resume preserves full context

**Files:** `duh/ui/app.py`, `duh/cli/repl.py`

- [ ] Remove post-resume force-compact (no longer needed — sessions are correctly sized)
- [ ] Verify `--continue` loads and displays all messages
- [ ] Write test: save session → resume → verify message count matches

---

## Phase 3: Auto-compact on canonical list (3 tasks)

### Task 3.1: Compact operates on self._messages directly

**Files:** `duh/kernel/engine.py`

- [ ] Auto-compact modifies `self._messages` in place (already does this)
- [ ] Verify compact preserves tool_result messages in the tail window
- [ ] Compacted sessions save correctly (smaller but complete alternation)

### Task 3.2: Microcompact clears old tool_result content

**Files:** `duh/adapters/compact/microcompact.py`

- [ ] Tool_result messages older than keep_last are eligible for content clearing
- [ ] Replace content with "[tool result cleared for context management]"
- [ ] Keep the message structure (role="user") so alternation is preserved

### Task 3.3: End-to-end context retention test

**Files:** `tests/integration/`

- [ ] Run 20-turn session with tool calls → save → resume → run 10 more turns
- [ ] Verify no context loss
- [ ] Verify model can reference earlier conversation
- [ ] Verify auto-compact fires correctly when approaching limit

---

## Phase 4: TUI integration (2 tasks)

### Task 4.1: TUI displays tool_result messages on resume

**Files:** `duh/ui/app.py`

- [ ] Show tool_result messages in restored session (abbreviated)
- [ ] Format: "[Tool: Read] output: first 100 chars..."

### Task 4.2: /compact command works with new flow

**Files:** `duh/ui/app.py`

- [ ] Verify /compact modifies engine._messages correctly
- [ ] Display before/after message count
- [ ] Session auto-saves after compact

---

## Acceptance Criteria

1. `engine._messages` always has correct user/assistant alternation (including tool_result user messages)
2. Session save to JSONL preserves all messages including tool_results
3. `--continue` loads full session with no context loss
4. No `validate_alternation()` needed on API call hot path
5. Auto-compact works correctly on the unified message list
6. 20-turn → save → resume → 10-turn test passes with full context retention

---

## Risk

- **loop.py is the most critical file** — any bug here breaks everything
- **Tool_result messages increase session size** — mitigated by microcompact clearing old ones
- **Existing sessions need migration** — one-time fix on first load

## Sequencing

Phase 1 first (core fix). Phase 2 immediately after (persistence). Phase 3 can follow. Phase 4 last.
Phases 1+2 should be one PR. Phases 3+4 can be separate.
