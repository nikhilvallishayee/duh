"""Deterministic stub :data:`WorkerRunner` callables — one per agile role.

Each runner is the runner-injection seam from ADR-029: in production it
would drive ``duh.kernel.engine.Engine`` against a real model with the
role's tool set. For the showpiece we return canned strings sized for
the role's job, so the demo's output is byte-reproducible.

Every runner:

- exercises the :class:`RLMHandleView` (peeks a small window from one
  exposed handle to prove the wiring is live);
- reads ``task.metadata`` for context (the role name, max_turns);
- returns a deterministic string — same input always produces the same
  bytes.

The router :func:`build_runner_router` dispatches on ``task.metadata["role"]``
and is what the host wires into :meth:`Spawn.attach_runner`.
"""
from __future__ import annotations

from duh.duhwave.coordinator.runner_protocol import WorkerRunner
from duh.duhwave.coordinator.view import RLMHandleView
from duh.duhwave.task.registry import Task


# ---------------------------------------------------------------------------
# Canned outputs — the deterministic core of the demo
# ---------------------------------------------------------------------------
#
# Each block is sized for its role's job:
#   PM        — ~8 lines:  bullet acceptance criteria + summary
#   Architect — ~30 lines: ADR with API + data model + tradeoffs
#   Engineer  — ~25 lines: real Python — a working TokenBucket
#   Tester    — ~15 lines: pytest snippet with two cases
#   Reviewer  — ~10 lines: review with one concern + verdict
#
# Style note: trailing newline on every block so byte-reproducibility
# is robust to file-write line-ending quirks. No f-strings: outputs are
# pure literals, identical across runs.


_PM_OUTPUT = """\
# Refined Spec — Token-Bucket Rate Limiter

User request: Add a token-bucket rate limiter to utils.py.

## Acceptance criteria
- A `TokenBucket` class with `capacity` and `refill_rate` (tokens/sec).
- `acquire(n: int = 1) -> bool` returns True iff `n` tokens are available.
- Tokens accrue continuously between calls (no fixed-window jitter).
- Thread-safety not required for v1; document the constraint.
- Zero new external dependencies — stdlib only.

Summary: ship a minimal, dependency-free TokenBucket; defer concurrency.
"""


_ARCHITECT_OUTPUT = """\
# ADR — Token-Bucket Rate Limiter

## Status
Proposed.

## Context
The user wants rate limiting in `utils.py`. A token bucket is the
canonical algorithm: tokens accrue at a fixed rate up to a capacity;
each operation consumes tokens. It is preferable to a fixed-window
counter because it smooths bursts naturally.

## Decision
Implement `TokenBucket` as a single class with three public attributes
and one method.

### API
```
class TokenBucket:
    capacity: float        # max tokens the bucket holds
    refill_rate: float     # tokens added per second
    tokens: float          # current token count
    def acquire(n: int = 1) -> bool
```

### Data model
The bucket stores `(capacity, refill_rate, tokens, last_refill_ts)`.
On each `acquire` call we lazily compute the refill since
`last_refill_ts`, clamp to `capacity`, and atomically (within a single
thread) decide whether to grant `n` tokens.

## Tradeoffs
- **Simple over correct under contention.** No locking; v1 is
  single-threaded. A future v2 can wrap mutations in a `threading.Lock`.
- **Float clock.** We use `time.monotonic()` for a steady clock; this
  costs ~50ns per call but avoids wall-clock skew.
- **Lazy refill.** Cheaper than a background thread; rate is exact at
  acquisition time, not continuously.

## Deferred
- Concurrency (no `Lock`).
- Per-key buckets (caller wraps a dict).
- Persistence across restarts.
"""


_ENGINEER_OUTPUT = '''\
"""Token-bucket rate limiter — single-threaded, stdlib only."""
from __future__ import annotations

import time


class TokenBucket:
    """Classic token-bucket rate limiter.

    Not thread-safe by design (see ADR). Wrap in a ``Lock`` if needed.
    """

    __slots__ = ("capacity", "refill_rate", "tokens", "_last_refill_ts")

    def __init__(self, capacity: float, refill_rate: float) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be positive")
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self.tokens = float(capacity)
        self._last_refill_ts = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill_ts
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self._last_refill_ts = now

    def acquire(self, n: int = 1) -> bool:
        if n <= 0:
            raise ValueError("n must be positive")
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False
'''


_TESTER_OUTPUT = '''\
"""Tests for TokenBucket."""
from __future__ import annotations

import time

import pytest

from implementation import TokenBucket


def test_acquire_within_capacity() -> None:
    bucket = TokenBucket(capacity=5, refill_rate=1)
    assert bucket.acquire(1) is True
    assert bucket.acquire(4) is True
    assert bucket.acquire(1) is False


def test_refill_over_time() -> None:
    bucket = TokenBucket(capacity=2, refill_rate=10)
    assert bucket.acquire(2) is True
    assert bucket.acquire(1) is False
    time.sleep(0.2)  # 0.2s * 10/sec = 2 tokens, clamped to capacity
    assert bucket.acquire(2) is True


def test_invalid_construction() -> None:
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_rate=1)
    with pytest.raises(ValueError):
        TokenBucket(capacity=1, refill_rate=0)
'''


_REVIEWER_OUTPUT = """\
# Review — Token-Bucket Rate Limiter

## Summary
Implementation matches the ADR. Tests cover the three acceptance
criteria. One nit before merge.

## Concerns
- **Style** (`implementation.py`, `__init__`): cast-to-float on already-
  float inputs is harmless but redundant. Either drop the casts or add
  a docstring note that ints are accepted.

## Verdict
APPROVE WITH NITS
"""


# ---------------------------------------------------------------------------
# Per-role runners
# ---------------------------------------------------------------------------


async def _peek_first_exposed(view: RLMHandleView) -> str:
    """Touch the first exposed handle to prove the view wiring is live."""
    exposed = view.list_exposed()
    if not exposed:
        return ""
    try:
        return await view.peek(exposed[0], start=0, end=64)
    except Exception:
        # Defensive: a real runner would log; the demo's determinism does
        # not depend on this read succeeding.
        return ""


async def pm_runner(task: Task, view: RLMHandleView) -> str:
    """Stub PM runner. Sees: spec, codebase. Returns: refined_spec."""
    await _peek_first_exposed(view)
    assert task.metadata.get("role") == "pm"
    return _PM_OUTPUT


async def architect_runner(task: Task, view: RLMHandleView) -> str:
    """Stub Architect runner. Sees: spec, refined_spec, codebase."""
    await _peek_first_exposed(view)
    assert task.metadata.get("role") == "architect"
    return _ARCHITECT_OUTPUT


async def engineer_runner(task: Task, view: RLMHandleView) -> str:
    """Stub Engineer runner. Sees: refined_spec, adr_draft, codebase."""
    await _peek_first_exposed(view)
    assert task.metadata.get("role") == "engineer"
    return _ENGINEER_OUTPUT


async def tester_runner(task: Task, view: RLMHandleView) -> str:
    """Stub Tester runner. Sees: refined_spec, implementation."""
    await _peek_first_exposed(view)
    assert task.metadata.get("role") == "tester"
    return _TESTER_OUTPUT


async def reviewer_runner(task: Task, view: RLMHandleView) -> str:
    """Stub Reviewer runner. Sees: adr_draft, implementation, test_suite."""
    await _peek_first_exposed(view)
    assert task.metadata.get("role") == "reviewer"
    return _REVIEWER_OUTPUT


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


_RUNNERS: dict[str, WorkerRunner] = {
    "pm": pm_runner,
    "architect": architect_runner,
    "engineer": engineer_runner,
    "tester": tester_runner,
    "reviewer": reviewer_runner,
}


def build_runner_router(active_role: dict[str, str]) -> WorkerRunner:
    """Build a single :data:`WorkerRunner` that dispatches by role name.

    The :class:`Spawn` tool only accepts one runner per instance, but
    we want a different one per stage. The host (``main.py``) sets the
    next stage's role into ``active_role["name"]`` immediately before
    calling ``Spawn.call(...)``; the router then looks up the matching
    runner and invokes it.

    A real implementation would set the role on the spawned ``Task``
    and dispatch on that — but ``parent_role.child_role()`` always
    produces the generic worker role today (see ADR-031 §A); rather
    than monkey-patch the role plumbing for the demo, we take the
    explicit dispatch slot.
    """

    async def router(task: Task, view: RLMHandleView) -> str:
        role = active_role.get("name", "")
        runner = _RUNNERS.get(role)
        if runner is None:
            raise ValueError(f"no stub runner registered for role {role!r}")
        # Re-stamp the task metadata so the per-role runner's
        # ``assert task.metadata['role'] == ...`` invariant holds —
        # the Spawn tool's child_role() always emits "worker", so we
        # overwrite it with the active role for the demo's purposes.
        task.metadata["role"] = role
        return await runner(task, view)

    return router


__all__ = [
    "pm_runner",
    "architect_runner",
    "engineer_runner",
    "tester_runner",
    "reviewer_runner",
    "build_runner_router",
]
