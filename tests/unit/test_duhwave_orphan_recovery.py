"""Unit tests for orphan-recovery on TaskRegistry.restore_from_disk.

Pins ADR-030 §"Resumption protocol": when a daemon crashes mid-flight,
RUNNING tasks left on disk are orphans on the next host start unless
the new host's executors explicitly claim them. The registry surfaces
the loss by transitioning each orphan to FAILED with
``error="orphaned (host crash)"``.

Six cases, one per surface × claim × liveness combination that matters:

1. in_process RUNNING + claimed_in_process=[]                → orphaned
2. in_process RUNNING + claimed_in_process=[task_id]         → kept
3. subprocess RUNNING + no metadata["pid"]                   → orphaned
4. subprocess RUNNING + stale metadata["pid"]                → orphaned
5. subprocess RUNNING + live pid + claimed_subprocess=[id]   → kept
6. terminal records (any surface)                            → unaffected
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from duh.duhwave.task.registry import (
    Task,
    TaskRegistry,
    TaskStatus,
    TaskSurface,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_running(
    reg: TaskRegistry,
    *,
    surface: TaskSurface,
    pid: int | None = None,
) -> Task:
    """Register a task and drive it to RUNNING. Optionally pin metadata.pid."""
    t = Task(
        task_id=reg.new_id(),
        session_id=reg._session_id,  # type: ignore[attr-defined]
        parent_id=None,
        surface=surface,
        prompt="orphan-test",
        model="inherit",
        tools_allowlist=(),
    )
    reg.register(t)
    if pid is not None:
        t.metadata["pid"] = pid
    reg.transition(t.task_id, TaskStatus.RUNNING)
    return t


def _fresh_registry(session_dir: Path, session_id: str) -> TaskRegistry:
    """A new registry over the same dir — simulates a daemon restart."""
    return TaskRegistry(session_dir=session_dir, session_id=session_id)


def _find_dead_pid() -> int:
    """A positive PID that does not name any live process."""
    for candidate in range(2_000_000, 1_000_000, -1):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except OSError:
            continue
    raise RuntimeError("could not find a dead pid")


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    d = tmp_path / "session"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# 1. in_process RUNNING, unclaimed → orphaned.
# ---------------------------------------------------------------------------


def test_in_process_running_unclaimed_is_orphaned(session_dir: Path) -> None:
    reg1 = _fresh_registry(session_dir, "s")
    t = _build_running(reg1, surface=TaskSurface.IN_PROCESS)
    del reg1

    reg2 = _fresh_registry(session_dir, "s")
    reg2.restore_from_disk(claimed_in_process=[])

    restored = reg2.get(t.task_id)
    assert restored is not None
    assert restored.status is TaskStatus.FAILED
    assert restored.error is not None
    assert "orphan" in restored.error.lower()
    assert restored.terminated_at is not None


# ---------------------------------------------------------------------------
# 2. in_process RUNNING, claimed → kept.
# ---------------------------------------------------------------------------


def test_in_process_running_claimed_is_kept(session_dir: Path) -> None:
    reg1 = _fresh_registry(session_dir, "s")
    t = _build_running(reg1, surface=TaskSurface.IN_PROCESS)
    del reg1

    reg2 = _fresh_registry(session_dir, "s")
    reg2.restore_from_disk(claimed_in_process=[t.task_id])

    restored = reg2.get(t.task_id)
    assert restored is not None
    assert restored.status is TaskStatus.RUNNING
    assert restored.error is None


# ---------------------------------------------------------------------------
# 3. subprocess RUNNING, no pid metadata → orphaned (cannot prove liveness).
# ---------------------------------------------------------------------------


def test_subprocess_running_no_pid_is_orphaned(session_dir: Path) -> None:
    reg1 = _fresh_registry(session_dir, "s")
    t = _build_running(reg1, surface=TaskSurface.SUBPROCESS, pid=None)
    del reg1

    reg2 = _fresh_registry(session_dir, "s")
    # Even if the caller claims it, no pid means no liveness check is
    # possible — the task is treated as orphaned.
    reg2.restore_from_disk(claimed_subprocess=[t.task_id])

    restored = reg2.get(t.task_id)
    assert restored is not None
    assert restored.status is TaskStatus.FAILED
    assert restored.error is not None
    assert "orphan" in restored.error.lower()


# ---------------------------------------------------------------------------
# 4. subprocess RUNNING, stale pid → orphaned.
# ---------------------------------------------------------------------------


def test_subprocess_running_stale_pid_is_orphaned(session_dir: Path) -> None:
    reg1 = _fresh_registry(session_dir, "s")
    t = _build_running(
        reg1,
        surface=TaskSurface.SUBPROCESS,
        pid=_find_dead_pid(),
    )
    del reg1

    reg2 = _fresh_registry(session_dir, "s")
    reg2.restore_from_disk(claimed_subprocess=[t.task_id])

    restored = reg2.get(t.task_id)
    assert restored is not None
    assert restored.status is TaskStatus.FAILED
    assert restored.error is not None
    assert "orphan" in restored.error.lower()


# ---------------------------------------------------------------------------
# 5. subprocess RUNNING, live pid + claimed → kept.
# ---------------------------------------------------------------------------


def test_subprocess_running_live_pid_claimed_is_kept(session_dir: Path) -> None:
    reg1 = _fresh_registry(session_dir, "s")
    # Our own process is, by definition, alive.
    t = _build_running(
        reg1,
        surface=TaskSurface.SUBPROCESS,
        pid=os.getpid(),
    )
    del reg1

    reg2 = _fresh_registry(session_dir, "s")
    reg2.restore_from_disk(claimed_subprocess=[t.task_id])

    restored = reg2.get(t.task_id)
    assert restored is not None
    assert restored.status is TaskStatus.RUNNING
    assert restored.error is None


# ---------------------------------------------------------------------------
# 6. Terminal records survive restore unchanged.
# ---------------------------------------------------------------------------


def test_terminal_records_unaffected_by_restore(session_dir: Path) -> None:
    reg1 = _fresh_registry(session_dir, "s")
    completed = _build_running(reg1, surface=TaskSurface.IN_PROCESS)
    reg1.transition(completed.task_id, TaskStatus.COMPLETED, result="done")
    failed = _build_running(reg1, surface=TaskSurface.SUBPROCESS, pid=os.getpid())
    reg1.transition(failed.task_id, TaskStatus.FAILED, error="prior")
    killed = _build_running(reg1, surface=TaskSurface.IN_PROCESS)
    reg1.transition(killed.task_id, TaskStatus.KILLED, error="user")
    del reg1

    reg2 = _fresh_registry(session_dir, "s")
    # No claims at all — terminal records must still be untouched.
    reg2.restore_from_disk()

    rc = reg2.get(completed.task_id)
    assert rc is not None
    assert rc.status is TaskStatus.COMPLETED
    assert rc.result == "done"

    rf = reg2.get(failed.task_id)
    assert rf is not None
    assert rf.status is TaskStatus.FAILED
    assert rf.error == "prior"  # untouched, no orphan rewrite

    rk = reg2.get(killed.task_id)
    assert rk is not None
    assert rk.status is TaskStatus.KILLED
    assert rk.error == "user"
