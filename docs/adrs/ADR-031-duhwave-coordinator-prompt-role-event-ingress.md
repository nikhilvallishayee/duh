# ADR-031 — Coordinator-as-prompt-role + event ingress

**Status:** Accepted (implemented)
**Date:** 2026-04-30 · 2026-05-01 (accepted)
**Scope:** `duh/kernel/coordinator/`, `duh/wave/ingress/`,
`duh/wave/triggers/`
**Depends on:** ADR-019 (universal harness architecture), ADR-024
(design principles), ADR-027 (native adapters), ADR-028 (RLM context
engine), ADR-029 (recursive multi-agent), ADR-063 (coordinator mode v1)
**Forward refs:** ADR-030 (Task primitive), ADR-032 (swarm topology +
`duh wave start`)

## Context

duhwave — D.U.H.'s persistent agentic-swarm extension — needs two
shifts that must land together, because each is load-bearing for
the other.

**Shift one: orchestration without engine bloat.** ADR-063 made
"coordinator mode" a CLI flag that swapped in a different system
prompt and added a `SwarmTool`. The implementation then grew: a
`CoordinatorEngine` subclass, a "coordinator-aware" message router,
"coordinator session metadata" sidecar. Three months in, the
coordinator has its own runtime path, bug surface, and test matrix
drifting from the kernel's. We are reinventing multi-agent
orchestration as engine code — the trap ADR-024 Principle II was
meant to forestall, applied to *role* lock-in.

The clean answer: orchestration is not an engine feature. It is a
**role** an agent plays, enforced by a system prompt and a
constrained tool set. The kernel has no idea it is running a
coordinator versus a worker. Same engine, same loop, same adapters;
different prompt, different tools.

**Shift two: command-driven to event-driven.** D.U.H. today is
command-driven: `duh "do thing"`, loop to completion, exit. duhwave
is meant to be persistent — a host running in the background that
ingests external signals (webhook, file change, cron tick, MCP
push) and decides whether each warrants spawning a Task. Without a
uniform ingress layer, every signal becomes a bespoke integration;
three integrations in we have three half-built event systems with
no consistent way to correlate events to Tasks.

Both shifts must land together. The coordinator role is the
consumer of triggered Tasks. Ship ingress alone and events route
to the existing engine path, entrenching the bloat. Ship the role
refactor alone and the coordinator has nothing to do beyond
ADR-063.

## Decision

duhwave defines orchestration as a **prompt-role** running on the
plain kernel, and adds an **event-ingress** layer that materialises
external signals as `Trigger` records which spawn Tasks via the same
primitive a human invocation uses.

### A. Coordinator role

#### A.1 Role, not subclass

The kernel runs one engine — the same loop in `duh/kernel/loop.py`
that handles a single-agent `duh "fix this bug"` invocation. There
is no `CoordinatorEngine`, no `coordinator_aware_router`, no
special session metadata path. ADR-063's `--coordinator` flag is
preserved as a UX shortcut; its sole effect is to select a
different **Role** at session start. A Role is:

```python
@dataclass(frozen=True)
class Role:
    name:           str           # e.g. "coordinator", "worker", "reviewer"
    system_prompt:  str           # full text — no merging at runtime
    tools:          frozenset[str]  # whitelist of tool names
    spawn_depth:    int           # max depth THIS role may spawn at
```

Two roles ship here; ADR-032 adds more via topology.

| Role | `system_prompt` source | `tools` | `spawn_depth` |
|------|------------------------|---------|---------------|
| `worker` (default) | `duh/kernel/prompts/worker.md` | full kernel tool set + role-specific tools | 0 (cannot `Spawn`) |
| `coordinator` | `duh/kernel/prompts/coordinator.md` | `Spawn`, `SendMessage`, `Stop`, `Peek`, `Search`, `Slice` | 1 |

The kernel reads `Role.tools` once at session start and filters the
tool registry to that whitelist before the first turn. Anything not
in the whitelist is *not registered* — the model never sees a schema
for `Bash` while in the coordinator role, so the failure mode is
"the model doesn't know that capability exists" rather than runtime
denial. Refusing tools at runtime trains the model to keep retrying.

#### A.2 The coordinator's tool set

The whitelist is tight:

- **`Spawn`** (ADR-029) — start a child agent in the `worker` role.
  Returns immediately with a `task_id`; result arrives later as a
  `<task-update>` block (see A.5).
- **`SendMessage`** — append a message to a still-running worker's
  inbox. Used to clarify, redirect, or cancel.
- **`Stop`** — terminate a running worker by `task_id`.
- **`Peek` / `Search` / `Slice`** (ADR-028) — read-only access to
  REPL handles. May *inspect* corpora; cannot transform them.

What is **not** in the whitelist: no `Bash`, `Edit`, `Write`, `Read`
(file), `Fetch`, `MCPCall`. The coordinator cannot touch the world
directly. No `Recurse` either — the coordinator delegates with
`Spawn`, not by recursing on its own context (A.6).

This is the **synthesis-mandate constraint at the tool layer**: with
no execution tools, the coordinator's only path to "do something" is
"write a worker prompt that does it."

#### A.3 The coordinator's system prompt — synthesis-mandate

`duh/kernel/prompts/coordinator.md` is short, opinionated, and
contains a section we call the **synthesis-mandate**. Verbatim:

```
You are a coordinator. You do not execute tools that touch the
world — no editing, no shell, no network. You delegate by writing
precise worker prompts and spawning workers.

A bad worker prompt: "Investigate the auth bug and fix it."

A good worker prompt: "In src/auth/session.py:142, session.refresh()
raises TokenExpired when called within 5s of token issue (test:
tests/auth/test_session.py::test_rapid_refresh, line 88). Add a 5s
grace window in _is_expired(). Add a regression test for the rapid-
refresh case. Do not modify any other file."

Every Spawn prompt MUST include: (1) specific file paths and line
numbers; (2) the exact change wanted, in enough detail that the
worker does not have to make architectural decisions; (3) a
'show-me' fragment asking the worker to repeat the task back. If
the repeat-back is wrong, SendMessage to correct before it proceeds.

When a worker returns, do not write 'based on the worker's findings,
the bug is fixed.' That is delegation theatre. Read the
<task-update>, verify the worker did what you asked (paths, line
numbers, test results), and report what was done with citations.

You may run multiple workers in parallel. You may NOT have a worker
spawn its own workers — workers are leaves. Recursion happens via
Recurse on REPL handles (which you do not have), not Spawn.
```

The 'show-me' fragment is enforced by template, not trust: the
default `Spawn` template appends `<show-me>Repeat the task back
before proceeding.</show-me>` unless the coordinator passes
`skip_show_me=True`.

#### A.4 Continue-vs-spawn — decision table

Coordinators get two decisions wrong: when to spawn vs think, and
when to parallelise vs serialise. The system prompt encodes:

| Situation | Action |
|-----------|--------|
| Need to read one file to understand it | `Peek` / `Search` / `Slice` on a REPL handle (no spawn) |
| Need to read many files and synthesise | `Spawn` worker(s); coordinator only reads result handles |
| Tasks are independent (touch different files / different concerns) | `Spawn` in parallel — multiple `Spawn` calls in one turn |
| Tasks are sequential (B depends on A's output) | `Spawn` A; await `<task-update>`; `Spawn` B with A's result handle |
| Decision requires architectural judgement | Coordinator decides; spawn workers to implement |
| Decision requires inspecting code semantics | Spawn a worker; do not guess |
| Worker returned an unexpected result | `SendMessage` to clarify; if blocked, `Stop` and `Spawn` fresh |
| Same task type recurs across the session | Add a worker template (ADR-032 topology file); do not re-derive prompt every time |

This table lives at the top of the coordinator prompt, indented as
prose. The model treats it as ground truth.

#### A.5 Async result delivery — `<task-update>`

The coordinator never blocks on a worker. `Spawn` returns
immediately with `{task_id, role, started_at}`. When the worker
reaches a stop condition (success, error, timeout, explicit `Stop`),
its terminal state arrives in the coordinator's inbox as a
structured block the kernel injects on the *next* turn:

```xml
<task-update task-id="t-7f3a" status="success" duration_s="12.4">
  <prompt>… the prompt the coordinator originally sent …</prompt>
  <result>… the worker's final assistant message …</result>
  <artifacts>
    <handle name="auth_fix_diff" kind="diff" bytes="2841"/>
    <handle name="test_output" kind="text" bytes="1192"/>
  </artifacts>
  <token-usage input="14223" output="3104" cached="9876"/>
</task-update>
```

Multiple `<task-update>` blocks may be present in one turn if
multiple workers completed. The coordinator never polls; the kernel
guarantees delivery on the next turn after completion. To react
sooner — to cancel a worker gone off-track — the coordinator must
`SendMessage` proactively.

#### A.6 Depth-1 invariant

A worker spawned by the coordinator has `spawn_depth = 0`. When a
`worker` role calls `Spawn`, the kernel rejects with `RoleError:
workers cannot spawn`.

The distinction from ADR-028's `Recurse` is load-bearing:

- `Spawn` (ADR-029) crosses an *agent* boundary — new session,
  context window, possibly model, new role. For parallelism and
  specialisation.
- `Recurse` (ADR-028) stays inside one agent's REPL substrate; the
  child call processes a *handle* and returns text. For scaling
  against bulk content.

Workers can `Recurse` (subject to ADR-028's depth-4 cap) because
that is bounded by per-handle byte budgets and stays in one process.
Workers cannot `Spawn` because that would let any task balloon into
a multi-agent tree we cannot reason about — token cost, debuggability,
and budget enforcement all degrade quadratically with uncontrolled
spawn depth. Depth-1 with intra-agent `Recurse` is the configuration
that gives bounded cost and a tractable trace.

ADR-032 will let advanced topologies relax depth-1 explicitly with
named roles and budget caps; the default here is one level of
`Spawn`.

### B. Event ingress

#### B.1 The `Trigger` type

External events are normalised into a single immutable record before
they touch the rest of the system:

```python
@dataclass(frozen=True)
class Trigger:
    kind:           Literal["webhook", "filewatch", "cron",
                            "mcp_push", "manual"]
    source:         str                    # e.g. "github:repo/issues",
                                           #      "fs:/path/**/*.py",
                                           #      "cron:*/5 * * * *",
                                           #      "mcp:server/notif/topic"
    payload:        dict[str, Any]         # parsed JSON or struct, ≤ 64 KB
    received_at:    datetime               # wall-clock, UTC
    correlation_id: str                    # ULID; flows into spawned Tasks
    raw_ref:        str | None             # blob path for payloads > 64 KB
```

Subtypes are tag-on-discriminator (`kind`), not class hierarchy.
Pattern-matching on `kind` is exhaustive in `dispatch.py`.

The 64 KB payload cap matters: triggers flow through the event log
(ADR-032) and unbounded payloads make replay expensive. Larger
payloads (e.g. a CI log dump) are stored to
`<wave_dir>/triggers/<correlation_id>.raw` and addressed via
`raw_ref`; the in-memory `payload` carries metadata the spawned
Task dereferences if it needs the full body.

#### B.2 Listener architecture

Listeners run inside the persistent duhwave host, **not** in the
CLI. `duh "do thing"` remains one-shot; `duh wave start` (ADR-032)
launches the long-running host with the ingress layer.

```
duh/wave/ingress/
├── __init__.py
├── host.py             # asyncio orchestrator — owns listener tasks
├── dispatch.py         # match Trigger → subscriptions → Task
├── log.py              # append-only trigger log (jsonl)
└── listeners/
    ├── webhook.py      # aiohttp server, HMAC verification per source
    ├── filewatch.py    # watchfiles wrapper, debounced
    ├── cron.py         # croniter-driven scheduler
    ├── mcp_push.py     # subscribes to MCP notifications/* channels
    └── manual.py       # Unix socket — duh wave fire <kind>
```

Each listener is `async def listen() -> AsyncIterator[Trigger]`.
`host.py` runs them concurrently via `asyncio.gather`; each yielded
`Trigger` flows through `dispatch.py`.

**Webhook.** Single aiohttp server bound to a configurable port
(default `127.0.0.1:7421`). Per-source HMAC verification with
secrets in `~/.duh/wave/secrets/<source>.key`. Listener parses,
validates, builds `Trigger`, emits. No business logic — that is
dispatch's job.

**Filewatch.** `watchfiles` (Rust-backed, cross-platform). Glob
patterns from topology. Bursts debounced: events on the same path
within `debounce_ms` (default 500 ms) coalesce into one `Trigger`.
Without it, a `git pull` over 300 files emits 300 triggers.

**Cron.** `croniter` parses 5-field expressions. Listener wakes on
the next match, emits, computes the following match. Drift bounded
by absolute wall-clock targets, not interval arithmetic, so a 5-min
job after a 10-min pause does not fire twice in a row.

**MCP push.** MCP servers send `notifications/<topic>` messages on
the protocol channel. Listener subscribes per spec and translates
each notification into a `Trigger`. The only listener that uses an
existing protocol primitive; the others are duhwave-native.

**Manual.** Unix socket at `<wave_dir>/sockets/manual.sock`. `duh
wave fire <kind> --payload <json>` writes a frame; listener emits
`Trigger(kind="manual", source="cli")`. The test seam — every other
listener is exercised via the manual one to isolate dispatch from
listener quirks.

#### B.3 Subscription matching

A `Subscription` declares "when this trigger arrives, spawn this
Task":

```python
@dataclass(frozen=True)
class Subscription:
    name:        str
    match_kind:  str                       # "webhook" | "filewatch" | …
    match_source_glob: str                 # glob against Trigger.source
    spawn_role:  str                       # "coordinator" | "worker" | …
    spawn_prompt_template: str             # Jinja-style; sees `trigger`
    rate_limit:  RateLimit | None          # optional throttle
```

Subscriptions are **declared in the swarm topology file** (ADR-032
will define the format), not registered programmatically at runtime.
A deliberate constraint from ADR-024 Principle V (config over code).
The topology is git-trackable; runtime registration is not. Users
who need dynamic subscriptions edit topology and reload (`duh wave
reload`); there is no in-process API.

A `Trigger` may match zero, one, or many subscriptions. Many is
intentional (one webhook spawning both a "triage" and a
"label-suggester" Task); zero is too (most triggers are noise).
Spawned Tasks (ADR-030) inherit the trigger's `correlation_id`, so
event log, Task log, and downstream `<task-update>`s share one ID
end-to-end. Debugging "why did this run?" reduces to `grep
<correlation_id> wave.jsonl`.

#### B.4 Reliability properties

- **At-least-once within the host.** Triggers append to
  `<wave_dir>/triggers.jsonl` *before* dispatch; a crash during
  dispatch replays unprocessed triggers on restart.
- **No cross-host guarantees.** duhwave runs on one host; multi-host
  is ADR-032 + consensus territory.
- **Idempotency is the subscription's problem.** Subscriptions
  declare `idempotency_key` derived from trigger fields; dispatch
  drops duplicates within a configurable window. Default is "no
  dedup" — most subscriptions are fine running twice.

## Alternatives considered

### Part A — coordinator role

1. **Keep `CoordinatorEngine` as an engine subclass (status quo
   from ADR-063).** Path of least resistance from the proof of
   concept. Every kernel feature (compaction, RLM, adapter changes)
   has to remember to update the coordinator path; the test matrix
   doubles. The "role is data, not code" answer is strictly smaller.

2. **Coordinator as an MCP server.** External process the kernel
   talks to. Composable in the ADR-024 sense, but introduces a
   process boundary for what is conceptually one agent's prompt
   selection. MCP is right for tool transports (ADR-027); it is
   wrong for in-process prompt data.

3. **Coordinator as a heavy framework abstraction (DAG of nodes,
   each with handlers).** Several agentic frameworks ship this.
   Generalises further than we need and forecloses the prompt-role
   design. ADR-024 Principle IV says wait for the third use case;
   it has not appeared.

4. **Allow workers to spawn workers (drop depth-1).** Tempting for
   "do this, and have your worker do that" intuition. RecursiveMAS
   shows context collapse within 4 layers on real benchmarks; our
   internal eval reproduced it. ADR-032 will allow one extra level
   explicitly, named, with budget caps.

### Part B — event ingress

1. **Per-source ad-hoc listeners (no `Trigger` type).** Each
   integration handles its own intake and spawns Tasks directly —
   what we have today, three in. No shared correlation IDs, no
   replay, no consistent throttling. The `Trigger` normalisation is
   the smallest sufficient shared type.

2. **Queue broker (Redis Streams, NATS, SQS).** Industrial-strength.
   duhwave's scale is "one developer's laptop"; a broker means a
   deployment dep for every user. The on-disk jsonl log gives
   at-least-once semantics, replay, zero deps. Cluster users can
   swap in a broker-backed driver later — the `Trigger` type does
   not change.

3. **Polling instead of webhooks.** Latency floor at poll interval,
   wasted API quota, no event identity for correlation. For
   webhooks and MCP push, polling is strictly worse.

4. **Programmatic runtime subscription registration.** Easier to
   demo (`wave.subscribe(...)`); harder to reason about. Git-
   trackable topology wins on ADR-024 Principle V grounds.

5. **Unbounded trigger payloads.** Replay on a busy host would
   re-load megabytes per event. The 64 KB cap is the elbow where
   the common case (JSON webhook body) is fine and the uncommon
   case (CI log dump) gets explicit by-reference handling.

## Consequences

Positive:

- Kernel stays generic. No "coordinator code" — one engine, two
  example roles, a topology file. A third role (e.g. "reviewer") is
  a prompt file plus a tool whitelist; no engine change.
- Synthesis-mandate enforced by *absence of execution tools*, not
  trust. The coordinator literally cannot reach for `Bash`; the
  tool is not in the schema the model sees.
- Event ingress consolidates four integration shapes behind one
  type. A new trigger source is one listener file; dispatch and
  subscription matching do not change.
- Correlation IDs end-to-end. Every Task, listener, and
  `<task-update>` carries one. Debugging duhwave is one `grep`
  away, not three log files.
- Composes with ADR-028 RLM and ADR-029 multi-agent: coordinator
  reads handles via `Peek`, delegates via `Spawn`, never sees bulk
  content; workers run the full RLM substrate.

Negative / tradeoffs:

- The coordinator role is more constrained than ADR-063's
  `CoordinatorEngine` was. A user who wants the coordinator to
  "just read this file directly" can't — the intended path is
  `Peek` an RLM handle or `Spawn` a worker. Some users will be
  annoyed once and adapt.
- Depth-1 forecloses lead-of-leads topologies. ADR-032 will reopen
  one level explicitly with named roles and budget caps; it will
  not reopen unbounded recursion.
- Event ingress introduces a long-running process. One-shot users
  unaffected; duhwave users now manage a daemon — start, stop, log
  rotation, port conflicts. Lifecycle in ADR-032.
- The 64 KB payload cap is arbitrary and will be wrong for
  someone. We will move it when three independent users hit it for
  distinct reasons.
- Persistent listener server is a small attack surface. HMAC on
  every webhook source is required; default bind is `127.0.0.1` so
  users must opt in to public exposure.

## Migration

ADR-063's `--coordinator` flag continues to work. Internally it now
selects the `coordinator` role rather than constructing a
`CoordinatorEngine`. Sessions resumed from before this ADR are
treated as `worker` role unless re-flagged.

ADR-063's `SwarmTool` is removed; its behaviour is recovered by
issuing multiple `Spawn` calls in one coordinator turn, which the
kernel runs concurrently. Migration for custom coordinator scripts:
replace one `SwarmTool` call with N `Spawn` calls.

Event ingress requires the duhwave host. The host is launched with
`duh wave start` (defined in ADR-032), **not** by `duh "do thing"`
or `duh --coordinator`. One-shot users see no change.

## Tests

After this ADR lands:

- `tests/unit/test_role.py` — `Role` shape, tool-whitelist filter at
  session start, coordinator role missing `Bash` from registered
  tools, worker role hitting `RoleError` on `Spawn`.
- `tests/unit/test_coordinator_prompt.py` — synthesis-mandate text
  present, decision table present, 'show-me' fragment auto-appended
  unless `skip_show_me`.
- `tests/unit/test_task_update_block.py` — `<task-update>` shape,
  multi-update batching, malformed-completion handling.
- `tests/unit/test_trigger.py` — immutability, 64 KB cap, `raw_ref`
  indirection, `correlation_id` propagation.
- `tests/unit/test_listeners_*.py` — one per listener; webhook HMAC,
  filewatch debounce, cron drift bounds, MCP-push subscription
  parsing, manual-socket round-trip.
- `tests/unit/test_dispatch.py` — subscription matching across
  zero/one/many cases, idempotency window dedup, throttle.
- `tests/integration/test_coordinator_e2e.py` — coordinator session
  against a synthetic codebase: must spawn ≥ 2 workers in parallel,
  every worker prompt includes file paths and line numbers, final
  response cites worker output (not paraphrase).
- `tests/integration/test_wave_ingress_e2e.py` — manual trigger
  fires, dispatch matches subscription, Task spawns, coordinator
  runs, workers return, event log shows one `correlation_id`
  end-to-end.

## Follow-up

- **ADR-032** — swarm topology file format and `duh wave start`
  lifecycle: how roles, subscriptions, and rate limits are declared
  together, and how the host process is supervised.
- **ADR-033 candidate** — multi-host duhwave with consensus-driven
  trigger ownership. Out of scope for the laptop case.
- **Synthesis-mandate telemetry** — measure how often workers hit
  the 'show-me' fragment with a wrong repeat-back. If rare, the
  `skip_show_me` opt-out is unnecessary; if common, the coordinator
  prompt needs another iteration.
- **Role-specific RLM policy** — coordinator's RLM activation
  threshold may differ from a worker's. ADR-028's policy table
  gains a `role` column when the data argues for it.
