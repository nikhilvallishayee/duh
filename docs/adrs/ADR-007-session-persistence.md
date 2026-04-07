# ADR-007: Session Persistence

**Status**: Accepted  
**Date**: 2026-04-07

## Context

Conversations must survive process restarts. Production harnesses store sessions as JSONL files with sophisticated features: deduplication via UUID sets, parent-UUID chains for forking/resuming, tombstone messages, content replacement records, file history snapshots, and agent sidechain transcripts.

D.U.H. needs reliable persistence that covers the core use cases: save, load, resume, list, delete.

### Patterns from production harnesses

1. **JSONL format**: One JSON object per line. Each entry has a `type` field and a `uuid` for deduplication.

2. **Storage location**: Sessions scoped to the working directory.

3. **Core write function**: Deduplicates by UUID -- only appends messages not already in the file. Tracks parent-UUID chains for resume/fork support.

4. **Resume protocol**: `--continue` resumes the most recent session. `--resume <id>` resumes a specific session. Loading reads the JSONL, filters to transcript entries, and reconstructs the conversation.

5. **Atomic writes**: Write + append with the session file locked. Content replacement records reference the original message UUID.

## Decision

Use the `FileStore` adapter (already implemented) with the `SessionStore` port from ADR-003. The implementation uses JSONL format at `~/.duh/sessions/`.

### Storage Location

```
~/.duh/sessions/
  abc-123.jsonl      # one file per session
  def-456.jsonl
```

Unlike Claude Code which scopes sessions to the project directory, D.U.H. uses a single flat directory. Project-scoping can be added later via a session metadata field.

### JSONL Format

One JSON object per line, matching `dataclasses.asdict(Message)`:

```jsonl
{"role": "user", "content": "fix the bug", "id": "msg-001", "timestamp": "2026-04-07T12:00:00+00:00", "metadata": {}}
{"role": "assistant", "content": [{"type": "text", "text": "I'll look at..."}], "id": "msg-002", "timestamp": "2026-04-07T12:00:01+00:00", "metadata": {"model": "claude-sonnet-4-6", "stop_reason": "end_turn"}}
```

### Append Semantics

`save(session_id, messages)` receives the full message list. It counts existing lines and only writes the delta (messages beyond the existing count). This is simpler than UUID-based deduplication and correct for the single-writer case.

### Atomic Writes

Writes go through `tempfile.mkstemp()` → write → `os.replace()`. This ensures a crash mid-write never corrupts the existing file. The error handler cleans up the temp file on failure.

### Resume Protocol

| Flag | Behavior |
|------|----------|
| `--continue` | Resume the most recently modified session |
| `--resume <id>` | Resume a specific session by ID |

Both load the JSONL, reconstruct `Message` objects, and continue the conversation. The `list_sessions()` method provides metadata (created, modified, message count) for session selection.

### Session Metadata

Each session carries metadata embedded in the JSONL itself — the first message's timestamp serves as creation time, the file's mtime as last-modified. No separate metadata files.

### Future Enhancements

- **Project-scoped storage**: `~/.duh/sessions/<project-hash>/`
- **UUID-based deduplication**: deduplicate by message UUID instead of line count
- **Parent-UUID chains**: enable forking and resume-from-midpoint
- **SQLite backend**: `SessionStore` port allows a `SqliteStore` adapter

## Consequences

- Sessions survive process crashes (atomic writes)
- One file per session, one line per message — easy to inspect with `cat`/`jq`
- Loading is fast — stream-parse JSONL, no full-file-in-memory required for large sessions
- `FileStore` has 100% test coverage across all branches
- Resume works via `--continue` (most recent) or `--resume <id>` (specific)
