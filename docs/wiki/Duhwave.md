# duhwave — D.U.H.'s persistent agentic-swarm extension

`duh.duhwave` is an opt-in extension that gives D.U.H. a substrate **above one CLI invocation**: a host daemon, an event-ingress layer, a persistent Task primitive, recursive cross-agent variable handles, and a topology-as-data DSL. Five Accepted ADRs (028–032) define it; 343 duhwave-specific tests cover it; the kernel is unchanged.

This page is the canonical wiki overview. For a narrative walkthrough with end-to-end examples, see the [build-your-own-swarm cookbook](https://github.com/nikhilvallishayee/duh/blob/main/docs/cookbook/build-your-own-swarm.md). For runnable demos of every primitive, see [Examples](Examples). For the per-ADR design rationale, see the [ADR index](https://github.com/nikhilvallishayee/duh/blob/main/adrs/ADR-028-032-INDEX.md).

---

## Mental model

A single-agent loop (the shape `Agent` and `Swarm` cover) is four things: an adapter, a tool registry, a dispatcher, a context manager. Loop until done; exit. duhwave adds five things on top.

**First**, it adds a persistent host process. The swarm is alive past any single user-facing CLI invocation; it accepts work from external triggers (HTTP webhooks, file changes, cron, MCP push, manual seams), not just a user prompt. Each external event normalises into one immutable `Trigger` type and a glob-matching `SubscriptionMatcher` routes it to zero, one, or many subscriptions — each match spawns a Task.

**Second**, it adds a substrate where the agent's working memory is **bytes by reference, not by summary**. Bulk inputs (the codebase, the spec, the trigger payload) bind to *named handles* in a sandboxed Python REPL subprocess; the agent sees a system block listing handles and operates via `Peek` / `Search` / `Slice` / `Recurse` / `Synthesize`. Compaction (summarise older turns into prose, drop the originals) cannot lose what the REPL still holds. Across an agent boundary, the same mechanism: the coordinator owns one REPL per session, workers get read-only views into selected handles, worker output binds back as a new handle in the coordinator's namespace.

**Third**, it gives you a topology-as-data DSL. The whole swarm shape — agents, models, tools, triggers, edges, budget — is one TOML file, packable into a deterministic Ed25519-signable `.duhwave` archive, manageable via a 10-subcommand control plane. Audit-able. Diffable. Sharable. Installable into `~/.duh/waves/<name>/<version>/` with permissions downgraded to "ask every time" if the bundle isn't signed.

---

## The 5 ADRs

### ADR-028 — RLM context engine

Defines the substrate. The coordinator owns one `RLMRepl` (a sandboxed `python3 -I` subprocess: no network, no shell, curated stdlib only, memory-capped via `RLIMIT_AS`) per session. Bulk values bind to named variables inside; the agent never sees the bytes inline. Five RLM tools (`Peek`, `Search`, `Slice`, `Recurse`, `Synthesize`) operate on handles by name. Cites Zhang, Kraska, Khattab — *Recursive Language Models* ([arXiv:2512.24601](https://arxiv.org/abs/2512.24601), January 2026). Implementation: `duh/duhwave/rlm/`. **Accepted (implemented).**

### ADR-029 — Recursive cross-agent links

Defines the cross-agent boundary. The `Spawn` tool starts a child agent with a selectively-exposed read-only `RLMHandleView` into the coordinator's REPL; the worker's final result text binds back as a new handle in the coordinator's namespace. The view is a `MappingProxyType`-style read-only wrapper — calls referencing handles outside the exposure list raise `ValueError("handle not exposed: ...")` *before* reaching the underlying REPL, so the view is the worker's complete attack surface. Cross-agent handoff is therefore "address by reference, never by summary," applied across an agent boundary instead of across a turn boundary. Cites Yang, Zou, Pan et al. — *Recursive Multi-Agent Systems* ([arXiv:2604.25917](https://arxiv.org/abs/2604.25917), April 2026; reported 75.6% fewer tokens vs prose-handoff multi-agent baselines). Implementation: `duh/duhwave/coordinator/{spawn,view,tool_filter}.py`. **Accepted (implemented).**

### ADR-030 — Persistent Task lifecycle, three execution surfaces

Defines the unit of work. A Task is a record on disk with a 5-state forward-only state machine (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`); illegal transitions raise. One writer (the executor); every transition emits an event. Three execution surfaces share the lifecycle: `InProcessExecutor` (asyncio.Task in the host, lowest latency, shared memory), `SubprocessExecutor` (isolated `python3 -I` child, survives parent crashes, attached on `--continue` or recorded as orphaned), `RemoteExecutor` (HTTP+bearer to a `RemoteTaskServer`, both shipped in `duh.duhwave.task.remote`). All implement the same `TaskExecutor` Protocol; the coordinator does not bifurcate on surface choice. Implementation: `duh/duhwave/task/`. **Accepted (implemented).**

### ADR-031 — Coordinator-as-prompt-role + event ingress

Defines who runs and what fires them. **Part A** (role): a "coordinator" is not an `Engine` subclass. It is a frozen `Role` dataclass (system prompt, tool allowlist, `spawn_depth`). The kernel filters the registered tool list to the role's allowlist *before the first turn* — anything outside is not registered, so the model never sees a schema for `Bash` or `Edit`. The synthesis-mandate is enforced by **absence**, not by trust. **Part B** (ingress): five listeners (`WebhookListener` with HMAC verification via `X-Duh-Signature`, `FileWatchListener`, `CronListener`, `MCPPushListener`, `ManualSeam`) normalise external events into one `Trigger` type. Glob matching on `(kind, source)` routes to subscriptions declared in the topology — first-match-wins. Triggers append to `triggers.jsonl` *before* dispatch (at-least-once); replay survives crash. Implementation: `duh/duhwave/coordinator/role.py`, `duh/duhwave/ingress/`, `duh/duhwave/cli/dispatcher.py`. **Accepted (implemented).**

### ADR-032 — Swarm topology DSL + bundles + control plane

Defines how a swarm is declared, packaged, installed, and operated. **DSL**: one TOML file describes agents, models, tools, triggers, edges, budget; parsed via `duh.duhwave.spec.parse_swarm`. **Bundle**: `pack_bundle` produces a deterministic ZIP (sorted entries, fixed mtime — re-packing the same source produces byte-identical output, which is what makes detached Ed25519 signatures meaningful) at `~/.duh/waves/<name>/<version>/`. Unsigned bundles install with permissions downgraded to "ask every time." **Control plane**: 10 subcommands (`duh wave start / stop / ls / inspect / pause / resume / logs / install / uninstall / web`) over a Unix-socket RPC to a `HostState` daemon. `duh wave logs --follow` streams newline-delimited JSON over the host socket. Implementation: `duh/duhwave/{spec,bundle,cli}/`. **Accepted (implemented).**

---

## Quick start

```bash
pip install duh-cli
```

For the full ingress surface (webhooks, file watches, cron) and bundle signing:

```bash
pip install watchfiles aiohttp croniter cryptography
```

These are optional — duhwave runs without them, but real deployments install the lot.

You also need an API key for whichever model(s) the swarm will use (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.). Stub-mode demos under `examples/duhwave/` need none.

### Smallest possible swarm

Walk through every primitive in 30 seconds:

```bash
.venv/bin/python3 examples/duhwave/01_rlm_demo.py        # RLM substrate
.venv/bin/python3 examples/duhwave/02_swarm_demo.py      # cross-agent handle-passing
.venv/bin/python3 examples/duhwave/03_event_driven.py    # webhook → trigger → match
.venv/bin/python3 examples/duhwave/04_topology_bundle.py # pack → install → daemon
```

Each is self-contained, deterministic, no API key needed.

### Boot a real persistent host

```bash
# Pack and install the repo-triage showpiece
duh wave install examples/duhwave/repo_triage/

# Start the daemon
duh wave start repo-triage

# Inspect state
duh wave inspect repo-triage
duh wave ls

# Tail the event log
duh wave logs repo-triage --follow

# Stop and clean up
duh wave stop repo-triage
duh wave uninstall repo-triage
```

The `examples/duhwave/repo_triage/main.py` script walks the same arc end-to-end in one runnable demo (~400 LOC of glue calling `duh.duhwave` as a library).

---

## Topology TOML — minimum viable swarm

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

Pack and install programmatically:

```python
from pathlib import Path
from duh.duhwave.bundle import pack_bundle, BundleInstaller

bundle = pack_bundle(
    spec_dir=Path("examples/duhwave/repo_triage/"),
    out_path=Path("./repo-triage-0.1.0.duhwave"),
)

installer = BundleInstaller(root=Path.home() / ".duh" / "waves")
result = installer.install(bundle, force=True)
print(result.trust_level)  # → "unsigned" / "untrusted" / "trusted"
print(result.path)          # → "~/.duh/waves/repo-triage/0.1.0/"
```

Bundles are deterministic; re-packing the same source produces byte-identical output, which is what makes detached Ed25519 signatures meaningful.

---

## `duh wave` CLI cheatsheet

The 10-subcommand control plane manages installed bundles and running daemons over a Unix-socket RPC:

| Subcommand | One-line description |
|------------|---|
| `duh wave start <name>` | Boot the daemon for an installed swarm; auto-starts listeners declared in its `IngressSpec`. |
| `duh wave stop <name>` | Send SIGTERM to a running daemon; it drains in-flight tasks before exit. |
| `duh wave ls` | List all installed bundles and their running state (started / stopped / paused). |
| `duh wave inspect <name>` | Show topology, registered triggers, active subscriptions, current Task counts, budget status. |
| `duh wave pause <name>` | Stop accepting new triggers; running tasks finish; new triggers append to log but don't dispatch. |
| `duh wave resume <name>` | Reverse of `pause`; queued triggers dispatch in order. |
| `duh wave logs <name> [--follow]` | Tail the host event log; `--follow` streams newline-delimited JSON over the host socket. |
| `duh wave install <path-or-url>` | Install a `.duhwave` bundle into `~/.duh/waves/<name>/<version>/`; HTTPS URLs and local paths both supported. |
| `duh wave uninstall <name>` | Remove an installed bundle; running daemon must be stopped first. |
| `duh wave web` | Launch a local web UI for browsing installed swarms, tasks, triggers, and event logs. |

The control plane is implemented in `duh/duhwave/cli/`; the daemon is `python -m duh.duhwave.cli.daemon`.

---

## Where to go next

- **[Examples](Examples)** — runnable index of every demo under `examples/duhwave/`, with one-line descriptions, file paths, run commands, and graduation order.
- **[build-your-own-swarm cookbook](https://github.com/nikhilvallishayee/duh/blob/main/docs/cookbook/build-your-own-swarm.md)** — narrative walkthrough of all six primitives (RLM substrate, persistent Task, coordinator role, recursive cross-agent links, ingress, topology + bundles), built up bottom-up to a full repo-triage swarm.
- **[ADR index](https://github.com/nikhilvallishayee/duh/blob/main/adrs/ADR-028-032-INDEX.md)** — implementation status table, demo cross-reference, dependency DAG, deferred-vs-shipped follow-ups.
- **[duhwave-agile benchmark](https://github.com/nikhilvallishayee/duh/blob/main/benchmarks/duhwave-agile/RESULT.md)** — first real-OpenAI benchmark (5-stage agile-team pipeline at $0.0015 per run on gpt-4o-mini).
- **[Multi-Agent Guide](Multi-Agent)** §"duhwave swarms" — when to reach for duhwave vs the simpler `Agent` / `Swarm` tools.
- **[Architecture](Architecture)** §5b — how duhwave composes on top of the kernel + adapters, with a dependency DAG diagram.
