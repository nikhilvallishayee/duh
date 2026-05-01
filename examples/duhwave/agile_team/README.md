# agile-team — duhwave's headline showpiece

A single CLI invocation triggers a 5-agent agile-team swarm to deliver
a feature end-to-end. **This is the demo that shows duhwave's value
proposition** — what no single-agent harness can do.

```
python examples/duhwave/agile_team/main.py "Add a token-bucket rate limiter to utils.py"
```

In ~0.04 seconds the demo runs the canonical agile pipeline:

```
       PM  ──▶  Architect  ──▶  Engineer  ──▶  Tester  ──▶  Reviewer
       │            │              │             │             │
       └────── coordinator's REPL handles flow between stages ─┘
```

…and writes six artefact files: a refined spec, an ADR, a working
Python module, a pytest suite, a review with verdict, and a synthesised
SUMMARY.md.

The architecture is real production duhwave code. The model calls are
deterministic stubs so the demo's output is byte-reproducible.

---

## What this proves

A single-agent harness has *one* loop: the same prompt, the same
context, the same model, working through the whole task. That pattern
breaks down on anything cross-cutting:

- the PM-vs-engineer mindset that catches missing acceptance criteria;
- the architect-vs-engineer split that forces design before code;
- the tester-vs-engineer adversarial check that catches bugs;
- the reviewer-vs-author distance that catches over-engineering.

**duhwave gets these for free.** Each role is a different system
prompt, a different tool allowlist, a different model size, and (in
production) a different temperature. They share state through the
coordinator's REPL — not through a shared context window.

This demo wires every duhwave primitive that makes that possible:

- a real `RLMRepl` (sandboxed Python subprocess) that holds the spec
  and the codebase as named handles;
- five real `Role` instances with stage-specific system prompts and
  tool whitelists;
- real `Spawn` calls with explicit `expose=[...]` lists per stage —
  the **selective handle exposure** boundary from ADR-029;
- real `RLMHandleView` (the worker's read-only window into the
  coordinator's REPL);
- real `TaskRegistry` recording every task's lifecycle;
- a real `InProcessExecutor` running each worker as an `asyncio.Task`;
- a coordinator role that obeys ADR-031 §A's **synthesis-mandate** —
  no Bash/Edit/Write tools, only delegation and inspection.

The runner-injection seam — `Spawn.attach_runner(...)` — is the only
place a real model call would land. Swap the stub router for one that
drives `duh.kernel.engine.Engine` and the same orchestration runs
against live agents.

---

## The agile-team mapping

| Stage | Role        | Handles received (`expose=`)            | Bound back as     |
| ----- | ----------- | --------------------------------------- | ----------------- |
| 1     | PM          | `spec`, `codebase`                      | `refined_spec`    |
| 2     | Architect   | `spec`, `refined_spec`, `codebase`      | `adr_draft`       |
| 3     | Engineer    | `refined_spec`, `adr_draft`, `codebase` | `implementation`  |
| 4     | Tester      | `refined_spec`, `implementation`        | `test_suite`      |
| 5     | Reviewer    | `adr_draft`, `implementation`, `test_suite` | `review_notes`|

Two properties to notice:

1. **Each stage sees only what it needs.** The Tester does not see the
   ADR (the test suite covers the spec, not the design); the Reviewer
   does not see the original user prompt (it reviews against the
   refined spec the PM produced). This is enforced at the boundary —
   the worker's `RLMHandleView` raises `ValueError` on any handle name
   not in `expose`.

2. **Cross-stage data flows through the coordinator only** (ADR-029
   §"Worker-to-worker via the coordinator only"). The Engineer cannot
   talk to the Architect directly; the Architect's ADR is bound back
   into the coordinator's REPL, then re-exposed to the Engineer on the
   next `Spawn`. This makes the dataflow *auditable* — every handle
   passing the boundary appears in the `TaskRegistry`.

---

## How to run

```bash
# Default: write artefacts to ./out_run/ alongside main.py.
python examples/duhwave/agile_team/main.py "Add a token-bucket rate limiter to utils.py"

# Custom output directory:
python examples/duhwave/agile_team/main.py "<your prompt>" --out-dir ~/agile-run

# Quiet mode — emits only "<path> <size>" lines, suitable for scripting:
python examples/duhwave/agile_team/main.py "<prompt>" --quiet
```

The default `out-dir` is `./out_run/` next to the script. The session
working directory (where the `TaskRegistry` writes per-task JSON
records) is a `tempfile.TemporaryDirectory` that is cleaned up on exit.

---

## Files in this directory

| File                  | Purpose |
| --------------------- | ------- |
| `main.py`             | Headless entry point. ~500 LOC, well-commented. |
| `roles.py`            | Five specialised `Role` instances + `BUILTIN_AGILE_ROLES` registry. |
| `runners.py`          | Five deterministic stub `WorkerRunner` callables + the dispatch router. |
| `swarm.toml`          | Declarative topology — for documentation and future daemon-driven runs. |
| `verify_run.py`       | Regression check: runs `main.py` and diffs against `expected_output/`. |
| `expected_output/`    | Pinned reference outputs for byte-level regression detection. |
| `out_run/`            | Default output directory (gitignored — your runs land here). |

---

## The runner-injection point

This is where you would replace stub runners with real model calls.

In `runners.py`:

```python
async def pm_runner(task: Task, view: RLMHandleView) -> str:
    """Stub PM runner. Sees: spec, codebase. Returns: refined_spec."""
    await _peek_first_exposed(view)
    return _PM_OUTPUT
```

In a real deployment that body becomes:

```python
async def pm_runner(task: Task, view: RLMHandleView) -> str:
    engine = Engine(
        model=task.model,
        system_prompt=BUILTIN_AGILE_ROLES["pm"].system_prompt,
        tools=filter_tools_for_role(host_tools, BUILTIN_AGILE_ROLES["pm"]),
        rlm_view=view,
    )
    return await engine.run(task.prompt, max_turns=task.metadata["max_turns"])
```

The `Spawn` tool, the `RLMRepl`, the `TaskRegistry`, the
`InProcessExecutor`, and the coordinator's role-filter all stay
unchanged. **That is the point.** The demo's plumbing *is* the
production plumbing.

---

## Determinism contract

The demo's outputs are byte-reproducible:

```bash
python examples/duhwave/agile_team/main.py "..." --out-dir /tmp/run-a --quiet
python examples/duhwave/agile_team/main.py "..." --out-dir /tmp/run-b --quiet
diff -r /tmp/run-a /tmp/run-b   # silent: identical bytes
```

This holds because:

- the stub runners return literal canned strings (no timestamps, no
  PIDs, no random IDs in the output);
- task IDs are derived from a monotonic counter rooted at 1;
- the synthesis pass uses only those deterministic inputs.

`verify_run.py` enforces the contract — it diffs each fresh run against
`expected_output/`. Run it after any change to a stub or role prompt;
update `expected_output/` only when the drift is intentional.

---

## See also

- ADR-028 — RLM substrate (sandboxed Python REPL, the five operations).
- ADR-029 — Recursive cross-agent links: handle-passing, expose lists,
  worker-to-worker via the coordinator only.
- ADR-031 §A — Coordinator role + synthesis-mandate (no Bash/Edit/Write).
- ADR-032 — Topology DSL (`swarm.toml`).
- `examples/duhwave/02_swarm_demo.py` — the minimal Spawn end-to-end
  demo this showpiece extends.
- `examples/duhwave/repo_triage/` — the "watch a repo, react to events"
  variant; same primitives, different topology.
- `examples/duhwave/parity_hermes/` — the daemon-driven swarm demo.
