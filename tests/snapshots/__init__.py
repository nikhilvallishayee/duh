"""Tier A: Visual snapshot tests for the D.U.H. TUI (ADR-074).

Each test in :mod:`test_tui_snapshots` invokes a small boot script under
``scripts/`` via ``pytest-textual-snapshot``'s ``snap_compare`` fixture.
Boot scripts construct a ``DuhApp`` around a deterministic stub engine
and expose it as ``__main__`` so ``snap_compare`` can launch them as a
subprocess.

Determinism:
    * Stub engine yields a canned event sequence — no real API calls.
    * No wall-clock-dependent rendering; spinner frames, elapsed times
      and similar are held constant.
    * Terminal size is fixed at 120×40 for every snapshot.

Baselines live under ``__snapshots__/`` and are committed to git.
"""
