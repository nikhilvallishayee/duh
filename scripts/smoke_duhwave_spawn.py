#!/usr/bin/env python3
"""Smoke test for duhwave coordinator: handle visibility + role/tool filter.

Verifies the boundary properties from ADR-029 / ADR-031 §A:

1. RLMHandleView blocks `peek` / `search` / `slice` for non-exposed
   handle names (raises ValueError("handle not exposed: ...")) and
   forwards exposed names to the underlying REPL.
2. filter_tools_for_role removes Bash / Edit / Write from a coordinator's
   tool set (and keeps them for a worker).
3. Role.child_role() succeeds exactly once for a coordinator
   (spawn_depth=1 -> 0); a second call from a depleted role raises.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

# Allow `python3 scripts/smoke_duhwave_spawn.py` from the repo root.
import pathlib
_REPO = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.coordinator import (  # noqa: E402  (path tweak above)
    BUILTIN_ROLES,
    RLMHandleView,
    Role,
    filter_tools_for_role,
)
from duh.duhwave.rlm.repl import RLMRepl  # noqa: E402


@dataclass(slots=True)
class _FakeTool:
    """Minimal stand-in for the kernel Tool protocol — needs only `.name`."""

    name: str


async def _amain() -> int:
    # ---------------------------------------------------------------
    # 1. Coordinator REPL with two handles.
    # ---------------------------------------------------------------
    repl = RLMRepl()
    await repl.start()
    try:
        await repl.bind("codebase", "def public(): pass\n")
        await repl.bind("secret_plan", "PHASE 1: be sneaky.\nPHASE 2: profit.\n")

        # ---------------------------------------------------------------
        # 2. View exposes only `codebase`.
        # ---------------------------------------------------------------
        view = RLMHandleView.from_names(repl, ["codebase"])

        seen = await view.peek("codebase", start=0, end=4)
        assert seen == "def ", f"expected 'def ', got {seen!r}"

        try:
            await view.peek("secret_plan", start=0, end=4)
        except ValueError as e:
            assert "handle not exposed" in str(e), e
            assert "secret_plan" in str(e), e
        else:
            print("FAIL: peek('secret_plan') should have raised", file=sys.stderr)
            return 1

        # search() must also raise on non-exposed.
        try:
            await view.search("secret_plan", "PHASE")
        except ValueError as e:
            assert "handle not exposed" in str(e), e
        else:
            print("FAIL: search('secret_plan') should have raised", file=sys.stderr)
            return 1

        # slice() must also raise on non-exposed source.
        try:
            await view.slice("secret_plan", 0, 5, "leaked")
        except ValueError as e:
            assert "handle not exposed" in str(e), e
        else:
            print("FAIL: slice('secret_plan', ...) should have raised", file=sys.stderr)
            return 1

        # ---------------------------------------------------------------
        # 3. Role tool filter — coordinator excludes Bash/Edit/Write.
        # ---------------------------------------------------------------
        all_tool_names = [
            "Read", "Edit", "Write", "Bash", "Glob", "Grep",
            "Peek", "Search", "Slice", "Recurse",
            "Spawn", "SendMessage", "Stop",
        ]
        all_tools = [_FakeTool(name=n) for n in all_tool_names]

        coord_filtered = filter_tools_for_role(all_tools, BUILTIN_ROLES["coordinator"])
        coord_names = {t.name for t in coord_filtered}
        for forbidden in ("Bash", "Edit", "Write", "Read", "Glob", "Grep", "Recurse"):
            assert forbidden not in coord_names, f"coordinator must not have {forbidden}"
        for required in ("Spawn", "SendMessage", "Stop", "Peek", "Search", "Slice"):
            assert required in coord_names, f"coordinator must have {required}"

        worker_filtered = filter_tools_for_role(all_tools, BUILTIN_ROLES["worker"])
        worker_names = {t.name for t in worker_filtered}
        for required in ("Bash", "Edit", "Write", "Read", "Glob", "Grep"):
            assert required in worker_names, f"worker must have {required}"
        assert "Spawn" not in worker_names, "worker must NOT have Spawn"

        # ---------------------------------------------------------------
        # 4. Role.child_role() is one-shot.
        # ---------------------------------------------------------------
        coord = BUILTIN_ROLES["coordinator"]
        assert coord.spawn_depth == 1, coord.spawn_depth
        child = coord.child_role()
        assert child.spawn_depth == 0, child.spawn_depth
        assert child.name == "worker", child.name

        # The original coordinator role is frozen — its spawn_depth is
        # still 1 (the budget is decremented on the *child*, not the
        # parent). The depth-1 invariant is enforced because the *child*
        # has spawn_depth=0 and child_role() will raise on it.
        try:
            child.child_role()
        except ValueError as e:
            assert "no spawn budget left" in str(e), e
        else:
            print("FAIL: child.child_role() should have raised", file=sys.stderr)
            return 1

        # And a worker built fresh from BUILTIN_ROLES is depleted too.
        try:
            BUILTIN_ROLES["worker"].child_role()
        except ValueError as e:
            assert "no spawn budget left" in str(e), e
        else:
            print("FAIL: worker.child_role() should have raised", file=sys.stderr)
            return 1

    finally:
        await repl.shutdown()

    print("spawn smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
