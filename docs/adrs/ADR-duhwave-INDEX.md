# duhwave ADR set — index + implementation status

Five ADRs that define D.U.H.'s persistent agentic-swarm extension.
All accepted on 2026-05-01; all implemented in `duh/duhwave/`; all
covered by unit + integration tests.

| # | Title | Implementation | Tests | Demo |
|---|---|---|---|---|
| **028** | RLM context engine | `duh/duhwave/rlm/` (REPL subprocess, 6 ops + Recurse, sandbox, snapshot/restore) | `tests/unit/test_duhwave_rlm_*.py` (50+ tests) | `examples/duhwave/01_rlm_demo.py` |
| **029** | Recursive cross-agent links | `duh/duhwave/coordinator/{spawn,view,tool_filter}.py` (Spawn, RLMHandleView, Role-aware tool filter) | `tests/unit/test_duhwave_coordinator_*.py` (32 tests) | `examples/duhwave/02_swarm_demo.py` |
| **030** | Persistent Task lifecycle, three execution surfaces | `duh/duhwave/task/{registry,executors}.py` (Task, 5-state machine, in-process + subprocess, orphan recovery) | `tests/unit/test_duhwave_task_*.py` + `test_duhwave_orphan_recovery.py` (40+ tests) | exercised by every demo |
| **031** | Coordinator-as-prompt-role + event ingress | `duh/duhwave/coordinator/role.py` + `duh/duhwave/ingress/*` + `duh/duhwave/cli/dispatcher.py` (5 listeners, matcher, trigger-to-spawn pump) | `tests/unit/test_duhwave_triggers.py`, `test_duhwave_listeners.py`, `test_duhwave_matcher.py` (29 tests) | `examples/duhwave/03_event_driven.py` |
| **032** | Swarm topology DSL + bundles + control plane | `duh/duhwave/{spec,bundle,cli}/` (TOML parser, Ed25519 signing, 10-subcommand CLI, Unix-socket RPC, daemon with HostState) | `tests/unit/test_duhwave_{spec,bundle,cli}_*.py` + `tests/integration/test_duhwave_daemon_ops.py` (89 tests) | `examples/duhwave/04_topology_bundle.py` + `examples/duhwave/repo_triage/main.py` |

## Test totals

```
tests/unit/test_duhwave_*.py        — 319 passed, 4 skipped
tests/integration/test_duhwave_*.py —  24 passed
─────────────────────────────────────────────────────────────
                                       343 passed, 0 failed
```

## End-to-end demonstrations

| Demo | What it proves | Requires API key |
|---|---|---|
| `examples/duhwave/01_rlm_demo.py` | RLM substrate single-agent | no |
| `examples/duhwave/02_swarm_demo.py` | Cross-agent handle-passing | no |
| `examples/duhwave/03_event_driven.py` | Webhook → trigger → match | no |
| `examples/duhwave/04_topology_bundle.py` | Pack → install → daemon → manual seam | no |
| `examples/duhwave/repo_triage/main.py` | Full multi-agent showpiece (stub workers) | no |
| `examples/duhwave/parity_hermes/run_all.py` | Hermes feature parity (5 patterns) | no |
| `examples/duhwave/parity_claw/run_all.py` | Always-on multi-channel (4 channels) | no |
| `examples/duhwave/agile_team/main.py` | 5-agent agile-team headless run | optional |
| `examples/duhwave/telegram_assistant/main.py` | Mock Telegram bus + real OpenAI | yes |
| **`examples/duhwave/real_e2e/main.py`** | **Daemon-driven webhook → real OpenAI agent → outbox** | yes |

The last one is the load-bearing real-usecase demo: a real daemon
subprocess with the dispatcher attached, fired by a real HTTP webhook,
running a real OpenAI completion, with the reply observable in a
real outbox file. Event log shows the full lifecycle:

```
host.start             — loaded swarm real-e2e 0.1.0
host.dispatcher_ready  — runner=openai_text_runner
trigger.spawned        — corr=ce46280a task=real-e2e-0.1.0:000001 agent=support_agent
trigger.completed      — corr=ce46280a task=real-e2e-0.1.0:000001 out=821b
```

## What's now shipping (previously deferred)

These items were follow-ups in the original ADR set; all five have
landed since:

| Item | Where | Status |
|---|---|---|
| `Recurse` self-cycle detection | `rlm/_bootstrap.py` `op_recurse_validate` | Lineage check + depth bound enforced at the wire |
| `--follow` true streaming for `duh wave logs` | `cli/daemon.py` streaming RPC shape, `cli/commands.py` client | Newline-delimited JSON over the host socket |
| Webhook HMAC verification | `ingress/webhook.py` | `X-Duh-Signature: sha256=…` checked with `hmac.compare_digest`; per-prefix secrets supported |
| Daemon auto-starting listeners | `cli/daemon.py` | Listeners boot from each swarm's `IngressSpec` at host start |
| Remote `TaskExecutor` surface | `task/remote.py`, `task/remote_server.py` | HTTP+bearer client + server, full lifecycle round-trip |

## What's still deferred

| Item | Where | Reason |
|---|---|---|
| MCP push-listener subscription path | ADR-031 §B / `ingress/mcp_push.py` | Stub class lands; depends on MCP client exposing notification API |

## Key dependencies between ADRs

```
   ADR-028 (RLM substrate)
     │
     ├─→ ADR-029 (cross-agent links — exposes handles via RLMHandleView)
     │
     └─→ ADR-031 (coordinator role + ingress — uses Peek/Search/Slice tools)

   ADR-030 (Task primitive) — independent infrastructure
     │
     └─→ ADR-031 (Spawn produces Tasks; trigger dispatcher consumes)
     │
     └─→ ADR-032 (HostState wraps a TaskRegistry)

   ADR-031 (coordinator + ingress)
     │
     └─→ ADR-032 (topology declares triggers + agents; daemon consumes)

   ADR-032 (topology + bundles + CLI) — composes all four
```

## Cited literature (load-bearing only)

- **Recursive Language Models** — Zhang, Kraska, Khattab. arXiv 2512.24601 (Jan 2026). Underpins ADR-028's "prompt-as-variable" substrate.
- **Recursive Multi-Agent Systems** — Yang, Zou, Pan et al. arXiv 2604.25917 (April 2026). Underpins ADR-029's variable-handle cross-agent passing (the "RecursiveLink" mechanism).

No other external systems are cited as design sources; all five ADRs
are first-principles syntheses of the literature + D.U.H.'s
existing primitives.

## How to verify

```bash
# Test sweep (~7s)
cd /Users/nomind/Code/duh
.venv/bin/python3 -m pytest tests/unit/test_duhwave_*.py tests/integration/test_duhwave_*.py

# Stub-only demos (no key needed)
for f in examples/duhwave/0[1-4]_*.py examples/duhwave/repo_triage/main.py \
         examples/duhwave/parity_hermes/run_all.py \
         examples/duhwave/parity_claw/run_all.py \
         examples/duhwave/agile_team/main.py; do
  .venv/bin/python3 "$f" "Add a token-bucket rate limiter to utils.py"
done

# Real e2e (requires OPENAI_API_KEY)
export OPENAI_API_KEY=sk-proj-...
.venv/bin/python3 examples/duhwave/real_e2e/main.py
.venv/bin/python3 examples/duhwave/agile_team/main.py \
    "Add a token-bucket rate limiter to utils.py" \
    --use-openai --openai-model gpt-4o-mini
.venv/bin/python3 examples/duhwave/telegram_assistant/main.py
```

## Verification quick-start

The exact commands a maintainer runs to confirm the duhwave surface
is healthy on a fresh checkout:

```bash
cd /Users/nomind/Code/duh

# 1. Full duhwave test sweep (unit + integration)
.venv/bin/python3 -m pytest tests/unit/test_duhwave_*.py \
                            tests/integration/test_duhwave_*.py -q

# 2. Stub-mode showpiece (deterministic, no API key)
.venv/bin/python3 examples/duhwave/repo_triage/main.py

# 3. Headline real-runner demo (needs OPENAI_API_KEY)
export OPENAI_API_KEY=sk-proj-...
.venv/bin/python3 examples/duhwave/real_e2e/main.py

# 4. Agile-team benchmark (5-stage pipeline, real OpenAI)
.venv/bin/python3 examples/duhwave/agile_team/main.py \
    "Add a token-bucket rate limiter to utils.py" \
    --use-openai --openai-model gpt-4o-mini \
    --out-dir /tmp/duhwave-agile-mini
# See benchmarks/duhwave-agile/RESULT.md for the reference numbers
# (gpt-4o-mini: 5/5 stages, 35.5s wall, $0.0015 per run).

# 5. Web-control plane smoke (background daemon + RPC)
.venv/bin/python3 examples/duhwave/04_topology_bundle.py
```

Expected outcome: every pytest passes, every stub demo prints its
end-to-end summary in <5 s, and every real-runner demo lands its
artefacts in the configured `--out-dir`.
