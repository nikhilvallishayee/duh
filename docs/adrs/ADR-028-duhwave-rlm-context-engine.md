# ADR-028 — RLM context engine: prompt-as-variable, programmatic recursion

**Status:** Accepted (implemented)
**Date:** 2026-04-30 · 2026-05-01 (accepted)
**Scope:** `duh/kernel/context/`, new `duh/kernel/rlm/`
**Depends on:** ADR-019 (universal harness architecture), ADR-024 (design principles), ADR-056 (auto-compact), ADR-060 (snip compaction)
**Cited literature:** Zhang, Kraska, Khattab — *Recursive Language Models* (arXiv 2512.24601, Jan 2026).

## Context

D.U.H.'s current context strategy on long inputs is **compaction**: when
the conversation crosses ~50 % of the context window, summarise the
older turns into a smaller block of prose and drop the originals
(ADR-056, ADR-060). This is what every other harness does. It works.

It also loses information.

Benchmark 3 (documentation over the D.U.H. source tree) made the
limit visible. At matched GPT-5.4, D.U.H. produced docs that resolved
100 % of cited symbols against source; the leading first-party CLI at
the same model resolved 52 %. Both agents had access to the same
codebase. The difference was *when they read*: D.U.H. pulled files on
demand; the other agent worked from a compacted snapshot of an
earlier scan. Names were right; signatures were guessed.

Compaction is a write-once-lose-information operation. Every byte
that gets summarised becomes inaccessible to later turns unless the
agent thinks to re-read the source. On a corpus larger than the
context window, this fails silently and produces plausible-looking
hallucinated detail. The B3 result is one example; long-running
support-chat sessions, multi-PR review threads, and code-archaeology
tasks have the same failure mode.

A different approach landed in the literature recently. Zhang, Kraska,
and Khattab (MIT OASYS lab, December 2025) show that an LLM can
process inputs **two orders of magnitude beyond its context window**
by treating the prompt as a *variable in a Python REPL* rather than as
text fed to the model. The agent peeks at slices, decomposes
programmatically, and recursively calls itself on snippets. Their
post-trained 8B model (RLM-Qwen3-8B) outperforms vanilla Qwen3-8B by
+28.3 % on long-context tasks at comparable cost.

This is the right substrate for D.U.H. The compaction layer can stay
for short interactive sessions; for read-heavy work the agent should
**address every byte by reference, never by summary**.

## Decision

D.U.H. adds an **RLM context engine** alongside the existing
compaction layer. When activated, large inputs are loaded as variables
inside a sandboxed Python REPL; the agent receives REPL-manipulation
tools instead of the raw text.

### Architecture

```
duh/kernel/rlm/
├── __init__.py
├── repl.py          # Sandboxed REPL (subprocess, isolated mode)
├── handles.py       # Variable handle protocol
├── tools.py         # Peek / Search / Slice / Recurse / Synthesize
├── decomposers.py   # Built-in decomposition strategies
└── policy.py        # When to activate RLM vs. compaction
```

### The REPL substrate

A long-running Python subprocess (`python3 -I`, isolated mode, no
network, restricted filesystem) holds the conversation's bulky inputs
as named variables:

```python
# Inside the REPL — invisible to the model
codebase = load_directory("/path/to/repo")  # ~30K LOC as one string
spec = load_file("requirements.md")
turn_history = []  # accumulates as conversation continues
```

The REPL persists across turns within a session. Variables outlive a
single tool call. Memory ceiling is configurable
(`DUH_RLM_MAX_HEAP_MB`, default 512 MB).

### The agent's view

Instead of receiving `codebase` as 90 % of the context window, the
agent sees a small system block:

```
You have a Python REPL with these variables loaded:
  codebase  (str, 1,234,567 chars, ~280k tokens)  — the source tree
  spec      (str, 3,891 chars)                    — requirements

Use Peek / Search / Slice / Recurse / Synthesize tools to interact.
The full content is addressable; nothing has been summarised.
```

The agent never sees the full corpus inline. It works against handles.

### Five tools

```python
class Peek(Tool):
    """Show a slice of a variable.

    args:
      handle: str         # variable name in REPL
      start: int = 0      # char offset
      end:   int = 4096   # char offset (exclusive)
      mode:  Literal["chars","lines","tokens"] = "chars"
    returns:
      slice: str          # exactly what's between [start, end)
      meta:  {total_chars, total_lines, ...}
    """

class Search(Tool):
    """Regex / literal search across a variable, returns line-anchored hits.

    args:
      handle: str
      pattern: str           # regex
      max_hits: int = 50
    returns:
      hits: list[{line: int, col: int, snippet: str}]
    """

class Slice(Tool):
    """Bind a sub-region as a new named handle.

    args:
      source: str            # existing handle name
      start: int
      end:   int
      bind_as: str           # new handle name
    """

class Recurse(Tool):
    """Spawn a child model call against a slice, return its synthesis.

    args:
      handle:      str       # what the child reads
      instruction: str       # what the child should do with it
      model:       str = "inherit"
      max_turns:   int = 5
    returns:
      result: str            # child's final text response
      handle: str            # new REPL handle pointing at result
    """

class Synthesize(Tool):
    """Combine multiple handles into one, with the model summarising.

    args:
      handles: list[str]
      instruction: str
      bind_as: str
    """
```

These five tools are **the agent's entire interface to bulk content**.
The agent's own context stays small (system prompt + tool schemas +
the running dialog); the data lives in the REPL.

### Recursion bounds

- Max recursion depth: 4 (configurable, hard cap 8).
- Per-call token budget: inherits parent's remaining budget.
- Per-call wall time: 5 minutes (configurable).
- Cycle detection: a `Recurse` call against a handle that includes the
  caller's own output is rejected at the policy layer.

### When the engine activates

`duh/kernel/rlm/policy.py` decides:

| Input shape                                         | Engine     |
|----------------------------------------------------|------------|
| Single short prompt, no attachments, < 25 % window | Compaction (legacy) |
| One large file or directory, > 25 % window         | **RLM**    |
| Conversation accumulating across turns             | Compaction starts; RLM kicks in if any single turn brings > 25 % new bulk |
| Explicit `--context-mode rlm`                      | **RLM** unconditionally |
| Explicit `--context-mode compact`                  | Compaction (legacy) |
| Model lacks tool calling                           | Compaction (only path) |

The default (`auto`) routes by input shape. Read-heavy tasks land on
RLM; chat-heavy tasks stay on compaction.

### Coexistence with compaction

Compaction (ADR-056 / ADR-060) is **not removed**. The two engines
serve different shapes of work:

- Compaction reduces an oversized *conversation* to fit a window.
- RLM lets a small conversation address an oversized *corpus*.

A long session with both a large reference corpus *and* an
accumulating dialog uses both: corpus lives in REPL handles;
conversation prose follows the existing snip-compaction policy.

### Sandboxing

The REPL runs as a subprocess with:

- `python3 -I` (isolated mode — no user site-packages, no `PYTHONPATH`
  influence).
- `os.chroot` not used (cross-platform); instead, filesystem ops are
  routed through D.U.H.'s permission gate (ADR-005), same as the
  existing tool layer.
- No network: subprocess inherits no proxy / no `urllib` factory; a
  monkey-patched `socket` raises on `connect`.
- Memory ceiling: `resource.setrlimit(RLIMIT_AS, ...)`.
- CPU ceiling: per-call wall-time enforcement on the parent side.

The REPL has access to a curated stdlib subset (`re`, `json`, `ast`,
`pathlib`, `collections`, `itertools`, `dataclasses`, `textwrap`,
`difflib`). No `os.system`, no `subprocess`, no `socket`, no `ctypes`.

### Persistence

Per session, the REPL's variable namespace is checkpointed to
`<session_dir>/rlm.pkl` after each turn (ADR-058 resume parity). On
`--continue` the namespace is restored, so the agent picks up with the
same handles loaded.

Bulk content (file contents, fetched URLs) is content-addressed by
SHA-256 and stored in `<session_dir>/rlm/blobs/` so resumption
doesn't re-fetch.

## Alternatives considered

1. **Bigger context windows.** Frontier models continue to grow
   windows; "just upgrade the model" is a tempting answer. But window
   growth is sub-exponential while corpus sizes are not, the per-token
   cost is real, and quality degrades on long inputs even within
   advertised limits (lost-in-the-middle remains a measurable problem
   on every model we tested in B3). RLM beats vanilla long-context
   scaffolds in the Zhang/Kraska/Khattab paper *at comparable cost*.

2. **Better compaction.** Smarter summarisers, hierarchical
   compaction, learned compactors. All still write-once-lose-info.
   The RLM substrate is the qualitative jump: every byte stays
   addressable.

3. **External vector store + RAG.** D.U.H. could index the corpus
   into a vector DB and retrieve on demand. This is the OpenCode-shape
   answer. It works but introduces (a) a new deployment dependency
   (vector store), (b) chunking decisions baked in at index time, (c)
   embedding-model bias. The RLM substrate is in-process, no
   embedding step, and slices are exact byte-ranges not approximate
   neighbours.

4. **MCP `resources/list` + `resources/read`.** The MCP resource
   protocol covers a subset of this — bulk content lives at the MCP
   server; the agent fetches by URI. Compatible with RLM (an MCP
   resource can become an RLM variable on first read), but the MCP
   resource protocol is read-only and lacks the
   peek/slice/recurse/synthesize shape. RLM uses MCP as one source
   of variables, not a replacement.

5. **Implement RLM but skip the REPL — just give the agent slice
   tools over raw strings in adapter memory.** Loses the
   programmability. The point of the Python REPL is that the model
   can write *code* — `re.findall(r"def \w+\(", codebase)` — to
   navigate. A fixed slice/search tool set is strictly less powerful
   than `Peek + Code` and matches less of the published RLM result.

## Consequences

Positive:

- Bytes never disappear. The agent can re-read what it forgot;
  compaction can't.
- Read-heavy benchmarks (B3-shape work) gain headroom independent of
  model context-window growth.
- Composes cleanly with multi-agent: one agent's REPL handle can be
  passed to another (sets up ADR-029).
- Local models with smaller windows become viable on real-world
  corpora — the agent doesn't need to fit the codebase in 32K tokens.
- Cost story is favourable: RLM does fewer big rounds with the
  full prompt; instead it does many smaller rounds against slices.
  On the published benchmarks, total tokens is comparable; on highly
  redundant corpora it's lower.

Negative / tradeoffs:

- New dependency: a Python subprocess per session. ~30 MB RSS at
  rest. Mitigated by lazy start (only spawn when policy activates).
- Sandboxing is real engineering. The curated-stdlib whitelist needs
  audit; the no-network guarantee needs an integration test that runs
  in CI on Linux + macOS.
- Recursion makes cost reasoning harder. A `Recurse` call can fan out
  more `Recurse` calls (depth 4). The token-budget propagation must
  enforce strict ceilings or sessions can blow their cost cap. Bounds
  declared above; tested in `tests/integration/test_rlm_budget.py`.
- Failure modes are new. A REPL OOM, a runaway `Search` regex, a
  malformed `Recurse` cycle — each needs a clean error path back to
  the parent agent.
- Tool surface grows by 5. We've kept D.U.H.'s tool set tight by
  policy; this is a deliberate exception. The five tools are tightly
  scoped and designed to compose, not pile up.

## Migration

No user action required. RLM activates automatically when input
crosses the threshold (default 25 % of window). Users who want the
old behaviour back can pass `--context-mode compact`; users who want
to force RLM can pass `--context-mode rlm`.

Sessions resumed from before this ADR continue on compaction; new
sessions on the same project can opt in.

ADR-056 and ADR-060 remain the authoritative reference for the
compaction layer and are unchanged.

## Tests

After this ADR lands:

- `tests/unit/test_rlm_repl.py` — REPL lifecycle, variable bind,
  Peek/Search/Slice round-trips, persistence to `rlm.pkl`.
- `tests/unit/test_rlm_policy.py` — input-shape routing decisions.
- `tests/unit/test_rlm_sandbox.py` — no-network, no-subprocess, no-
  ctypes assertions; `os.system` raises; `import socket; ...connect()`
  raises.
- `tests/unit/test_rlm_budget.py` — recursion-depth cap, token-budget
  propagation, cycle detection.
- `tests/integration/test_rlm_b3.py` — end-to-end run of a B3-shape
  task at matched GPT-5.4 with `--context-mode rlm` vs
  `--context-mode compact`. Exit criterion: signature-consistency on
  the resulting docs is ≥ 90 % (vs. the historical 52 % on compaction).

## Follow-up

- **ADR-029** — Recursive cross-agent links: how a child agent can
  *receive* an REPL handle and operate on the same variable space as
  the parent. Builds on this substrate.
- **ADR-031** — `Recurse` as the substrate for coordinator-style
  delegation: instead of spawning a sub-agent and round-tripping
  prose, the coordinator calls `Recurse(handle, instruction)` and
  binds the result as a new handle.
- **Benchmark 4** — long-running, multi-corpus reading task explicitly
  designed to defeat compaction; a published number on RLM vs.
  compaction at three model sizes.
- **RLM-native fine-tuning experiments** — Zhang et al. show post-
  training a model on RLM trajectories yields +28.3 %. D.U.H.'s
  benchmark substrate could host the public eval lane for any RLM-
  tuned model that ships.
