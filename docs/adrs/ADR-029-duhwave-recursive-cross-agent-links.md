# ADR-029 — Recursive cross-agent links: variable handles instead of prose summaries

**Status:** Accepted (implemented)
**Date:** 2026-04-30 · 2026-05-01 (accepted)
**Scope:** `duh/kernel/rlm/`, `duh/kernel/coordinator/`, new `duh/kernel/rlm/spawn.py`
**Depends on:** ADR-019 (universal harness architecture), ADR-024 (design principles), ADR-028 (RLM context engine), ADR-063 (coordinator mode)
**Cited literature:** Yang, Zou, Pan et al. — *Recursive Multi-Agent Systems* (arXiv 2604.25917, April 2026).

## Context

ADR-028 introduces a Python REPL as the substrate for a single agent's
working memory: bytes are addressed by reference, not by summary, and
the agent operates on `Peek` / `Search` / `Slice` / `Recurse` /
`Synthesize` over named handles. That solves the single-agent corpus
problem.

The multi-agent problem is still open. ADR-063's coordinator mode is
prose-in / prose-out: the coordinator emits a `Spawn` of a worker
agent with a text instruction, the worker runs to completion, and
returns a text result that the coordinator stitches into its next
turn. The handoff is **lossy** in two directions:

- **Coordinator → worker.** The coordinator has whatever bulk content
  it has been working with (a 30K-LOC codebase as a REPL handle, a
  spec document, a prior worker's findings). To delegate, it
  serialises a description of the relevant slice into the worker's
  prompt. The worker re-reads from disk or re-parses from the
  prompt — losing the precise byte-ranges the coordinator already
  identified.
- **Worker → coordinator.** The worker's answer is condensed into a
  prose response. If the worker found 47 candidate call sites, the
  coordinator gets a paragraph naming "approximately 50 call sites,
  notably …" and has to re-derive the list to act on it. Every
  detail the coordinator needs is bottlenecked through one summary.

Yang, Zou, Pan et al. (April 2026) ran the obvious experiment: skip
the prose. In their RecursiveMAS framework, sub-agents share the
parent's REPL substrate; a worker's output binds back as a new
*handle* in the parent's namespace; the parent peeks and slices that
handle just as it would any local variable. Reported deltas against
prose-handoff multi-agent baselines:

- **+8.3 % task accuracy** on long-context multi-step benchmarks
- **2.4 × wall-clock speedup** (no re-reading, no re-parsing)
- **75.6 % fewer tokens** end-to-end (the largest line items in
  multi-agent token spend are repeated context loads, which this
  eliminates)

The numbers fit the same pattern as ADR-028's single-agent gains: the
mechanism is the same — *address by reference, never by summary* —
applied across an agent boundary instead of across a turn boundary.
ADR-028's follow-up section flagged exactly this as the next ADR.
This is that ADR.

## Decision

D.U.H. adds **recursive cross-agent links** on top of the RLM context
engine. When RLM is active, multi-agent delegation uses **selective
handle exposure** instead of prose-only `Spawn`; worker output binds
back as a new handle in the coordinator's REPL.

When RLM is *not* active (compaction-only sessions, models without
tool calling, explicit `--context-mode compact`), the existing
ADR-063 prose-result path is unchanged. RecursiveLink is an RLM-active
feature; it is not a regression for users who don't opt into RLM.

### The shared substrate

A coordinator's REPL is the *single* substrate for the session. There
is one Python subprocess per session, owned by the coordinator. Sub-
agents do not get their own REPLs; they get a **view** into the
coordinator's REPL, scoped by an explicit exposure list.

```
coordinator REPL (one subprocess per session)
├── codebase     (str, ~280k tokens)
├── spec         (str, ~3k chars)
├── findings_a   (bound on worker_a completion)
├── findings_b   (bound on worker_b completion)
└── plan         (bound by coordinator after synthesis)

worker_a's view:  {codebase, spec}      ← read-only
worker_b's view:  {codebase, findings_a} ← read-only
```

This is the depth-1 invariant from the legacy harness design
philosophy: workers do not spawn workers. A worker that needs further
delegation returns to the coordinator, which decides whether to fan
out again. Cycle bounds and recursion limits sit at the coordinator;
the worker view never has access to `Spawn` itself.

### Selective handle exposure

The coordinator declares, per spawn, which handles a worker can see.
Other variables in the namespace are invisible to that worker — not
just unmentioned, but not bound in the worker's REPL view at all.

```python
Spawn(
    prompt="Find every call site of `Tool.run` and classify by approval tier.",
    expose=["codebase", "spec"],          # worker's read-only view
    bind_result_as="findings_a",           # name in coordinator's REPL
    model="inherit",
    max_turns=10,
)
```

The worker sees a system block analogous to the single-agent RLM
block (ADR-028), but pruned to its exposure list:

```
You have a Python REPL view with these variables (read-only):
  codebase  (str, 1,234,567 chars, ~280k tokens)
  spec      (str, 3,891 chars)

Use Peek / Search / Slice / Recurse / Synthesize to interact.
Your final response will be bound back to the coordinator as a new handle.
```

A worker's `Slice` calls bind into the worker's *local* namespace,
not the coordinator's. Worker-local handles are isolated until commit
(see "binding back" below).

### The `Spawn` tool

`Spawn` replaces the prose-only AgentTool path for RLM-active
sessions. Its schema:

```python
class Spawn(Tool):
    """Run a sub-agent against a handle-scoped view of the REPL.

    Available only to the coordinator agent; not exposed in worker views.

    args:
      prompt: str           # the worker's instruction
      expose: list[str]     # handle names the worker can read
      bind_result_as: str   # new handle name in coordinator REPL
      model: str = "inherit"
      max_turns: int = 10
      timeout_s: int = 300
    returns:
      handle: str           # name now bound in coordinator REPL
      summary: str          # short prose digest (for the coordinator's dialog)
      meta:    {turns_used, tokens_in, tokens_out, exit: "ok"|"timeout"|"error"}
    """
```

Two return values, by design:

- `handle` is the load-bearing one — a name the coordinator can
  `Peek` / `Search` / `Slice` against. This is the latent-state
  transfer.
- `summary` is a short prose digest (≤ 512 tokens) that goes into the
  coordinator's *visible dialog*, so the coordinator's own model has
  context for what just happened. It is not the load-bearing channel.

If the coordinator never `Peek`s the handle and works only from the
summary, the system degrades gracefully to ADR-063 behaviour.

### Binding back

When a worker's loop terminates with a final assistant message, the
coordinator's spawn bridge:

1. Captures the worker's final text response.
2. Captures any handles the worker bound during its run that were
   marked for export (the worker's `Slice(... bind_as="result", export=True)`
   pattern; default is non-exported).
3. Commits the exported handles into the coordinator's REPL under
   namespaced names: `bind_result_as` for the final response, and
   `<bind_result_as>__<inner>` for any export-marked intermediates.

The worker's local namespace is then discarded. The worker subprocess
is *not* a separate process — it shares the coordinator's REPL — so
"discarding" means dropping the worker-local bindings from the
namespace; the underlying string objects survive only if reachable
from a committed handle.

```python
# Worker agent's run, conceptually:
view = {"codebase": coord["codebase"], "spec": coord["spec"]}
local = {}
# ... worker runs Peek/Search/Slice/Recurse/Synthesize ...
local["matches"] = re.findall(...)            # local-only
local["report"]  = "...prose summary..."      # local-only
worker_final_text = local["report"]
exported_handles  = {"matches": local["matches"]}  # if marked export

# Bridge commits to coordinator:
coord["findings_a"]            = worker_final_text
coord["findings_a__matches"]   = local["matches"]
```

The coordinator's next turn sees `findings_a` and `findings_a__matches`
as new entries in its namespace summary. It can `Peek(findings_a__matches,
start=0, end=4096)` to walk the actual list of call sites — no
re-derivation needed.

### Two-level concurrency, depth-1 preserved

The coordinator may issue multiple `Spawn` calls in a single turn;
they run concurrently via `asyncio.gather` (the same plumbing as
ADR-063's `SwarmTool`). The depth-1 invariant holds: workers do not
have `Spawn` in their tool set.

A worker that wants to "delegate" must return to the coordinator. The
coordinator can then issue further `Spawn`s. This keeps cycle
detection trivial (only one site, the coordinator, can spawn) and
keeps the cost ceiling auditable (only one site charges spawn fan-out).

```
coordinator turn N:
  Spawn(worker_a, expose=[codebase, spec])  ┐
  Spawn(worker_b, expose=[codebase, spec])  ├─ asyncio.gather
  Spawn(worker_c, expose=[codebase, spec])  ┘
coordinator turn N+1:
  Peek(findings_a__matches, ...)
  Peek(findings_b, 0, 8000)
  Spawn(worker_d, expose=[codebase, findings_a, findings_b])
```

### Worker-to-worker via the coordinator only

Worker B cannot `expose` from worker A directly. Cross-worker data
flows go through the coordinator: worker A returns a handle, the
coordinator inspects (or just trusts) it, and exposes it to worker B
on the next spawn. This is the ADR-024 simplicity-first principle:
peer-to-peer multi-agent is a separate problem (ADR-032 candidate); v1
keeps the topology a tree of depth 1.

### Failure handling

| Failure mode             | Bridge behaviour                                                                 |
|--------------------------|----------------------------------------------------------------------------------|
| Worker crashes mid-run   | `meta.exit = "error"`; `handle` bound to the partial transcript; coordinator sees the failure in `summary` and decides to retry / fall back. |
| Worker exceeds `timeout_s` | Coroutine cancelled; `meta.exit = "timeout"`; partial output (last assistant message) bound under `<bind_result_as>__partial`. |
| Worker exceeds `max_turns`| Worker returns whatever it has at the cap; `meta.exit = "ok"` with `turns_used == max_turns` — the coordinator can decide. |
| Worker returns empty text | `handle` bound to empty string; `meta.exit = "ok"`; coordinator sees `(empty)` in summary. |
| Coordinator `Peek`s a handle that timed out partial | Returns the partial slice; `meta.is_partial = True` is surfaced. |
| Worker tries to write to a handle outside its local namespace | REPL raises; worker tool layer surfaces the error to the worker; worker can recover or return. |

The coordinator never sees a silent failure: every `Spawn` returns a
`meta.exit` value that pattern-matches one of {ok, timeout, error},
and the corresponding handle is always bound (possibly to partial
content) so the coordinator can inspect rather than re-spawn blind.

### Compatibility with ADR-063

When RLM is *not* active, `Spawn` is not registered as a tool;
`AgentTool` and `SwarmTool` from ADR-063 remain the delegation path.
Coordinator-mode sessions migrate transparently: if RLM activates
mid-session (a large input arrives), subsequent delegations use
`Spawn`; earlier `AgentTool` results stay in the dialog as prose, no
retroactive conversion.

A session can be inspected with `duh session inspect <id>` to see which
delegation mechanism each spawn used; the metric is logged on each
worker invocation.

## Comparison: prose handoff vs. RecursiveLink

| Property                                | ADR-063 prose `AgentTool`     | ADR-029 `Spawn` over REPL          |
|-----------------------------------------|-------------------------------|------------------------------------|
| Coordinator → worker channel            | Prose in worker's prompt      | Handle exposure (no copy)          |
| Worker → coordinator channel            | Final assistant text          | Bound handle + ≤ 512-token digest  |
| Re-reading bulk content                 | Worker re-reads from disk     | Worker `Peek`s shared handle       |
| Coordinator can drill into worker output| Has to ask the worker again   | `Peek(findings_a__matches, ...)`   |
| Token cost on large corpora             | O(N · workers)                | O(N) once, then O(slices)          |
| Failure surface                         | Prose-shaped ("worker said…") | Typed: `meta.exit ∈ {ok,timeout,error}` |
| Depth-1 invariant                       | Yes                           | Yes                                |
| Available without RLM                   | Yes                           | No (RLM-active sessions only)      |

## Alternatives considered

1. **Prose-only handoff with smarter summarisation.** Better
   summarisers, structured-output workers (worker returns JSON, not
   prose), worker-side compaction. All retain the
   write-once-lose-information property: whatever the worker
   summarises is gone. The published RecursiveMAS numbers are against
   strong structured-output baselines and still show the +8.3 % / 2.4 ×
   / 75.6 % deltas. Compaction at the agent boundary is the same
   problem as compaction at the turn boundary (ADR-028).

2. **One REPL per agent (worker has its own subprocess).** Cleaner
   isolation, but the coordinator and worker can no longer share
   variable identity — passing `codebase` from coordinator to worker
   becomes a copy across process boundaries, defeating the
   no-copy property that gives RecursiveMAS its token savings. The
   single-substrate model also means `findings_a` is *the same string
   object* in coordinator and worker views; there is no
   serialisation, no re-parse, no drift.

3. **Let workers `Spawn` other workers (depth > 1).** Tempting for
   tree-shaped tasks (research → outline → write → critique →
   refine). Two reasons to defer:
   - Cycle detection becomes non-trivial (a worker can issue a
     `Spawn` whose `expose` includes a handle written by an
     ancestor — same worker observing its own output).
   - Cost ceilings get hard to reason about. With depth 1, the
     coordinator's spawn budget is the session's spawn budget;
     deeper nesting needs a propagating budget like ADR-028's
     `Recurse`.

   The depth-1 invariant lets v1 ship without these. ADR-032
   candidate revisits.

4. **Expose the entire coordinator namespace by default.** Simpler
   API (no `expose=`), but creates capability creep: any worker can
   peek at any prior worker's intermediate handles, including ones
   the coordinator considers private. The selective exposure is
   ADR-024 principle VIII (safety as architecture): the worker's view
   is the worker's only attack surface, and we keep it minimal by
   default.

5. **Use MCP `resources` as the inter-agent channel.** MCP resources
   are read-only, URI-addressed, and cross-process; they could
   conceptually carry handles between agents. The shape doesn't fit:
   MCP resources are designed for external content (files, DB rows),
   not in-process REPL bindings, and the round-trip through MCP's
   transport layer reintroduces serialisation cost. RecursiveLink
   is intra-process by design.

## Consequences

Positive:

- Every byte the coordinator gathered stays addressable by every
  worker the coordinator chooses to expose it to. No re-reading.
- Worker output is a first-class operand for subsequent coordinator
  reasoning, not a prose digest. The coordinator can `Search` worker
  A's output for a regex when planning worker B.
- Fewer tokens end-to-end on multi-agent tasks. Published number is
  75.6 % reduction; D.U.H.'s benchmark substrate will publish its own.
- Failure surface is typed. `meta.exit` is one of three values; no
  parsing prose to detect timeouts.
- Composes cleanly with ADR-028's existing `Recurse` tool: a
  `Spawn` is `Recurse` with explicit exposure scoping and a different
  name, intended for delegation-shaped subtasks rather than
  decomposition-shaped ones.

Negative / tradeoffs:

- The selective-exposure model is new surface for the coordinator
  agent to learn. Coordinator system prompts need updating to
  describe `expose=` as a first-class concept; otherwise the
  coordinator may default to exposing everything.
- Worker isolation is in-namespace, not in-process. A buggy worker
  that mutates a string in place could corrupt a shared handle. We
  enforce read-only exposure by binding workers' views as
  `MappingProxyType` over the underlying objects; primitive strings
  in CPython are already immutable, but lists / dicts / dataclasses
  can be mutated. Audit lives in `tests/unit/test_rlm_isolation.py`.
- Coordinator's REPL becomes the single point of failure for the
  session: if it OOMs (`DUH_RLM_MAX_HEAP_MB`), every worker depending
  on it stops. Same risk as the single-agent case in ADR-028, scaled
  by fan-out.
- The `Spawn` tool's `expose=` argument adds one more decision the
  coordinator has to make per delegation. Workers that don't need
  bulk content (a quick "format this list" call) shouldn't need to
  declare exposure; we default `expose=[]` to mean "no shared
  handles, prose-only".
- Cost reasoning shifts. Multi-agent runs no longer have a clean
  "each worker pays its own context-load tokens" model; the loaded
  bytes are a session-level fixed cost amortised across spawns.
  Cost reports surface this in the per-session breakdown.

## Migration

For users on coordinator mode (ADR-063):

- No flag change. Coordinator mode auto-detects RLM activation and
  promotes `AgentTool` → `Spawn` for that session.
- Saved sessions resume in their original mode: a coordinator session
  recorded under prose-only `AgentTool` continues with that tool;
  a session recorded under `Spawn` continues with `Spawn`. Mixed
  sessions (RLM activated mid-stream) replay the original tool calls
  exactly.
- The `--coordinator` CLI flag and `/mode coordinator` slash command
  are unchanged. A new `--coordinator-handoff {prose|handle|auto}`
  flag forces a specific handoff mode for benchmark / debugging
  purposes; default is `auto`.

For SDK users instantiating coordinator agents programmatically: the
new `Spawn` tool is in `duh.kernel.rlm.spawn`; the existing
`AgentTool` / `SwarmTool` remain in `duh.kernel.coordinator`. The
agent's tool registry routes by RLM-active status; SDK users do not
need to wire this themselves.

ADR-028 and ADR-063 both remain authoritative; this ADR composes on
top of them rather than replacing either.

## Tests

After this ADR lands:

- `tests/unit/test_rlm_spawn.py` — `Spawn` tool's input validation,
  exposure scoping, handle naming, the bind-back protocol.
- `tests/unit/test_rlm_spawn_failures.py` — every row of the failure-
  handling table: crash, timeout, max-turns, empty result, partial-
  on-timeout `Peek`.
- `tests/unit/test_rlm_isolation.py` — `MappingProxyType` over shared
  handles; worker can `Peek` but cannot rebind the coordinator's
  variable; mutating-on-mutable-handle assertion.
- `tests/unit/test_rlm_depth1.py` — `Spawn` is *not* in worker tool
  registry; a worker that emits a `Spawn`-shaped tool call hits the
  unknown-tool path.
- `tests/integration/test_rlm_coordinator_b3.py` — end-to-end run of
  a B3-shape multi-agent task with `--coordinator
  --coordinator-handoff handle` vs `--coordinator-handoff prose` at
  matched GPT-5.4. Exit criteria, mirroring the RecursiveMAS paper:
  - accuracy delta ≥ +5 % (paper: +8.3 %)
  - wall-clock speedup ≥ 1.8 × (paper: 2.4 ×)
  - token reduction ≥ 50 % (paper: 75.6 %)
  Three of three required to mark the integration test as passing;
  any below threshold is a regression worth investigating before
  release.
- `tests/integration/test_rlm_coordinator_resume.py` — kill mid-run
  during a `Spawn`; resume; verify partial handle is restored under
  `<bind_result_as>__partial` and the coordinator's next turn can
  inspect it.

## Follow-up

- **ADR-030** — Worker-side `expose=` introspection: tooling that
  lets the worker enumerate exactly what it has access to, for
  agents whose system prompts need to negotiate scope.
- **ADR-031** — `Recurse` (ADR-028) and `Spawn` (this ADR)
  unification: both are sub-call mechanisms; the schema differences
  are scoping (`expose=`) and result-binding (`bind_result_as`).
  An ADR-031 candidate consolidates them into one tool with two
  modes.
- **ADR-032** — Depth-N delegation: budget-propagating recursive
  spawns that lift the depth-1 invariant for tree-shaped workflows
  (research → outline → write → critique). Cycle detection and
  cost-ceiling propagation are the hard parts; this ADR explicitly
  defers them.
- **Benchmark 5** — published reproduction of the RecursiveMAS
  numbers on a D.U.H.-shape multi-agent task. The harness already
  has the multi-agent substrate; B5's job is to publish the deltas
  on the same task type at three model sizes, with both handoff
  modes available behind a flag for direct comparison.
