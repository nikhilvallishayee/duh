"""Host-side cycle + depth defense-in-depth for RLM Recurse — ADR-028.

The wire validator in ``duh.duhwave.rlm._bootstrap.op_recurse_validate``
gates depth + cycles by reading the ``lineage`` array the host passes
in. That check is correct *only* if the host threads lineage
faithfully. A buggy or malicious runner could pass an empty lineage on
every recursive call and induce infinite recursion.

These tests pin the host-side enforcement that runs *before* the wire
validate call:

* :class:`RLMRepl` tracks the set of handles currently on the
  recursion stack in ``self._active_recursions``.
* :meth:`RLMRepl.recurse` checks that set first; if the requested
  handle is already on the stack it raises ``RLMReplError("cycle
  detected: handle <name> already on the stack")``.
* The host also performs the ``depth >= max_depth`` check before the
  wire call, so a bypassed bootstrap cannot enable runaway recursion.
* The ``finally`` clause pops the handle from the active set even when
  the runner raises, preventing false-positive cycle rejections on
  subsequent legitimate calls against the same handle.

Run with::

    /Users/nomind/Code/duh/.venv/bin/python3 -m pytest \\
        tests/unit/test_duhwave_rlm_recurse_cycle_host.py -v
"""
from __future__ import annotations

import pytest

from duh.duhwave.rlm import RLMRepl, RLMReplError


@pytest.fixture
async def repl():
    r = RLMRepl()
    await r.start()
    try:
        yield r
    finally:
        await r.shutdown()


# ---------------------------------------------------------------------------
# 1. Runner recurses straight back into the same handle → host catches it
#    *before* any wire validate call goes out.
# ---------------------------------------------------------------------------


async def test_runner_self_cycle_rejected_by_host_before_wire_call(repl) -> None:
    """A runner that recurses on its own handle hits the host-side
    cycle gate, regardless of what lineage it threads.

    We simulate the malicious-runner case by deliberately passing
    ``lineage=()`` from inside the runner — exactly the bug the
    bootstrap's lineage check fails to catch. The host-side
    ``_active_recursions`` set must reject before the wire call.
    """
    await repl.bind("body", "data")

    runner_calls = {"n": 0}

    async def malicious_runner(
        h: str, instruction: str, depth: int, lineage: tuple[str, ...]
    ) -> str:
        runner_calls["n"] += 1
        # Lineage is deliberately reset to () — the bug we're
        # defending against. The host must still reject.
        return await repl.recurse(
            "body",
            instruction="loop",
            depth=0,
            lineage=(),
            max_depth=4,
        )

    repl.attach_recurse_runner(malicious_runner)

    with pytest.raises(RLMReplError, match="already on the stack"):
        await repl.recurse(
            "body",
            instruction="start",
            depth=0,
            lineage=(),
            max_depth=4,
        )

    # The runner ran exactly once (at the outer call); the inner
    # recurse() call short-circuited inside the host before invoking
    # the runner again.
    assert runner_calls["n"] == 1
    # And ``_active_recursions`` is empty after the failure — the
    # ``finally`` clause cleaned up.
    assert repl._active_recursions == set()


# ---------------------------------------------------------------------------
# 2. ``depth >= max_depth`` is checked on the host, before wire validate.
# ---------------------------------------------------------------------------


async def test_host_depth_check_fires_before_wire_validate(repl) -> None:
    """The host-side depth cap rejects without sending a wire op.

    We swap out the REPL subprocess's stdin pipe so that *any* attempt
    to ``_send`` raises immediately. If the host's pre-flight depth
    gate fires correctly, the call raises ``RLMReplError("max
    recursion depth 4 exceeded")`` before we ever try to write.
    """
    await repl.bind("body", "data")

    async def must_not_run(*_a, **_kw) -> str:
        raise AssertionError("runner reached despite depth cap")

    repl.attach_recurse_runner(must_not_run)

    # Sentinel: we wrap _send to count calls.
    send_calls = {"n": 0}
    real_send = repl._send

    async def counting_send(msg):
        send_calls["n"] += 1
        return await real_send(msg)

    repl._send = counting_send  # type: ignore[method-assign]

    with pytest.raises(RLMReplError, match="max recursion depth 4 exceeded"):
        await repl.recurse(
            "body",
            instruction="bump",
            depth=4,
            lineage=(),
            max_depth=4,
        )

    # Critical assertion: the host's depth gate fired *before* any
    # wire call.
    assert send_calls["n"] == 0


# ---------------------------------------------------------------------------
# 3. Recurse on a *different* handle nested in the same call — allowed,
#    no false-positive from the active-set cycle check.
# ---------------------------------------------------------------------------


async def test_nested_recurse_different_handle_allowed(repl) -> None:
    """A → B nested recursion is fine; only A→A or B→B is a cycle.

    The runner is called for handle ``a``. From inside that runner we
    call ``recurse("b", ...)``. Both calls succeed: ``a`` is on the
    stack, ``b`` is not, so the host-side check passes. The bootstrap
    also passes because lineages differ.
    """
    await repl.bind("a", "outer")
    await repl.bind("b", "inner")

    seen: list[tuple[str, set[str]]] = []

    async def runner(
        h: str, instruction: str, depth: int, lineage: tuple[str, ...]
    ) -> str:
        # Snapshot the active set as the runner sees it (the parent
        # handle should be in there; the current handle was just added
        # before the runner was invoked).
        seen.append((h, set(repl._active_recursions)))
        if h == "a":
            return await repl.recurse(
                "b",
                instruction="inner work",
                depth=depth,
                lineage=lineage,
                max_depth=4,
            )
        return f"leaf:{h}"

    repl.attach_recurse_runner(runner)
    result = await repl.recurse(
        "a", instruction="outer", depth=0, lineage=(), max_depth=4
    )

    # Outer runner produced the inner runner's output, since the outer
    # ``return await repl.recurse("b", ...)`` returns the child's
    # synthesis (not auto-wrapped at this layer).
    assert result == "leaf:b"

    # When the outer runner ran, only "a" was on the stack.
    assert seen[0] == ("a", {"a"})
    # When the inner runner ran, both "a" and "b" were on the stack.
    assert seen[1] == ("b", {"a", "b"})

    # Both popped after the calls returned.
    assert repl._active_recursions == set()


# ---------------------------------------------------------------------------
# 4. ``finally`` clause cleans up ``_active_recursions`` on exception.
# ---------------------------------------------------------------------------


async def test_finally_clears_active_recursions_on_runner_exception(repl) -> None:
    """If the runner raises, the handle is still removed from the
    active set so a subsequent legitimate call doesn't see a phantom
    cycle.
    """
    await repl.bind("body", "data")

    class _Boom(RuntimeError):
        pass

    async def boom_runner(*_a, **_kw) -> str:
        raise _Boom("runner exploded")

    repl.attach_recurse_runner(boom_runner)

    with pytest.raises(_Boom, match="runner exploded"):
        await repl.recurse(
            "body", instruction="will fail", depth=0, lineage=(), max_depth=4
        )

    # Critical: the active set is empty even though the runner raised.
    assert repl._active_recursions == set()

    # Sanity check: a follow-up call against the same handle must not
    # be rejected as a cycle.
    async def quiet_runner(*_a, **_kw) -> str:
        return "ok"

    repl.attach_recurse_runner(quiet_runner)
    out = await repl.recurse(
        "body", instruction="retry", depth=0, lineage=(), max_depth=4
    )
    assert out == "ok"
    assert repl._active_recursions == set()
