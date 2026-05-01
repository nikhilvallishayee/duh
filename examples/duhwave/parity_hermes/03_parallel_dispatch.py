#!/usr/bin/env python3
"""03 — Parallel-safe tool dispatch (Hermes pattern → coordinator fan-out).

Hermes Agent maintains a ``_PARALLEL_SAFE_TOOLS`` allowlist
(``Read`` / ``Glob`` / ``Grep`` / ``WebFetch`` / ``WebSearch``)
plus ``_MAX_TOOL_WORKERS=8``. Read-only tools dispatch concurrently;
mutating tools run sequentially to preserve ordering. The wall-clock
win on multi-Read turns is large.

duhwave realises the same opinion at the coordinator layer: when a
coordinator emits multiple ``Spawn`` calls in one turn, the host can
dispatch them concurrently via :func:`asyncio.gather`. Each worker
gets its own scoped :class:`RLMHandleView` over the coordinator's
REPL handles; results bind back into the coordinator's namespace
under distinct ``bind_as`` names — no data races.

This script spawns **three** researcher workers in parallel against
the same ``codebase`` handle, each with a different prompt and
``bind_as``. Stub runner sleeps 0.2 s per call. Wall-clock should
be ≈ 0.2 s (parallel) rather than ≈ 0.6 s (sequential).

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_hermes/03_parallel_dispatch.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.coordinator import BUILTIN_ROLES, RLMHandleView  # noqa: E402
from duh.duhwave.coordinator.spawn import Spawn  # noqa: E402
from duh.duhwave.rlm.repl import RLMRepl  # noqa: E402
from duh.duhwave.task.registry import (  # noqa: E402
    Task,
    TaskRegistry,
    TaskStatus,
)


# ---- pretty output -------------------------------------------------------


def section(title: str) -> None:
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def step(msg: str) -> None:
    print(f"  → {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


# ---- demo content --------------------------------------------------------

_CODEBASE = "\n".join(
    f"def func_{i}(x: int) -> int: return x * {i}" for i in range(40)
)


# ---- stub WorkerRunner ---------------------------------------------------


_WORKER_DELAY_S = 0.20


async def stub_worker_runner(task: Task, view: RLMHandleView) -> str:
    """Canned worker: sleep 0.2s, peek the codebase, return a fake count.

    A real runner would call the engine. We do not — this script
    measures the dispatch-layer parallelism, not model latency.
    """
    await asyncio.sleep(_WORKER_DELAY_S)
    head = await view.peek("codebase", start=0, end=80)
    # Pretend we counted matches; deterministic per-prompt result.
    n = task.prompt.count(" ") + 3
    return f"found {n} lines matching for prompt {task.prompt!r}; head={head[:32]!r}"


class _NullToolContext:
    """Placeholder — Spawn's depth gate fires before context is touched."""

    pass


# ---- the demo ------------------------------------------------------------


async def main() -> int:
    section("03 — Parallel-safe dispatch (Hermes → coordinator fan-out)")
    print()
    print("  Hermes:  _PARALLEL_SAFE_TOOLS allowlist + _MAX_TOOL_WORKERS=8.")
    print("  duhwave: multiple Spawn calls in one coordinator turn,")
    print("           dispatched concurrently via asyncio.gather.")
    print()
    print(f"  Stub worker latency: {_WORKER_DELAY_S}s each.")
    print(f"  Workers spawned:     3 (parallel).")
    print(f"  Expected wall-time:  ≈{_WORKER_DELAY_S:.2f}s (NOT 3 × {_WORKER_DELAY_S:.2f}s = "
          f"{3 * _WORKER_DELAY_S:.2f}s)")

    coord_repl = RLMRepl()
    await coord_repl.start()
    try:
        section("Setup")
        await coord_repl.bind("codebase", _CODEBASE)
        ok(f"coordinator REPL bound 'codebase' ({len(_CODEBASE):,} chars)")

        with tempfile.TemporaryDirectory() as td:
            session_dir = Path(td)
            session_id = "parity-hermes-03"
            registry = TaskRegistry(session_dir, session_id)

            # One Spawn tool per call — each is independent state.
            def make_spawn() -> Spawn:
                return Spawn(
                    repl=coord_repl,
                    registry=registry,
                    parent_role=BUILTIN_ROLES["coordinator"],
                    session_id=session_id,
                    worker_runner=stub_worker_runner,
                )

            section("Three Spawn calls dispatched in parallel via asyncio.gather")
            specs = [
                ("Find all functions", "researcher_funcs"),
                ("Find all imports", "researcher_imports"),
                ("Find all classes", "researcher_classes"),
            ]
            for prompt, bind_as in specs:
                step(f"prepare Spawn(prompt={prompt!r}, bind_as={bind_as!r})")

            ctx = _NullToolContext()

            async def run_one(prompt: str, bind_as: str) -> dict[str, object]:
                spawn = make_spawn()
                result = await spawn.call(
                    {
                        "prompt": prompt,
                        "expose": ["codebase"],
                        "bind_as": bind_as,
                        "max_turns": 1,
                    },
                    ctx,
                )
                return {
                    "bind_as": bind_as,
                    "is_error": result.is_error,
                    "summary": (result.metadata or {}).get("summary", ""),
                }

            t0 = time.perf_counter()
            results = await asyncio.gather(
                *(run_one(prompt, bind_as) for prompt, bind_as in specs)
            )
            wall = time.perf_counter() - t0

            section("Results")
            for r in results:
                marker = "✓" if not r["is_error"] else "✗"
                print(f"  {marker} bind_as={r['bind_as']!r}  summary={r['summary']!r}")

            print()
            print(f"  wall-time:    {wall:.3f}s")
            print(f"  sequential:   ≈{len(specs) * _WORKER_DELAY_S:.3f}s")
            print(f"  parallel:     ≈{_WORKER_DELAY_S:.3f}s")

            section("Verification")
            # Wall must be much closer to single-leg latency than to sum.
            sequential = len(specs) * _WORKER_DELAY_S
            ceiling = _WORKER_DELAY_S * 1.8  # generous: scheduler jitter + REPL roundtrips
            if wall > ceiling:
                fail(
                    f"wall-time {wall:.3f}s exceeds parallel ceiling {ceiling:.3f}s "
                    f"— dispatch is NOT actually parallel."
                )
                return 1
            ok(f"wall-time {wall:.3f}s ≪ sequential {sequential:.3f}s — parallel confirmed")

            # Verify each result handle was bound back into the coordinator REPL.
            for _, bind_as in specs:
                if coord_repl.handles.get(bind_as) is None:
                    fail(f"handle {bind_as!r} was not bound back into coordinator REPL")
                    return 1
            ok(f"all {len(specs)} worker results bound back as named handles")

            # Verify each task transitioned to COMPLETED in the registry.
            completed = sum(1 for t in registry if t.status is TaskStatus.COMPLETED)
            if completed != len(specs):
                fail(f"only {completed}/{len(specs)} tasks reached COMPLETED")
                return 1
            ok(f"all {completed}/{len(specs)} tasks COMPLETED in registry")

            section("Summary")
            ok(f"parallel-safe dispatch: {len(specs)} spawns in {wall:.2f}s")
            return 0
    finally:
        await coord_repl.shutdown()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
