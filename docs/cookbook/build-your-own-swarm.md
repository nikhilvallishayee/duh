# Build your own swarm on D.U.H. — a duhwave walkthrough

This cookbook is the swarm-shaped successor to
[`build-your-own-agent.md`](./build-your-own-agent.md). The single-agent
cookbook walks you through a Hermes-style coding agent: one model, one
loop, four opinions layered on top of D.U.H.'s primitives. This one
walks you through a *swarm*: a persistent multi-agent layer where the
loop is a host daemon, the trigger is a webhook (or a file change, or
a cron tick), and the "agent" is one of several roles operating on a
shared substrate of variable handles.

By the end you will have several runnable swarms. The headline
demonstration is `examples/duhwave/real_e2e/` — a real daemon
subprocess with the dispatcher attached, fired by a real HTTP
webhook, running a real OpenAI completion, with the reply observable
in a real outbox file. Together with `examples/duhwave/repo_triage/`
(stub-runner showpiece, no API key needed) and the `agile_team/`
benchmark, the cookbook walks through swarms that:

- Listen for external events (webhooks, file changes, cron, MCP push).
- Spawn a coordinator Task on each match.
- Delegate to specialised workers via handle exposure, never prose
  summaries.
- Persist every Task to disk, lifecycle them through a real state
  machine, and survive a daemon restart.
- Declare the whole topology in one TOML file, packed and signed as a
  `.duhwave` bundle.

D.U.H.'s `duhwave` extension provides every primitive; this cookbook
is glue plus a ~400 LOC example file.

---

## 0. What you actually need

```bash
pip install duh-cli
```

For the runnable example, also:

```bash
pip install watchfiles aiohttp croniter cryptography
```

`watchfiles`/`aiohttp`/`croniter` power the three real ingress
listeners; `cryptography` gates the optional `.duhwave` bundle
signing path. The architecture works without them — the demo's
`main.py` only exercises the parts that don't need them — but real
deployments install the lot.

You also need an API key for whichever model(s) the swarm uses
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.). The runnable example
ships with stub workers that return canned strings, so you can see
the whole swarm move end-to-end without spending a cent.

---

## 1. Mental model — what a swarm is, vs a single agent

A single-agent loop is four things (see the agent cookbook): an
adapter, a tool registry, a dispatcher, a context manager. Loop until
done; exit.

A swarm adds five things on top:

1. **A persistent host process.** The swarm is alive past any single
   user-facing CLI invocation. It accepts work from external triggers,
   not just a user prompt.
2. **An event-ingress layer.** Webhooks, file watches, cron, and a
   manual seam all materialise as one normalised `Trigger` type. A
   trigger may match zero, one, or many subscriptions; each match
   spawns a Task.
3. **A persistent Task primitive.** A Task is a record on disk with a
   state machine. It has identity (`session-id:seq`), a parent, an
   output log, a tool allowlist, and a surface (in-process, subprocess,
   or remote). Tasks survive host restarts.
4. **Recursive cross-agent links.** A coordinator owns one REPL
   substrate per session; workers get **read-only views** into that
   REPL scoped by an explicit exposure list. Worker output binds back
   as a new handle in the coordinator's REPL — addressed by reference,
   never by summary.
5. **A topology DSL.** The whole swarm shape — agents, models, tools,
   triggers, edges, budget — is one TOML file. Audit-able. Diffable.
   Sharable as a `.duhwave` bundle.

Two academic papers gave us the shape:

- **RLM** ([arXiv 2512.24601][rlm], January 2026) — the prompt-as-
  variable substrate. Treat large inputs as Python REPL variables;
  give the agent `Peek` / `Search` / `Slice` over named handles
  instead of feeding bytes inline. ADR-028 is D.U.H.'s implementation.
- **RecursiveMAS** ([arXiv 2604.25917][rmas], April 2026) — the
  cross-agent extension. A coordinator's REPL is the *single*
  substrate for the session; workers get read-only views; their
  output binds back as new handles. Reported deltas vs prose-handoff
  multi-agent baselines: +8.3% accuracy, 2.4× speedup, 75.6% fewer
  tokens. ADR-029 is D.U.H.'s implementation.

The architecture below threads through both.

[rlm]: https://arxiv.org/abs/2512.24601
[rmas]: https://arxiv.org/abs/2604.25917

---

## 2. Walkthrough — the duhwave primitives, ported

Six primitives compose into the swarm. We'll walk them bottom-up; each
one is small, each one is independently testable.

### 2.1 RLM substrate — the REPL is the working memory (ADR-028)

The coordinator owns one Python REPL subprocess per session. Bulk
inputs (the codebase, the spec, the trigger payload) bind to *named
variables* inside the REPL. The agent never sees the bytes inline —
it sees a system block listing the handles, and operates via the five
RLM tools.

```python
import asyncio
from duh.duhwave.rlm.repl import RLMRepl

async def demo() -> None:
    repl = RLMRepl()
    await repl.start()

    # Bind a large value. The bytes never enter the agent's context.
    fake_repo = "src/auth.py:1: def authenticate(user, password):\n" * 5000
    await repl.bind("repo_handle", fake_repo)

    # The agent's view: just a handle summary.
    print(repl.handles.system_block())
    # ─── Output (truncated) ──
    # You have a Python REPL with these variables loaded:
    #   repo_handle  (str, 235,000 chars, 5,000 lines)  bound_by=user
    # Use Peek / Search / Slice / Recurse / Synthesize tools to interact.
    # The full content is addressable; nothing has been summarised.

    # The agent peeks an exact byte range — what's between [start, end).
    head = await repl.peek("repo_handle", start=0, end=80)
    print(head)
    # → "src/auth.py:1: def authenticate(user, password):\nsrc/auth.py:1: def..."

    # Or searches it. Pattern is regex; hits include line numbers.
    hits = await repl.search("repo_handle", r"def authenticate", max_hits=3)
    print(hits[0])  # → {"line": 1, "col": 15, "snippet": "..."}

    await repl.shutdown()

asyncio.run(demo())
```

The REPL is a sandboxed `python3 -I` subprocess: no network, no
shell, curated stdlib only, with a memory ceiling enforced via
`RLIMIT_AS`. See ADR-028's "Sandboxing" section for the full surface;
the security boundary is in `duh/duhwave/rlm/_bootstrap.py`.

**Why this matters:** every byte stays addressable. The agent can
re-read what it forgot. Compaction (summarise older turns into prose,
drop the originals) cannot.

### 2.2 Persistent Task lifecycle (ADR-030)

A Task is a record on disk with a state machine. Forward-only
transitions, one writer (the executor), every transition emits an
event. The dataclass:

```python
from duh.duhwave.task.registry import (
    Task, TaskRegistry, TaskStatus, TaskSurface,
)

# A registry is per-session. It persists JSON records under
# <session_dir>/tasks/<task_id>.json and output to .log.
registry = TaskRegistry(session_dir=Path("./demo-session"), session_id="sess-A")

task = Task(
    task_id=registry.new_id(),               # → "sess-A:000001"
    session_id="sess-A",
    parent_id=None,                          # top-level
    surface=TaskSurface.IN_PROCESS,          # asyncio.Task in this process
    prompt="Investigate the auth bug.",
    model="anthropic/claude-haiku-4-5",
    tools_allowlist=("Read", "Grep"),
    expose_handles=("repo_handle",),
)
registry.register(task)
```

The state machine is enforced; illegal transitions raise:

```python
registry.transition(task.task_id, TaskStatus.RUNNING)
# ... some agent loop runs to completion ...
registry.transition(task.task_id, TaskStatus.COMPLETED, result="found the bug")

# Forward-only — terminal states are immutable.
registry.transition(task.task_id, TaskStatus.RUNNING)
# → TaskTransitionError: ... completed → running not allowed
```

Three execution surfaces share the lifecycle:
`InProcessExecutor` (lowest latency, shared memory),
`SubprocessExecutor` (isolated `python3 -I` child, survives parent
crashes), and `RemoteExecutor` (HTTP+bearer to a `RemoteTaskServer`,
both shipped in `duh.duhwave.task.remote`). All implement the
same `TaskExecutor` Protocol; the coordinator code does not bifurcate
on surface choice.

**Why this matters:** a subagent running when the host crashed is
re-attached on `--continue`, or recorded as orphaned with a clear
cause. No silent loss. The coordinator never blocks on a worker — it
polls the registry (or, later, subscribes to events on the bus).

### 2.3 Coordinator-as-prompt-role (ADR-031, part A)

The kernel runs **one engine**. There is no `CoordinatorEngine`
subclass, no special routing path, no sidecar metadata. A
"coordinator" is a `Role` — a frozen dataclass holding a system
prompt, a tool allowlist, and a `spawn_depth`:

```python
from duh.duhwave.coordinator import BUILTIN_ROLES, filter_tools_for_role

coord = BUILTIN_ROLES["coordinator"]
print(coord.tool_allowlist)
# → ('Spawn', 'SendMessage', 'Stop', 'Peek', 'Search', 'Slice')

print(coord.spawn_depth)
# → 1   (workers will inherit child_role with depth=0)

# At session start, the kernel filters the registered tool list to
# the role's allowlist BEFORE the first turn. Anything outside is not
# registered — the model never sees a schema for `Bash`.
class _Tool:
    def __init__(self, name): self.name = name

all_kernel_tools = [_Tool(n) for n in (
    "Read", "Edit", "Write", "Bash", "Glob", "Grep",
    "Spawn", "SendMessage", "Stop", "Peek", "Search", "Slice", "Recurse",
)]
visible = filter_tools_for_role(all_kernel_tools, coord)
print([t.name for t in visible])
# → ['Spawn', 'SendMessage', 'Stop', 'Peek', 'Search', 'Slice']
# No Bash. No Edit. No Write. The synthesis-mandate is enforced by
# *absence*, not by trust.
```

The synthesis-mandate (ADR-031 §A.3) lives in the coordinator system
prompt at `prompts/coordinator.md` in your bundle. Its core rule:
"You do not have execution tools. You delegate by writing precise
worker prompts and spawning workers." With no execution tools in the
schema, the coordinator's only path to "do something" is "write a
worker prompt that does it."

### 2.4 Recursive cross-agent links — `Spawn` over the REPL (ADR-029)

The coordinator's `Spawn` tool starts a child agent with a
selectively-exposed **view** into the coordinator's REPL. The
worker's final result text binds back as a new handle in the
coordinator's namespace.

```python
import asyncio
from duh.duhwave.rlm.repl import RLMRepl
from duh.duhwave.task.registry import TaskRegistry
from duh.duhwave.coordinator import BUILTIN_ROLES, RLMHandleView
from duh.duhwave.coordinator.spawn import Spawn
from duh.kernel.tool import ToolContext

async def demo_spawn() -> None:
    repl = RLMRepl()
    await repl.start()
    await repl.bind("repo_handle", "src/auth.py: def authenticate(): ...")
    await repl.bind("spec_handle", '{"issue": 1147, "files": ["src/auth.py"]}')

    registry = TaskRegistry(session_dir=Path("./demo"), session_id="sess-A")

    # The runner is YOUR code — it drives the agent loop. The Spawn
    # tool does not own an engine; it owns the bind-back protocol.
    async def my_worker_runner(task, view):
        # `view` is an RLMHandleView scoped to this worker's exposure list.
        # Real runner: drive duh.kernel.engine.Engine here.
        # Demo runner: peek what's exposed and return canned text.
        names = view.list_exposed()
        sample = await view.peek(names[0], start=0, end=40)
        return f"Found '{sample}' in {names[0]}; 1 candidate."

    spawn = Spawn(
        repl=repl,
        registry=registry,
        parent_role=BUILTIN_ROLES["coordinator"],
        session_id="sess-A",
        parent_model="anthropic/claude-opus-4-7",
    )
    spawn.attach_runner(my_worker_runner)        # ← the runner-injection seam

    ctx = ToolContext(session_id="sess-A", tool_name="Spawn")
    result = await spawn.call({
        "prompt": "research: find every authenticate() call site",
        "expose": ["repo_handle", "spec_handle"],
        "bind_as": "findings_a",
    }, ctx)

    print(result.metadata)
    # → {'task_id': 'sess-A:000001', 'bind_as': 'findings_a',
    #    'status': 'completed', 'summary': "Found 'src/auth.py: def authenticate' ..."}

    # The coordinator now Peeks the bound handle — same operation as any
    # other REPL variable, no re-reading from disk.
    findings = await repl.peek("findings_a", start=0, end=80)
    print(findings)
    # → "Found 'src/auth.py: def authenticate' in repo_handle; 1 candidate."

    await repl.shutdown()

asyncio.run(demo_spawn())
```

The cross-worker handoff is the load-bearing part: on the *next* turn,
the coordinator can issue another Spawn that exposes `findings_a` to
a different worker. The implementer worker reads the researcher's
result as a handle in its own view — no prose paraphrase, no
re-derivation, no token cost beyond the single bind.

The worker's view is a `MappingProxyType`-style read-only wrapper.
Calls referencing handles outside the exposure list raise
`ValueError("handle not exposed: ...")` *before* reaching the
underlying REPL, so the worker's view is its complete attack surface.

**Why this matters:** Yang, Zou, Pan et al. (RecursiveMAS, April
2026) report 75.6% fewer tokens end-to-end on multi-agent tasks
versus prose-handoff baselines. The mechanism is the same as ADR-028:
*address by reference, never by summary*, applied across an agent
boundary instead of across a turn boundary.

### 2.5 Event ingress — triggers, listeners, the matcher (ADR-031, part B)

External events normalise into a single immutable `Trigger`:

```python
from duh.duhwave.ingress import Trigger, TriggerKind, TriggerLog
from pathlib import Path

log = TriggerLog(Path("./host_dir/triggers.jsonl"))

# A webhook would synthesise this from an HTTP POST; a filewatch
# from a watchfiles batch; cron from croniter. Same shape every time.
tr = Trigger(
    kind=TriggerKind.WEBHOOK,
    source="/github/issue",
    payload={"action": "opened", "issue": {"number": 1147}},
)
log.append(tr)        # at-least-once: log BEFORE dispatch
```

Five listeners ship in `duh.duhwave.ingress`:

- `WebhookListener`  — aiohttp, bound to `127.0.0.1` by default.
- `FileWatchListener`— `watchfiles` wrapper, debounced (default 500ms).
- `CronListener`     — `croniter` driving wall-clock fires.
- `MCPPushListener`  — subscribes to MCP `notifications/*` channels.
- `ManualSeam`       — Unix socket; the test path every other listener
  is exercised against in unit tests.

Subscriptions are declared in the topology — never registered
programmatically:

```python
from duh.duhwave.ingress import SubscriptionMatcher
from duh.duhwave.spec import parse_swarm

spec = parse_swarm("examples/duhwave/repo_triage/swarm.toml")
matcher = SubscriptionMatcher.from_spec(spec)

target = matcher.route(tr)        # → "coordinator"
```

Routing is **fnmatch glob match on `(kind, source)`** with first-match-wins. Topologies are hand-authored; subscription order is authorial.

### 2.6 Topology DSL + bundles (ADR-032)

A swarm is **one TOML file**:

```toml
# swarm.toml
secrets = ["GITHUB_TOKEN"]

[swarm]
name           = "repo-triage"
version        = "0.1.0"
description    = "Watch a repo. Triage on new issues."
format_version = 1

[[agents]]
id            = "coordinator"
role          = "coordinator"
model         = "anthropic/claude-opus-4-7"
tools         = ["Spawn", "SendMessage", "Stop", "Peek", "Search", "Slice"]
expose        = ["repo_handle", "spec_handle"]
system_prompt = "prompts/coordinator.md"

[[agents]]
id            = "researcher"
role          = "worker"
model         = "anthropic/claude-haiku-4-5"
tools         = ["Read", "Grep", "Glob", "Peek", "Search", "Slice"]
system_prompt = "prompts/researcher.md"

[[agents]]
id            = "implementer"
role          = "worker"
model         = "anthropic/claude-sonnet-4-6"
tools         = ["Read", "Edit", "Write", "Bash", "Glob", "Grep", "Peek", "Search", "Slice"]
system_prompt = "prompts/implementer.md"

[[triggers]]
kind            = "webhook"
source          = "/github/issue"
target_agent_id = "coordinator"

[[triggers]]
kind            = "filewatch"
source          = "./watch_dir"
target_agent_id = "coordinator"

[[edges]]
from_agent_id = "coordinator"
to_agent_id   = "researcher"
kind          = "spawn"

[[edges]]
from_agent_id = "coordinator"
to_agent_id   = "implementer"
kind          = "spawn"

[budget]
max_concurrent_tasks = 3
max_tokens_per_hour  = 500_000
max_usd_per_day      = 5.00
```

Parse it:

```python
from duh.duhwave.spec import parse_swarm

spec = parse_swarm("swarm.toml")
print([a.id for a in spec.agents])     # → ['coordinator', 'researcher', 'implementer']
print(spec.budget.max_usd_per_day)     # → 5.0
```

Pack it into a deterministic, signable ZIP:

```python
from pathlib import Path
from duh.duhwave.bundle import pack_bundle

# Source dir must contain manifest.toml, swarm.toml, permissions.toml.
# prompts/ and skills/ subdirs are included if present.
bundle = pack_bundle(
    spec_dir=Path("examples/duhwave/repo_triage/"),
    out_path=Path("./repo-triage-0.1.0.duhwave"),
)
```

Bundles are **deterministic**: sorted entries, fixed mtime. Re-packing
the same source produces byte-identical output — which is what makes
detached Ed25519 signatures meaningful.

Install it:

```python
from duh.duhwave.bundle import BundleInstaller

installer = BundleInstaller(root=Path.home() / ".duh" / "waves")
result = installer.install(bundle, force=True)
print(result.trust_level)              # → "unsigned" / "untrusted" / "trusted"
print(result.path)                     # → "~/.duh/waves/repo-triage/0.1.0/"
```

Unsigned bundles install with permissions downgraded to "ask every
time". Useful in dev, painful in prod, by design.

---

## 3. The runnable example — `examples/duhwave/repo_triage/`

Open the directory:

```
examples/duhwave/repo_triage/
├── manifest.toml          # bundle identity
├── swarm.toml             # topology DSL (above)
├── permissions.toml       # FS / network / tools envelope
├── prompts/
│   ├── coordinator.md     # synthesis-mandate prompt
│   ├── researcher.md      # read-only worker prompt
│   └── implementer.md     # full-execution worker prompt
├── main.py                # ~400 LOC end-to-end demo
└── README.md              # architecture overview
```

Run it:

```bash
cd /path/to/duh
.venv/bin/python3 examples/duhwave/repo_triage/main.py
```

The demo runs through seven stages in ~2 seconds:

1. **Build** the bundle from the spec dir.
2. **Install** into a tmp_path-rooted ~/.duh/waves (your real one is
   not touched).
3. **Start** the host daemon as a background subprocess.
4. **Trigger**: append a synthetic webhook trigger to the host's log;
   walk the matcher routing for three example triggers (one match,
   one match, one drop).
5. **Orchestrate**: the centerpiece. Boot a real RLMRepl, bind two
   handles, construct the coordinator role, instantiate the Spawn
   tool, attach a stub WorkerRunner, issue two Spawns (researcher
   then implementer-with-researcher-result-exposed), Peek every bound
   handle to verify round-trip.
6. **Inspect** via the host RPC over the Unix socket.
7. **Stop** the daemon, uninstall the bundle, print "demo complete".

The stub WorkerRunner returns canned strings, so the demo is
deterministic and free. The architecture is real — it's the same
RLMRepl, the same TaskRegistry, the same Spawn tool, the same
ingress matcher, the same daemon RPC that a real deployment would
use. The single thing that's stubbed is the model call: where a real
runner would invoke `duh.kernel.engine.Engine` against a live API,
the demo's runner returns text. For the live-model end-to-end arc
(daemon → webhook → real OpenAI call → outbox), see
`examples/duhwave/real_e2e/main.py`; for a five-stage agile-team
pipeline benchmarked against gpt-4o-mini and gpt-4o, see
`examples/duhwave/agile_team/main.py` and the result write-up at
[`benchmarks/duhwave-agile/RESULT.md`](../../benchmarks/duhwave-agile/RESULT.md).

To swap the stub for a real runner in any of these demos, use:

```python
# Real WorkerRunner — drop-in replacement for `stub_worker_runner` in main.py
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.deps import Deps
from duh.providers.registry import build_model_backend, infer_provider_from_model

async def real_worker_runner(task: Task, view: RLMHandleView) -> str:
    provider = infer_provider_from_model(task.model) or "anthropic"
    backend = build_model_backend(provider, task.model)
    deps = Deps(call_model=backend.call_model)
    cfg = EngineConfig(
        model=task.model,
        max_turns=task.metadata.get("max_turns", 5),
    )
    engine = Engine(deps=deps, config=cfg)

    # Render the worker's view as a system block so the model knows
    # what handles it can address.
    handle_lines = []
    for name in view.list_exposed():
        h = view.repl.handles.get(name)
        if h is not None:
            handle_lines.append(f"  {name}  ({h.kind}, {h.total_chars:,} chars)")
    system_block = (
        "You have a Python REPL view with these variables (read-only):\n"
        + "\n".join(handle_lines)
        + "\n\nUse Peek / Search / Slice to interact.\n"
    )
    full_prompt = system_block + "\n" + task.prompt

    result_text = ""
    async for event in engine.run(full_prompt):
        if event.get("type") == "text_delta":
            result_text += event.get("text", "")
        elif event.get("type") == "done":
            break
    return result_text
```

Wire it once, on the host's startup path:

```python
spawn_tool.attach_runner(real_worker_runner)
```

Everything else — the topology, the role, the bind-back, the
registry, the daemon, the RPC — stays as-is.

---

## 4. What this is *not*

- **Not a multi-agent framework like CrewAI / AutoGen.** Those
  prescribe orchestration patterns (sequential pipelines, debate
  loops, auctions). duhwave does not. It gives you persistence,
  event ingress, variable handles, role/tool filtering, and a control
  plane. The coordinator-with-two-workers shape in `repo_triage/` is
  one example; a fan-of-five-reviewers shape, a long-running
  watchdog, or a single agent with a webhook ingress are all equally
  idiomatic. *duhwave is harness-level, not orchestration-prescribing.*

- **Not a fork of D.U.H.** The example's `main.py` is ~400 LOC of
  glue calling `duh.duhwave` as a library. Same pattern for any
  custom swarm: write your own Python, import the primitives, run
  the host.

- **Not a re-implementation of the academic papers.** RLM and
  RecursiveMAS each ship with model fine-tunes, custom evaluation
  benchmarks, and their own training trajectories — those are
  separate concerns. This cookbook borrows the *substrate* patterns
  (prompt-as-variable, handle-passing instead of prose handoff), not
  the surface API.

- **Not the only way.** A single-agent harness is the right answer
  for one-shot interactive work — `duh "fix this bug"` exits when
  the work is done. duhwave is for the cases that single-agent
  harnesses can't do at all: persistence past one CLI invocation,
  event-driven spawning, multi-agent topologies with cross-agent
  handle references.

---

## 5. What to build on top

Once `repo_triage/` runs end-to-end, the obvious extensions:

- **Custom triggers.** A new ingress source is one async generator
  yielding `Trigger`s. ~150 LOC. Add to `duh/duhwave/ingress/`,
  declare the kind in the topology, you're done.
- **Custom roles.** Beyond `coordinator` and `worker`, define
  `reviewer`, `archiver`, `notifier`. Each is a system prompt + tool
  allowlist + spawn_depth. Add to `BUILTIN_ROLES` or load from
  topology via `Role.from_dict`.
- **Multi-tenancy.** `BundleInstaller` supports many bundles in one
  `~/.duh/waves` root. Per-bundle isolation is enforced by the
  permission gate (ADR-005); two swarms cannot read each other's
  state. Add a manifest field for `tenant_id`, route incoming
  webhooks to the right swarm by header.
- **Distributed swarms.** `RemoteExecutor` and `RemoteTaskServer`
  ship in `duh.duhwave.task.remote` — HTTP+bearer transport, the same
  state-machine + orphan-recovery semantics as the in-process surface.
  Wire any task with `surface=TaskSurface.REMOTE` to a `RemoteExecutor`
  and the rest of duhwave is unchanged.
- **Bundle registry.** Local-file and HTTPS install paths are
  shipped; a centralised registry (`duh wave install repo-triage`
  shorthand) is a deferred follow-up. ~500 LOC + a hosted index.

If you ship any of these, send a PR. The patterns generalise.

---

## See also

- [ADR-028](../../adrs/ADR-028-rlm-context-engine.md) — RLM context
  engine (the substrate this whole cookbook stands on).
- [ADR-029](../../adrs/ADR-029-recursive-cross-agent-links.md) —
  recursive cross-agent links (handle exposure, bind-back).
- [ADR-030](../../adrs/ADR-030-persistent-task-lifecycle.md) — Task
  primitive (state machine, three surfaces).
- [ADR-031](../../adrs/ADR-031-coordinator-prompt-role-event-ingress.md)
  — coordinator role + event ingress.
- [ADR-032](../../adrs/ADR-032-swarm-topology-bundles-control-plane.md)
  — topology DSL, bundle format, control plane.
- [`build-your-own-agent.md`](./build-your-own-agent.md) — the
  single-agent cookbook this one extends.

Cited papers (load-bearing):

- Zhang, Y.; Kraska, T.; Khattab, O. *Recursive Language Models.*
  arXiv:2512.24601, January 2026. <https://arxiv.org/abs/2512.24601>
- Yang, R.; Zou, K.; Pan, F.; et al. *Recursive Multi-Agent Systems.*
  arXiv:2604.25917, April 2026. <https://arxiv.org/abs/2604.25917>
