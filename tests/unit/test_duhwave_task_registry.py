"""Tests for duh.duhwave.task.registry — Task + TaskRegistry.

Pure-data tests: no subprocesses, no async. Each test exercises one
invariant from ADR-030 §"Lifecycle" and §"Persistence".
"""

from __future__ import annotations

import json
import time

import pytest

from duh.duhwave.task.registry import (
    Task,
    TaskRegistry,
    TaskStatus,
    TaskSurface,
    TaskTransitionError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(registry: TaskRegistry, *, surface: TaskSurface = TaskSurface.IN_PROCESS) -> Task:
    return Task(
        task_id=registry.new_id(),
        session_id="sess-A",
        parent_id=None,
        surface=surface,
        prompt="do a thing",
        model="haiku",
        tools_allowlist=("Read", "Grep"),
    )


@pytest.fixture
def registry(tmp_path):
    return TaskRegistry(session_dir=tmp_path, session_id="sess-A")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestNewId:
    def test_ids_are_session_prefixed(self, registry):
        tid = registry.new_id()
        assert tid.startswith("sess-A:")

    def test_ids_are_monotonic_and_sortable(self, registry):
        ids = [registry.new_id() for _ in range(5)]
        # Each subsequent id sorts after the previous (zero-padded sequence).
        assert ids == sorted(ids)
        # And they're unique.
        assert len(set(ids)) == 5


# ---------------------------------------------------------------------------
# register / get / list
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_register_and_get(self, registry):
        t = _make_task(registry)
        registry.register(t)
        assert registry.get(t.task_id) is t

    def test_get_unknown_returns_none(self, registry):
        assert registry.get("missing") is None

    def test_list_returns_all_registered(self, registry):
        t1 = _make_task(registry)
        t2 = _make_task(registry)
        registry.register(t1)
        registry.register(t2)
        listed = registry.list()
        assert {t.task_id for t in listed} == {t1.task_id, t2.task_id}

    def test_register_duplicate_raises(self, registry):
        t = _make_task(registry)
        registry.register(t)
        with pytest.raises(ValueError, match="duplicate task_id"):
            registry.register(t)


# ---------------------------------------------------------------------------
# Transitions — happy paths
# ---------------------------------------------------------------------------


class TestValidTransitions:
    def test_pending_to_running_to_completed(self, registry):
        t = _make_task(registry)
        registry.register(t)
        assert t.status is TaskStatus.PENDING
        registry.transition(t.task_id, TaskStatus.RUNNING)
        assert t.status is TaskStatus.RUNNING
        registry.transition(t.task_id, TaskStatus.COMPLETED, result="ok")
        assert t.status is TaskStatus.COMPLETED

    def test_pending_to_failed(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.FAILED, error="never started")
        assert t.status is TaskStatus.FAILED

    def test_pending_to_killed(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.KILLED, error="user cancelled")
        assert t.status is TaskStatus.KILLED

    def test_running_to_killed(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.RUNNING)
        registry.transition(t.task_id, TaskStatus.KILLED, error="killed by host")
        assert t.status is TaskStatus.KILLED


# ---------------------------------------------------------------------------
# Transitions — guard rails
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    def test_completed_to_running_raises(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.RUNNING)
        registry.transition(t.task_id, TaskStatus.COMPLETED, result="r")
        with pytest.raises(TaskTransitionError):
            registry.transition(t.task_id, TaskStatus.RUNNING)

    def test_running_to_pending_raises(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.RUNNING)
        with pytest.raises(TaskTransitionError):
            registry.transition(t.task_id, TaskStatus.PENDING)

    def test_failed_is_terminal(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.FAILED, error="x")
        with pytest.raises(TaskTransitionError):
            registry.transition(t.task_id, TaskStatus.RUNNING)

    def test_killed_is_terminal(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.KILLED, error="x")
        with pytest.raises(TaskTransitionError):
            registry.transition(t.task_id, TaskStatus.COMPLETED, result="r")

    def test_transition_unknown_id_raises_keyerror(self, registry):
        with pytest.raises(KeyError):
            registry.transition("does-not-exist", TaskStatus.RUNNING)


# ---------------------------------------------------------------------------
# Timestamps & captured fields
# ---------------------------------------------------------------------------


class TestTimestamps:
    def test_started_at_set_on_running(self, registry):
        t = _make_task(registry)
        registry.register(t)
        assert t.started_at is None
        registry.transition(t.task_id, TaskStatus.RUNNING)
        assert t.started_at is not None
        assert t.started_at <= time.time()

    def test_terminated_at_set_on_terminal(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.RUNNING)
        assert t.terminated_at is None
        registry.transition(t.task_id, TaskStatus.COMPLETED, result="ok")
        assert t.terminated_at is not None
        assert t.terminated_at <= time.time()

    def test_terminated_at_unset_on_running(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.RUNNING)
        assert t.terminated_at is None

    def test_result_captured_on_completion(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.RUNNING)
        registry.transition(t.task_id, TaskStatus.COMPLETED, result="42")
        assert t.result == "42"
        assert t.error is None

    def test_error_captured_on_failure(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.RUNNING)
        registry.transition(t.task_id, TaskStatus.FAILED, error="boom")
        assert t.error == "boom"
        assert t.result is None


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_register_writes_json_to_disk(self, tmp_path):
        reg = TaskRegistry(session_dir=tmp_path, session_id="s1")
        t = _make_task(reg)
        reg.register(t)
        path = tmp_path / "tasks" / f"{t.task_id}.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["task_id"] == t.task_id
        assert data["status"] == "pending"

    def test_transition_overwrites_disk_record(self, tmp_path):
        reg = TaskRegistry(session_dir=tmp_path, session_id="s1")
        t = _make_task(reg)
        reg.register(t)
        reg.transition(t.task_id, TaskStatus.RUNNING)
        reg.transition(t.task_id, TaskStatus.COMPLETED, result="ok")
        data = json.loads((tmp_path / "tasks" / f"{t.task_id}.json").read_text())
        assert data["status"] == "completed"
        assert data["result"] == "ok"
        assert data["started_at"] is not None
        assert data["terminated_at"] is not None

    def test_restore_from_disk_round_trip(self, tmp_path):
        # Session 1: register, transition to running, then to terminal.
        # (RUNNING-on-disk after a fresh-registry restore is treated as
        # an orphan per ADR-030 §"Resumption protocol"; this test
        # exercises the plain field round-trip, so we drive the task
        # all the way to COMPLETED before tearing reg1 down.)
        reg1 = TaskRegistry(session_dir=tmp_path, session_id="s1")
        t = _make_task(reg1)
        reg1.register(t)
        reg1.transition(t.task_id, TaskStatus.RUNNING)
        reg1.transition(t.task_id, TaskStatus.COMPLETED, result="ok")

        # Session 2: fresh registry, restore.
        reg2 = TaskRegistry(session_dir=tmp_path, session_id="s1")
        assert reg2.list() == []
        reg2.restore_from_disk()

        loaded = reg2.get(t.task_id)
        assert loaded is not None
        assert loaded.status is TaskStatus.COMPLETED
        assert loaded.result == "ok"
        assert loaded.prompt == t.prompt
        assert loaded.tools_allowlist == t.tools_allowlist


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------


class TestEviction:
    def test_evict_removes_old_terminal(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.RUNNING)
        registry.transition(t.task_id, TaskStatus.COMPLETED, result="ok")
        # Force terminated_at far in the past.
        t.terminated_at = time.time() - (TaskRegistry.GRACE_SECONDS + 60)
        evicted = registry.evict_expired()
        assert evicted == [t.task_id]
        assert registry.get(t.task_id) is None

    def test_evict_keeps_fresh_terminal(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.RUNNING)
        registry.transition(t.task_id, TaskStatus.COMPLETED, result="ok")
        # terminated_at is "now" — within grace.
        evicted = registry.evict_expired()
        assert evicted == []
        assert registry.get(t.task_id) is t

    def test_evict_keeps_non_terminal_regardless_of_age(self, registry):
        t = _make_task(registry)
        registry.register(t)
        registry.transition(t.task_id, TaskStatus.RUNNING)
        # started_at long ago, terminated_at None — must not evict.
        t.started_at = time.time() - 10_000.0
        evicted = registry.evict_expired()
        assert evicted == []
        assert registry.get(t.task_id) is t


# ---------------------------------------------------------------------------
# output_path_for
# ---------------------------------------------------------------------------


class TestOutputPath:
    def test_output_path_under_session_tasks_dir(self, tmp_path):
        reg = TaskRegistry(session_dir=tmp_path, session_id="s1")
        path = reg.output_path_for("s1:000001")
        assert path.parent == tmp_path / "tasks"
        assert path.name == "s1:000001.log"
