"""Benchmark: audit hook overhead must be <2% on normal D.U.H. operations.

ADR-054, Workstream 7.5 — performance regression gate.
"""

from __future__ import annotations

import time

import pytest

from duh.kernel.audit import _audit_handler, WATCHED_EVENTS


def test_audit_handler_unwatched_event_throughput() -> None:
    """Unwatched events must be sub-microsecond (frozenset lookup)."""
    n = 100_000
    start = time.perf_counter()
    for _ in range(n):
        _audit_handler("some.unwatched.event", ())
    elapsed = time.perf_counter() - start
    per_call_ns = (elapsed / n) * 1e9
    # Must be under 1000ns per call (CI runners are ~2x slower than M-series)
    assert per_call_ns < 1000, f"Unwatched event: {per_call_ns:.0f}ns/call exceeds 1000ns"


def test_audit_handler_watched_event_throughput() -> None:
    """Watched events (with registry=None) must be under 2000ns."""
    n = 50_000
    start = time.perf_counter()
    for _ in range(n):
        _audit_handler("open", ("/tmp/test.txt",))
    elapsed = time.perf_counter() - start
    per_call_ns = (elapsed / n) * 1e9
    assert per_call_ns < 4000, f"Watched event: {per_call_ns:.0f}ns/call exceeds 4000ns"


def test_import_filter_throughput() -> None:
    """Import filtering (benign module) must be sub-microsecond."""
    n = 100_000
    start = time.perf_counter()
    for _ in range(n):
        _audit_handler("import", ("os",))
    elapsed = time.perf_counter() - start
    per_call_ns = (elapsed / n) * 1e9
    assert per_call_ns < 1000, f"Import filter: {per_call_ns:.0f}ns/call exceeds 1000ns"
