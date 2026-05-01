#!/usr/bin/env python3
"""01 — Four-channel routing (no daemon).

Parses ``swarm.toml``, builds a :class:`SubscriptionMatcher` from it,
synthesises one :class:`Trigger` of each of the four kinds the spec
declares, and prints the ``(kind, source) -> agent_id`` resolution.

Demonstrates one half of OpenClaw's headline shape: a single declared
topology that fans inbound events of *different kinds* out to
different handlers. The other half — persistence across restarts — is
script 02's job.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_claw/01_four_channels.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.ingress import (  # noqa: E402
    SubscriptionMatcher,
    Trigger,
    TriggerKind,
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


def main() -> int:
    section("01 - four-channel routing (no daemon)")
    step(f"parsing spec: {SPEC_PATH.name}")
    spec = parse_swarm(SPEC_PATH)
    step(
        f"swarm '{spec.name}' v{spec.version}: "
        f"{len(spec.agents)} agents, {len(spec.triggers)} triggers"
    )

    matcher = SubscriptionMatcher.from_spec(spec)
    step(f"matcher built with {len(matcher)} subscription row(s)")

    # One synthetic Trigger per channel. Sources are picked to *match*
    # the subscriptions in swarm.toml exactly so all four route.
    examples: list[Trigger] = [
        Trigger(
            kind=TriggerKind.WEBHOOK,
            source="/github/issue",
            payload={"action": "opened", "issue": {"number": 42}},
        ),
        Trigger(
            kind=TriggerKind.FILEWATCH,
            source="./watch",
            payload={"changes": [{"type": "modified", "path": "src/auth.py"}]},
        ),
        Trigger(
            kind=TriggerKind.CRON,
            source="*/5 * * * *",
            payload={"fired_at": 1745625900.0},
        ),
        Trigger(
            kind=TriggerKind.MANUAL,
            source="manual:nudge",
            payload={"by": "operator", "note": "kick the tires"},
        ),
    ]

    section("Routing each synthetic trigger through the matcher")
    print(f"    {'kind':<10}  {'source':<20}  ->  agent_id")
    print(f"    {'-'*10}  {'-'*20}      {'-'*12}")
    matched = 0
    for tr in examples:
        target = matcher.route(tr)
        verdict = target if target is not None else "(no match)"
        print(f"    {tr.kind.value:<10}  {tr.source:<20}  ->  {verdict}")
        if target is not None:
            matched += 1

    section("Result")
    if matched == len(examples):
        ok(f"4-channel routing: {matched}/{len(examples)} triggers matched")
        return 0
    print(f"  x only {matched}/{len(examples)} triggers matched")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
