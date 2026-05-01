#!/usr/bin/env python3
"""01 — Single-agent RLM walkthrough (ADR-028).

Demonstrates the Recursive Language Model substrate without touching
any model. Bulk content is bound as a *named variable* in a sandboxed
Python REPL; the agent (us, in this script) interacts with it through
five operations:

    bind     — load a value under a handle name
    peek     — read a slice without loading the whole value into context
    search   — regex over a handle, get line/col/snippet hits
    slice    — bind a sub-region as a new handle
    snapshot — pickle the REPL namespace to disk
    restore  — re-hydrate a fresh REPL from a snapshot

This is the substrate ADR-028 reaches for when input exceeds 25% of the
context window. The agent's own prompt stays small; the bulk lives in
the REPL.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/01_rlm_demo.py

Self-contained. Embeds a small Python source string as the demo
"codebase" — no external files, no network.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

# Make the demo runnable from anywhere — ensure the repo root is on sys.path.
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.rlm.repl import RLMRepl  # noqa: E402


# ---- demo "codebase" — embedded inline so the script is hermetic --------

_DEMO_SOURCE = '''\
"""token_bucket — a tiny rate limiter, used in three places by the demo."""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class TokenBucket:
    """Classic token-bucket: ``capacity`` tokens, refilled at ``rate``/sec."""

    capacity: int
    rate: float
    _tokens: float = 0.0
    _last: float = 0.0

    def __post_init__(self) -> None:
        self._tokens = float(self.capacity)
        self._last = time.monotonic()

    def take(self, n: int = 1) -> bool:
        """Try to consume ``n`` tokens. Return True on success."""
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False


def burst(bucket: TokenBucket, count: int) -> int:
    """Take ``count`` tokens as fast as possible. Return how many succeeded."""
    won = 0
    for _ in range(count):
        if bucket.take():
            won += 1
    return won


def safe_divide(a: float, b: float) -> float:
    """Return a/b, or 0.0 if b is zero."""
    if b == 0:
        return 0.0
    return a / b


def fibonacci(n: int) -> int:
    """Return the nth Fibonacci number. Iterative; no recursion."""
    if n < 2:
        return n
    prev, curr = 0, 1
    for _ in range(n - 1):
        prev, curr = curr, prev + curr
    return curr
'''


# ---- pretty output -------------------------------------------------------


def section(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def step(arrow: str) -> None:
    print(f"  → {arrow}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


# ---- the demo -----------------------------------------------------------


async def main() -> int:
    section("RLM substrate demo — ADR-028")
    print()
    print("  Bulk content lives in a sandboxed REPL subprocess. The")
    print("  agent reads it through Peek / Search / Slice — never by")
    print("  loading the full value into its own context.")

    repl = RLMRepl()
    await repl.start()
    try:
        # ---- 1. bind ------------------------------------------------
        section("1. Bind")
        step("bind('codebase', <~2 KB Python source>)")
        handle = await repl.bind("codebase", _DEMO_SOURCE)
        ok(
            f"handle bound: name={handle.name!r}  kind={handle.kind} "
            f"chars={handle.total_chars:,}  lines={handle.total_lines}  "
            f"sha256={handle.sha256[:12]}…"
        )

        # ---- 2. peek a slice ----------------------------------------
        section("2. Peek")
        step("peek('codebase', start=0, end=80)  — first 80 chars")
        head = await repl.peek("codebase", start=0, end=80)
        print(f"    {head!r}")
        ok("peek returned without loading the full value into our context")

        step("peek('codebase', start=0, end=10, mode='lines')  — first 10 lines")
        head_lines = await repl.peek("codebase", start=0, end=10, mode="lines")
        for line in head_lines.splitlines():
            print(f"    │ {line}")
        ok("peek('lines') is line-addressed; the underlying value is unchanged")

        # ---- 3. search ----------------------------------------------
        section("3. Search")
        step(r"search('codebase', r'def \w+')  — find every function definition")
        hits = await repl.search("codebase", r"def \w+")
        for h in hits:
            print(f"    line {h['line']:3d} col {h['col']:2d}: {h['snippet'][:60]}…")
        ok(f"matched {len(hits)} function definitions; cost = O(N) over the handle")

        # ---- 4. slice ----------------------------------------------
        section("4. Slice")
        step("search for 'class TokenBucket' span; slice it out as 'token_bucket_only'")
        cls_hits = await repl.search("codebase", r"class TokenBucket")
        if not cls_hits:
            print("  ✗ unexpected: did not find class definition")
            return 1
        cls_start = cls_hits[0]["span"][0]
        # Slice from class start to end of fibonacci function — find via search.
        burst_hits = await repl.search("codebase", r"def burst")
        if not burst_hits:
            print("  ✗ unexpected: did not find 'def burst'")
            return 1
        cls_end = burst_hits[0]["span"][0]
        sliced_handle = await repl.slice(
            "codebase",
            cls_start,
            cls_end,
            "token_bucket_only",
        )
        ok(
            f"slice bound: name={sliced_handle.name!r}  "
            f"chars={sliced_handle.total_chars:,}  "
            f"bound_by={sliced_handle.bound_by}"
        )
        # Show the first 80 chars of the slice.
        slice_head = await repl.peek("token_bucket_only", start=0, end=80)
        print(f"    head: {slice_head!r}")

        # ---- 5. snapshot --------------------------------------------
        section("5. Snapshot + restore (turn-boundary persistence)")
        with tempfile.TemporaryDirectory() as td:
            snap_path = Path(td) / "rlm.snap"
            step(f"snapshot → {snap_path.name}")
            await repl.snapshot(snap_path)
            ok(f"snapshot written: {snap_path.stat().st_size:,} bytes on disk")

            # Build a fresh REPL and restore.
            step("shutdown current REPL → start a fresh one → restore the snapshot")
            await repl.shutdown()
            new_repl = RLMRepl()
            await new_repl.start()
            try:
                await new_repl.restore(snap_path)
                ok("restored — the new REPL has the same handles")
                # Verify by peeking the original handle.
                restored_head = await new_repl.peek(
                    "codebase", start=0, end=80
                )
                if restored_head == head:
                    ok("peek('codebase') after restore matches pre-snapshot bytes")
                else:
                    print("  ✗ restored content differs from original")
                    return 1
                # The slice we made earlier should also be present.
                restored_slice = await new_repl.peek(
                    "token_bucket_only", start=0, end=40
                )
                ok(f"slice persisted across restart: {restored_slice!r}")
            finally:
                await new_repl.shutdown()
                # Replace the outer var so the finally below is a no-op.
                repl_replaced = True  # noqa: F841

        # No need to shutdown again; the outer finally would, but we
        # replaced the REPL above. Skip the outer shutdown to avoid a
        # double-shutdown noise.
        print()
        print("rlm demo OK")
        return 0
    except Exception as e:
        print(f"\n  ✗ FAILED: {type(e).__name__}: {e}")
        # Show the full traceback for diagnostic value.
        import traceback
        traceback.print_exc()
        return 1
    finally:
        # Best-effort shutdown — if we replaced the REPL inside the
        # snapshot block this is a no-op on the already-stopped proc.
        try:
            await repl.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
