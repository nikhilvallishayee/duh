"""Recursion-bound invariants for the duhwave coordinator/RLM stack.

Verifies the depth + cycle-detection contracts asserted by ADR-028
(``Recurse`` policy) and ADR-029 (``Spawn`` depth budget):

1.  **Spawn depth** — ``Role.child_role()`` succeeds exactly once
    starting from a coordinator (depth 1 → 0); a second call from
    the depleted child raises ``ValueError``. The kernel-side
    ``Spawn.check_permissions`` mirrors this property.

2.  **Recurse depth cap** — ADR-028 §"Recursion bounds" specifies a
    soft cap of 4 with a hard cap of 8. The host-side
    :meth:`RLMRepl.recurse` validates against the cap by routing
    through the bootstrap's ``recurse_validate`` op before invoking
    the attached runner.

3.  **Recurse cycle detection** — the bootstrap rejects a Recurse
    request when the target handle appears in the caller's lineage,
    so a self-cycle is caught *before* the runner is invoked.

Run with::

    /Users/nomind/Code/duh/.venv/bin/python3 -m pytest \\
        tests/integration/test_duhwave_recursion_bounds.py -v
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

from duh.duhwave.coordinator import (
    BUILTIN_ROLES,
    RLMHandleView,
    Role,
    filter_tools_for_role,
)
from duh.duhwave.rlm.repl import RLMRepl


# ---- Test 1: Spawn-depth invariant ---------------------------------------


def test_spawn_depth_coordinator_one_shot() -> None:
    """Coordinator may spawn once; the spawned worker may not spawn again.

    This is the depth=1 invariant that keeps the recursion tree
    bounded at one level. Encodes ADR-029's "single hop" rule.
    """
    coord = BUILTIN_ROLES["coordinator"]
    assert coord.spawn_depth == 1

    # First spawn: succeeds, returns a worker with depth 0.
    child = coord.child_role()
    assert child.name == "worker"
    assert child.spawn_depth == 0

    # Second spawn from the child: raises.
    with pytest.raises(ValueError, match="no spawn budget left"):
        child.child_role()

    # A fresh worker out of BUILTIN_ROLES has depth 0 too.
    with pytest.raises(ValueError, match="no spawn budget left"):
        BUILTIN_ROLES["worker"].child_role()


def test_spawn_custom_role_depth_two() -> None:
    """A topology may declare a custom role with deeper budget.

    Demonstrates that Role accepts arbitrary depths, but each
    descendant decrements the budget — the *child* always becomes a
    worker (depth 0) per the default ``child_role`` policy. This is
    a deliberate ADR-029 design choice: depth is enforced as a hard
    boundary, not a tree.
    """
    deep = Role(
        name="super-coord",
        system_prompt="(test)",
        tool_allowlist=("Spawn",),
        spawn_depth=2,
    )
    # Even though the parent has depth 2, child_role() returns the
    # default worker preset — depth 0 — by design (no fan-out beyond
    # one hop). This is the property the test pins.
    child = deep.child_role()
    assert child.spawn_depth == 0
    # Grandchild call raises.
    with pytest.raises(ValueError, match="no spawn budget left"):
        child.child_role()


# ---- Test 2: Spawn ToolResult error path on depleted role ----------------


async def test_spawn_tool_blocks_when_role_depleted() -> None:
    """The Spawn tool itself rejects calls from a depth-0 role.

    Defence-in-depth check: even if the kernel forgot to filter
    ``Spawn`` out of a worker's toolset, the tool's runtime gate
    refuses to fire. This test imports ``Spawn`` lazily because it
    pulls in kernel-side dependencies that are heavier than the
    rest of the duhwave imports.
    """
    spawn_mod = importlib.import_module("duh.duhwave.coordinator.spawn")
    Spawn = spawn_mod.Spawn

    repl = RLMRepl()
    await repl.start()
    try:
        await repl.bind("data", "x")
        # Build a registry rooted in a tmp session dir.
        from duh.duhwave.task.registry import TaskRegistry
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            registry = TaskRegistry(Path(td), session_id="t-spawn")
            tool = Spawn(
                repl=repl,
                registry=registry,
                parent_role=BUILTIN_ROLES["worker"],  # depth 0!
                session_id="t-spawn",
                worker_runner=lambda task, view: _unreachable_runner(task, view),
            )
            ctx = _NullToolContext()
            perms = await tool.check_permissions(
                {"prompt": "x", "bind_as": "out"}, ctx
            )
            assert perms.get("allowed") is False
            assert "no spawn budget" in perms.get("reason", "")
            # Direct .call() also rejects.
            result = await tool.call(
                {"prompt": "x", "bind_as": "out"}, ctx
            )
            assert result.is_error is True
            assert "no spawn budget" in result.output
    finally:
        await repl.shutdown()


# ---- Test 3: Tool-filter excludes Spawn from worker, includes for coord -


def test_filter_excludes_spawn_for_worker() -> None:
    """Belt-and-braces: the kernel-side filter is the primary defence.

    The Spawn capability never reaches a worker session because the
    tool registry filter strips it at session start. This test pins
    the filter behaviour — both directions of the boundary.
    """

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    pool = [
        _Tool("Spawn"),
        _Tool("SendMessage"),
        _Tool("Stop"),
        _Tool("Bash"),
        _Tool("Edit"),
        _Tool("Peek"),
        _Tool("Search"),
        _Tool("Slice"),
        _Tool("Recurse"),
    ]
    coord_tools = {t.name for t in filter_tools_for_role(pool, BUILTIN_ROLES["coordinator"])}
    worker_tools = {t.name for t in filter_tools_for_role(pool, BUILTIN_ROLES["worker"])}

    assert "Spawn" in coord_tools
    assert "Spawn" not in worker_tools
    assert "Bash" not in coord_tools
    assert "Bash" in worker_tools
    # Recurse is a worker-side RLM tool; coordinators do not call
    # the model recursively on bulk handles — they delegate.
    assert "Recurse" not in coord_tools
    assert "Recurse" in worker_tools


# ---- Test 4: Recurse depth cap (ADR-028) -------------------------------


async def test_recurse_depth_cap_enforced() -> None:
    """The ``Recurse`` tool rejects calls past the configured cap.

    Per ADR-028 §"Recursion bounds": soft cap 4. We attach a runner
    that recurses straight back; on the call where ``depth + 1`` would
    exceed ``max_depth`` the bootstrap-side validator rejects with a
    ``max recursion depth N exceeded`` error, surfaced as
    :class:`RLMReplError` on the host.
    """
    from duh.duhwave.rlm import RLMReplError

    # Cap value is fixed by ADR-028 §"Recursion bounds" at 4 (soft).
    # We pass it explicitly rather than importing the bootstrap module
    # constant — importing ``_bootstrap`` would trigger its
    # ``_apply_sandbox()`` in *this* process, which poisons
    # ``sys.modules["subprocess"]`` and breaks unrelated tooling.
    RECURSE_MAX_DEPTH = 4

    repl = RLMRepl()
    await repl.start()
    try:
        await repl.bind("body", "the slice contents")

        # Runner that recurses straight back, bumping depth until the
        # bootstrap rejects. We track the max depth observed so the
        # test can assert the cap actually fired (rather than e.g. the
        # runner returning early).
        max_depth_seen = {"d": 0}

        async def recursive_runner(
            h: str, instruction: str, depth: int, lineage: tuple[str, ...]
        ) -> str:
            max_depth_seen["d"] = max(max_depth_seen["d"], depth)
            # Recurse from a *different* handle each call to avoid the
            # cycle-detection short-circuit; we want to hit the depth
            # cap, not the cycle gate.
            child_handle = f"body{depth}"
            await repl.bind(child_handle, "child slice")
            return await repl.recurse(
                child_handle,
                instruction=instruction,
                depth=depth,
                lineage=lineage,
                max_depth=RECURSE_MAX_DEPTH,
            )

        repl.attach_recurse_runner(recursive_runner)

        with pytest.raises(RLMReplError, match="max recursion depth"):
            await repl.recurse(
                "body",
                instruction="recurse forever",
                depth=0,
                lineage=(),
                max_depth=RECURSE_MAX_DEPTH,
            )

        # Cap is 4 — the runner sees depths 1, 2, 3, 4; the *next*
        # (depth=4 → 5) call is what the validator rejects.
        assert max_depth_seen["d"] == RECURSE_MAX_DEPTH
    finally:
        await repl.shutdown()


# ---- Test 5: Recurse cycle detection (ADR-028) -------------------------


async def test_recurse_rejects_self_cycle() -> None:
    """A ``Recurse`` call against a handle in the caller's lineage is
    rejected at the bootstrap layer, *before* the runner is invoked.

    The wire-validate-then-execute split means the cycle check happens
    in the sandboxed subprocess; the runner attached here would raise
    if reached, so observing :class:`RLMReplError` from
    :meth:`RLMRepl.recurse` proves the gate fired in the right order.
    """
    from duh.duhwave.rlm import RLMReplError

    repl = RLMRepl()
    await repl.start()
    try:
        await repl.bind("x", "value")
        await repl.bind("y", "ancestor")

        async def must_not_run(*_a, **_kw):
            raise AssertionError("runner reached despite cycle in lineage")

        repl.attach_recurse_runner(must_not_run)

        # ``x`` already appears in the lineage → cycle.
        with pytest.raises(RLMReplError, match="cycle detected"):
            await repl.recurse(
                "x",
                instruction="produce x'",
                depth=1,
                lineage=("y", "x"),
            )
    finally:
        await repl.shutdown()


# ---- Test 6: handle visibility scoping (already enforced) ---------------


async def test_handle_view_blocks_unexposed() -> None:
    """An ``RLMHandleView`` rejects access to handles outside its
    exposed set, even if those handles exist in the underlying REPL.

    This is the worker-side data boundary that ADR-029 §"Selective
    handle exposure" relies on. Worth pinning in the recursion-bounds
    file because it limits the *blast radius* of any future Recurse
    cycle: a worker can never broaden its own visibility.
    """
    repl = RLMRepl()
    await repl.start()
    try:
        await repl.bind("public", "ok")
        await repl.bind("secret", "private!")
        view = RLMHandleView.from_names(repl, ["public"])

        # Exposed handle works.
        assert await view.peek("public", start=0, end=2) == "ok"

        # Unexposed handle raises before reaching the REPL.
        with pytest.raises(ValueError, match="handle not exposed"):
            await view.peek("secret", start=0, end=4)
        with pytest.raises(ValueError, match="handle not exposed"):
            await view.search("secret", "private")
        with pytest.raises(ValueError, match="handle not exposed"):
            await view.slice("secret", 0, 4, "leak")
    finally:
        await repl.shutdown()


# ---- helpers -------------------------------------------------------------


async def _unreachable_runner(task, view):  # type: ignore[no-untyped-def]
    """Runner used for assertions where the call must not reach it.

    If the depth gate fails to short-circuit, this raises so the
    test fails loudly rather than silently dropping into a runner.
    """
    raise AssertionError(
        f"runner reached despite depth=0 role on task {task.task_id}"
    )


class _NullToolContext:
    """Minimal stand-in for ``ToolContext`` for tests.

    The Spawn tool's check_permissions / call signatures take a
    ``ToolContext`` but the depleted-role gate fires before any
    context attribute is touched, so a bare object is sufficient.
    """

    pass
