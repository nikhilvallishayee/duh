# repo-triage — a duhwave showpiece

A persistent, event-driven, multi-agent swarm built on the duhwave
substrate (ADRs 028–032). Watches a repository (or a local directory
standing in for one) and reacts to two kinds of events:

- **GitHub issue webhooks** (POST to `/github/issue`)
- **File changes** under `./watch_dir`

Each event spawns a coordinator Task. The coordinator decides whether
to delegate to a **researcher** (Haiku-class, read-only) or an
**implementer** (Sonnet-class, full execution), and what to expose
from its REPL to each worker.

This example demonstrates five things that single-agent harnesses
cannot do:

1. **Persistence** — the swarm runs as a long-lived daemon past a
   single CLI invocation.
2. **Event-driven ingress** — webhook + filewatch trigger spawns
   without a human typing a prompt.
3. **RLM-native context** — the codebase is loaded once into the
   coordinator's REPL; workers see selected handles, not prose
   summaries.
4. **Variable-handle passing across agents** — researcher findings
   bind back as handles the coordinator can `Peek` / `Search`, then
   selectively expose to the implementer on the next Spawn.
5. **Topology declared in one file** — the whole swarm shape is
   `swarm.toml`; no Python wiring required to share or audit it.

---

## Running the demo

```bash
cd /path/to/duh
.venv/bin/python3 examples/duhwave/repo_triage/main.py
```

The demo:

1. Builds the bundle from this directory.
2. Installs it into a temporary `~/.duh/waves` root (your real one is
   not touched).
3. Starts the host daemon as a background subprocess.
4. Sends a synthetic trigger via the manual seam — representing
   "new GitHub issue".
5. Walks through the matcher routing → coordinator-orchestration →
   handle-binding path with **stub WorkerRunners** that return canned
   strings, so the demo is deterministic and free.
6. Inspects the topology + state via the host's RPC interface.
7. Stops the daemon, uninstalls the bundle, prints a summary.

It does **not** make real LLM calls. The showpiece is the
*architecture* — the runner-injection point in `main.py` is where you
would plug in `duh.kernel.engine.Engine` to drive real models.

Expected output (last few lines):

```
[stop]      ✓ daemon stopped (exit 0)
[uninst]    ✓ uninstalled repo-triage
[done]      Demo complete. 7/7 stages OK.
```

Exit code: `0`.

---

## Files in this directory

| File | Purpose |
|------|---------|
| `manifest.toml`         | Bundle identity (name, version, author, signing). |
| `swarm.toml`            | The topology DSL — agents, triggers, edges, budget. |
| `permissions.toml`      | Declarative envelope (FS / network / tools). |
| `prompts/coordinator.md`| Synthesis-mandate role prompt. |
| `prompts/researcher.md` | Read-only worker role prompt. |
| `prompts/implementer.md`| Full-execution worker role prompt. |
| `main.py`               | The runnable end-to-end demo (~400 LOC, stubbed runners). |
| `README.md`             | This file. |

---

## How to make it real

Replace the stub runner in `main.py` with a real engine call:

```python
async def real_worker_runner(task: Task, view: RLMHandleView) -> str:
    from duh.kernel.engine import Engine, EngineConfig
    from duh.kernel.deps import Deps
    from duh.providers.registry import build_model_backend, infer_provider_from_model

    provider = infer_provider_from_model(task.model) or "anthropic"
    backend = build_model_backend(provider, task.model)

    deps = Deps(call_model=backend.call_model)
    cfg = EngineConfig(model=task.model, max_turns=task.metadata.get("max_turns", 5))
    engine = Engine(deps=deps, config=cfg)

    # Build the worker's view of the coordinator's REPL into its system
    # prompt. The handles in `view.list_exposed()` become a system block.
    system_block = render_view_as_system_block(view)
    full_prompt = system_block + "\n\n" + task.prompt

    result_text = ""
    async for event in engine.run(full_prompt):
        if event.get("type") == "text_delta":
            result_text += event.get("text", "")
        elif event.get("type") == "done":
            break
    return result_text
```

Wire it on the host's startup path, attach to each Spawn tool
instance, and the rest of the architecture stays as-is.

---

## What this is NOT

- **Not a fork of duhwave** — `main.py` is ~400 lines of glue calling
  duhwave as a library. Same pattern for any custom swarm: write your
  own Python, import the primitives, run the host.
- **Not a multi-agent framework** in the CrewAI/AutoGen sense.
  duhwave is harness-level: it gives you persistence, event ingress,
  variable handles, role/tool filtering, and a control plane. It does
  *not* prescribe a particular orchestration pattern. The
  coordinator-with-two-workers shape here is one example; a
  fan-of-five-reviewers shape, a pipeline shape, or a single
  long-running watchdog are all equally idiomatic.
- **Not the only way.** Topology is data. The `.duhwave` bundle is
  one immutable artefact; you could ship 12 of them on the same host.

---

## ADR references

The architecture in this directory composes five ADRs:

- **ADR-028** — RLM context engine (`repo_handle`, `spec_handle`,
  `Peek`/`Search`/`Slice`).
- **ADR-029** — Recursive cross-agent links (handle exposure on
  `Spawn`, result binding via `bind_as`).
- **ADR-030** — Persistent Task lifecycle (the coordinator and worker
  Tasks each have an id, status, output log, surface).
- **ADR-031** — Coordinator-as-prompt-role + event ingress (the
  `coordinator` role is data, not a subclass; the manual seam is the
  test path for webhook/filewatch).
- **ADR-032** — Topology DSL + bundle format + control plane (this
  whole directory).

For a step-by-step walkthrough of those primitives in code, see
`docs/cookbook/build-your-own-swarm.md`.
