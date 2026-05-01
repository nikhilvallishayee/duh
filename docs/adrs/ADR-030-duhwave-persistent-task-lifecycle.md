# ADR-030 — Persistent Task lifecycle: one abstraction, three execution surfaces

**Status:** Accepted (implemented)
**Date:** 2026-04-30 · 2026-05-01 (accepted)
**Scope:** `duh/kernel/task/`, `duh/kernel/registry/`, `duh/cli/spawn.py`
**Depends on:** ADR-019 (universal harness architecture), ADR-024 (design
principles), ADR-028 (RLM context engine), ADR-058 (resume modes),
ADR-063 (coordinator mode).

## Context

D.U.H. agents today are ephemeral. A turn starts; the model emits tool
calls; the kernel runs them; control returns; the loop continues. When
the host process exits — or crashes, or a child spawned by `AgentTool`
is still running — the work in flight is lost. There is no record of
"this subagent was halfway through scanning the repo when the parent
went away."

This is fine for one-shot interactive use. It is wrong for everything
duhwave wants to do next:

- **Coordinator mode** (ADR-063) spawns subagents and synthesises
  their results. Today the coordinator blocks on slow children, the
  host exit kills them with no record, and there is no way to *check
  on* a long-running child without holding it in memory.
- **Always-on duhwave servers** need to accept work from a remote
  client, run it for minutes or hours, and let the client reconnect.
  Today there is nothing to reconnect to.
- **Multi-agent swarms** need every member to be observable — name,
  state, output-so-far, terminal status. Today the only handle is the
  child's `asyncio.Task` object, invisible from outside the parent.
- **Resumption** (ADR-058) restores conversation history but not
  in-flight work. A subagent running when the host crashed becomes a
  ghost: no PID, no log, no record.

The unifying gap is the absence of a **persistent first-class
primitive for "a piece of agentic work."** Every subsystem above
papers over it differently. They should share one substrate.

The Task primitive — a record with id, status, prompt, output, parent,
timestamps — is what lets agency persist across process boundaries and
restarts. Once Tasks are first-class, the coordinator polls instead of
blocks, the duhwave server lists instead of forgets, and resumption
reattaches instead of orphans.

## Decision

D.U.H. introduces **`Task` as the persistent unit of agency.** A Task
is not a function call and not a turn — it is a record on disk with a
state machine, an output artifact, a parent, and a declared
capabilities boundary. It can be executed on any of three surfaces
sharing one lifecycle.

### Task data model

```python
# duh/kernel/task/model.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class TaskState(str, Enum):
    PENDING    = "pending"      # accepted, not yet started
    RUNNING    = "running"      # executor has begun
    COMPLETED  = "completed"    # terminal; result available
    FAILED     = "failed"       # terminal; error recorded
    KILLED     = "killed"       # terminal; cancelled by user/parent

class TaskSurface(str, Enum):
    IN_PROCESS = "in_process"   # asyncio.Task in host
    SUBPROCESS = "subprocess"   # forked python interpreter
    REMOTE     = "remote"       # duhwave HTTP endpoint

@dataclass
class Task:
    id: str                                  # "<session_id>:<seq>"
    session_id: str
    parent_id: Optional[str]                 # None for top-level
    surface: TaskSurface
    state: TaskState
    prompt: str
    agent_type: str                          # "coder", "researcher", ...
    model: str                               # resolved model spec
    tool_allowlist: list[str]                # capabilities boundary
    output_artifact: str                     # path to output.log
    result: Optional[str] = None             # short final string
    error: Optional[str] = None              # set when state=FAILED
    created_at: float = 0.0                  # epoch seconds
    started_at: Optional[float] = None
    terminated_at: Optional[float] = None    # set on COMPLETED/FAILED/KILLED
    metadata: dict = field(default_factory=dict)
```

The dataclass is the wire format. Persistence is JSON at
`<session_dir>/tasks/<task_id>.json`; output goes to a separate
append-only log at `<session_dir>/tasks/<task_id>/output.log`. The
record is small and rewritten on every state transition; the log
may grow large and is decoupled.

### Lifecycle

```
                      Spawn(prompt, surface, ...)
                              │
                              ▼
                       ┌─────────────┐
                       │   PENDING   │
                       └──────┬──────┘
                              │ executor accepts
                              ▼
                       ┌─────────────┐    Kill()
                       │   RUNNING   │──────────────────┐
                       └──────┬──────┘                  │
                  agent       │     agent               │
                  finishes    │     errors              │
                  cleanly     │     out                 │
                  ┌───────────┘                         │
                  ▼                                     ▼
           ┌────────────┐  ┌────────────┐       ┌────────────┐
           │ COMPLETED  │  │   FAILED   │       │   KILLED   │
           └────────────┘  └────────────┘       └────────────┘
                  │                │                    │
                  └────────────────┴────────────────────┘
                                   │
                                   ▼
                            terminal — immutable
```

Three rules govern transitions:

1. **Forward-only.** Once terminal (`COMPLETED`/`FAILED`/`KILLED`),
   the record never transitions again. A new attempt is a new Task
   with a new id.
2. **One transition per writer.** The owning executor is the sole
   writer; readers (coordinator, UI, peer agents) observe via the
   registry. No co-writers, no last-write-wins.
3. **Every transition emits an event.** The state-change is published
   on the session event bus before the on-disk record is updated, so
   subscribers cannot miss a transition. (Bus design — forward
   reference to ADR-031.)

### Three execution surfaces

A Task's `surface` field selects its executor. All three share the
lifecycle above; they differ in isolation, latency, and failure
independence.

**1. In-process (`asyncio.Task`).** Runs inside the host process, in
the same event loop. Lowest latency. Shared memory — RLM handles
(ADR-028) pass by reference. Loses everything if the host dies.

```python
# duh/kernel/task/surfaces/in_process.py
async def run_in_process(task: Task, deps: Deps) -> None:
    await deps.task_registry.transition(task.id, TaskState.RUNNING)
    try:
        result = await deps.agent_loop.run(
            prompt=task.prompt,
            tools=_filter_tools(deps.tools, task.tool_allowlist),
            output_sink=open(task.output_artifact, "a"),
        )
        await deps.task_registry.complete(task.id, result=result)
    except asyncio.CancelledError:
        await deps.task_registry.transition(task.id, TaskState.KILLED)
        raise
    except Exception as e:
        await deps.task_registry.fail(task.id, error=repr(e))
```

**2. Subprocess (forked Python).** Runs in a child Python process.
Full memory isolation; resilient to host crashes — the child keeps
running and the host can reattach by PID. Startup state travels as a
JSON payload on stdin; bulk inputs go via on-disk artifacts (the RLM
blob store from ADR-028 doubles as the transport).

```python
# duh/kernel/task/surfaces/subprocess.py
def spawn_subprocess(task: Task, deps: Deps) -> int:
    payload = json.dumps({"task": asdict(task), "session_dir": deps.session_dir})
    proc = subprocess.Popen(
        [sys.executable, "-m", "duh.kernel.task.runner"],
        stdin=subprocess.PIPE,
        stdout=open(task.output_artifact, "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,    # detach from host's session
    )
    proc.stdin.write(payload.encode()); proc.stdin.close()
    deps.task_registry.record_pid(task.id, proc.pid)
    return proc.pid
```

The child writes state transitions back through a UNIX socket at
`<session_dir>/tasks/<task_id>.sock`; the host rewrites the JSON
record on each transition.

**3. Remote (HTTP + bearer).** Runs on a duhwave server. The host
POSTs the spawn request; the server returns a remote task id; state
and output are read by long-poll. The host's Task record carries
`duhwave_url` and `bearer` in `metadata`; the server keeps its own
copy and runs an in-process or subprocess executor remotely.

```python
# duh/kernel/task/surfaces/remote.py
async def spawn_remote(task: Task, deps: Deps) -> None:
    server = task.metadata["duhwave_url"]
    async with deps.http.post(
        f"{server}/v1/tasks", json=asdict(task),
        headers={"authorization": f"Bearer {task.metadata['bearer']}"},
    ) as resp:
        remote = await resp.json()
    task.metadata["remote_id"] = remote["id"]
    deps.long_poll.subscribe(task.id, server, remote["id"])
```

Long-poll reads `/v1/tasks/{id}/events` and rewrites the local
record per event; output streams by `/v1/tasks/{id}/log?from=<offset>`.

All three surfaces implement one `TaskExecutor` interface:

```python
# duh/kernel/task/executor.py
class TaskExecutor(Protocol):
    async def spawn(self, task: Task, deps: Deps) -> None: ...
    async def kill(self, task: Task)                -> None: ...
    async def reattach(self, task: Task)            -> bool:
        """Return True if the task is still alive on this surface."""
```

### Output as artifact, not as result

A Task's *result* is a small string — the model's final text response.
Its *output* is everything streamed during the run: tool calls, tool
outputs, intermediate model prose. They are stored separately:

- `result` (≤ 64 KB) lives in the JSON record.
- `output_artifact` is append-only stdout/stderr-shape text.

The coordinator does not block on a Task to read its progress. It
calls `Peek` (the RLM tool from ADR-028) against the output log:

```python
Peek(handle=f"task:{task_id}:output", start=last_seen, end=last_seen + 8192)
```

`Peek` returns whatever has been written so far; the coordinator
checkpoints `last_seen` and resumes on the next iteration. Long-
running children become observable without round-tripping prose
through model turns.

### Resumption protocol

On `--continue` or after host crash, load every JSON record under
`<session_dir>/tasks/` and process by state:

- **terminal** — load as historical record. Done.
- **`RUNNING` in-process** — the asyncio.Task is gone. Transition to
  `FAILED` with `error="orphaned: host restart"`.
- **`RUNNING` subprocess** — try to reattach by PID. If
  `os.kill(pid, 0)` succeeds, reconnect to the UNIX socket and resume.
  Otherwise transition to `FAILED` with `error="orphaned: pid dead"`.
- **`RUNNING` remote** — `GET /v1/tasks/{remote_id}`. If still
  running, resubscribe to events. If terminal or 404, transition to
  `FAILED` with `error="orphaned: remote <status>"`.
- **`PENDING`** — requeue.

Re-attachment is best-effort. The user gets a clear message about
which tasks survived and which were orphaned. No silent loss.

### Eviction policy

Terminal tasks linger in the in-memory registry for a grace period
(default 30 s, hard cap 10 min) so the coordinator and UI can read
their result. After the grace period, the in-memory entry is evicted
provided no readers are pending; the on-disk JSON and log file
remain. Output logs are retained until the session itself is purged
(ADR-058) — disk usage is the user's choice; an in-memory ghost is
not.

### Identity

`task_id = f"{session_id}:{monotonic_seq}"`

Globally unique (session ids already are), sortable by spawn order
(seq is monotonic per session), traceable (every event, every log
line, every record carries a task id that names its session and
ordinal). No hash, no UUID; humans can read and compare task ids at
a glance. A child records its parent's id in `parent_id`; tracing a
tree of work is one column lookup deep.

### Capabilities boundary

A Task has a declared `tool_allowlist`; the executor enforces it.
In-process surface filters the deps tool registry. Subprocess and
remote surfaces carry the allowlist in their spawn payload and
instantiate only those tools; user-facing approval requests (ADR-005)
round-trip back to the host gate over the UNIX socket or long-poll
channel, so a child cannot exceed the host's permission grant. The
allowlist is part of the Task record — a resumed Task picks up the
same boundary it was spawned with.

### TaskRegistry interface

```python
# duh/kernel/registry/task_registry.py
class TaskRegistry(Protocol):
    async def spawn(self, spec: TaskSpec) -> Task: ...
    async def get(self, task_id: str)     -> Task: ...
    async def list(self,
                   session_id: str | None = None,
                   state: TaskState | None = None) -> list[Task]: ...
    async def kill(self, task_id: str)    -> None: ...
    async def transition(self, task_id: str, to: TaskState) -> None: ...
    async def complete(self, task_id: str, result: str)     -> None: ...
    async def fail(self,     task_id: str, error: str)      -> None: ...
    async def reattach_all(self, session_id: str) -> ReattachReport: ...
```

The registry is the single point of truth. Executors write; the
coordinator and UI read. No executor-to-coordinator path bypasses it.

## Alternatives considered

1. **One surface only — pick in-process and stop.** Simplest by far.
   Forfeits resilience to host crashes and remote duhwave entirely.
   The three surfaces share enough structure (lifecycle, registry,
   output streaming, capabilities boundary) that defining the
   abstraction once is cheap; retrofitting later means rewriting every
   coordinator, every UI subscriber, every resume path.

2. **No persistence — Tasks are in-memory objects.** Today's approach.
   Coordinator mode papers over it with `asyncio.gather`; multi-agent
   swarms can't observe each other; remote duhwave is impossible. The
   persistence cost is one JSON write per state transition; the
   capability gain is large.

3. **Queue-based instead of state-machine.** Model agency as messages
   on a work queue. Loses the *identity* of a piece of work — once
   dequeued, who ran it, when, under what permissions, with what
   parent? A state machine over a persistent record gives every Task
   identity and history; a queue gives only message-passing. Readers
   and writers need to refer to the same Task by id repeatedly over
   time.

4. **Actor model (one mailbox per agent).** Erlang-style. Powerful but
   redundant — an actor *is* a long-lived process with a mailbox; we'd
   reinvent the actor lifecycle on top of asyncio while losing the
   artifact-on-disk story. The Task primitive is actor-shaped at the
   boundary (id + state + lifecycle) without committing to actor
   semantics inside. Inter-Task messaging uses the event bus
   (ADR-031) on top of the registry.

5. **Make existing `AgentTool` the persistent primitive.** Promote
   `AgentTool` (ADR-012) from tool-call to record. But `AgentTool` is
   *one way to invoke a child*, not the unit of agency. A
   coordinator-spawned subagent, a user-typed `duh task spawn`, and a
   remote duhwave POST are all Tasks; only one goes through
   `AgentTool`. The Task primitive sits *under* `AgentTool`.

## Consequences

Positive:

- **Agency persists.** A subagent running when the host crashed is
  reattached on `--continue`, or recorded as orphaned with a clear
  cause. No silent loss.
- **Observability for free.** Every Task has a name, a state, a log
  file, and timestamps. `duh task list` shows the active set;
  coordinator polls instead of blocking.
- **Three surfaces share one abstraction.** Same coordinator code
  whether children run in-process for speed, in subprocesses for
  crash isolation, or on a remote duhwave server. Surface choice is
  opt-in per spawn; the code path does not bifurcate.
- **Capabilities travel with the Task.** A subprocess child cannot
  silently exceed its parent's permissions — allowlist is in the
  spawn payload, approvals round-trip to the host gate.
- **Foundation for the rest of duhwave.** Event bus (ADR-031),
  scheduler (ADR-032), remote server — every later piece references
  Task ids and registry queries. Defining them once keeps later ADRs
  short.

Negative / tradeoffs:

- **More state to manage.** A new directory tree under
  `<session_dir>/tasks/`. Disk usage grows with conversation length
  and number of subagents. Mitigated by `duh session purge`.
- **Three surface-areas to keep correct.** The shared lifecycle
  helps; the executor protocol pins down the contract; tests cover
  all three. But the subprocess UNIX-socket channel and the remote
  long-poll channel need their own reconnection paths.
- **State transitions are now an interface.** Internal callers used
  to call `agent_loop.run(...)` and receive a result; now they go
  through the registry. ~200 LOC of mechanical refactor.
- **Eviction is policy.** A pathological coordinator that refuses to
  read terminal Tasks could pin memory until the grace period.
  Mitigated by the 30 s default, a hard cap of 10 minutes, and a
  warning log when pending-eviction entries exceed N.
- **JSON spawn payload, not arbitrary Python objects.** Closures
  cannot cross into a subprocess child. Rare host-side-closure
  dependencies need a handle protocol — documented in ADR-031.

## Migration

Existing `AgentTool` calls keep working unchanged. Internally,
`AgentTool` now creates a Task with `surface=in_process` and waits
for its result; user-visible behaviour is identical.

Subprocess and remote surfaces are opt-in via CLI:

```bash
duh task spawn --surface subprocess --agent-type coder --prompt "..."
duh task spawn --surface remote --duhwave-url <url> --agent-type researcher --prompt "..."
```

Or programmatically through the Spawn tool (defined in ADR-031;
this ADR defines the substrate):

```python
Spawn(prompt="...", surface="subprocess", agent_type="coder",
      tool_allowlist=["Read", "Edit", "Bash:rg"])
```

`--continue` on a pre-ADR session loads zero Tasks (the directory
is absent) and behaves as today. New sessions include Tasks in the
resumption pass.

## Tests

After this ADR lands:

- `tests/unit/test_task_model.py` — dataclass round-trip, state enum
  invariants, terminal-immutability assertion.
- `tests/unit/test_task_registry.py` — spawn/get/list/transition,
  concurrent-transition rejection, eviction grace period, parent_id
  traversal.
- `tests/unit/test_task_in_process.py` — in-process surface: spawn,
  result, kill, output streaming.
- `tests/unit/test_task_subprocess.py` — subprocess surface: spawn,
  PID record, UNIX-socket transitions, parent-side approval
  round-trip, SIGTERM kill.
- `tests/unit/test_task_remote.py` — remote surface against a fake
  duhwave server (aiohttp test server): spawn, long-poll events,
  output streaming, reattach.
- `tests/integration/test_task_resume.py` — kill -9 on host with all
  three surfaces in flight; restart; verify reattach for live
  subprocess/remote, orphan transition for in-process.
- `tests/integration/test_task_capabilities.py` — subprocess child
  attempting an out-of-allowlist tool is rejected; approval
  round-trip works for in-allowlist tools that require confirmation.

## Follow-up

- **ADR-031** — Event bus and Spawn tool: how Tasks publish state
  transitions and output to subscribers, and the model-facing tool
  for spawning. Builds on the registry defined here.
- **ADR-032** — Scheduler and quotas: per-session and per-surface
  caps on concurrent Tasks; backpressure when a coordinator fans out
  faster than the host can run children.
- **Benchmark 5** — coordinator-mode comparison at matched model
  with in-process vs subprocess surface, measuring latency overhead
  and crash-recovery success rate.
- **Remote duhwave server design** — separate ADR for HTTP endpoints,
  auth, multi-tenancy. This ADR fixes only the *client-side* contract.
