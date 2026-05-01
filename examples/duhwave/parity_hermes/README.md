# Hermes Agent → duhwave parity matrix

A runnable, hermetic engineering sketch — five short Python scripts,
each demonstrating one of the four headline opinions of
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
realised on top of duhwave primitives, plus the depth-1 invariant
that lets workers + coordinators share an iteration budget cleanly.

This is a **developer-facing parity demonstration**, not a user
journey. Each script is short, deterministic, requires no API keys,
and prints a one-line `✓` summary. `run_all.py` aggregates them into
the matrix below.

The original cookbook that introduced these patterns lives at
[`docs/cookbook/build-your-own-agent.md`](../../../docs/cookbook/build-your-own-agent.md).

## Parity matrix

| # | Hermes pattern | duhwave realisation | Script |
|---|---|---|---|
| 1 | Multi-mode native adapters (per-API-mode) | ADR-027 native adapters — 8 providers in `duh.providers.registry.PROVIDER_TIER_MODELS`, each with `small`/`medium`/`large` tier maps | `01_multimode_adapters.py` |
| 2 | `_repair_tool_call_arguments` — permissive JSON parser | `duh.adapters.tool_repair.repair_tool_arguments` (ADR-028 / cookbook §2.2). Round-trips trailing commas, Python literals, smart quotes, prose wrappers, control chars in strings, combined breakage | `02_tool_arg_repair.py` |
| 3 | `_PARALLEL_SAFE_TOOLS` allowlist + `_MAX_TOOL_WORKERS=8` | duhwave coordinator fan-out — multiple `Spawn` calls in one turn dispatched concurrently via `asyncio.gather`. Wall-clock ≈ longest-leg, not sum-of-legs | `03_parallel_dispatch.py` |
| 4 | `context_compressor` — threshold-based summarisation (50% trigger / 20% target) | ADR-028 RLM substrate **replaces** this — bytes addressed by reference (`Peek` / `Search` / `Slice`) over a sandboxed REPL handle, never summarised. Demonstrated by binding 500 KB and reading from byte 499,950 | `04_rlm_replaces_compaction.py` |
| 5 | `_active_children` + shared `IterationBudget` | duhwave coordinator role with `spawn_depth=1` (depth-1 invariant); workers cannot spawn workers. Plus a tiny `IterationBudget` dataclass mirroring the cookbook's example | `05_shared_budget.py` |

## How to run

```bash
cd /Users/nomind/Code/duh
.venv/bin/python3 examples/duhwave/parity_hermes/run_all.py
```

Or run a single demo:

```bash
.venv/bin/python3 examples/duhwave/parity_hermes/01_multimode_adapters.py
.venv/bin/python3 examples/duhwave/parity_hermes/02_tool_arg_repair.py
.venv/bin/python3 examples/duhwave/parity_hermes/03_parallel_dispatch.py
.venv/bin/python3 examples/duhwave/parity_hermes/04_rlm_replaces_compaction.py
.venv/bin/python3 examples/duhwave/parity_hermes/05_shared_budget.py
```

Every script:

- Uses `from __future__ import annotations` and full type hints.
- Makes **no** network calls, **no** model calls. Stub runners only.
- Exits 0 on success; non-zero on any pattern failure.
- Prints a single final `✓` line that `run_all.py` aggregates into the matrix.

## What this demonstrates

The point is not "duhwave is identical to Hermes" — the point is
that every Hermes opinion has a clean realisation in duhwave's
primitives, and pattern (4) is a **strict generalisation**: where
Hermes summarises bytes when context fills, duhwave keeps every
byte addressable by reference and the agent only loads the slice
it needs into its working window.

A developer reading the five scripts top-to-bottom should be able
to map each Hermes pattern to the duhwave primitive that realises
it, with no cross-references, no hidden globals, and no surprises.

## Related ADRs

- [ADR-027](../../../../Tengu/tengu-cockpit/adrs/ADR-027-native-adapters-only.md) — native adapters per provider
- [ADR-028](../../../../Tengu/tengu-cockpit/adrs/ADR-028-rlm-context-engine.md) — RLM context engine + tool-arg repair
- [ADR-029](../../../../Tengu/tengu-cockpit/adrs/ADR-029-recursive-cross-agent-links.md) — coordinator/worker handle exposure
- [ADR-030](../../../../Tengu/tengu-cockpit/adrs/ADR-030-persistent-task-lifecycle.md) — Task registry + executors
- [ADR-031](../../../../Tengu/tengu-cockpit/adrs/ADR-031-coordinator-prompt-role-event-ingress.md) — Role / spawn_depth invariants
- [ADR-032](../../../../Tengu/tengu-cockpit/adrs/ADR-032-swarm-topology-bundles-control-plane.md) — swarm topology bundles
