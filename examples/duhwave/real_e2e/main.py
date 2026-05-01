#!/usr/bin/env python3
"""real_e2e — full daemon-driven webhook → agent → reply demo.

Closes the duhwave loop end-to-end through real production code paths
*including the persistent host process*. Every step of this script
exercises code that ships in ``duh/duhwave/`` — none of the agent
glue lives in this example.

The arc:

    1. Synthesize a tiny ``swarm.toml`` with one webhook trigger, an
       ``[ingress]`` port, and one agent that has an outbox configured.
    2. Pack it into a ``.duhwave`` bundle, install into a tmp waves
       root, start the daemon as a subprocess.
    3. The daemon walks the swarm's triggers and **auto-boots its own
       WebhookListener** on the ``[ingress] webhook_port`` declared
       in the topology (ADR-031 §B). No in-process listener — the
       demo POSTs to the daemon's bound socket directly.
    4. POST a real HTTP webhook to the daemon's listener.
    5. The daemon's :class:`Dispatcher` picks the trigger off the log,
       builds a Task, runs the OpenAI :data:`HostRunner`, transitions
       the Task to COMPLETED, and writes the reply to the agent's
       outbox.
    6. We poll the outbox, print the reply, and tear everything down.

Requires ``OPENAI_API_KEY``. Without it, the daemon attaches a
disabled runner and the outbox records a "no runner attached"
sentinel — the arc still demonstrates *that the trigger reached the
agent and a Task lifecycle ran*, just without a real reply.

Usage::

    export OPENAI_API_KEY=sk-proj-...
    /path/to/duh/.venv/bin/python3 examples/duhwave/real_e2e/main.py
"""
from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Path bootstrap so this runs as a script.
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.bundle import BundleInstaller, pack_bundle  # noqa: E402
from duh.duhwave.cli.rpc import (  # noqa: E402
    HostRPCError,
    call as rpc_call,
    is_daemon_running,
)


SWARM_TOML_TEMPLATE = """\
[swarm]
name = "real-e2e"
version = "0.1.0"
description = "minimal real e2e demo"
format_version = 1

[[agents]]
id = "support_agent"
role = "worker"
model = "gpt-4o-mini"
tools = []
expose = []
outbox = "outbox.jsonl"
system_prompt = '''You are a friendly developer-support assistant. \
You receive a JSON envelope describing a webhook event. Read the \
``payload.message.text`` field. Reply concisely (≤2 paragraphs, \
markdown OK). Never include preambles like "Sure!" or "Of course!".'''

[[triggers]]
kind = "webhook"
source = "/support/inbox"
target_agent_id = "support_agent"

[ingress]
webhook_port = {port}
webhook_host = "127.0.0.1"

[budget]
max_concurrent_tasks = 1
"""


MANIFEST_TOML = """\
[bundle]
name = "real-e2e"
version = "0.1.0"
description = "minimal real e2e demo"
author = "duhwave"
format_version = 1
created_at = 1730000000.0

[signing]
signed = false
"""


PERMISSIONS_TOML = """\
[filesystem]
read = []
write = ["./*"]

[network]
allow = ["api.openai.com"]

[tools]
allow = []
"""


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _port_is_open(host: str, port: int) -> bool:
    """Return True iff something is accepting TCP connections at ``host:port``."""
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


async def _post_json(host: str, port: int, path: str, body: dict[str, Any]) -> int:
    def _do() -> int:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.request(
                "POST",
                path,
                body=json.dumps(body),
                headers={"Content-Type": "application/json"},
            )
            r = conn.getresponse()
            r.read()
            return r.status
        finally:
            conn.close()

    return await asyncio.to_thread(_do)


async def _wait_for(predicate, *, timeout: float = 10.0, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await asyncio.to_thread(predicate):
            return True
        await asyncio.sleep(interval)
    return False


async def _amain(args: argparse.Namespace) -> int:
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    print("══════════════════════════════════════════════════════════════════════")
    print("  real_e2e — daemon-driven webhook → agent → reply")
    print("══════════════════════════════════════════════════════════════════════")
    if has_key:
        print("  OPENAI_API_KEY: present (real OpenAI runner)")
    else:
        print("  OPENAI_API_KEY: absent (disabled runner — arc still observable)")

    # macOS AF_UNIX path-cap — keep waves_root short.
    waves_root = Path(tempfile.mkdtemp(prefix="dwv-e2e-"))
    src_dir = Path(tempfile.mkdtemp(prefix="dwv-e2e-src-"))
    bundle_path = waves_root / "real-e2e.duhwave"
    # Reserve a free port so re-runs don't trip over a stuck listener.
    listener_port = args.port or _free_port()

    print(f"  waves_root: {waves_root}")
    print(f"  bundle src: {src_dir}")
    print(f"  listener port (declared in swarm.toml): {listener_port}")

    # 1. Write the spec source tree.
    (src_dir / "swarm.toml").write_text(
        SWARM_TOML_TEMPLATE.format(port=listener_port)
    )
    (src_dir / "manifest.toml").write_text(MANIFEST_TOML)
    (src_dir / "permissions.toml").write_text(PERMISSIONS_TOML)

    # 2. Pack + install.
    pack_bundle(src_dir, bundle_path)
    installer = BundleInstaller(root=waves_root)
    result = installer.install(bundle_path, force=True)
    print(f"\n  installed: {result.name} {result.version} trust={result.trust_level}")

    install_dir = Path(result.path)
    outbox_path = install_dir / "state" / "outbox.jsonl"
    event_log_path = install_dir / "state" / "event.log"

    # 3. Start the daemon as a subprocess.
    env = os.environ.copy()
    daemon_log = waves_root / "host.log"
    daemon_log_fp = daemon_log.open("ab")
    proc = subprocess.Popen(
        [sys.executable, "-m", "duh.duhwave.cli.daemon", str(waves_root)],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=daemon_log_fp,
        stderr=daemon_log_fp,
        start_new_session=True,
    )
    print(f"  daemon pid={proc.pid}")

    try:
        if not await _wait_for(lambda: is_daemon_running(waves_root), timeout=8.0):
            print("ERROR: daemon never bound socket", file=sys.stderr)
            return 1

        # The daemon walks each installed swarm's [[triggers]] and
        # auto-boots the matching listener (ADR-031 §B). Wait for the
        # webhook port to come up so we know the listener is live.
        if not await _wait_for(
            lambda: _port_is_open("127.0.0.1", listener_port),
            timeout=8.0,
        ):
            print(
                f"ERROR: daemon never bound webhook listener to port {listener_port}",
                file=sys.stderr,
            )
            return 1
        print(
            f"  listener (daemon-managed): "
            f"http://127.0.0.1:{listener_port}/support/inbox"
        )

        # 4. Sanity ping the daemon's RPC.
        try:
            pong = await asyncio.to_thread(rpc_call, waves_root, {"op": "ping"})
            print(f"  daemon ping: {pong}")
        except HostRPCError as e:
            print(f"  daemon ping FAILED: {e}", file=sys.stderr)
            return 1

        # 5. POST a real webhook to the listener — this is the
        # external-message-bus event the user would see in production.
        update = {
            "message": {
                "id": 42,
                "text": "What's the difference between asyncio.gather and asyncio.wait?",
                "from": {"id": 9001, "name": "developer"},
            },
            "delivered_at": time.time(),
        }
        status = await _post_json(
            "127.0.0.1", listener_port, "/support/inbox", update
        )
        print(f"\n  POST /support/inbox → HTTP {status}")

        # 6. Wait for the dispatcher to pick it up + run + write outbox.
        # Daemon's poll interval is 0.5s; OpenAI call adds ~3-10s.
        timeout = 30.0 if has_key else 5.0

        def _outbox_has_one() -> bool:
            if not outbox_path.exists():
                return False
            return outbox_path.stat().st_size > 0

        landed = await _wait_for(_outbox_has_one, timeout=timeout)

        # 7. Inspect via the daemon's RPC + read the outbox.
        try:
            ls = await asyncio.to_thread(
                rpc_call, waves_root, {"op": "ls_tasks"}
            )
            print(f"\n  ls_tasks: {len(ls.get('tasks', []))} task(s)")
            for t in ls.get("tasks", []):
                print(
                    f"    - {t.get('task_id')} status={t.get('status')} "
                    f"prompt={t.get('prompt','')[:60]!r}"
                )
        except HostRPCError as e:
            print(f"  ls_tasks failed: {e}", file=sys.stderr)

        try:
            inspect = await asyncio.to_thread(
                rpc_call, waves_root, {"op": "inspect", "swarm_id": "real-e2e"}
            )
            state = inspect.get("state", {})
            print(
                f"  inspect: active={state.get('active_tasks')} "
                f"completed={state.get('completed_tasks')} "
                f"failed={state.get('failed_tasks')}"
            )
        except HostRPCError as e:
            print(f"  inspect failed: {e}", file=sys.stderr)

        if landed:
            print("\n  ── outbox.jsonl ─────────────────────────────────────────")
            for line in outbox_path.read_text().splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                result_blob = rec.get("result", "")
                # The runner serialises {"text": ..., "usage": ...} —
                # parse so the demo prints the reply itself.
                try:
                    parsed = json.loads(result_blob)
                    text = parsed.get("text", "")
                    usage = parsed.get("usage", {})
                    print(f"  agent: {rec['agent']}")
                    print(f"  trigger_kind: {rec['trigger_kind']}")
                    print(f"  trigger_source: {rec['trigger_source']}")
                    print(f"  reply ({len(text)}b):  {text!r}")
                    print(f"  usage: {usage}")
                except json.JSONDecodeError:
                    print(f"  raw: {result_blob[:200]!r}")
        else:
            print(
                f"\n  ⚠  outbox not written within {timeout}s — "
                f"dispatcher may not have routed the trigger"
            )

        # 8. Tail the event log for trace evidence.
        if event_log_path.exists():
            print("\n  ── event.log (tail) ─────────────────────────────────────")
            for line in event_log_path.read_text().splitlines()[-8:]:
                print(f"    {line}")

        # 9. Stop the daemon cleanly. The daemon-managed listener
        # tears down inside the daemon's own finally block.
        try:
            await asyncio.to_thread(rpc_call, waves_root, {"op": "shutdown"})
        except HostRPCError:
            os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
        print(f"\n  daemon stopped (rc={proc.returncode})")

    finally:
        daemon_log_fp.close()
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=2)

    print("\ndemo complete.")
    return 0 if has_key and landed else (0 if not has_key else 1)


def main() -> int:
    parser = argparse.ArgumentParser(prog="real_e2e", description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="webhook listener port (0 = pick a free ephemeral port)",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("\n[real_e2e] interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
