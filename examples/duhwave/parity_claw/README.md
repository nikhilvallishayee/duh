# parity_claw — OpenClaw-shape feature-parity demo for duhwave

A **multi-channel persistent-assistant sketch** built on the duhwave
substrate. Demonstrates that duhwave can do the always-on,
multi-channel-routing shape that OpenClaw was built for, but as a
coding-agent harness rather than a messaging product.

This is a **sketch**, not a competitor. OpenClaw is a personal AI
assistant brokered across 20+ messaging APIs (WhatsApp, Telegram,
Slack, Discord, Signal, iMessage…). parity_claw mirrors only the
*architectural shape* — the channels here are duhwave's native
ingress kinds: `webhook`, `filewatch`, `cron`, `manual`. There is no
chat UI, no Slack adapter, no model call.

## What it proves

| OpenClaw property            | parity_claw realisation                                                | Where                                  |
|------------------------------|------------------------------------------------------------------------|----------------------------------------|
| Always-on persistent runtime | `python -m duh.duhwave.cli.daemon` host process                        | `02_persistent_state.py`               |
| Multi-channel ingress        | 4 trigger kinds: `webhook`, `filewatch`, `cron`, `manual`              | `swarm.toml` + `01_four_channels.py`   |
| Per-channel routing          | `SubscriptionMatcher.from_spec` → `(kind, source) → agent_id`          | `01_four_channels.py`                  |
| Persistent state             | append-only `triggers.jsonl` + `TriggerLog.replay()` on restart        | `02_persistent_state.py`               |
| Crash-safe (SIGKILL)         | replay survives even with no shutdown handler running                  | `02_persistent_state.py`               |
| Concurrent fan-in            | shared O_APPEND log, all four listeners write to the same JSONL        | `03_concurrent_ingress.py`             |
| Per-skill isolation          | `<waves_root>/<bundle-name>/<version>/` per installed bundle           | `04_per_channel_isolation.py`          |
| Bot-like UX                  | (out of scope — duhwave is harness-level; UX layers above)             | —                                      |

What we explicitly do **not** demo:

- Real LLM calls (each agent's role is data; no model is invoked).
- Real Slack/WhatsApp/etc. adapters (OpenClaw's value proposition).
- A `watchfiles` or `croniter` listener (those deps may not be
  installed). Instead the demo appends `Trigger` records with
  `kind=FILEWATCH` / `kind=CRON` directly to the log — the *same data
  path* the real listeners would take.

## What's in the bundle

| File                | Purpose                                                                |
|---------------------|------------------------------------------------------------------------|
| `swarm.toml`        | Topology DSL — 4 agents, 4 triggers (one per channel), budget.         |
| `manifest.toml`     | Bundle identity (name=`parity-claw`, version=`0.1.0`).                 |
| `permissions.toml`  | Minimal envelope (no FS, no network, only the duhwave RLM tool names). |

## Running

```bash
cd /Users/nomind/Code/duh
.venv/bin/python3 examples/duhwave/parity_claw/run_all.py
```

`run_all.py` invokes the four scripts in sequence:

1. **`01_four_channels.py`** — non-daemon. Parse the spec, build a
   `SubscriptionMatcher`, synthesise one `Trigger` per kind, print the
   resolved `(kind, source) → agent_id` table.

2. **`02_persistent_state.py`** — daemon lifecycle. Pack the bundle,
   install it, start the daemon, append three manual triggers,
   **SIGKILL** the daemon (no graceful shutdown), restart, replay
   `triggers.jsonl`, verify all three records survived with original
   correlation ids.

3. **`03_concurrent_ingress.py`** — fire all four channel kinds
   concurrently via `asyncio.gather`: 4 channels × 4 fanout = 16
   appends in flight, all into the same `TriggerLog`. Verify all 16
   land, replay shape matches, every record routes to its agent.

4. **`04_per_channel_isolation.py`** — install **two** small bundles
   (`claw-a`, `claw-b`) into one `waves/` root. Verify they get
   sibling `<root>/<name>/<version>/` subtrees and both are listed in
   `index.json`. Uninstall `claw-a`, verify `claw-b`'s tree and index
   entry remain intact.

Expected final output:

```
PASSED: 4/4 stages
```

Exit code: `0`.

All paths are routed through `tempfile.mkdtemp(prefix="dwv-...")` so
the user's real `~/.duh/` is never touched. macOS AF_UNIX has a
~104-byte path-length cap; rooting `waves_root` at `/var/folders/.../dwv-XXX`
(rather than nested under `pytest`'s `tmp_path`) keeps the host
socket path within that cap.

## Caveat — what this is not

OpenClaw is a **messaging-product** for end-users; the value lives in
its 20+ chat-platform adapters and its always-listening gateway.
parity_claw is **harness-level**: it borrows the architectural
characteristics (always-on, multi-channel, persistent, per-skill
state) and shows that duhwave already has the substrate to do them,
without claiming feature parity with the messaging product itself.

A real OpenClaw-style messaging deployment on duhwave would still
need:

- A Slack/Telegram/Discord adapter that translates inbound messages
  into `Trigger(kind=WEBHOOK, source="slack:#channel", ...)`.
- A reply path back through the matched agent's worker.
- The conversational UX layer (history, threading, mentions).

Those live outside the harness. The substrate they sit on is what
this demo proves.

## ADR references

- **ADR-031** — Coordinator-as-prompt-role + event ingress (the
  `Trigger` / `TriggerLog` / `SubscriptionMatcher` primitives).
- **ADR-032** — Topology DSL + bundle format + control plane (the
  whole `swarm.toml` / `pack_bundle` / `BundleInstaller` /
  `cli.daemon` chain).

For an end-to-end *orchestration* demo (one channel, three agents,
coordinator-with-workers shape), see `examples/duhwave/repo_triage/`.
For background on the OpenClaw architecture itself, see
`research/openclaw-hermes-architecture.md` in the cockpit notes.
