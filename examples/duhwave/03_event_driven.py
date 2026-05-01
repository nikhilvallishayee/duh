#!/usr/bin/env python3
"""03 — Event-driven ingress: webhook → trigger → subscription match.

Demonstrates the data flow ADR-031 §B describes:

    HTTP POST → WebhookListener → Trigger record → TriggerLog (jsonl)
                                                      │
                                                      └→ SubscriptionMatcher
                                                          (kind+source glob → agent_id)

A webhook listener binds on an OS-chosen free port, the demo POSTs
three requests at three different URL paths, and the
:class:`SubscriptionMatcher` (built from a small in-line swarm spec)
routes each landed trigger to the appropriate agent.

This is what makes a duhwave host "always-on": a daemon process
listening for external events instead of being launched per query.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/03_event_driven.py

Self-contained. No model calls. No external network — everything is
on 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import socket
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.ingress import (  # noqa: E402
    SubscriptionMatcher,
    TriggerLog,
    WebhookListener,
)
from duh.duhwave.spec import parse_swarm  # noqa: E402


# ---- inline swarm topology ---------------------------------------------

_SWARM_TOML = """\
[swarm]
name = "event-demo"
version = "0.1.0"
description = "event-demo: route webhooks to agents"
format_version = 1

[[agents]]
id = "issue_triage"
role = "researcher"
model = "anthropic/claude-haiku-4-5"
tools = ["search"]

[[agents]]
id = "deploy_bot"
role = "coordinator"
model = "anthropic/claude-sonnet-4-5"
tools = ["bash"]

[[agents]]
id = "default_handler"
role = "researcher"
model = "anthropic/claude-haiku-4-5"
tools = []

# First match wins. The wildcard route is the catch-all.
[[triggers]]
kind = "webhook"
source = "/issues/*"
target_agent_id = "issue_triage"

[[triggers]]
kind = "webhook"
source = "/deploy/*"
target_agent_id = "deploy_bot"

[[triggers]]
kind = "webhook"
source = "/*"
target_agent_id = "default_handler"

[budget]
max_tokens_per_hour = 100000
max_usd_per_day = 1.0
max_concurrent_tasks = 2
"""


# ---- pretty output -----------------------------------------------------


def section(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def step(msg: str) -> None:
    print(f"  → {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


# ---- helpers -----------------------------------------------------------


def _free_port() -> int:
    """Bind a transient socket on 127.0.0.1 to discover an unused port."""
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    return port


def _post_json_blocking(host: str, port: int, path: str, body: dict) -> int:
    """POST a JSON body to the webhook (blocking). Return status code.

    Wrapped in :func:`asyncio.to_thread` by the caller because the
    webhook listener shares this event loop — calling
    ``http.client.HTTPConnection.getresponse()`` from the same loop
    would deadlock.
    """
    conn = http.client.HTTPConnection(host, port, timeout=2.0)
    try:
        payload = json.dumps(body).encode("utf-8")
        conn.request(
            "POST",
            path,
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        resp.read()  # drain
        return resp.status
    finally:
        conn.close()


async def _post_json(host: str, port: int, path: str, body: dict) -> int:
    """Async wrapper — runs the blocking POST in a worker thread."""
    return await asyncio.to_thread(_post_json_blocking, host, port, path, body)


# ---- the demo ----------------------------------------------------------


async def main() -> int:
    section("Event-driven ingress demo — ADR-031 §B")
    print()
    print("  webhook listener on 127.0.0.1:<free port>")
    print("  → POST /issues/123     should route to 'issue_triage'")
    print("  → POST /deploy/prod    should route to 'deploy_bot'")
    print("  → POST /unknown/foo    should fall through to 'default_handler'")

    # ---- 1. parse the topology --------------------------------------
    section("1. Parse swarm topology")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        spec_path = td_path / "swarm.toml"
        spec_path.write_text(_SWARM_TOML)
        spec = parse_swarm(spec_path)
        ok(
            f"parsed swarm {spec.name!r} v{spec.version}: "
            f"{len(spec.agents)} agents, {len(spec.triggers)} subscriptions"
        )

        # ---- 2. build the matcher --------------------------------
        section("2. Build SubscriptionMatcher")
        matcher = SubscriptionMatcher.from_spec(spec)
        ok(f"matcher has {len(matcher)} subscriptions")

        # ---- 3. start webhook listener ---------------------------
        section("3. Start WebhookListener on a free port")
        triggers_path = td_path / "triggers.jsonl"
        log = TriggerLog(triggers_path)
        port = _free_port()
        listener = WebhookListener(log, port=port, host="127.0.0.1")
        await listener.start()
        ok(f"listener bound to http://127.0.0.1:{port}")

        try:
            # ---- 4. POST three webhooks --------------------------
            section("4. POST three webhooks (different paths)")
            posts = [
                ("/issues/123", {"title": "bug: race in retry", "user": "alice"}),
                ("/deploy/prod", {"sha": "abcd123", "env": "production"}),
                ("/unknown/foo", {"random": True}),
            ]
            for path, body in posts:
                status = await _post_json("127.0.0.1", port, path, body)
                ok(f"POST {path}  → HTTP {status}")
                if status != 202:
                    print(f"  ✗ unexpected status: {status}")
                    return 1

            # The webhook handler runs in the listener's event loop;
            # let it drain before we read the log.
            await asyncio.sleep(0.05)

            # ---- 5. replay + route -------------------------------
            section("5. Replay triggers and route each via the matcher")
            replayed = TriggerLog(triggers_path).replay()
            ok(f"trigger log has {len(replayed)} entries")
            print()

            expected = {
                "/issues/123": "issue_triage",
                "/deploy/prod": "deploy_bot",
                "/unknown/foo": "default_handler",
            }
            for trig in replayed:
                target = matcher.route(trig)
                expected_target = expected.get(trig.source, "?")
                status = "✓" if target == expected_target else "✗"
                print(
                    f"    {status} {trig.kind.value:8} {trig.source:20} "
                    f"→ {target}"
                )
                if target != expected_target:
                    print(
                        f"      expected {expected_target}, got {target}"
                    )
                    return 1

        finally:
            # ---- 6. clean teardown ------------------------------
            section("6. Stop the listener")
            await listener.stop()
            ok("listener stopped")

    print()
    print("event demo OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
