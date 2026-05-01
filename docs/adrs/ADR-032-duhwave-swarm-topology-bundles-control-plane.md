# ADR-032 — Swarm topology DSL + bundle format + control plane

**Status:** Accepted (implemented)
**Date:** 2026-04-30 · 2026-05-01 (accepted)
**Scope:** `duh/wave/spec/`, `duh/wave/bundle/`, `duh/wave/cli/`, new
file extension `.duhwave`, new CLI subcommand group `duh wave …`.
**Depends on:** ADR-028 (RLM context engine), ADR-029 (recursive
cross-agent links), ADR-030 (persistent task lifecycle), ADR-031
(coordinator prompt + role + event ingress).

## Context

ADRs 028–031 define the runtime substrate for **duhwave**: a long-
running multi-agent layer on top of D.U.H. — REPL-backed context
(028), handle-passing between agents (029), persistent task lifecycle
(030), coordinator role + event ingress (031).

What's missing is the connective tissue. A user who wants a "watch a
repo, triage issues, hand off to a reviewer" swarm today has to
hand-write Python that wires agents, models, prompts, triggers, and
edges; ship that code somehow; and tail logs + read sqlite to see
what's running. There is no declarative way to describe a swarm, no
install path for sharing one, and no built-in way to inspect live
state. Together those gaps keep duhwave at "library for engineers"
instead of "tool that ships."

This ADR closes the gaps in three pieces:

- **(A) A topology DSL.** One TOML file describes an entire swarm —
  agents, triggers, edges, models, budgets — validated against a
  JSON Schema at parse time.
- **(B) A `.duhwave` bundle format.** A signed ZIP packaging topology,
  skills, prompts, and a permissions manifest, installable in one
  command.
- **(C) A local control plane.** `duh wave` subcommands to start,
  stop, inspect, pause, resume, and observe running swarms; an
  optional local web UI.

The three pieces ship as one ADR because each is useless alone: a DSL
with no installer is a copy/paste workflow; a bundle with no DSL is a
tarball; a control plane with neither is `ps aux`.

## Decision

### Part A — Topology DSL (TOML)

A swarm is one file, `swarm.toml`. D.U.H. discovers it in this order:

1. Path passed explicitly: `duh wave start ./my-swarm.toml`
2. Project root: `./duhwave.toml`
3. Installed bundle: `~/.duh/waves/<name>/swarm.toml`

**Why TOML.** D.U.H. already standardised on TOML for config (ADR-016).
Specs are small (50–150 lines), often include multi-line prompts
(TOML triple-quoted strings handle them cleanly), and want comments.
YAML and Python-DSL alternatives were rejected (see Alternatives).

**Top-level keys** (validated by `duh/wave/spec/schema.json`):

| Key             | Cardinality | Required | Purpose                              |
|-----------------|-------------|----------|--------------------------------------|
| `[swarm]`       | one         | yes      | Identity (name, version, description)|
| `[[agents]]`    | one+        | yes      | Each persistent role in the swarm    |
| `[[triggers]]`  | zero+       | no       | External events that wake an agent   |
| `[[edges]]`     | zero+       | no       | Inter-agent spawn/message permissions|
| `[budget]`      | one         | no       | Cost ceilings (tokens, USD, concurrency) |
| `[secrets]`     | one         | no       | Declared `${ENV_VAR}` references     |

**Variable interpolation.** Any string field may contain `${ENV_VAR}`
or `${secrets.NAME}`. Parser rejects unresolved references;
`[secrets]` declares which env vars the swarm requires so `duh wave
install` surfaces them at install time.

**Agent record:**

```toml
[[agents]]
id              = "researcher"            # unique within swarm
role            = "researcher"            # one of: researcher,
                                          #   implementer, reviewer,
                                          #   coordinator, custom
model           = "anthropic/claude-sonnet-4-6"
tools           = ["bash", "edit", "search", "fetch"]
expose          = ["bash", "search"]      # subset visible to peers
                                          #   when this agent is the
                                          #   target of an edge
system_prompt   = "prompts/researcher.md" # path within bundle, or
                                          #   inline triple-quoted
context_mode    = "rlm"                   # ADR-028: rlm | compact | auto
max_turns       = 50                      # per task
on_idle         = "park"                  # park | terminate
```

**Trigger record:**

```toml
[[triggers]]
kind          = "github_webhook"   # webhook | cron | filewatch |
                                   #   message | mcp_resource_change
target_agent  = "researcher"       # which agent receives the event
filter        = "issues.opened OR pull_request.opened"
secret        = "${secrets.GH_WEBHOOK_SECRET}"
```

**Edge record:**

```toml
[[edges]]
from = "researcher"
to   = "implementer"
kind = "spawn"            # spawn (creates a child task) | message
                          #   (appends to existing task's inbox)
when = "triage.priority == 'high'"   # optional CEL expression
                                     # over the source task's output
```

`spawn` edges create a new persistent task (ADR-030) with parent RLM
handles passed by reference (ADR-029). `message` edges append to an
existing task's inbox without creating a new lifecycle.

**Budget record:**

```toml
[budget]
max_tokens_per_hour   = 5_000_000
max_usd_per_day       = 25.00
max_concurrent_tasks  = 8
on_exhausted          = "pause"   # pause | terminate | alert
```

The budget enforcer is a control-plane singleton; it rejects new tool
calls when any ceiling is crossed. `pause` suspends the swarm and
emits an event; `alert` posts to the configured notification sink and
continues.

#### Worked example: a repo-watching triage swarm

```toml
[swarm]
name        = "repo-triage"
version     = "0.3.0"
description = "Watches one GitHub repo: triages issues, drafts replies, reviews PRs."

[secrets]
GH_TOKEN          = "GitHub PAT (repo:read, issues:write, pulls:write)"
GH_WEBHOOK_SECRET = "Shared secret configured on the GitHub webhook"

# ─── Agents ─────────────────────────────────────────────────────────

[[agents]]
id            = "researcher"
role          = "researcher"
model         = "anthropic/claude-haiku-4-5"
tools         = ["bash", "search", "fetch"]
system_prompt = "prompts/researcher.md"
context_mode  = "rlm"
max_turns     = 30

[[agents]]
id            = "implementer"
role          = "implementer"
model         = "anthropic/claude-sonnet-4-6"
tools         = ["bash", "edit", "search", "fetch", "git"]
system_prompt = "prompts/implementer.md"
context_mode  = "rlm"
max_turns     = 80

[[agents]]
id            = "reviewer"
role          = "reviewer"
model         = "anthropic/claude-sonnet-4-6"
tools         = ["bash", "search", "fetch", "github"]
system_prompt = "prompts/reviewer.md"
context_mode  = "auto"
max_turns     = 40

# ─── Triggers ───────────────────────────────────────────────────────

[[triggers]]
kind         = "github_webhook"
target_agent = "researcher"
filter       = "issues.opened"
secret       = "${secrets.GH_WEBHOOK_SECRET}"

[[triggers]]
kind         = "github_webhook"
target_agent = "reviewer"
filter       = "pull_request.opened OR pull_request.synchronize"
secret       = "${secrets.GH_WEBHOOK_SECRET}"

[[triggers]]
kind = "cron"
target_agent = "researcher"
spec = "0 9 * * MON"
payload = { task = "stale_digest" }

# ─── Edges ──────────────────────────────────────────────────────────

[[edges]]
from = "researcher"
to   = "implementer"
kind = "spawn"
when = "triage.priority == 'high' AND triage.has_repro == true"

[[edges]]
from = "implementer"
to   = "reviewer"
kind = "spawn"
when = "implementation.draft_pr_url != null"

# ─── Budget ─────────────────────────────────────────────────────────

[budget]
max_tokens_per_hour  = 2_000_000
max_usd_per_day      = 10.00
max_concurrent_tasks = 4
on_exhausted         = "pause"
```

This file plus the three prompt files is enough for `duh wave start`
to bring up a persistent swarm. Schema validation at parse rejects
unknown keys, missing required fields, and unresolved `${...}`
references with line numbers.

### Part B — `.duhwave` bundle format

A `.duhwave` file is a ZIP archive (ZIP, not tar — Windows has builtin
support) with the extension `.duhwave`. Layout:

```
my-swarm.duhwave/
├── manifest.toml          # bundle identity + signing info
├── swarm.toml             # the topology (Part A)
├── permissions.toml       # declared FS / network / tool permissions
├── prompts/               # system prompt files referenced from swarm.toml
│   ├── researcher.md
│   ├── implementer.md
│   └── reviewer.md
├── skills/                # custom skills (D.U.H. skill format)
│   └── triage-rubric/
│       ├── SKILL.md
│       └── scripts/...
└── README.md              # human-readable overview
```

**`manifest.toml`:**

```toml
[bundle]
name         = "repo-triage"
version      = "0.3.0"
author       = "Jane Doe <jane@example.com>"
homepage     = "https://example.com/repo-triage"
license      = "MIT"
duh_min_ver  = "0.9.0"

[signing]
algorithm    = "ed25519"
public_key   = "ed25519:Z2FudGltYXR0ZXJzbnRfd29yZHNAYWxsX2RyYWZ0..."
signature_file = "my-swarm.duhwave.sig"   # detached, alongside .duhwave
```

**`permissions.toml`:**

```toml
[filesystem]
read  = ["${HOME}/.duh/waves/repo-triage/", "${PWD}"]
write = ["${HOME}/.duh/waves/repo-triage/"]

[network]
allow = [
  "api.github.com",
  "api.anthropic.com",
]

[tools]
require = ["bash", "edit", "search", "fetch", "git", "github"]

[triggers]
listen = ["github_webhook", "cron"]
```

Permissions are declarative and inspectable. `duh wave install` reads
them, displays a diff against any previously-installed version, and
asks the user to approve. At runtime the kernel's permission gate
(ADR-005) enforces them — a tool call outside the declared envelope
is rejected even if the bundle's code requests it.

**Signing.** Bundles are signed with Ed25519. The signature is a
**detached** file (`my-swarm.duhwave.sig`) next to the `.duhwave`
file. Detached lets re-signing by a new author leave bundle bytes
unchanged and lets sharing without the signature degrade cleanly.

Verification rules:

- **Signed and trusted key** (in `~/.duh/waves/keys/` or matched via
  TOFU on first install): installs with declared permissions.
- **Signed but untrusted key**: install warns, shows the fingerprint,
  asks user to trust.
- **Unsigned**: install warns; permissions are downgraded to "ask
  every time" — every tool call goes through interactive approval.
  Useful in development, painful in production, by design.

**Install flow** (`duh wave install <path-or-url>`):

```
$ duh wave install ./repo-triage-0.3.0.duhwave
[1/5] Verifying signature...                ✓ ed25519:Z2FudGl...
[2/5] Resolving identity:                   repo-triage 0.3.0
[3/5] Permissions diff vs. installed (0.2.1):
        + network: api.github.com  (was anthropic-only)
        + trigger: cron            (new)
[4/5] Required env vars: GH_TOKEN, GH_WEBHOOK_SECRET (NOT SET)
[5/5] Approve install? [y/N] y
Installed to ~/.duh/waves/repo-triage/
```

**Per-swarm isolation.** Each installed bundle gets its own
`~/.duh/waves/<name>/` (read-only after install), its own `state/`
directory (ADR-030 task store, ADR-028 RLM blobs, per-session
checkpoints), its own `wave.log`, and its own permission envelope.
Two swarms cannot read each other's state or invoke each other's
tools; cross-swarm communication goes over the message-bus interface
in ADR-031, not via shared filesystem.

**Distribution.** Bundles install from a local file path, an HTTPS URL
(signature must verify against a trusted key or TOFU is offered), or a
bundle registry (`duh wave install repo-triage` shorthand — registry
implementation deferred to a follow-up ADR).

### Part C — Local control plane

The control plane is a long-running host process plus a CLI client.
The host is a single `duhwaved` process started by `duh wave start`;
the CLI subcommands are thin clients talking to it over a Unix domain
socket at `~/.duh/waves/control.sock`.

**Subcommands:**

```
duh wave start [<name>]            # start host, or just one swarm
duh wave stop                      # stop host (gracefully)
duh wave ls                        # list installed swarms + tasks
duh wave inspect <swarm-id>        # topology + current state
duh wave pause <swarm-id>          # suspend without losing state
duh wave resume <swarm-id>         # resume from suspend
duh wave logs <swarm-id> [-f]      # tail event log
duh wave install <path-or-url>     # install a bundle
duh wave uninstall <swarm-id>      # remove a bundle + its state
duh wave web                       # local web UI on localhost:8729
```

**`duh wave ls`** output:

```
SWARM         VERSION  STATUS    AGENTS  TASKS  PAUSED  TOKENS/HR  USD/DAY
repo-triage   0.3.0    running   3       2      —       423,118    1.42
docs-watch    0.1.4    paused    2       0      manual  —          —
deploy-bot    1.2.0    running   4       0      —       89         0.01
```

**`duh wave inspect repo-triage`** prints the parsed topology with
per-agent task counts, budget consumption, recent trigger fires, and
active inboxes. Human-readable by default, `--json` for scripts.

**`duh wave logs repo-triage -f`** tails a structured event log:

```
14:32:01Z  trigger.fire    github_webhook → researcher  (issues.opened #1147)
14:32:02Z  task.start      task-3f9a       researcher
14:32:18Z  tool.call       task-3f9a       fetch         api.github.com/...
14:32:24Z  agent.handle    task-3f9a       bound h_3f9a_repo (rlm, ~280k tok)
14:33:11Z  task.complete   task-3f9a       researcher    output→edge
14:33:11Z  edge.fire       researcher → implementer  (when matched)
14:33:11Z  task.start      task-3f9b       implementer (parent=task-3f9a)
```

Each line is one structured event matching `duh/wave/cli/events.py`;
`--json` emits NDJSON.

**`duh wave web`** (opt-in). Local-only web UI on
`http://localhost:8729`:

- **Topology view** — nodes for agents, edges for spawn/message
  permissions, edges fade in/out as they fire over a WebSocket fed by
  the same event stream as `duh wave logs`.
- **Task graph** — active tasks as nodes, parent links solid, ADR-029
  RLM handle links dotted.
- **Budget panel** — tokens/hour, USD/day, concurrent count, ceilings.
- **Inbox panel** — pending messages per agent.

The UI is a view over the control-plane API; no business logic
client-side. Bound to `127.0.0.1` only; remote access requires an
explicit reverse proxy. Auth deferred — local-only is the v1 trust
model.

**Process model.** `duhwaved` is one Python process: one asyncio
loop, one scheduler thread for cron + filewatch triggers, one HTTP
listener for webhook triggers (port configurable), one Unix-socket
listener for CLI/web clients, and a worker pool sized by the sum of
`[budget].max_concurrent_tasks` across installed swarms.

Crash recovery: on restart the host reads ADR-030's task store,
restores in-progress tasks (RLM handles rehydrated from disk), and
resumes scheduled triggers from last-fired timestamps. Tasks mid-
tool-call get one retry; subsequent failure marks them `errored`.

## Alternatives considered

1. **YAML instead of TOML.** YAML is more familiar to ops/devops.
   Rejected: D.U.H. already standardised on TOML (ADR-016), YAML's
   indentation makes multi-line prompts awkward, and tabs-vs-spaces
   is a recurring bug source.

2. **A Python DSL — swarm definition is `swarm.py`.** Maximum power,
   maximum mess. A spec is *configuration*, not a program; a Python
   module runs arbitrary code at parse (security hole), is not
   diff-able for the permissions-diff UX, and excludes non-Python
   tooling. The same expressivity is available by referencing
   scripts/skills *from* the TOML.

3. **A bundle is a git repo, not a ZIP.** Familiar workflow, but
   signature verification over a git tree is harder, install
   shouldn't require a git binary, and versioning becomes a
   tag-vs-branch ambiguity. A `.duhwave` ZIP is one immutable artifact
   with one signature.

4. **HTTP API as primary control-plane interface.** Considered for
   symmetry with the web UI. Rejected for v1: the CLI is canonical,
   and a Unix socket avoids binding any TCP port the user didn't
   ask for. `web` is the only subcommand that opens TCP, and it's
   explicit.

5. **No web UI — CLI only.** Considered as the minimum surface. The
   UI is opt-in, adding zero overhead for users who don't want it.
   The topology view is genuinely useful for debugging why an edge
   isn't firing, the dominant question in running swarms.

6. **Dynamic agents — add/remove at runtime via API.** Rejected for
   v1: topology is static once started; adding an agent means
   editing `swarm.toml` and `duh wave reload`. Static topology
   makes inspection meaningful; dynamic agents need their own audit
   trail. Revisit on real demand.

## Consequences

Positive:

- **Sharable swarms.** A `.duhwave` bundle is one file you can
  hand-off, install, audit, and run — Docker-image shape:
  declarative spec, signed artifact, locked permissions.
- **Inspectable swarms.** `duh wave inspect` answers "what's running"
  in one command; the web UI takes it to a live graph.
- **Per-swarm permission envelopes.** A bundle can only do what its
  `permissions.toml` declares; the kernel's permission gate (ADR-005)
  enforces the envelope.
- **Schema-validated specs catch errors at parse.** A typo in an
  agent ID referenced by an edge fails before any model call.
- **Composable with the rest of D.U.H.** Same model adapters
  (ADR-027), context engine (ADR-028), task store (ADR-030) —
  duhwave is not a parallel runtime.

Negative / tradeoffs:

- **Three new artifacts to learn.** TOML schema, bundle layout, CLI.
  Mitigated by `duh wave init` scaffolding, `examples/waves/`, and
  actionable validator errors.
- **Signing infrastructure to ship.** ~50 LOC for sign/verify; key-
  management UX (storage, trust, revocation) needs design. v1 is
  minimal — `~/.duh/waves/keys/<fpr>.pub` plus TOFU on first install.
- **A daemon now exists.** Strictly opt-in; existing one-shot flows
  don't require it. `duh wave start/stop` controls it.
- **Web UI surface area.** ~800 LOC TS+JSX with its own tests;
  mitigated by being a thin view over the event stream.
- **Cross-swarm isolation depends on the permission gate.** Gate
  bugs become bundle-isolation bugs; ADR-005's tests need bundle-
  scoped additions.

## Migration

duhwave is net-new. No existing topology format to migrate from, no
installed-swarms directory to upgrade. D.U.H. users who don't use
`duh wave …` see zero change. `~/.duh/waves/` is created on first
`duh wave install`; its absence is silent.

The bundle format is versioned via `manifest.toml`'s `bundle.version`
plus a `format_version = 1` field at the top of `swarm.toml`. Future
breaking changes bump `format_version`; older bundles continue to
parse against the v1 schema until explicitly upgraded.

## Tests

After this ADR lands:

- `tests/unit/test_wave_spec_parse.py` — parser round-trips every
  schema key; invalid specs (missing required, unknown key,
  unresolved interpolation, dangling agent ID in an edge) fail with
  line numbers.
- `tests/unit/test_wave_spec_schema.py` — JSON Schema validation
  against 12 known-good and 30 known-bad fixture specs.
- `tests/unit/test_wave_bundle_layout.py` — missing `manifest.toml`,
  missing `swarm.toml`, malformed permissions, and dangling prompt-
  file references all fail install with a clear error.
- `tests/unit/test_wave_bundle_signing.py` — Ed25519 sign + verify
  round-trip; tampered bytes fail; detached-signature swap fails;
  unsigned bundle installs in degraded mode.
- `tests/unit/test_wave_permissions_diff.py` — diff against a
  previously-installed version flags additions and removals.
- `tests/unit/test_wave_cli.py` — CLI parse for every subcommand;
  `--json` output is valid JSON.
- `tests/integration/test_wave_lifecycle.py` — `repo-triage` bundle
  install → start → fire webhook → task creation → pause → resume →
  uninstall.
- `tests/integration/test_wave_isolation.py` — two bundles installed;
  triggering one leaves the other's state untouched and rejects
  envelope-violating FS reads.
- `tests/integration/test_wave_web.py` — `duh wave web` + WebSocket
  client; simulated trigger arrives within 200 ms.
- `tests/integration/test_wave_recovery.py` — kill `duhwaved` mid-
  task, restart; in-progress task resumes from last checkpoint with
  RLM handles restored.

## Follow-up

This closes the duhwave series (028 / 029 / 030 / 031 / 032). With
the four substrate ADRs accepted and this one implemented, duhwave
is feature-complete for v1.

Implementation skeleton, sequenced for incremental landing:

1. **Skeleton.** `duh/wave/spec/parser.py` + `schema.json`, no
   runtime — just `duh wave validate <swarm.toml>` returning
   pass/fail. One PR.
2. **Bundle.** `duh/wave/bundle/{pack,unpack,sign,verify}.py` +
   `duh wave install/uninstall`. Tested against fixtures, no daemon
   yet.
3. **Daemon — single-swarm.** `duhwaved` running one swarm; cron +
   manual triggers; `start/stop/ls/inspect/logs/pause/resume`.
   Integrates with ADR-030's task store.
4. **Trigger sources.** `github_webhook`, `filewatch`,
   `mcp_resource_change`. One PR each.
5. **Multi-swarm.** Concurrent execution under one daemon, per-swarm
   permission enforcement, isolation tests.
6. **Web UI.** `duh wave web` + topology view + task graph + budget
   panel. Last; everything before it is testable from the CLI.

Each step ships independently; users can stop at any point and have
a working subset. A bundle registry (`npm install` shape) is
deliberately deferred — local-file and HTTPS install paths cover the
design space until centralised distribution is in demand.
