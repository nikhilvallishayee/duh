"""Async background job queue for D.U.H.

Runs coroutines as background tasks via asyncio.create_task with a
concurrency limit of 5.  Each job transitions through states:
pending -> running -> completed | failed.

    queue = JobQueue()
    job_id = queue.submit("run tests", some_coro())
    info = queue.status(job_id)       # {"id": ..., "state": "running", ...}
    all_jobs = queue.list_jobs()      # [{"id": ..., "state": ...}, ...]
    result = queue.results(job_id)    # str output (only when completed/failed)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Coroutine


class JobState(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


MAX_CONCURRENT_JOBS = 5


@dataclass
class _Job:
    id: str
    name: str
    state: JobState = JobState.pending
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    _task: asyncio.Task[Any] | None = field(default=None, repr=False)
    _coro: Coroutine[Any, Any, Any] | None = field(default=None, repr=False)


class JobQueue:
    """Submit, track, and retrieve results of background async jobs."""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_JOBS) -> None:
        self._max_concurrent = max_concurrent
        self._jobs: dict[str, _Job] = {}
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        """Lazily create semaphore (must be in an event loop)."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        return self._semaphore

    def submit(self, name: str, coro: Coroutine[Any, Any, Any]) -> str:
        """Submit a coroutine for background execution. Returns a job_id."""
        job_id = uuid.uuid4().hex[:8]
        job = _Job(id=job_id, name=name, _coro=coro)
        self._jobs[job_id] = job
        job._task = asyncio.create_task(self._run(job))
        return job_id

    async def _run(self, job: _Job) -> None:
        """Acquire the concurrency semaphore, then execute the coroutine."""
        sem = self._get_semaphore()
        await sem.acquire()
        try:
            job.state = JobState.running
            job.started_at = time.monotonic()
            result = await job._coro  # type: ignore[misc]
            job.result = str(result) if result is not None else ""
            job.state = JobState.completed
        except Exception as exc:
            job.error = str(exc)
            job.state = JobState.failed
        finally:
            job.finished_at = time.monotonic()
            job._coro = None  # allow GC
            sem.release()

    def status(self, job_id: str) -> dict[str, Any]:
        """Return status dict for a single job. Raises KeyError if not found."""
        job = self._jobs[job_id]
        return self._job_to_dict(job)

    def results(self, job_id: str) -> str:
        """Return the result string of a completed/failed job.

        Returns the result for completed jobs, or the error for failed jobs.
        Raises KeyError if job_id is unknown.
        Raises ValueError if the job has not finished yet.
        """
        job = self._jobs[job_id]
        if job.state in (JobState.pending, JobState.running):
            raise ValueError(f"Job {job_id} is still {job.state.value}")
        if job.state == JobState.failed:
            return f"FAILED: {job.error}"
        return job.result

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return a list of status dicts for all jobs (newest first)."""
        return [
            self._job_to_dict(job)
            for job in sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )
        ]

    @staticmethod
    def _job_to_dict(job: _Job) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": job.id,
            "name": job.name,
            "state": job.state.value,
        }
        if job.state == JobState.completed:
            d["result"] = job.result
        if job.state == JobState.failed:
            d["error"] = job.error
        elapsed: float | None = None
        if job.started_at is not None:
            end = job.finished_at if job.finished_at is not None else time.monotonic()
            elapsed = round(end - job.started_at, 2)
        d["elapsed_s"] = elapsed
        return d
