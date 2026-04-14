# ADR-046: Model-Call Compaction

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-11
**Depends on**: ADR-035 (Advanced Compaction)

## Context

The existing `SimpleCompactor` (ADR-035) manages context windows via deterministic text truncation: it drops older messages and generates a mechanical summary by concatenating `[role] text` snippets. This preserves structure but loses semantic content — a 50-message debugging session gets summarized as a wall of truncated text snippets that the model struggles to use effectively.

The reference TS harness uses the model itself to generate intelligent summaries during compaction. The model can identify which decisions matter, which file paths are still relevant, and which tool results have been superseded — information that heuristic truncation cannot infer.

## Decision

Introduce `ModelCompactor` as a new adapter implementing the same `CompactFn` signature (`async (messages, token_limit) -> messages`):

### Strategy

1. **Check threshold** — if messages fit within the token limit, return unchanged (no model call).
2. **Partition** — separate system messages (always kept), a tail window of recent messages (always kept), and older messages to be summarized.
3. **Summarize via model** — send the older messages to the model with a summarization prompt. The prompt instructs the model to preserve key decisions, file paths, tool results, and active instructions.
4. **Reassemble** — return `system + [summary message] + tail_window`.
5. **Fallback** — if the model call fails (rate limit, timeout, error), silently fall back to `SimpleCompactor` behavior.

### Configuration

A `compactor_strategy` config option controls which compactor is used:
- `"simple"` — always use `SimpleCompactor` (current behavior, default)
- `"model"` — always use `ModelCompactor`
- `"auto"` — use `ModelCompactor` when a model is available, fall back to simple

### Integration

`ModelCompactor` takes the same `call_model` function that the engine uses. It reuses the provider's existing HTTP connection, so no additional setup is needed. The compaction prompt is minimal (a single user message) to avoid excessive cost.

## Consequences

### Positive
- Higher-quality summaries that preserve semantic context across long sessions
- Graceful fallback to `SimpleCompactor` means zero risk of data loss
- Same `CompactFn` signature — engine and REPL code unchanged
- Model connection is reused, so compaction is fast after the first turn

### Negative
- Each compaction triggers an additional model call — added cost (~$0.001-0.01 per compaction)
- Compaction latency increases from ~0ms (simple) to ~1-3s (model)
- The summarization prompt itself consumes tokens

### Risks
- Recursive prompt-too-long: the messages being summarized might themselves be too large for the model. Mitigated by truncating individual messages to 500 chars before sending to the summarizer, and capping total input to 10K chars.

## Implementation Notes

- `duh/adapters/model_compactor.py` — `ModelCompactor(call_model=...)` implements the
  `CompactFn` signature, partitions system + tail-window + older messages, calls the
  model for summarization, and falls back to `SimpleCompactor` on failure.
