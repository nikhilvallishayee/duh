"""Tests for ADR-073 Wave 3 #13: frame-rate cap via text_delta coalescing.

The :class:`TextDeltaCoalescer` merges ``text_delta`` events that arrive
within an 8 ms window into a single widget update, reducing widget update
cost from O(characters) to O(frames) on high-rate streams.

Tests verify:

1. 10 rapid deltas coalesce into 1–2 flushes (depending on timer timing).
2. Slow deltas (> 8 ms apart) each flush independently.
3. Explicit ``flush()`` drains pending text immediately (tool_use path).
4. Flush on ``done`` / boundary events loses no data.
5. Final delivered text equals the concatenation of every input delta
   (no data loss invariant).
6. ``flush()`` is idempotent when buffer is empty.
7. Cancellation path: ``flush()`` before cleanup preserves pending text.
8. Sub-8ms deltas accumulate via a single timer tick (only one flush).
"""

from __future__ import annotations

import asyncio

import pytest

from duh.ui.app import TextDeltaCoalescer


# ---------------------------------------------------------------------------
# Helper: in-memory flush sink
# ---------------------------------------------------------------------------


class _Sink:
    """Records every payload delivered by the coalescer."""

    def __init__(self) -> None:
        self.payloads: list[str] = []

    def __call__(self, payload: str) -> None:
        self.payloads.append(payload)

    @property
    def joined(self) -> str:
        return "".join(self.payloads)

    @property
    def count(self) -> int:
        return len(self.payloads)


# ---------------------------------------------------------------------------
# Tests — coalescing window
# ---------------------------------------------------------------------------


class TestCoalescing:
    @pytest.mark.asyncio
    async def test_rapid_deltas_coalesce_into_few_flushes(self):
        """Ten deltas emitted back-to-back must coalesce into far fewer
        flushes than one-flush-per-delta (ideally 1 or 2)."""
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.008, flush_cb=sink)
        # Emit 10 synchronous adds — no awaits between them so they all
        # arrive inside the 8 ms window.
        for ch in "0123456789":
            coalescer.add(ch)
        # Let the timer fire.
        await asyncio.sleep(0.02)
        assert sink.count <= 2, f"Expected ≤ 2 flushes, got {sink.count}"
        # And every character must be delivered.
        assert sink.joined == "0123456789"

    @pytest.mark.asyncio
    async def test_slow_deltas_each_trigger_flush(self):
        """Deltas spaced > 8 ms apart should each flush independently —
        the coalescer must NOT merge across idle gaps."""
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.008, flush_cb=sink)
        coalescer.add("A")
        await asyncio.sleep(0.03)
        coalescer.add("B")
        await asyncio.sleep(0.03)
        coalescer.add("C")
        await asyncio.sleep(0.03)
        assert sink.count == 3
        assert sink.payloads == ["A", "B", "C"]

    @pytest.mark.asyncio
    async def test_explicit_flush_drains_pending(self):
        """Calling flush() with pending text must deliver it immediately
        (used by the tool_use / assistant boundary handlers)."""
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.1, flush_cb=sink)
        coalescer.add("hello ")
        coalescer.add("world")
        # Timer has NOT fired (100 ms window).  Explicit flush must still
        # deliver.
        coalescer.flush()
        assert sink.count == 1
        assert sink.joined == "hello world"
        assert coalescer.has_pending is False

    @pytest.mark.asyncio
    async def test_flush_on_tool_use_event(self):
        """Simulated event loop: text_delta, text_delta, tool_use.
        The flush at tool_use must deliver the pending buffer before any
        tool-result event is processed."""
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.1, flush_cb=sink)
        coalescer.add("Let me check")
        coalescer.add(" the files")
        # "tool_use" arrives — the worker calls flush() synchronously.
        coalescer.flush()
        assert sink.joined == "Let me check the files"
        assert sink.count == 1

    @pytest.mark.asyncio
    async def test_flush_on_done_event(self):
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.1, flush_cb=sink)
        coalescer.add("final ")
        coalescer.add("answer")
        # "done" arrives before the 100 ms timer — flush must drain.
        coalescer.flush()
        assert sink.joined == "final answer"


# ---------------------------------------------------------------------------
# Tests — data-loss invariant
# ---------------------------------------------------------------------------


class TestNoDataLoss:
    @pytest.mark.asyncio
    async def test_all_deltas_eventually_delivered(self):
        """Under arbitrary interleaving of add() and wait(), the joined
        flush output must equal the sum of all deltas."""
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.008, flush_cb=sink)
        deltas = ["The ", "quick ", "brown ", "fox ", "jumps"]
        for d in deltas:
            coalescer.add(d)
            # Randomly vary the gap.
            await asyncio.sleep(0.001)
        # Wait for any pending timer, then flush to catch a last buffer.
        await asyncio.sleep(0.03)
        coalescer.flush()
        assert sink.joined == "".join(deltas)

    @pytest.mark.asyncio
    async def test_cancellation_flush_preserves_buffer(self):
        """Simulates a worker cancellation: before the except/finally
        block cleans up, flush() must deliver any pending text so the
        visible transcript is complete."""
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.2, flush_cb=sink)
        coalescer.add("streaming ")
        coalescer.add("text that ")
        coalescer.add("must survive")
        # Timer has NOT fired (200ms window).  A simulated cancel calls
        # flush() in the except block.
        coalescer.flush()
        assert sink.joined == "streaming text that must survive"
        assert sink.count == 1

    @pytest.mark.asyncio
    async def test_flush_after_timer_fire_is_noop(self):
        """If the timer has already fired (buffer drained), calling
        flush() again must NOT double-deliver."""
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.008, flush_cb=sink)
        coalescer.add("abc")
        await asyncio.sleep(0.02)
        # Timer has fired by now.
        assert sink.count == 1
        coalescer.flush()
        # Second flush: empty buffer, no new delivery.
        assert sink.count == 1


# ---------------------------------------------------------------------------
# Tests — edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_delta_ignored(self):
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.01, flush_cb=sink)
        coalescer.add("")
        await asyncio.sleep(0.02)
        assert sink.count == 0
        assert coalescer.delta_count == 0

    def test_flush_when_empty_is_noop(self):
        """flush() on a virgin coalescer must not call flush_cb."""
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.01, flush_cb=sink)
        coalescer.flush()
        coalescer.flush()
        assert sink.count == 0

    @pytest.mark.asyncio
    async def test_has_pending_reflects_buffer_state(self):
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.2, flush_cb=sink)
        assert coalescer.has_pending is False
        coalescer.add("x")
        assert coalescer.has_pending is True
        coalescer.flush()
        assert coalescer.has_pending is False

    @pytest.mark.asyncio
    async def test_sub_interval_deltas_single_flush(self):
        """All deltas within a single 8 ms window must produce exactly
        one flush from the timer."""
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.050, flush_cb=sink)
        # Emit quickly — inside the 50 ms window.
        coalescer.add("a")
        coalescer.add("b")
        coalescer.add("c")
        await asyncio.sleep(0.08)
        assert sink.count == 1
        assert sink.payloads[0] == "abc"

    def test_add_without_running_loop_flushes_sync(self):
        """When called with no running event loop (unit tests), add()
        must still deliver the data — via a synchronous flush fallback."""
        sink = _Sink()
        coalescer = TextDeltaCoalescer(interval_s=0.008, flush_cb=sink)
        # No asyncio loop running here — this is a regular sync test.
        coalescer.add("sync-delivery")
        # Either the add() path flushed synchronously, or buffer still
        # holds pending.  Either way an explicit flush must top-up.
        coalescer.flush()
        assert sink.joined == "sync-delivery"
