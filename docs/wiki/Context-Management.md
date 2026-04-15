# Context Management

## The Problem

Large language models have a finite context window -- a hard ceiling on the total tokens (system prompt + conversation history + tool schemas + new user input + model output) that can be sent in a single API request. As sessions grow longer, tool results accumulate, and file contents are read into conversation, the token count steadily climbs toward the limit.

If the context overflows, one of two things happens:

1. **The API rejects the request** with a "prompt too long" error and the session stalls.
2. **Older context is silently dropped** by the provider, and the model loses track of earlier work.

Neither outcome is acceptable for a coding agent that may run dozens of agentic turns in a single task. D.U.H. solves this with a **4-tier compaction pipeline** that progressively reclaims context before the limit is hit, plus a hard gate that blocks new queries when the window is critically full.

---

## 4-Tier Compaction Pipeline

Compaction runs as an ordered cascade. Each tier is cheaper and faster than the next. The pipeline stops as soon as context fits within budget.

```
Tier 0: Microcompact  (clear old tool results)     -- free, <1 ms
Tier 0.5: Snip        (remove old API rounds)       -- free, <1 ms
Tier 1: Dedup         (deduplicate + strip images)   -- free, <1 ms
Tier 2: Summarize     (model-backed summary)         -- 1 API call, ~2-5 s
```

The `AdaptiveCompactor` orchestrates this cascade. It subtracts an output buffer (default 20,000 tokens) from the context limit before comparing, so there is always room for the model's response. A circuit breaker (3 consecutive failures) prevents infinite retry loops.

**Source:** `duh/adapters/compact/adaptive.py`

---

## Thresholds

Each tier has a **threshold gate** -- it only fires when context usage reaches a certain percentage of the effective limit. This avoids running expensive tiers when cheaper ones have already solved the problem.

| Usage Level | What Fires | Cost |
|-------------|-----------|------|
| 75% | Snip compaction | Free (structural pruning) |
| 80% | Auto-compact triggered by Engine | Free/cheap tiers first |
| 85% | Summary compaction (model call) | 1 API call |
| 95% | **Context gate blocks** | Session halted |

The 80% threshold is checked in the Engine before every query. If the estimated token count exceeds 80% of the model's context limit, the full `AdaptiveCompactor` pipeline runs. The 75% and 85% thresholds are enforced inside `AdaptiveCompactor` itself via the `_THRESHOLD_GATES` dict.

**Source:** `duh/kernel/engine.py` (auto-compact section), `duh/adapters/compact/adaptive.py`

---

## Tier 0: Microcompact

**What it does:** Replaces the content of old tool-result blocks with the placeholder `[tool result cleared for context management]`. No model call, sub-millisecond.

**Which tools are clearable:**

```
Read, read, ReadFile, read_file, cat,
Bash, bash,
Grep, grep, rg,
Glob, glob,
WebFetch, web_fetch, WebSearch, web_search
```

These are the `_CLEARABLE_TOOLS` set -- tools whose output is bulky and transient (file contents, command output, search results).

**How it works:**

1. Scan backward through messages to find tool-result blocks from clearable tools.
2. Keep the last N results intact (default `keep_last=3`).
3. Apply a **time-gap rule**: if a tool result is separated from the most recent assistant message by more than 5 minutes (`_TIME_GAP_SECONDS = 300`), it is cleared even if it falls within the `keep_last` window.
4. Replace all other clearable tool-result blocks with the placeholder text.

The time-gap rule catches sessions where the user walked away -- the old tool results are likely stale regardless of recency.

**Source:** `duh/adapters/compact/microcompact.py`

---

## Tier 0.5: Snip Compaction

**What it does:** Structurally prunes complete API rounds (assistant message + tool-result user message pairs) from the oldest end of the conversation. Zero cost, sub-millisecond.

**Rules:**

1. **Never snip the first user message.** This is the original prompt / task context and must be preserved.
2. **Never snip the last N messages** (default `keep_last=6`). These are the recent working context.
3. **Only snip complete rounds** -- an assistant message immediately followed by a user message. Incomplete rounds are left intact to preserve role alternation.
4. **Insert a snip boundary marker** appended to the first user message, recording how many messages were removed and approximate tokens freed.

**Boundary marker format:**

```
(Earlier conversation snipped for context management. 14 messages removed, ~8,200 tokens freed.)
```

The marker is embedded in the first user message rather than inserted as a standalone message, which avoids breaking the required user/assistant alternation.

**Projection estimator:** The `estimate_savings()` method lets the system predict how many tokens snip would free *without actually snipping*. This supports the decision of whether snip alone is sufficient or if the more expensive summary tier is also needed.

**Source:** `duh/adapters/compact/snip.py`

---

## Tier 1: Dedup

**What it does:** Removes duplicate file reads (same file read multiple times, keeps the latest) and redundant tool results (same tool + same input, keeps the latest). Also strips image blocks from messages older than the most recent N (default 3).

This tier delegates to the `SimpleCompactor` helpers (`_deduplicate_messages`, `strip_images`).

**Source:** `duh/adapters/compact/dedup.py`

---

## Tier 2: Summary Compaction

**What it does:** Tail-window compaction with model-backed summarization. This is the most expensive tier -- it makes one API call to summarize the dropped messages.

**How it works:**

1. Partition messages into system messages and conversation messages.
2. Walk backward through conversation messages, adding them to the "kept" set until the token budget is exceeded (minimum 2 messages always kept).
3. Summarize the dropped messages using the model (or a mechanical fallback).
4. Insert a **compact boundary marker** at the compaction point.
5. Run **post-compact file restoration** to re-inject recently accessed files.

### Structured Handoff Prompt

The summarization prompt is not free-form "summarize this." It requests a structured handoff with 5 sections:

| Section | Contents |
|---------|----------|
| **Progress** | What has been accomplished so far |
| **Decisions** | Key architectural or design choices and why |
| **Constraints** | Requirements, limitations, user preferences |
| **Pending** | Remaining work in priority order |
| **Context** | Critical file paths, variable names, error messages |

The prompt instructs the model to be concise, use bullet points, and preserve specifics (exact file paths, function names, error messages) rather than generalizing.

If the model call fails for any reason, a **mechanical fallback** concatenates message texts with role labels, truncated to 2,000 characters.

**Source:** `duh/adapters/compact/handoff.py` (prompt), `duh/adapters/compact/summarize.py` (implementation)

### Compact Boundary Marker

After summarization, a boundary marker message is inserted with metadata:

```python
Message(
    role="user",
    content="[Conversation compacted. Summary of prior context follows.]",
    metadata={
        "subtype": "compact_boundary",
        "pre_compact_count": <original message count>,
        "tokens_freed": <estimated tokens freed>,
    },
)
```

This marker lets session restore and the UI show exactly where compaction occurred.

### Post-Compact File Rebuild

After compaction removes old messages, the model may lose awareness of files it recently read. The post-compact restoration system re-injects recently accessed files:

1. Walk the file tracker's operation list in reverse to find up to 5 unique file paths (`DEFAULT_MAX_FILES = 5`).
2. For each file that still exists on disk, read its contents (truncated to 5,000 tokens / 20,000 chars per file).
3. Append system messages carrying the file contents, tagged with `subtype: post_compact_file_restore`.

Additionally, the `SummarizeCompactor` itself can inject a list of recent file paths and active skill context as a post-restoration system message, capped at 50,000 tokens.

**Source:** `duh/kernel/post_compact.py`, `duh/adapters/compact/summarize.py`

---

## Context Gate

The context gate is the hard stop. At **95% context usage**, the gate blocks new queries entirely and forces the user to run `/compact` before proceeding.

```python
class ContextGate:
    BLOCK_THRESHOLD = 0.95

    def check(self, token_estimate: int) -> tuple[bool, str]:
        ratio = token_estimate / self._context_limit
        if ratio >= self.BLOCK_THRESHOLD:
            return False, f"Context {ratio:.0%} full (...). Run /compact to free space."
        return True, ""
```

The gate runs **after** auto-compaction in the Engine, so compaction always has a chance to free space first. If context is still over 95% after all compaction tiers have run, the Engine yields a `context_blocked` event and returns without calling the model.

**Architecture of the three-layer defense:**

```
75%  -- snip fires (free structural pruning)
85%  -- auto-compact fires (model summary if needed)
95%  -- BLOCK -- refuse new queries, force /compact
```

**Source:** `duh/kernel/context_gate.py`

---

## Reactive Compaction: PTL Retry

Even with proactive compaction, the API may still reject a request with a "prompt too long" (PTL) error -- the token estimator is heuristic-based and can undercount. When this happens, the Engine performs **reactive compaction** with progressive targets:

| Retry | Target | Explanation |
|-------|--------|-------------|
| 1 | 70% of context limit | Light compaction |
| 2 | 50% of context limit | Moderate compaction |
| 3 | 30% of context limit | Aggressive compaction |

After compacting to the target, the Engine retries the query. Maximum 3 PTL retries. Each retry also triggers post-compact file rebuild and records compact analytics.

PTL triggers are detected by matching error text against known patterns:

```
"prompt is too long", "prompt_too_long", "context length exceeded",
"maximum context length", "max_tokens", "too many tokens",
"content too large", "request too large", "input is too long"
```

**Source:** `duh/kernel/engine.py` (`MAX_PTL_RETRIES`, `_PTL_COMPACTION_TARGETS`)

---

## Prompt Caching

D.U.H. uses the Anthropic prompt caching API to avoid resending stable content on every turn. This reduces latency and cost by up to ~90% for the cached prefix.

### System Prompt Caching

The system prompt is wrapped in a structured content block with a `cache_control` marker:

```json
[{
    "type": "text",
    "text": "<system prompt>",
    "cache_control": {"type": "ephemeral"}
}]
```

This tells the API to cache the system prompt across turns. Since the system prompt is identical every turn, it is served from cache after the first request.

### Prefix Caching

The conversation prefix (all messages except the newest user input) is also marked for caching. The `_add_prefix_cache_marker` function adds `cache_control` to the last content block of the **second-to-last** message:

```python
# The second-to-last message is the end of the "prefix"
prefix_msg = api_messages[-2]
last_block = content[-1]
last_block["cache_control"] = {"type": "ephemeral"}
```

This marks the boundary between stable history (cacheable) and the new user input (not cached).

### Cache Break Detection

The `CacheTracker` monitors `cache_creation_input_tokens` and `cache_read_input_tokens` from API usage metadata. A **cache break** is detected when:

- At least 2 turns have been recorded.
- The previous turn had a meaningful cache read ratio (> 10%).
- The current turn's ratio dropped by more than 40% (`_BREAK_THRESHOLD`).
- No compaction happened between the two turns (compaction naturally causes a cache break, and `notify_compaction()` suppresses the false positive).

Cache tracker data is included in the `/context` output and cost summary.

**Source:** `duh/adapters/anthropic.py` (`_build_cached_system`, `_add_prefix_cache_marker`), `duh/kernel/cache_tracker.py`

---

## Compact Analytics

The `CompactStats` tracker accumulates compaction statistics for the session:

- **Total compactions** performed
- **Total tokens freed** across all compactions
- **Per-tier counts**: microcompact, snip, dedup, summary
- **Per-event history**: ordered list of (tier, tokens_freed) pairs

The Engine records a `CompactStats.record()` call after every auto-compact and PTL-retry compaction, including the tier name and estimated tokens freed.

### `/compact-stats` Output

```
Compaction statistics:
  Total compactions:  3
  Total tokens freed: 42,800

  By tier:
    Snip:         1
    Summary:      2

  History:
    1. auto: ~12,400 tokens freed
    2. auto: ~18,200 tokens freed
    3. ptl_retry: ~12,200 tokens freed
```

**Source:** `duh/kernel/compact_analytics.py`

---

## Commands

### `/compact`

Manually trigger compaction. Runs the configured compaction function (typically the `AdaptiveCompactor` pipeline) on the current message history.

```
> /compact
  Compacted to 12 messages.
```

### `/context`

Show the context window token breakdown. Displays system prompt size, conversation history size, tool schema size, total usage percentage, cache stats, and compact analytics (if any compactions have occurred).

```
> /context
  Context window: 200,000 tokens (claude-sonnet-4-20250514)

  Component               Tokens       %
  ---------------------- ---------- -------
  System prompt               3,200   1.6%
  Conversation history       42,800  21.4%
  Tool schemas                8,100   4.1%
  ---------------------- ---------- -------
  Used                       54,100  27.1%
  Available                 145,900  72.9%

  Cache: 89% read ratio | 42,800 read / 3,200 created | 5 turn(s) | OK
```

### `/compact-stats`

Show compaction analytics for the current session. Displays the full `CompactStats.summary()` output.

---

## Configuration

### `DUH_MAX_COST`

Environment variable or `--max-cost` CLI flag. Sets a session cost budget in USD. When 80% of the budget is consumed, a warning is emitted. At 100%, the session stops. Compaction is cost-aware -- the summary tier costs one API call, but saves money by keeping the session within its context window rather than forcing a restart.

```bash
DUH_MAX_COST=2.00 duh
# or
duh --max-cost 2.00
```

### `--max-turns`

CLI flag (default 100). Limits the number of agentic turns in a session. When reached, the model is asked to summarize its work and the session ends. This indirectly controls context growth by capping session length.

```bash
duh --max-turns 50
```

### `--summarize` on Resume

When resuming a session with `--continue` or `--resume`, the `--summarize` flag compacts the restored message history to 50% of the default token limit. This prevents a resumed session from immediately hitting the context ceiling.

```bash
duh --continue --summarize
```

**Source:** `duh/cli/parser.py`, `duh/cli/runner.py`, `duh/cli/repl.py`

---

## Architecture Diagram

```
                    User prompt arrives
                           |
                           v
                  Estimate token count
                           |
                    +------+------+
                    |  >= 80%?    |
                    +------+------+
                      yes  |    no --> proceed to query
                           v
              +-- AdaptiveCompactor --+
              |                      |
              v                      |
    Tier 0: Microcompact             |
       (clear old tool results)      |
              |                      |
          under budget? --yes------->+---> proceed to query
              | no                   |
              v                      |
    Tier 0.5: Snip (>= 75%)         |
       (remove old rounds)           |
              |                      |
          under budget? --yes------->+
              | no                   |
              v                      |
    Tier 1: Dedup                    |
       (deduplicate + strip images)  |
              |                      |
          under budget? --yes------->+
              | no                   |
              v                      |
    Tier 2: Summarize (>= 85%)      |
       (model-backed summary)        |
       + post-compact file rebuild   |
              |                      |
              +----------------------+
                           |
                           v
                  Context Gate (95%)
                           |
                  +--------+--------+
                  | >= 95%?         |
                  +--------+--------+
                     yes   |    no --> proceed to query
                           v
                  BLOCK: yield context_blocked
                  "Run /compact to free space."
```
