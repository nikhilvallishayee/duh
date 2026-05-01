#!/usr/bin/env python3
"""Run all four parity_claw demo scripts in sequence.

Prints a unified "Clawbot parity matrix" at the end. Exits 0 only if
every script exited 0.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_claw/run_all.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).parent.resolve()
_REPO = HERE.parents[2]


@dataclass(slots=True)
class _Stage:
    label: str
    property_: str
    script: Path
    rc: int = -1
    final_line: str = ""


_STAGES: list[_Stage] = [
    _Stage(
        label="01 four-channel routing",
        property_="multi-channel routing",
        script=HERE / "01_four_channels.py",
    ),
    _Stage(
        label="02 persistent state",
        property_="persistent across crash",
        script=HERE / "02_persistent_state.py",
    ),
    _Stage(
        label="03 concurrent ingress",
        property_="concurrent fan-in",
        script=HERE / "03_concurrent_ingress.py",
    ),
    _Stage(
        label="04 per-swarm isolation",
        property_="per-swarm isolation",
        script=HERE / "04_per_channel_isolation.py",
    ),
]


def _banner(title: str) -> None:
    print()
    print("#" * 72)
    print(f"#  {title}")
    print("#" * 72)


def _final_line(captured: str) -> str:
    """Pull the last non-empty line from a captured stdout chunk."""
    for line in reversed(captured.splitlines()):
        s = line.strip()
        if s:
            return s
    return "(no output)"


def _run_stage(stage: _Stage) -> None:
    """Run one script, stream its stdout live, capture for the summary."""
    _banner(stage.label)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, str(stage.script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        bufsize=1,
        text=True,
    )
    captured: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        captured.append(line)
    rc = proc.wait()
    stage.rc = rc
    stage.final_line = _final_line("".join(captured))


def main() -> int:
    print()
    print("parity_claw - OpenClaw-shape feature-parity demo for duhwave")
    print("------------------------------------------------------------")
    print("Four scripts, one matrix. Each script tests one OpenClaw-style")
    print("property and prints its result on the final line.")

    for stage in _STAGES:
        _run_stage(stage)

    _banner("Clawbot parity matrix")
    print()
    print(f"  {'OpenClaw property':<26}  {'duhwave realisation':<22}  result")
    print(f"  {'-' * 26}  {'-' * 22}  ------")
    rows: list[tuple[str, str, str]] = [
        ("multi-channel routing",      "SubscriptionMatcher",     _STAGES[0].final_line),
        ("persistent across restart",  "TriggerLog.replay",       _STAGES[1].final_line),
        ("concurrent fan-in",          "shared O_APPEND log",     _STAGES[2].final_line),
        ("per-skill isolation",        "<root>/<name>/<version>", _STAGES[3].final_line),
    ]
    for openclaw_prop, duhwave_prop, result in rows:
        # Trim long final-line text — keep up to 28 chars
        trimmed = result if len(result) < 60 else result[:57] + "..."
        print(f"  {openclaw_prop:<26}  {duhwave_prop:<22}  {trimmed}")

    print()
    fails = [s for s in _STAGES if s.rc != 0]
    if fails:
        print(f"FAILED: {len(fails)}/{len(_STAGES)} stages")
        for s in fails:
            print(f"  - {s.label}: rc={s.rc}")
        return 1

    print(f"PASSED: {len(_STAGES)}/{len(_STAGES)} stages")
    print()
    print("Caveat: OpenClaw is a multi-channel personal-assistant gateway")
    print("(WhatsApp, Slack, iMessage, ...). parity_claw demonstrates the")
    print("same architectural shape — always-on, multi-channel, persistent,")
    print("per-skill-isolated — but as a coding-agent harness substrate, not")
    print("as a messaging product. The channels here (webhook + filewatch +")
    print("cron + manual) are duhwave's native ingress kinds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
