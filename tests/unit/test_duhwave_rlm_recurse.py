"""Unit tests for the RLM ``Recurse`` tool — ADR-028 §"Five tools".

The Recurse op splits across two processes:

* The **bootstrap** (``duh.duhwave.rlm._bootstrap``) is sandboxed and
  cannot make model calls. It only *validates* a Recurse request:
  the handle exists, ``depth <= max_depth``, and the handle is not
  already in the caller's lineage. On success it returns a "ready"
  payload with the incremented depth.

* The **host** (``duh.duhwave.rlm.repl.RLMRepl.recurse``) drives the
  validation through the wire, then invokes a host-attached
  :data:`RecurseRunner` that actually calls a model. The runner's
  result is bound back as a new handle named
  ``<handle>__recurse_<seq>``.

These tests pin both halves of that contract.
"""
from __future__ import annotations

import pytest

from duh.duhwave.rlm import RLMRepl, RLMReplError


# Note: the bootstrap module applies its sandbox at import time
# (clearing ``sys.modules["subprocess"]`` etc.), so we *don't* import
# its op handler directly here — that would poison the test process
# for any other test that needs ``subprocess``. Instead the
# bootstrap-side tests drive ``recurse_validate`` through the real
# subprocess wire, which is the honest contract anyway.


# ---------------------------------------------------------------------------
# Bootstrap-side validation (driven through the subprocess wire).
# ---------------------------------------------------------------------------


@pytest.fixture
async def repl():
    r = RLMRepl()
    await r.start()
    try:
        yield r
    finally:
        await r.shutdown()


class TestBootstrapValidation:
    async def test_validates_handle_exists(self, repl) -> None:
        """Unknown handle → structured error, no recursion descended."""
        resp = await repl._send(
            {
                "op": "recurse_validate",
                "handle": "nope",
                "depth": 0,
                "max_depth": 4,
                "lineage": [],
            }
        )
        assert resp.ok is False
        assert "unknown handle" in resp.payload["error"]
        assert "nope" in resp.payload["error"]

    async def test_returns_ready_payload_at_depth_zero(self, repl) -> None:
        """Happy path: handle exists, depth < max → ready payload."""
        await repl.bind("body", "the slice")
        resp = await repl._send(
            {
                "op": "recurse_validate",
                "handle": "body",
                "depth": 0,
                "max_depth": 4,
                "lineage": [],
            }
        )
        assert resp.ok is True
        assert resp.payload["ready"] is True
        assert resp.payload["depth"] == 1  # incremented
        assert resp.payload["handle"]["name"] == "body"
        assert resp.payload["handle"]["kind"] == "str"
        assert resp.payload["handle"]["total_chars"] == len("the slice")

    async def test_rejects_when_depth_equals_max(self, repl) -> None:
        """``depth == max_depth`` is the boundary that rejects.

        The runner has already executed at ``depth-1``; this call
        would push to ``depth + 1 > max`` so the validator stops it.
        """
        await repl.bind("body", "x")
        resp = await repl._send(
            {
                "op": "recurse_validate",
                "handle": "body",
                "depth": 4,
                "max_depth": 4,
                "lineage": [],
            }
        )
        assert resp.ok is False
        assert "max recursion depth 4 exceeded" in resp.payload["error"]

    async def test_rejects_when_depth_exceeds_max(self, repl) -> None:
        """Even larger depths get the same error shape."""
        await repl.bind("body", "x")
        resp = await repl._send(
            {
                "op": "recurse_validate",
                "handle": "body",
                "depth": 7,
                "max_depth": 4,
                "lineage": [],
            }
        )
        assert resp.ok is False
        assert "max recursion depth" in resp.payload["error"]

    async def test_rejects_when_handle_in_lineage(self, repl) -> None:
        """Cycle detection: same handle present in lineage list."""
        await repl.bind("x", "value")
        resp = await repl._send(
            {
                "op": "recurse_validate",
                "handle": "x",
                "depth": 1,
                "max_depth": 4,
                "lineage": ["y", "x"],
            }
        )
        assert resp.ok is False
        assert "cycle detected" in resp.payload["error"]
        assert "x" in resp.payload["error"]

    async def test_lineage_with_other_names_is_fine(self, repl) -> None:
        """Lineage that doesn't contain the target handle → ready."""
        await repl.bind("x", "value")
        resp = await repl._send(
            {
                "op": "recurse_validate",
                "handle": "x",
                "depth": 1,
                "max_depth": 4,
                "lineage": ["a", "b"],
            }
        )
        assert resp.ok is True
        assert resp.payload["depth"] == 2

    def test_module_constant_is_four(self) -> None:
        """ADR-028 §"Recursion bounds": soft cap is 4.

        Verified by *parsing* the bootstrap source rather than
        importing it — importing the module triggers
        ``_apply_sandbox()`` in *this* process, which would poison
        ``sys.modules["subprocess"]`` and break unrelated tests later
        in the run.
        """
        from pathlib import Path

        bootstrap = (
            Path(__file__).resolve().parents[2]
            / "duh"
            / "duhwave"
            / "rlm"
            / "_bootstrap.py"
        )
        text = bootstrap.read_text()
        assert "RECURSE_MAX_DEPTH = 4" in text


# ---------------------------------------------------------------------------
# Host-side: RLMRepl.recurse + attach_recurse_runner
# ---------------------------------------------------------------------------


@pytest.fixture
async def repl():
    r = RLMRepl()
    await r.start()
    try:
        yield r
    finally:
        await r.shutdown()


class TestHostRecurse:
    async def test_raises_when_no_runner_attached(self, repl) -> None:
        """Host raises a clear error when the runner isn't attached."""
        await repl.bind("body", "data")
        with pytest.raises(RLMReplError, match="no recurse runner attached"):
            await repl.recurse("body", instruction="summarise")

    async def test_invokes_runner_at_incremented_depth(self, repl) -> None:
        """Runner sees ``depth + 1`` and the parent handle in lineage."""
        await repl.bind("body", "data")
        captured: dict[str, object] = {}

        async def runner(
            h: str, instruction: str, depth: int, lineage: tuple[str, ...]
        ) -> str:
            captured["handle"] = h
            captured["instruction"] = instruction
            captured["depth"] = depth
            captured["lineage"] = lineage
            return "child synthesis"

        repl.attach_recurse_runner(runner)
        result = await repl.recurse(
            "body", instruction="summarise it", depth=0, lineage=()
        )

        assert result == "child synthesis"
        assert captured["handle"] == "body"
        assert captured["instruction"] == "summarise it"
        assert captured["depth"] == 1  # 0 + 1
        assert captured["lineage"] == ("body",)

    async def test_binds_result_as_new_handle(self, repl) -> None:
        """Child output is bound as ``<handle>__recurse_<seq>``."""

        async def runner(*_a, **_kw) -> str:
            return "synthesis-output"

        repl.attach_recurse_runner(runner)
        await repl.bind("body", "data")

        await repl.recurse("body", instruction="x")
        # Sequence starts at 0.
        h = repl.handles.get("body__recurse_0")
        assert h is not None
        assert h.kind == "str"
        assert h.total_chars == len("synthesis-output")

        # Second call increments the sequence.
        await repl.recurse("body", instruction="y")
        assert repl.handles.get("body__recurse_1") is not None

    async def test_lineage_propagates_through_nested_runner(self, repl) -> None:
        """When the runner itself recurses, lineage carries the parent."""
        await repl.bind("a", "outer")
        await repl.bind("b", "inner")

        seen: list[tuple[str, tuple[str, ...]]] = []

        async def runner(
            h: str, instruction: str, depth: int, lineage: tuple[str, ...]
        ) -> str:
            seen.append((h, lineage))
            if h == "a":
                # Nest one more level — lineage now includes 'a'.
                return await repl.recurse(
                    "b", instruction="inner", depth=depth, lineage=lineage
                )
            return "leaf"

        repl.attach_recurse_runner(runner)
        await repl.recurse("a", instruction="outer", depth=0, lineage=())

        # Outer call: handle=a, lineage=(a,) (caller's lineage was empty
        # but RLMRepl.recurse extends it with the parent before
        # invoking the runner).
        assert seen[0] == ("a", ("a",))
        # Inner call: handle=b, lineage=(a, b) — 'a' carried through,
        # 'b' added when RLMRepl.recurse extended for the inner step.
        assert seen[1] == ("b", ("a", "b"))

    async def test_cycle_rejected_before_runner_invocation(self, repl) -> None:
        """The cycle check fires inside the bootstrap, so a runner
        that would explode is never invoked."""
        await repl.bind("x", "v")

        async def must_not_run(*_a, **_kw):
            raise AssertionError("runner invoked despite cycle in lineage")

        repl.attach_recurse_runner(must_not_run)
        with pytest.raises(RLMReplError, match="cycle detected"):
            await repl.recurse("x", instruction="loop", lineage=("x",))

    async def test_max_depth_rejected_before_runner_invocation(self, repl) -> None:
        """Depth-cap check also fires before the runner runs."""
        await repl.bind("x", "v")

        async def must_not_run(*_a, **_kw):
            raise AssertionError("runner invoked despite depth cap")

        repl.attach_recurse_runner(must_not_run)
        with pytest.raises(RLMReplError, match="max recursion depth"):
            await repl.recurse(
                "x", instruction="bump", depth=4, max_depth=4
            )

    async def test_unknown_handle_rejected(self, repl) -> None:
        """Recurse on a non-existent handle → RLMReplError."""

        async def runner(*_a, **_kw) -> str:
            return ""

        repl.attach_recurse_runner(runner)
        with pytest.raises(RLMReplError, match="unknown handle"):
            await repl.recurse("never-bound", instruction="x")
