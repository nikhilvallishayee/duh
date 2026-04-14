# ADR-035: Advanced Context Compaction

**Status:** Accepted — partial (`strip_images`, `partial_compact`, and `restore_context`
all exist in `duh/adapters/simple_compactor.py`; however `strip_images` removes ALL
images (no `keep_recent=3`) and the staged pipeline — image strip → partial removal →
aggressive removal with early exit — is not implemented: `compact()` strips images
then applies a tail-window truncation)
**Date**: 2026-04-08

## Context

The current context compactor does a single operation: tail-window truncation (keep the last N messages). This is crude — it discards everything before the window regardless of importance, and it cannot handle specific scenarios:

- **Image-heavy conversations** balloon context with base64 data that compresses poorly
- **Partial compaction** is impossible — it's all-or-nothing
- **Post-compaction state** is incomplete — file contents, skill definitions, and system context loaded at session start are lost

The reference TS harness has a multi-strategy compactor that strips images first, supports partial compaction, and restores essential context after compacting.

## Decision

Replace the single-strategy compactor with a pipeline of compaction stages:

### Stage 1: Image Stripping

Before any message removal, strip base64 image content from messages older than the last 3 turns. Replace with a placeholder: `[image removed during compaction — re-attach if needed]`. This alone can recover 50-80% of context in image-heavy sessions.

```python
def strip_images(messages: list[Message], keep_recent: int = 3) -> list[Message]:
    for msg in messages[:-keep_recent]:
        msg.content = [
            block if block.type != "image"
            else TextBlock("[image removed during compaction]")
            for block in msg.content
        ]
    return messages
```

### Stage 2: Partial Compaction

Instead of removing all old messages, use a target ratio. If the target is 70%, remove the oldest messages until context is at 70% of the limit. Summarize removed messages into a single system message: `"[Compacted: {n} messages from earlier in conversation. Key topics: ...]"`

### Stage 3: Post-Compaction Restoration

After compaction, re-inject essential context that may have been removed:
- **Active file contents**: Files the model is currently editing (from recent tool results)
- **Skill definitions**: Any `/skill` content loaded during the session
- **System prompt**: Always preserved (never compacted)
- **Tool schemas**: Re-sent if they were part of compacted messages

### Compaction Order

The stages run in order, stopping as soon as context is under the target:

1. Strip images from old messages
2. If still over target: partial message removal (oldest first)
3. If still over target: aggressive removal (keep only last 5 turns)
4. After any compaction: restore essential context

## Consequences

### Positive
- Image stripping recovers massive context without losing conversation flow
- Partial compaction preserves more history than tail-window
- Post-compaction restoration prevents the model from losing track of active work
- Integrates with PTL retry (ADR-031) as the compaction backend

### Negative
- Multi-stage pipeline is more complex than simple truncation
- Summarization of removed messages adds a small amount of context itself

### Risks
- Image stripping may remove images the model needs to reference — mitigated by keeping the last 3 turns intact
- Restoration may re-add enough context to push back over the limit — mitigated by reserving 5% headroom for restored content

## Implementation Notes

- `duh/adapters/simple_compactor.py` — `SimpleCompactor.compact()`, `partial_compact()`,
  `strip_images()`, `restore_context()`, plus `POST_COMPACT_MAX_FILES` /
  `POST_COMPACT_TOKEN_BUDGET` constants.
- `duh/adapters/model_compactor.py` — `ModelCompactor` adapter that uses the model
  itself to summarize old turns, with fallback to `SimpleCompactor` (ADR-046).

Related: ADR-031 (PTL retry), ADR-046 (model-call compaction).
