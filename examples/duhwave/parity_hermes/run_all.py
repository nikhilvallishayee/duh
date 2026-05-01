#!/usr/bin/env python3
"""run_all — run every parity demo, then print the unified Hermes parity matrix.

Spawns the five companion scripts as subprocesses (so each runs in
its own process / asyncio loop / RLM REPL — no cross-contamination).
Captures the final ``✓`` / ``✗`` line from each, and prints a single
matrix at the end.

Exit 0 only if every script exited 0 *and* its final line started
with ``✓``.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_hermes/run_all.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]


@dataclass(slots=True, frozen=True)
class ParityScript:
    """One row of the parity matrix."""

    number: int
    script: str
    hermes_pattern: str
    duhwave_realisation: str


SCRIPTS: list[ParityScript] = [
    ParityScript(
        number=1,
        script="01_multimode_adapters.py",
        hermes_pattern="multi-mode native adapters",
        duhwave_realisation="ADR-027: 8 native providers in PROVIDER_TIER_MODELS",
    ),
    ParityScript(
        number=2,
        script="02_tool_arg_repair.py",
        hermes_pattern="_repair_tool_call_arguments",
        duhwave_realisation="ADR-028: duh.adapters.tool_repair.repair_tool_arguments",
    ),
    ParityScript(
        number=3,
        script="03_parallel_dispatch.py",
        hermes_pattern="_PARALLEL_SAFE_TOOLS + _MAX_TOOL_WORKERS",
        duhwave_realisation="coordinator fan-out via asyncio.gather over Spawn",
    ),
    ParityScript(
        number=4,
        script="04_rlm_replaces_compaction.py",
        hermes_pattern="context_compressor (50% trigger / 20% target)",
        duhwave_realisation="ADR-028 RLM substrate — bytes by reference, never summarised",
    ),
    ParityScript(
        number=5,
        script="05_shared_budget.py",
        hermes_pattern="_active_children + shared IterationBudget",
        duhwave_realisation="ADR-031 spawn_depth=1 invariant + IterationBudget dataclass",
    ),
]


@dataclass(slots=True)
class ScriptOutcome:
    spec: ParityScript
    exit_code: int
    last_line: str
    wall_seconds: float

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and self.last_line.lstrip().startswith("✓")


# ---- pretty output -------------------------------------------------------


def banner(title: str) -> None:
    print()
    print("╔" + "═" * 70 + "╗")
    print(f"║  {title:<68}║")
    print("╚" + "═" * 70 + "╝")


def section(title: str) -> None:
    print()
    print("─" * 72)
    print(f"  {title}")
    print("─" * 72)


def run_one(spec: ParityScript) -> ScriptOutcome:
    section(f"[{spec.number}/5] {spec.script}  —  {spec.hermes_pattern}")
    script_path = _HERE / spec.script
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    )
    wall = time.perf_counter() - t0

    # Surface stderr only on failure — stdout always passes through abridged.
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    last_lines = [ln for ln in stdout.splitlines() if ln.strip()]
    last_line = last_lines[-1] if last_lines else "(no output)"

    if proc.returncode != 0:
        print(f"  ✗ exit_code={proc.returncode}")
        if stderr.strip():
            print("  stderr:")
            for line in stderr.rstrip().splitlines()[-15:]:
                print(f"    {line}")
        if stdout.strip():
            print("  last stdout lines:")
            for line in last_lines[-10:]:
                print(f"    {line}")
    else:
        print(f"  → {last_line}")
        print(f"  wall: {wall:.3f}s  exit: {proc.returncode}")

    return ScriptOutcome(
        spec=spec,
        exit_code=proc.returncode,
        last_line=last_line,
        wall_seconds=wall,
    )


def print_matrix(outcomes: list[ScriptOutcome]) -> None:
    banner("Hermes parity matrix")
    print()
    # Column widths.
    n_w = 3
    pat_w = 42
    real_w = 64
    res_w = 6
    print(f"  {'#':<{n_w}} {'Hermes pattern':<{pat_w}} {'duhwave realisation':<{real_w}} {'pass':<{res_w}}")
    print(f"  {'-' * n_w} {'-' * pat_w} {'-' * real_w} {'-' * res_w}")
    for o in outcomes:
        marker = "✓" if o.passed else "✗"
        pat = o.spec.hermes_pattern
        real = o.spec.duhwave_realisation
        # Truncate gracefully so the table stays aligned.
        if len(pat) > pat_w:
            pat = pat[: pat_w - 1] + "…"
        if len(real) > real_w:
            real = real[: real_w - 1] + "…"
        print(f"  {o.spec.number:<{n_w}} {pat:<{pat_w}} {real:<{real_w}} {marker:<{res_w}}")
    print()
    total = len(outcomes)
    passed = sum(1 for o in outcomes if o.passed)
    total_wall = sum(o.wall_seconds for o in outcomes)
    print(f"  passed:    {passed}/{total}")
    print(f"  wall-time: {total_wall:.2f}s")
    print()
    if passed == total:
        print("  ╔══════════════════════════════════════════════════════════════════╗")
        print("  ║  ✓  Hermes parity demonstrated across all 5 patterns.            ║")
        print("  ╚══════════════════════════════════════════════════════════════════╝")
    else:
        print("  ╔══════════════════════════════════════════════════════════════════╗")
        print(f"  ║  ✗  Parity FAILED: {passed}/{total} patterns passed.{' ' * 33}║")
        print("  ╚══════════════════════════════════════════════════════════════════╝")


def main() -> int:
    banner("Hermes Agent → duhwave parity demonstration")
    print()
    print("  Five hermetic scripts, one Hermes opinion each. No model calls,")
    print("  no network, deterministic stub runners only.")

    outcomes: list[ScriptOutcome] = []
    for spec in SCRIPTS:
        outcomes.append(run_one(spec))

    print_matrix(outcomes)

    return 0 if all(o.passed for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
