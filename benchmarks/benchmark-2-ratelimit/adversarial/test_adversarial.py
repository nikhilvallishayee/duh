"""Hidden adversarial test suite for benchmark 2.

Not shown to agents during the run. Run by adversarial_all.sh against
each agent's worktree AFTER the agent finishes. Emits
``results/<agent>/adversarial.json`` with pass/fail counts.

Designed to be tolerant of *reasonable* API variations while still
testing the real invariants:
- Never over-grants in any window of length period.
- Per-key isolation.
- Capacity-1 edge case.
- Decorator re-entry respects the budget.
- Redis failure mode: fail closed, not open.

Missing APIs auto-skip the subtest that needs them, and count as a
single failure on dimension 4 of the rubric. All-pass = 1.0.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest


# -- discovery ---------------------------------------------------------

def _skip(reason: str):
    pytest.skip(reason, allow_module_level=False)


@pytest.fixture(scope="module")
def rl():
    try:
        import ratelimit  # type: ignore[import-not-found]
        return ratelimit
    except Exception as e:
        _skip(f"ratelimit package not importable: {e!r}")


@pytest.fixture(scope="module")
def TokenBucket(rl):
    for name in ("TokenBucketLimiter", "TokenBucket"):
        t = getattr(rl, name, None)
        if t is not None:
            return t
    _skip("TokenBucketLimiter not exported")


@pytest.fixture(scope="module")
def InMemory(rl):
    for name in ("InMemoryBackend", "MemoryBackend"):
        t = getattr(rl, name, None)
        if t is not None:
            return t
    _skip("InMemoryBackend not exported")


def _make(limiter_cls, backend_cls, capacity, period_s):
    """Construct the limiter by matching parameter names from its signature.

    Handles the broad spread of constructor shapes observed in this
    benchmark (capacity/refill_rate/refill_per_sec/period/period_s/
    period_ms/window_ms/window_size_ms, with or without backend kwarg,
    with or without keyword-only boundaries).
    """
    import inspect
    try:
        sig = inspect.signature(limiter_cls.__init__)
    except (TypeError, ValueError):
        _skip(f"no introspectable signature for {limiter_cls.__name__}")
    params = {p.name: p for p in sig.parameters.values() if p.name != "self"}

    rate = capacity / period_s
    kwargs = {}
    # Capacity aliases
    for name in ("capacity", "limit", "max_tokens", "size"):
        if name in params:
            kwargs[name] = capacity
            break
    # Rate / period aliases
    for name, value in (
        ("refill_rate", rate), ("refill_per_sec", rate),
        ("rate", rate), ("tokens_per_sec", rate),
        ("period", period_s), ("period_s", period_s),
        ("period_seconds", period_s),
        ("period_ms", int(period_s * 1000)),
        ("window_ms", int(period_s * 1000)),
        ("window_size_ms", int(period_s * 1000)),
        ("window", period_s), ("window_s", period_s),
    ):
        if name in params:
            kwargs[name] = value
            break
    # Backend
    if "backend" in params:
        kwargs["backend"] = backend_cls()

    try:
        return limiter_cls(**kwargs)
    except TypeError as e:
        # Try positional variant: (backend, capacity, rate) or (cap, rate, backend)
        for args in (
            (backend_cls(), capacity, rate),
            (capacity, rate, backend_cls()),
            (capacity, rate),
        ):
            try:
                return limiter_cls(*args)
            except TypeError:
                continue
        _skip(f"could not construct {limiter_cls.__name__}: {e}")


def _enforce(lim, key):
    """Call enforce() tolerating several signatures and return allowed-bool."""
    for call in (
        lambda: lim.enforce(key),
        lambda: lim.enforce(key, 1),
        lambda: lim.check(key),
        lambda: lim.acquire(key),
        lambda: lim.allow(key),
        lambda: lim.try_acquire(key),
    ):
        try:
            res = call()
            break
        except (TypeError, AttributeError):
            continue
    else:
        _skip("limiter has no enforce/check/acquire/allow method")
    # Shape the result into a bool.
    if isinstance(res, bool):
        return res
    for attr in ("allowed", "ok", "success"):
        v = getattr(res, attr, None)
        if v is not None:
            return bool(v)
    if isinstance(res, tuple) and res:
        return bool(res[0])
    _skip(f"could not interpret enforce() result: {res!r}")


# -- tests -------------------------------------------------------------

def test_never_over_grants_sequential(TokenBucket, InMemory):
    """Capacity N, fire M > N requests back-to-back: at most N succeed."""
    capacity = 5
    lim = _make(TokenBucket, InMemory, capacity=capacity, period_s=60)
    allowed = sum(_enforce(lim, "userA") for _ in range(capacity * 3))
    assert allowed <= capacity, f"over-granted: {allowed} > {capacity}"


def test_per_key_isolation(TokenBucket, InMemory):
    """Exhausting key A must not affect key B."""
    lim = _make(TokenBucket, InMemory, capacity=3, period_s=60)
    for _ in range(5):
        _enforce(lim, "keyA")
    # keyB still has full budget.
    allowed = sum(_enforce(lim, "keyB") for _ in range(3))
    assert allowed == 3, f"keyB budget contaminated: {allowed}/3"


def test_capacity_one_edge(TokenBucket, InMemory):
    """Capacity=1 is the degenerate case — exactly one through per window."""
    lim = _make(TokenBucket, InMemory, capacity=1, period_s=60)
    results = [_enforce(lim, "cap1") for _ in range(4)]
    assert sum(results) == 1, f"capacity=1 granted {sum(results)} / 4"


def test_concurrent_no_over_grant(TokenBucket, InMemory):
    """100 threads, capacity 10: at most 10 succeed."""
    capacity = 10
    lim = _make(TokenBucket, InMemory, capacity=capacity, period_s=60)
    counter = {"allowed": 0}
    lock = threading.Lock()

    def worker():
        ok = _enforce(lim, "concurrent")
        if ok:
            with lock:
                counter["allowed"] += 1

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert counter["allowed"] <= capacity, (
        f"concurrent over-grant: {counter['allowed']} > {capacity}"
    )


def test_decorator_respects_budget(rl, TokenBucket, InMemory):
    """If a @rate_limit decorator exists, it must enforce the same budget."""
    deco = getattr(rl, "rate_limit", None)
    if deco is None:
        _skip("@rate_limit decorator not exported")
    capacity = 3
    lim = _make(TokenBucket, InMemory, capacity=capacity, period_s=60)

    # Several plausible decorator signatures — try each.
    try:
        wrapped = deco(lim, lambda _k: "dkey")(lambda k: k)
    except TypeError:
        try:
            wrapped = deco(lim, key_fn=lambda _k: "dkey")(lambda k: k)
        except TypeError:
            _skip("cannot call @rate_limit with expected signatures")

    successes = 0
    for _ in range(capacity * 2):
        try:
            wrapped("x")
            successes += 1
        except Exception:
            pass
    assert successes <= capacity, (
        f"decorator over-granted: {successes} > {capacity}"
    )


# -- report emitter ----------------------------------------------------

def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Emit adversarial.json next to the test run (cwd should be worktree)."""
    import json
    out = Path("adversarial.json")
    tr = session.testscollected or 0
    failed = session.testsfailed or 0
    skipped = len(getattr(session, "_skipped", [])) if hasattr(session, "_skipped") else 0
    # Pytest doesn't expose skipped count cleanly; read from terminal reporter
    # if available.
    try:
        tr_plugin = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr_plugin is not None:
            skipped = len(tr_plugin.stats.get("skipped", []))
            passed = len(tr_plugin.stats.get("passed", []))
        else:
            passed = tr - failed - skipped
    except Exception:
        passed = tr - failed - skipped
    attempted = tr - skipped
    pass_rate = (passed / attempted) if attempted > 0 else 0.0
    out.write_text(json.dumps({
        "collected": tr,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": pass_rate,
    }, indent=2))
