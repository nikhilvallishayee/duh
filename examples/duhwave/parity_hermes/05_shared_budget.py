#!/usr/bin/env python3
"""05 — Shared iteration budget + depth-1 invariant (Hermes pattern → ADR-031).

Hermes Agent prevents runaway recursion through ``_active_children`` +
a shared :class:`IterationBudget`: parent and any sub-agents draw
from the same pool, so a sub-agent can't blow the budget out by
recursively spawning more.

duhwave realises this through a structural invariant — the
:class:`Role` dataclass carries a ``spawn_depth`` field, and
:meth:`Role.child_role` returns a child with ``spawn_depth=0``.
Workers therefore *cannot* spawn workers; the recursion tree is
bounded at depth 1 by construction. Any attempt by a worker to
construct a child role raises ``ValueError`` immediately.

This script demonstrates both halves:

1. The depth-1 invariant — coordinator can spawn (depth=1), the
   resulting worker has depth=0, and ``worker_role.child_role()``
   raises ``ValueError``.
2. A tiny ``IterationBudget`` dataclass mirroring the cookbook's
   example, exhausting cleanly when its remaining count hits zero.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_hermes/05_shared_budget.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.coordinator import BUILTIN_ROLES, Role  # noqa: E402


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


# ---- the cookbook's IterationBudget --------------------------------------
# Mirrors docs/cookbook/build-your-own-agent.md §2.5 verbatim.


@dataclass
class IterationBudget:
    """Shared turn budget — parent and children draw from the same pool."""

    remaining: int

    def take(self, n: int = 1) -> bool:
        if self.remaining < n:
            return False
        self.remaining -= n
        return True


# ---- the demo ------------------------------------------------------------


def main() -> int:
    section("05 — Shared budget + depth-1 invariant (Hermes → ADR-031)")
    print()
    print("  Hermes:  shared IterationBudget across parent + sub-agents.")
    print("  duhwave: structural depth-1 invariant — Role.spawn_depth=1 for")
    print("           coordinator, =0 for workers. Workers cannot spawn workers.")
    print("           Any child_role() call from a worker raises ValueError.")

    # ---- 1. depth-1 invariant ---------------------------------------
    section("1. The depth-1 invariant — workers cannot spawn workers")

    coord_role: Role = BUILTIN_ROLES["coordinator"]
    worker_role: Role = BUILTIN_ROLES["worker"]

    print()
    print(f"  coordinator role: name={coord_role.name!r}  spawn_depth={coord_role.spawn_depth}")
    print(f"  worker role:      name={worker_role.name!r}  spawn_depth={worker_role.spawn_depth}")
    print()

    if coord_role.spawn_depth != 1:
        fail(f"expected coordinator.spawn_depth=1, got {coord_role.spawn_depth}")
        return 1
    if worker_role.spawn_depth != 0:
        fail(f"expected worker.spawn_depth=0, got {worker_role.spawn_depth}")
        return 1
    ok("coordinator.spawn_depth == 1; worker.spawn_depth == 0")

    step("coordinator.child_role()  — should succeed (depth budget available)")
    child_from_coord = coord_role.child_role()
    print(
        f"    child role: name={child_from_coord.name!r}  "
        f"spawn_depth={child_from_coord.spawn_depth}  "
        f"tool_allowlist={list(child_from_coord.tool_allowlist)!r}"
    )
    if child_from_coord.spawn_depth != 0:
        fail(f"child of coordinator should have spawn_depth=0, got {child_from_coord.spawn_depth}")
        return 1
    ok("coordinator → child_role() returns worker with spawn_depth=0")

    step("worker.child_role()  — must raise ValueError (depth=0)")
    try:
        worker_role.child_role()
    except ValueError as e:
        ok(f"raised as expected: {e}")
    else:
        fail("worker.child_role() did NOT raise — depth-1 invariant broken!")
        return 1

    step("child_from_coord.child_role()  — must also raise (recursive case)")
    try:
        child_from_coord.child_role()
    except ValueError as e:
        ok(f"raised as expected: {e}")
    else:
        fail(
            "spawned child .child_role() did NOT raise — recursion bound broken!"
        )
        return 1

    ok("depth-1 invariant: workers cannot spawn workers")

    # ---- 2. shared IterationBudget (cookbook §2.5) -------------------
    section("2. Shared IterationBudget (cookbook §2.5)")

    print()
    print("  Mirroring the cookbook's example: a single budget object passed")
    print("  to parent + any children. Decrement on each simulated turn;")
    print("  exhaustion blocks further work.")
    print()

    budget = IterationBudget(remaining=5)
    step(f"budget: IterationBudget(remaining={budget.remaining})")

    turns_run = 0
    for turn in range(1, 8):
        if not budget.take():
            print(f"    turn {turn}: ✗ budget refused (remaining={budget.remaining})")
            break
        turns_run += 1
        print(f"    turn {turn}: ✓ took 1 (remaining={budget.remaining})")

    if turns_run != 5:
        fail(f"expected 5 turns to run, got {turns_run}")
        return 1
    if budget.remaining != 0:
        fail(f"expected budget.remaining=0 after exhaustion, got {budget.remaining}")
        return 1
    ok(f"budget exhausted cleanly: {turns_run} turns ran, then take() refused")

    # ---- 3. larger take across parent + child (sharing semantics) ----
    section("3. Sharing semantics — parent and child draw from the same pool")

    shared = IterationBudget(remaining=10)
    step(f"shared = IterationBudget(remaining={shared.remaining})")
    step("parent.take(3)  — reserves a chunk for itself")
    assert shared.take(3)
    print(f"    after parent: remaining={shared.remaining}")
    step("child.take(5)  — child draws from the SAME budget object")
    assert shared.take(5)
    print(f"    after child:  remaining={shared.remaining}")
    step("child.take(5)  — must refuse (only 2 left)")
    if shared.take(5):
        fail("budget allowed take(5) when only 2 remained — sharing broken")
        return 1
    ok(f"shared budget refused over-draw (remaining={shared.remaining})")

    section("Summary")
    ok("depth-1 invariant: workers cannot spawn workers")
    ok("shared budget exhausts cleanly at limit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
