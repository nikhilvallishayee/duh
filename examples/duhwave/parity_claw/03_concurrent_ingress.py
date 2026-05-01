#!/usr/bin/env python3
"""03 — Four channels firing concurrently into one TriggerLog.

OpenClaw's gateway brokers events from many channels at once: a Slack
message, a calendar tick, an email, and a webhook can all hit the same
agent runtime simultaneously. The duhwave realisation is one shared
:class:`TriggerLog` with append-only durability — every listener
appends to the same JSONL, no per-channel queues, no per-channel locks
beyond what filesystem ``O_APPEND`` already gives us.

This script simulates that fan-in by running 4 channels (webhook,
filewatch, cron, manual) x 4 concurrent triggers each = 16 total
appends through ``asyncio.gather``. We bypass the listener processes
themselves (filewatch + cron need ``watchfiles`` / ``croniter`` deps
that may not be installed) and instead append :class:`Trigger` records
directly with the appropriate ``kind`` — which is the same data path
the real listeners take.

Then we replay the log and verify all 16 made it.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_claw/03_concurrent_ingress.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.ingress import (  # noqa: E402
    SubscriptionMatcher,
    Trigger,
    TriggerKind,
    TriggerLog,
)
from duh.duhwave.spec import parse_swarm  # noqa: E402

SPEC_PATH = Path(__file__).parent / "swarm.toml"


def section(title: str) -> None:
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def step(msg: str) -> None:
    print(f"  -> {msg}")


def ok(msg: str) -> None:
    print(f"  \u2713 {msg}")


def fail(msg: str) -> None:
    print(f"  x {msg}")


# Each channel produces 4 triggers. The source on each trigger matches
# the subscription pattern in swarm.toml so the matcher resolves them.
_CHANNEL_PLAN: list[tuple[TriggerKind, str]] = [
    (TriggerKind.WEBHOOK, "/github/issue"),
    (TriggerKind.FILEWATCH, "./watch"),
    (TriggerKind.CRON, "*/5 * * * *"),
    (TriggerKind.MANUAL, "manual:nudge"),
]
_FANOUT_PER_CHANNEL = 4


async def _fire_one(log: TriggerLog, kind: TriggerKind, source: str, idx: int) -> Trigger:
    """Append one Trigger; return it for accounting.

    ``log.append`` is synchronous (file IO), so we don't need to do any
    locking — kernel-level ``O_APPEND`` already serialises the writes.
    Wrapping in a coroutine lets ``asyncio.gather`` interleave them.
    """
    tr = Trigger(
        kind=kind,
        source=source,
        payload={"channel": kind.value, "fanout_idx": idx, "ts": time.time()},
    )
    # Yield once so other coroutines really do interleave (otherwise the
    # event loop would run each task to completion in spawn order).
    await asyncio.sleep(0)
    log.append(tr)
    return tr


async def main() -> int:
    section("03 - 4 channels x 4 concurrent triggers each")

    # Demonstrate the matcher will route each kind correctly.
    spec = parse_swarm(SPEC_PATH)
    matcher = SubscriptionMatcher.from_spec(spec)
    step(f"swarm '{spec.name}' has {len(matcher)} subscription(s)")

    waves_root = Path(tempfile.mkdtemp(prefix="dwv-claw-cc-")).resolve()
    triggers_path = waves_root / "triggers.jsonl"
    log = TriggerLog(triggers_path)

    rc = 1
    try:
        # ---- build the 16-task gather list ------------------------
        section("1. Build 16 concurrent append coroutines")
        tasks: list[asyncio.Task[Trigger]] = []
        for kind, source in _CHANNEL_PLAN:
            for i in range(_FANOUT_PER_CHANNEL):
                tasks.append(asyncio.create_task(_fire_one(log, kind, source, i)))
        step(f"{len(_CHANNEL_PLAN)} channels x {_FANOUT_PER_CHANNEL} fanout = {len(tasks)} coroutines")

        # ---- gather -----------------------------------------------
        section("2. asyncio.gather all 16")
        wall_start = time.perf_counter()
        fired = await asyncio.gather(*tasks)
        wall_ms = (time.perf_counter() - wall_start) * 1000
        ok(f"all gathered in {wall_ms:.1f} ms")
        if len(fired) != len(tasks):
            fail(f"expected {len(tasks)} fired, got {len(fired)}")
            return 1

        # ---- count by channel -------------------------------------
        by_kind = Counter(t.kind for t in fired)
        for kind, _ in _CHANNEL_PLAN:
            print(f"    {kind.value:<10}  fired={by_kind[kind]}")

        # ---- verify the log has all 16 ---------------------------
        section("3. Replay TriggerLog and verify shape")
        replayed = TriggerLog(triggers_path).replay()
        ok(f"replay returned {len(replayed)} record(s)")
        if len(replayed) != len(tasks):
            fail(f"expected {len(tasks)} records, got {len(replayed)}")
            return 1
        replayed_by_kind = Counter(t.kind for t in replayed)
        if replayed_by_kind != by_kind:
            fail(f"per-kind shape mismatch:\n   fired   ={dict(by_kind)}"
                 f"\n   replayed={dict(replayed_by_kind)}")
            return 1
        ok(f"replay shape matches: {dict(replayed_by_kind)}")

        # ---- routing verification ---------------------------------
        section("4. Verify each replayed Trigger routes to its agent")
        routed: dict[str, int] = {}
        unrouted = 0
        for tr in replayed:
            target = matcher.route(tr)
            if target is None:
                unrouted += 1
            else:
                routed[target] = routed.get(target, 0) + 1
        for agent_id in sorted(routed):
            print(f"    -> {agent_id:<14}  {routed[agent_id]} trigger(s)")
        if unrouted != 0:
            fail(f"{unrouted} replayed triggers had no routing target")
            return 1
        if len(routed) != len(_CHANNEL_PLAN):
            fail(f"expected {len(_CHANNEL_PLAN)} agents covered, got {len(routed)}")
            return 1
        ok(f"all {len(replayed)} triggers routed across {len(routed)} agents")

        section("Result")
        ok(f"4 channels x {_FANOUT_PER_CHANNEL} concurrent triggers each "
           f"-> {len(replayed)} total in log")
        rc = 0
        return rc
    finally:
        shutil.rmtree(waves_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
