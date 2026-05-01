# Examples

Runnable demonstrations of every D.U.H. primitive. Each example is self-contained and prints a `✓` / `✗` summary; the duhwave demos all live under `examples/duhwave/`, the single-agent cookbook companion lives under `examples/hermes_style/`.

## Graduation order

Run them in this order to graduate from "RLM substrate" to "real-OpenAI persistent host":

1. `01_rlm_demo.py` — feel the substrate (handles, no model).
2. `02_swarm_demo.py` — feel the cross-agent boundary (coordinator + worker, stub).
3. `03_event_driven.py` — feel the ingress layer (real HTTP webhook, no model).
4. `04_topology_bundle.py` — feel the bundle + daemon (pack → install → daemon → manual seam).
5. `repo_triage/main.py` — feel the full architecture (everything wired, stub workers).
6. `parity_hermes/run_all.py` and `parity_claw/run_all.py` — feel the design space (5 + 4 patterns).
7. `agile_team/main.py` — feel the value proposition (5-stage pipeline; runs in stub mode by default).
8. `agile_team/main.py --use-openai` — switch the runner; same architecture, real model.
9. `telegram_assistant/main.py` — feel the persistent-process shape (3 flow types, real OpenAI, mock Telegram boundary).
10. `real_e2e/main.py` — the load-bearing real demo: real daemon, real webhook, real OpenAI, real outbox.
11. `examples/hermes_style/agent.py` — for contrast: the single-agent cookbook agent (no duhwave; just shows how the same primitives feel without persistence).

---

## duhwave — single-file walk-throughs

### `examples/duhwave/01_rlm_demo.py`

**Path**: `/Users/nomind/Code/duh/examples/duhwave/01_rlm_demo.py`
**Proves**: The RLM substrate (ADR-028) without any model. Bulk content binds as a *named variable* in a sandboxed Python REPL; the agent (us, in this script) interacts with it through `bind` / `peek` / `search` / `slice` / `snapshot` / `restore`. The agent's prompt stays small; the bulk lives in the REPL.
**Run**: `.venv/bin/python3 examples/duhwave/01_rlm_demo.py`
**Expected output (snippet)**:
```
You have a Python REPL with these variables loaded:
  repo_handle  (str, 235,000 chars, 5,000 lines)  bound_by=user
Use Peek / Search / Slice / Recurse / Synthesize tools to interact.
✓ RLM substrate end-to-end OK
```

### `examples/duhwave/02_swarm_demo.py`

**Path**: `/Users/nomind/Code/duh/examples/duhwave/02_swarm_demo.py`
**Proves**: Cross-agent handle-passing (ADR-029, ADR-031 §A). Three properties: (1) selective handle exposure — workers see only the names listed in `expose=`; access to other handles raises before reaching the underlying REPL; (2) runner injection seam — `Spawn` does not own an engine, the host injects a `WorkerRunner` callable; (3) result rebinding — the worker's output text binds back as a new handle in the coordinator's namespace.
**Run**: `.venv/bin/python3 examples/duhwave/02_swarm_demo.py`
**Expected output (snippet)**:
```
spawn result: {'task_id': 'sess-A:000001', 'bind_as': 'findings_a', 'status': 'completed'}
coordinator can now Peek findings_a: "Found 'src/auth.py: def authenticate' ..."
worker access to non-exposed handle raised: "handle not exposed: spec_handle"
✓ cross-agent handle-passing OK
```

### `examples/duhwave/03_event_driven.py`

**Path**: `/Users/nomind/Code/duh/examples/duhwave/03_event_driven.py`
**Proves**: Event-driven ingress (ADR-031 §B). HTTP POST → `WebhookListener` → `Trigger` record → `TriggerLog` (jsonl) → `SubscriptionMatcher` → routed agent_id. A real webhook listener binds on an OS-chosen free port; the demo POSTs three requests at three different URL paths; the matcher (built from a small in-line swarm spec) routes each landed trigger.
**Run**: `.venv/bin/python3 examples/duhwave/03_event_driven.py`
**Expected output (snippet)**:
```
POST /github/issue → matched: coordinator
POST /github/pr    → matched: coordinator
POST /unrelated    → dropped (no subscription)
✓ event ingress + matcher routing OK
```

### `examples/duhwave/04_topology_bundle.py`

**Path**: `/Users/nomind/Code/duh/examples/duhwave/04_topology_bundle.py`
**Proves**: The full ADR-032 control plane in one runnable script. Builds a `swarm.toml` + `manifest.toml` + `permissions.toml` in a tmp_path source tree, packs into a `.duhwave` bundle, installs into a tempdir-rooted `~/.duh/waves/`, spawns the daemon as a subprocess, pings the host RPC socket, sends a manual trigger, verifies it landed, then SIGTERMs and uninstalls.
**Run**: `.venv/bin/python3 examples/duhwave/04_topology_bundle.py`
**Expected output (snippet)**:
```
pack:    repo-triage-0.1.0.duhwave (3.2 KiB)
install: ~/.duh/waves/repo-triage/0.1.0/  trust=unsigned
daemon:  pid=53129  socket=<tmpdir>/host.sock
ping:    pong (host_uptime=0.04s)
manual seam fired → trigger landed in triggers.jsonl
SIGTERM → daemon exited cleanly
✓ pack → install → daemon → manual seam → uninstall OK
```

---

## duhwave — multi-script showpieces

### `examples/duhwave/repo_triage/main.py`

**Path**: `/Users/nomind/Code/duh/examples/duhwave/repo_triage/main.py`
**Proves**: The OSS showpiece for duhwave. Wires every primitive the ADRs (028–032) define into one ~400-line runnable demo: build the bundle from a fully-formed example tree (with `prompts/coordinator.md`, `prompts/researcher.md`, `prompts/implementer.md`), install into a tmp_path, start the daemon, send a synthetic GitHub-issue webhook trigger via the manual seam, walk the matcher routing, drive a coordinator Spawn that exposes selected handles to a researcher then an implementer (with the researcher's result bound back as a handle the implementer can read), inspect via the host RPC, then SIGTERM and uninstall.
**Run**: `.venv/bin/python3 examples/duhwave/repo_triage/main.py`
**Expected output (snippet)**:
```
[1/8] build:   ./repo-triage-0.1.0.duhwave  (signed=False)
[2/8] install: <tmp>/.duh/waves/repo-triage/0.1.0/
[3/8] start:   daemon pid=53129
[4/8] trigger: webhook /github/issue (#1147)
[5/8] match:   → coordinator (expose: repo_handle, spec_handle)
[6/8] orchestrate:
        spawn researcher  → bound 'findings_a' (412 chars)
        spawn implementer with expose=findings_a → bound 'plan_a' (873 chars)
[7/8] inspect: 2 tasks completed; 0 running; 0 orphaned
[8/8] stop + uninstall
✓ repo-triage end-to-end OK
```

### `examples/duhwave/parity_hermes/run_all.py`

**Path**: `/Users/nomind/Code/duh/examples/duhwave/parity_hermes/run_all.py`
**Proves**: Hermes feature-parity. Five short scripts, each demonstrating one of the four headline opinions of NousResearch's `hermes-agent` realised on duhwave primitives, plus the depth-1 invariant that lets workers + coordinators share an iteration budget. Demonstrates: multi-mode native adapters, tool-arg repair middleware, parallel-safe tool dispatch (coordinator fan-out), RLM-replaces-compaction (binds 500 KB and reads from byte 499,950), shared `IterationBudget` with `spawn_depth=1`.
**Run**: `.venv/bin/python3 examples/duhwave/parity_hermes/run_all.py`
**Expected output (snippet)**:
```
[1/5] 01_multimode_adapters.py     ✓
[2/5] 02_tool_arg_repair.py        ✓
[3/5] 03_parallel_dispatch.py      ✓
[4/5] 04_rlm_replaces_compaction.py ✓
[5/5] 05_shared_budget.py          ✓
Hermes parity matrix: 5/5 patterns realised on duhwave
```

### `examples/duhwave/parity_claw/run_all.py`

**Path**: `/Users/nomind/Code/duh/examples/duhwave/parity_claw/run_all.py`
**Proves**: Always-on multi-channel parity. Four scripts demonstrate the architectural shape OpenClaw is built for, mapped onto duhwave's native ingress kinds (webhook, filewatch, cron, manual). Demonstrates: persistent runtime, multi-channel ingress, per-channel routing via `SubscriptionMatcher.from_spec`, persistent state via append-only `triggers.jsonl` + `TriggerLog.replay()` on restart, crash-safety (replay survives SIGKILL), concurrent fan-in (shared O_APPEND log), per-skill isolation per installed bundle.
**Run**: `.venv/bin/python3 examples/duhwave/parity_claw/run_all.py`
**Expected output (snippet)**:
```
[1/4] 01_four_channels.py        ✓
[2/4] 02_persistent_state.py     ✓
[3/4] 03_concurrent_ingress.py   ✓
[4/4] 04_per_channel_isolation.py ✓
Clawbot parity matrix: 4/4 channels routed correctly
```

### `examples/duhwave/agile_team/main.py`

**Path**: `/Users/nomind/Code/duh/examples/duhwave/agile_team/main.py`
**Proves**: The headline duhwave showpiece. A single CLI invocation triggers a 5-agent agile-team swarm: PM → Architect → Engineer → Tester → Reviewer. Each stage spawns a worker via `Spawn` (ADR-029), reads exposed handles from the coordinator's RLM REPL, binds its result back. Six artefacts: `refined_spec.md`, `adr_draft.md`, `implementation.py`, `test_suite.py`, `review_notes.md`, `SUMMARY.md`. Stub-mode is byte-reproducible (~0.04 s wall, no network); `--use-openai` swaps to a real runner without any architectural change.

**Stub run**:
```
.venv/bin/python3 examples/duhwave/agile_team/main.py "Add a token-bucket rate limiter to utils.py"
```

**Real-OpenAI run** (5/5 stages, 35.5 s, **$0.0015** on gpt-4o-mini — see [benchmark](https://github.com/nikhilvallishayee/duh/blob/main/benchmarks/duhwave-agile/RESULT.md)):
```bash
export OPENAI_API_KEY=sk-proj-...
.venv/bin/python3 examples/duhwave/agile_team/main.py \
    "Add a token-bucket rate limiter to utils.py" \
    --use-openai --openai-model gpt-4o-mini --out-dir /tmp/bench-mini
```

**Expected output (snippet)**:
```
[pm]        ✓ refined_spec.md         (532 bytes)
[architect] ✓ adr_draft.md            (1.8 KiB)
[engineer]  ✓ implementation.py       (1.2 KiB)
[tester]    ✓ test_suite.py           (2.1 KiB)
[reviewer]  ✓ review_notes.md         (823 bytes)
[synthesise]✓ SUMMARY.md              (1.4 KiB)
agile-team end-to-end: 5/5 stages, 0.04s wall (stub mode)
```

### `examples/duhwave/telegram_assistant/main.py`

**Path**: `/Users/nomind/Code/duh/examples/duhwave/telegram_assistant/main.py`
**Proves**: Three message flows through one persistent duhwave process. **INBOUND** — a (mock) Telegram webhook posts an "update" → ingress trigger → real OpenAI agent → reply written to a mock outbox file. **SCHEDULED** — a timer fires every N seconds; the agent drafts a short "tip of the day"; tip lands in the outbox. **ON-DEMAND** — a manual seam fires; the agent produces a one-off message. Everything except the Telegram boundary is real (RLMRepl, TriggerLog, WebhookListener, OpenAI streaming via D.U.H.'s native adapter).
**Requires**: `OPENAI_API_KEY`
**Run**: `.venv/bin/python3 examples/duhwave/telegram_assistant/main.py`
**Expected output (snippet)**: 686 tokens total, ~$0.0002, ~14 s for 5 messages across three flow types; outbox at `<tmpdir>/telegram_outbox.jsonl`.

### `examples/duhwave/real_e2e/main.py`

**Path**: `/Users/nomind/Code/duh/examples/duhwave/real_e2e/main.py`
**Proves**: The load-bearing real-usecase demo. A real daemon subprocess with the dispatcher attached, fired by a real HTTP webhook, running a real OpenAI completion, with the reply observable in a real outbox file. The daemon walks the swarm's triggers and **auto-boots its own `WebhookListener`** on the `[ingress] webhook_port` declared in the topology (ADR-031 §B) — no in-process listener; the demo POSTs to the daemon's bound socket directly. Closes the duhwave loop end-to-end through real production code paths *including the persistent host process*.
**Requires**: `OPENAI_API_KEY`
**Run**: `.venv/bin/python3 examples/duhwave/real_e2e/main.py`
**Expected output (snippet)** — the full lifecycle from the host event log:
```
host.start             — loaded swarm real-e2e 0.1.0
host.dispatcher_ready  — runner=openai_text_runner
trigger.spawned        — corr=ce46280a task=real-e2e-0.1.0:000001 agent=support_agent
trigger.completed      — corr=ce46280a task=real-e2e-0.1.0:000001 out=821b
✓ real e2e OK — outbox has 1 reply
```

---

## Single-agent cookbook companion

### `examples/hermes_style/agent.py`

**Path**: `/Users/nomind/Code/duh/examples/hermes_style/agent.py`
**Proves**: The single-agent shape this whole extension *isn't*. A complete Hermes-style coding agent loop in one file, demonstrating four opinions ported onto D.U.H.'s primitives without any duhwave: multi-mode native adapters (D.U.H. provides per-provider adapters; pick one), tool-arg repair middleware (applied automatically via the OpenAI-shape adapter), parallel-safe tool dispatch (read-only tools run concurrently under a bounded thread pool), shared turn budget across parent + sub-agents. Useful for contrast: this is what one CLI invocation looks like; everything in `examples/duhwave/` lives above this.
**Run**: `.venv/bin/python3 examples/hermes_style/agent.py --model claude-opus-4-7 -p "Audit auth.py."`
**Companion docs**: [`docs/cookbook/build-your-own-agent.md`](https://github.com/nikhilvallishayee/duh/blob/main/docs/cookbook/build-your-own-agent.md)

---

## See also

- **[duhwave](Duhwave)** — the canonical wiki page for the persistent agentic-swarm extension.
- **[Multi-Agent Guide](Multi-Agent)** — `Agent` / `Swarm` tools (the simpler shape) plus a "duhwave swarms" subsection covering when to escalate.
- **[build-your-own-swarm cookbook](https://github.com/nikhilvallishayee/duh/blob/main/docs/cookbook/build-your-own-swarm.md)** — narrative walkthrough; the `repo_triage/` showpiece is the runnable target.
- **[duhwave-agile benchmark](https://github.com/nikhilvallishayee/duh/blob/main/benchmarks/duhwave-agile/RESULT.md)** — first real-OpenAI benchmark (5-stage pipeline at $0.0015 per run on gpt-4o-mini).
- **[ADR index](https://github.com/nikhilvallishayee/duh/blob/main/adrs/ADR-028-032-INDEX.md)** — implementation status table, demo cross-reference, dependency DAG.
