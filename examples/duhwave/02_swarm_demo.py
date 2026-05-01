#!/usr/bin/env python3
"""02 — Coordinator + worker with handle-passing (ADR-029, ADR-031 §A).

Demonstrates the cross-agent boundary:

    coordinator owns the RLMRepl
                ↓ Spawn(...) with expose=["codebase"]
                  (worker can read "codebase" but NOT "spec" — ValueError)
    worker sees an RLMHandleView, runs to completion
                ↓ result text bound back as a new handle in the coordinator's REPL

Three properties exercised:

1.  **Selective handle exposure** — workers see only the names listed
    in ``expose``; access to other handles raises before reaching the
    underlying REPL.
2.  **Runner injection seam** — :class:`Spawn` is decoupled from any
    real model loop. The host injects a ``WorkerRunner`` callable; in
    this script we use a deterministic stub.
3.  **Result rebinding** — when the worker finishes, its output text
    is bound back into the coordinator's REPL as a new handle.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/02_swarm_demo.py

Self-contained. No model calls. No network.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.coordinator import (  # noqa: E402
    BUILTIN_ROLES,
    RLMHandleView,
    Role,
)
from duh.duhwave.coordinator.spawn import Spawn  # noqa: E402
from duh.duhwave.rlm.repl import RLMRepl  # noqa: E402
from duh.duhwave.task.registry import (  # noqa: E402
    Task,
    TaskRegistry,
    TaskStatus,
)


# ---- demo content (small + readable) -----------------------------------

_CODEBASE = '''\
def public_api(user_id: int) -> dict:
    """Return the public profile for ``user_id``."""
    return {"id": user_id, "name": "Anon", "tier": "free"}


def admin_dump(user_id: int) -> dict:
    """Return everything we know — internal-only."""
    return {"id": user_id, "name": "Real", "ssn": "redacted-in-demo"}
'''

_SPEC = '''\
SECRET PROJECT SPEC — DO NOT LEAK

Phase 1: Build the public API.
Phase 2: Migrate from internal store.
Phase 3: Quietly deprecate admin_dump in v3.
'''


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


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


# ---- the demo ----------------------------------------------------------


async def main() -> int:
    section("Swarm demo — coordinator + worker, handle-scoped (ADR-029)")
    print()
    print("  coordinator owns the REPL with two handles:")
    print("    - codebase  (visible to the worker)")
    print("    - spec      (NOT visible — secret)")

    # ---- coordinator REPL with two handles ---------------------------
    coord_repl = RLMRepl()
    await coord_repl.start()
    try:
        await coord_repl.bind("codebase", _CODEBASE)
        await coord_repl.bind("spec", _SPEC)
        ok("coordinator REPL has 2 handles bound")

        # ---- visibility scoping ---------------------------------------
        section("1. Selective handle exposure")
        view = RLMHandleView.from_names(coord_repl, ["codebase"])
        ok(f"built worker view exposing: {view.list_exposed()}")

        step("worker view: peek('codebase', 0, 32)  — allowed")
        head = await view.peek("codebase", start=0, end=32)
        print(f"    {head!r}")

        step("worker view: peek('spec', 0, 32)  — must raise")
        try:
            await view.peek("spec", start=0, end=32)
        except ValueError as e:
            ok(f"raised as expected: {e}")
        else:
            fail("peek('spec') did not raise — boundary violation!")
            return 1

        step("worker view: search('spec', 'PHASE')  — must raise")
        try:
            await view.search("spec", r"PHASE")
        except ValueError as e:
            ok(f"raised as expected: {e}")
        else:
            fail("search('spec', ...) did not raise — boundary violation!")
            return 1

        # ---- Spawn input schema ---------------------------------------
        section("2. Spawn tool — input schema")
        ok("Spawn.name = " + Spawn.name)
        ok("Spawn.input_schema:")
        for line in json.dumps(Spawn.input_schema, indent=2).splitlines():
            print(f"    {line}")

        # ---- runner injection + end-to-end Spawn ----------------------
        section("3. Spawn end-to-end with a stub WorkerRunner")
        step("define a fake worker runner that just peeks the codebase")

        async def fake_worker_runner(task: Task, view: RLMHandleView) -> str:
            """Stand-in for a real agent loop. Returns canned text.

            A real runner would call ``duh.kernel.engine.Engine`` with
            the role's tool set and stream the result. For demo purposes
            we just peek the exposed handle and return a synthesis.
            """
            preview = await view.peek("codebase", start=0, end=80)
            return (
                f"worker analysed {task.expose_handles}\n"
                f"first 80 chars of codebase: {preview!r}\n"
                f"finding: 2 functions defined; one public, one admin-only"
            )

        ok("runner defined (no model calls; deterministic output)")

        with tempfile.TemporaryDirectory() as td:
            session_dir = Path(td)
            session_id = "swarm-demo-session"
            registry = TaskRegistry(session_dir, session_id)

            step("construct Spawn with parent_role=coordinator and inject runner")
            spawn_tool = Spawn(
                repl=coord_repl,
                registry=registry,
                parent_role=BUILTIN_ROLES["coordinator"],
                session_id=session_id,
                worker_runner=fake_worker_runner,
            )

            step('call Spawn(prompt="...", expose=["codebase"], bind_as="audit")')
            result = await spawn_tool.call(
                {
                    "prompt": "Audit the codebase for public/private boundary smells.",
                    "expose": ["codebase"],
                    "bind_as": "audit",
                    "max_turns": 1,
                },
                _NullToolContext(),
            )

            if result.is_error:
                fail(f"Spawn returned error: {result.output}")
                return 1
            ok("Spawn returned successfully")
            print("    output:")
            for line in result.output.splitlines():
                print(f"    │ {line}")

            # ---- result rebinding -----------------------------------
            section("4. Result rebound into coordinator REPL")
            audit_handle = coord_repl.handles.get("audit")
            if audit_handle is None:
                fail("'audit' handle was not bound in coordinator REPL")
                return 1
            ok(
                f"new handle bound: name={audit_handle.name!r}  "
                f"chars={audit_handle.total_chars}  "
                f"bound_by={audit_handle.bound_by}"
            )

            step("coordinator can now peek the worker's result")
            audit_text = await coord_repl.peek("audit", start=0, end=200)
            print(f"    {audit_text!r}")

            # ---- registry inspection --------------------------------
            section("5. Task registry — what was recorded")
            for t in registry:
                print(
                    f"    task={t.task_id}  status={t.status.value}  "
                    f"expose={t.expose_handles}  role={t.metadata.get('role')}"
                )
                assert t.status is TaskStatus.COMPLETED
            ok("task transitioned PENDING → RUNNING → COMPLETED")

        print()
        print("swarm demo OK")
        return 0
    finally:
        await coord_repl.shutdown()


class _NullToolContext:
    """Stand-in for the kernel's ``ToolContext`` for demo purposes.

    The Spawn tool's depleted-role gate fires before any context
    attribute is touched in our happy path, so a bare object is fine.
    """

    pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
