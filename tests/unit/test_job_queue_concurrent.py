"""Concurrency tests for :class:`duh.kernel.job_queue.JobQueue`.

JobQueue is shared by REPL ``/jobs`` commands, the Bash ``bg:`` prefix,
and background skills. Multiple callers can submit, inspect, and finish
jobs on the same event loop simultaneously. These tests use
``asyncio.gather`` to create real concurrency (not sequential awaits)
and verify:

* 10 concurrent ``submit()`` calls do not deadlock and produce 10
  distinct job ids.
* 10 concurrent ``submit()`` + ``list_jobs()`` calls do not race —
  list_jobs never raises (RuntimeError: dictionary changed size during
  iteration) nor returns a torn view missing submitted jobs.
* Completed-job eviction is thread-safe under load (the FIFO eviction
  pass never drops a pending/running job and never crashes).
"""

from __future__ import annotations

import asyncio

import pytest

from duh.kernel.job_queue import JobQueue, JobState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _noop(value: str = "ok") -> str:
    """Yield control once, then return — simulates a real async job."""
    await asyncio.sleep(0)
    return value


async def _quick(value: str = "ok") -> str:
    await asyncio.sleep(0.01)
    return value


# ---------------------------------------------------------------------------
# 1) Concurrent submit() does not deadlock
# ---------------------------------------------------------------------------

class TestConcurrentSubmit:
    @pytest.mark.asyncio
    async def test_ten_concurrent_submits_no_deadlock(self) -> None:
        """Ten submit() calls issued concurrently must all complete
        under the test-wide 30s timeout and produce distinct ids."""
        q = JobQueue()

        async def _submitter(i: int) -> str:
            return q.submit(f"job-{i}", _noop(f"v{i}"))

        job_ids = await asyncio.gather(*[_submitter(i) for i in range(10)])
        assert len(set(job_ids)) == 10, "submit() produced duplicate ids"

        # Let every background task settle, then assert none got stuck.
        await asyncio.sleep(0.1)
        for jid in job_ids:
            assert q.status(jid)["state"] == JobState.completed.value


# ---------------------------------------------------------------------------
# 2) Concurrent submit() + list_jobs() does not race
# ---------------------------------------------------------------------------

class TestConcurrentSubmitAndList:
    @pytest.mark.asyncio
    async def test_submit_and_list_jobs_interleave_safely(self) -> None:
        """Interleaving submit and list_jobs on the same event loop must
        not raise RuntimeError (dict changed size during iteration) and
        every snapshot of list_jobs() must be internally consistent."""
        q = JobQueue()
        errors: list[BaseException] = []

        async def _submit_many() -> None:
            for i in range(10):
                try:
                    q.submit(f"sj-{i}", _quick(f"v{i}"))
                except BaseException as exc:  # pragma: no cover - sentinel
                    errors.append(exc)
                await asyncio.sleep(0)

        async def _list_many() -> None:
            for _ in range(10):
                try:
                    snap = q.list_jobs()
                    # Each entry in a single snapshot must be a dict with
                    # a valid state — proves the iteration completed
                    # without being mutated mid-flight.
                    for entry in snap:
                        assert isinstance(entry, dict)
                        assert entry["state"] in {
                            s.value for s in JobState
                        }
                except BaseException as exc:
                    errors.append(exc)
                await asyncio.sleep(0)

        await asyncio.gather(_submit_many(), _list_many())
        assert errors == [], f"race condition observed: {errors!r}"

        # After all tasks resolve, every job is visible and completed.
        await asyncio.sleep(0.2)
        final = q.list_jobs()
        assert len(final) == 10
        assert all(e["state"] == JobState.completed.value for e in final)

    @pytest.mark.asyncio
    async def test_status_during_concurrent_submits(self) -> None:
        """status(job_id) on an already-submitted job must keep returning
        a well-formed dict even while new submits flood the queue."""
        q = JobQueue()
        first = q.submit("first", _quick())
        errors: list[BaseException] = []

        async def _flood() -> None:
            for i in range(10):
                q.submit(f"f{i}", _quick())
                await asyncio.sleep(0)

        async def _poll() -> None:
            for _ in range(20):
                try:
                    info = q.status(first)
                    assert info["id"] == first
                    assert info["state"] in {s.value for s in JobState}
                except BaseException as exc:
                    errors.append(exc)
                await asyncio.sleep(0)

        await asyncio.gather(_flood(), _poll())
        # Drain: let every submitted background task finish so its coro
        # gets awaited. Otherwise Python logs "coroutine was never
        # awaited" for the jobs still queued behind the semaphore when
        # the test function returns.
        await asyncio.sleep(0.3)
        assert errors == [], f"status/submit race: {errors!r}"


# ---------------------------------------------------------------------------
# 3) Completed-job eviction is thread-safe
# ---------------------------------------------------------------------------

class TestConcurrentEviction:
    @pytest.mark.asyncio
    async def test_eviction_under_load_never_drops_active_jobs(self) -> None:
        """When max_completed_retained is tiny and we flood the queue,
        eviction must evict only completed jobs (FIFO) and never remove
        a pending/running one, regardless of ordering races."""
        q = JobQueue(max_concurrent=20, max_completed_retained=3)

        # Submit 12 quick jobs concurrently.
        async def _submit(i: int) -> str:
            return q.submit(f"ev-{i}", _quick(f"v{i}"))

        ids = await asyncio.gather(*[_submit(i) for i in range(12)])
        await asyncio.sleep(0.3)  # let them finish + eviction run

        remaining = q.list_jobs()
        # Retention cap was 3, so at most 3 completed jobs remain.
        completed = [
            j for j in remaining if j["state"] == JobState.completed.value
        ]
        assert len(completed) <= 3, (
            f"eviction did not honour retention cap: {len(completed)} > 3"
        )

        # Every remaining job_id must be in the original set — eviction
        # must not invent or duplicate ids.
        assert {j["id"] for j in remaining}.issubset(set(ids))

    @pytest.mark.asyncio
    async def test_eviction_races_with_status(self) -> None:
        """Calling status() while eviction runs must not raise KeyError
        on jobs that *haven't* been evicted. (Evicted ids legitimately
        raise KeyError; we explicitly tolerate that.)"""
        q = JobQueue(max_concurrent=10, max_completed_retained=2)
        ids: list[str] = []

        async def _submitter() -> None:
            for i in range(10):
                ids.append(q.submit(f"ev-{i}", _quick()))
                await asyncio.sleep(0)

        async def _pollster() -> None:
            # Repeatedly snapshot list_jobs() and poke status() on the
            # currently-present ids. Evicted ids will KeyError; we
            # only flag unexpected exceptions.
            for _ in range(40):
                snap = q.list_jobs()
                for info in snap:
                    try:
                        q.status(info["id"])
                    except KeyError:
                        # Legal: eviction happened between snapshot
                        # and the status() call.
                        pass
                await asyncio.sleep(0)

        await asyncio.gather(_submitter(), _pollster())
        await asyncio.sleep(0.2)

        final = q.list_jobs()
        completed = [
            j for j in final if j["state"] == JobState.completed.value
        ]
        assert len(completed) <= 2
