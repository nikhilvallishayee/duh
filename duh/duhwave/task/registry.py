"""Task data model + registry — ADR-030.

The :class:`Task` dataclass is the persistent unit of agency in
duhwave: a JSON-serialisable record with a forward-only state machine
(:class:`TaskStatus`) and one of three execution surfaces
(:class:`TaskSurface`). The :class:`TaskRegistry` owns the in-memory
index and writes one ``<task_id>.json`` file per record on every
transition; restart loads them back and orphan-fails any RUNNING
records that weren't claimed.

Public exports: :class:`Task`, :class:`TaskRegistry`,
:class:`TaskStatus`, :class:`TaskSurface`,
:class:`TaskTransitionError`.
"""
from __future__ import annotations

import itertools
import json
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator


class TaskStatus(str, Enum):
    """Task lifecycle states. Forward-only transitions enforced by the
    registry; ``COMPLETED``/``FAILED``/``KILLED`` are terminal.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"

    @property
    def terminal(self) -> bool:
        """True when this status admits no further transitions."""
        return self in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED}


class TaskSurface(str, Enum):
    """Where a Task runs. Same lifecycle, different host."""

    IN_PROCESS = "in_process"
    SUBPROCESS = "subprocess"
    REMOTE = "remote"


@dataclass(slots=True)
class Task:
    """A persistent unit of agency.

    Identity: ``task_id = "<session_id>:<monotonic_seq>"`` — sortable,
    globally unique, traceable to a session.
    """

    task_id: str
    session_id: str
    parent_id: str | None
    surface: TaskSurface
    prompt: str
    model: str
    tools_allowlist: tuple[str, ...]
    expose_handles: tuple[str, ...] = ()
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    terminated_at: float | None = None
    output_path: str | None = None
    result: str | None = None
    error: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-safe dict (Enums collapsed to string values)."""
        d = asdict(self)
        # Enums to strings for JSON.
        d["surface"] = self.surface.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> "Task":
        """Deserialise from a dict produced by :meth:`to_dict`."""
        return cls(
            task_id=str(d["task_id"]),
            session_id=str(d["session_id"]),
            parent_id=d.get("parent_id"),  # type: ignore[arg-type]
            surface=TaskSurface(d["surface"]),
            prompt=str(d["prompt"]),
            model=str(d["model"]),
            tools_allowlist=tuple(d.get("tools_allowlist", ())),  # type: ignore[arg-type]
            expose_handles=tuple(d.get("expose_handles", ())),  # type: ignore[arg-type]
            status=TaskStatus(d.get("status", "pending")),
            created_at=float(d.get("created_at", time.time())),  # type: ignore[arg-type]
            started_at=d.get("started_at"),  # type: ignore[arg-type]
            terminated_at=d.get("terminated_at"),  # type: ignore[arg-type]
            output_path=d.get("output_path"),  # type: ignore[arg-type]
            result=d.get("result"),  # type: ignore[arg-type]
            error=d.get("error"),  # type: ignore[arg-type]
            metadata=dict(d.get("metadata", {})),  # type: ignore[arg-type]
        )


# Forward-only state machine. Listed transitions are valid; everything else raises.
_VALID_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.KILLED, TaskStatus.FAILED}),
    TaskStatus.RUNNING: frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.KILLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.KILLED: frozenset(),
}


class TaskTransitionError(RuntimeError):
    """Attempted a forbidden state transition."""


class TaskRegistry:
    """In-memory + on-disk index of Tasks for one session.

    Persists each transition to ``<session_dir>/tasks/<task_id>.json``
    (overwrite-on-update). Output streamed to ``<session_dir>/tasks/<task_id>.log``.
    """

    GRACE_SECONDS = 30.0  # how long terminal tasks stay readable before eviction

    def __init__(self, session_dir: Path, session_id: str) -> None:
        self._session_dir = Path(session_dir)
        self._session_id = session_id
        self._tasks: dict[str, Task] = {}
        self._seq = itertools.count(1)
        self._tasks_dir = self._session_dir / "tasks"
        self._tasks_dir.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        """Allocate a fresh ``<session_id>:<6-digit-seq>`` task id."""
        return f"{self._session_id}:{next(self._seq):06d}"

    def register(self, task: Task) -> None:
        """Insert a Task. Raises ``ValueError`` on duplicate id."""
        if task.task_id in self._tasks:
            raise ValueError(f"duplicate task_id: {task.task_id}")
        self._tasks[task.task_id] = task
        self._persist(task)

    def get(self, task_id: str) -> Task | None:
        """Return the in-memory Task for ``task_id``, or ``None``."""
        return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        """Snapshot of every Task currently in the in-memory index."""
        return list(self._tasks.values())

    def transition(
        self,
        task_id: str,
        new_status: TaskStatus,
        *,
        result: str | None = None,
        error: str | None = None,
        force: bool = False,
    ) -> Task:
        """Transition a Task to a new status.

        Normally enforces the forward-only state machine in
        ``_VALID_TRANSITIONS``: a task may only move PENDING → RUNNING →
        terminal, and terminal states are immutable.

        Pass ``force=True`` ONLY during recovery paths — specifically
        :meth:`restore_from_disk` orphan transitions, where the on-disk
        record's ``RUNNING`` status reflects a host crash and we need
        to mark it ``FAILED`` to surface the loss to the caller. Any
        other use of ``force`` is a bug: it bypasses the invariant that
        terminal records are immutable and that no state ever moves
        backward, which downstream subscribers (event bus, coordinator,
        UI) rely on.
        """
        t = self._tasks.get(task_id)
        if t is None:
            raise KeyError(f"unknown task: {task_id}")
        if not force:
            allowed = _VALID_TRANSITIONS.get(t.status, frozenset())
            if new_status not in allowed:
                raise TaskTransitionError(
                    f"{task_id}: {t.status.value} → {new_status.value} not allowed"
                )
        t.status = new_status
        if new_status == TaskStatus.RUNNING and t.started_at is None:
            t.started_at = time.time()
        if new_status.terminal:
            t.terminated_at = time.time()
            if result is not None:
                t.result = result
            if error is not None:
                t.error = error
        self._persist(t)
        return t

    def evict_expired(self, *, now: float | None = None) -> list[str]:
        """Drop terminal tasks whose grace period has elapsed.

        Disk records are kept; only the in-memory index is shrunk.
        """
        cutoff = (now or time.time()) - self.GRACE_SECONDS
        evicted: list[str] = []
        for tid, t in list(self._tasks.items()):
            if t.status.terminal and t.terminated_at and t.terminated_at < cutoff:
                del self._tasks[tid]
                evicted.append(tid)
        return evicted

    def restore_from_disk(
        self,
        *,
        claimed_in_process: list[str] | None = None,
        claimed_subprocess: list[str] | None = None,
    ) -> None:
        """Load persisted tasks and orphan-fail any unclaimed RUNNING records.

        When a daemon crashes mid-flight (SIGKILL, OOM, panic), Tasks
        left in ``RUNNING`` status persist on disk. On the next host
        start their in-memory executor handle (asyncio.Task or
        subprocess.Process) is gone — they are orphans. Per ADR-030
        §"Resumption protocol" we must surface this rather than silently
        leave the records stuck.

        ``claimed_in_process`` / ``claimed_subprocess`` name the
        ``task_id`` s the new host's executors are about to reattach.
        Anything in ``RUNNING`` that is not claimed transitions to
        ``FAILED`` with ``error="orphaned (host crash)"``.

        Per-surface rules:

        - **IN_PROCESS** — there is no on-disk live signal (asyncio
          tasks live only in memory). A RUNNING in-process record on a
          fresh restore is *always* orphaned unless the caller
          explicitly claims it.
        - **SUBPROCESS** — if ``metadata["pid"]`` is present and
          ``os.kill(pid, 0)`` succeeds AND the task is in
          ``claimed_subprocess``, keep RUNNING. Otherwise orphan.
          (Liveness alone is not enough: a recycled PID could match a
          stranger's process. Both signals are required.)
        - **REMOTE** — no liveness probe here; treat as orphaned unless
          the caller explicitly claims (a remote reattach must run
          *before* this method, or the caller passes the id through
          the appropriate claimed list once that surface lands).

        Terminal records (COMPLETED/FAILED/KILLED) are loaded as-is.
        """
        in_proc_claims = set(claimed_in_process or ())
        sub_claims = set(claimed_subprocess or ())

        for path in self._tasks_dir.glob("*.json"):
            try:
                t = Task.from_dict(json.loads(path.read_text()))
            except (json.JSONDecodeError, KeyError):
                continue
            self._tasks[t.task_id] = t

        for t in list(self._tasks.values()):
            if t.status is not TaskStatus.RUNNING:
                continue
            if self._is_claim_alive(t, in_proc_claims, sub_claims):
                continue
            # orphan recovery — bypasses state-machine
            self.transition(
                t.task_id,
                TaskStatus.FAILED,
                error="orphaned (host crash)",
                force=True,
            )

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Return True if ``pid`` names a live process on this host.

        Uses signal 0 (existence check, no signal delivered). Errors
        other than ``ProcessLookupError`` (e.g. ``PermissionError`` from
        a foreign-user PID) count as alive — we cannot prove the
        process is gone, so the conservative answer is "still there".
        """
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _is_claim_alive(
        self,
        t: Task,
        in_proc_claims: set[str],
        sub_claims: set[str],
    ) -> bool:
        """Decide whether a RUNNING task survives orphan recovery."""
        if t.surface is TaskSurface.IN_PROCESS:
            return t.task_id in in_proc_claims
        if t.surface is TaskSurface.SUBPROCESS:
            if t.task_id not in sub_claims:
                return False
            pid = t.metadata.get("pid") if isinstance(t.metadata, dict) else None
            if not isinstance(pid, int):
                return False
            return self._pid_alive(pid)
        # REMOTE (and any future surface): no on-host liveness probe.
        # Always orphaned on a fresh restore.
        return False

    def output_path_for(self, task_id: str) -> Path:
        """Path the executor streams the Task's stdout/stderr to."""
        return self._tasks_dir / f"{task_id}.log"

    def __iter__(self) -> Iterator[Task]:
        return iter(self._tasks.values())

    # ---- internal --------------------------------------------------

    def _persist(self, task: Task) -> None:
        path = self._tasks_dir / f"{task.task_id}.json"
        path.write_text(json.dumps(task.to_dict(), indent=2, sort_keys=True))
