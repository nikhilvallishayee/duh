"""Tests for duh.duhwave.coordinator.spawn — Spawn tool.

Spawn is the coordinator's delegation seam. We exercise it with a real
RLM REPL (so the bind-back path goes through the real subprocess) and
mock-injected worker runners (so the test focuses on the orchestration,
not on a downstream agent loop).
"""

from __future__ import annotations

import asyncio

import pytest

from duh.duhwave.coordinator.role import BUILTIN_ROLES, Role
from duh.duhwave.coordinator.spawn import Spawn
from duh.duhwave.coordinator.view import RLMHandleView
from duh.duhwave.rlm import RLMRepl
from duh.duhwave.task.registry import Task, TaskRegistry
from duh.kernel.tool import ToolContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def repl():
    r = RLMRepl()
    await r.start()
    try:
        yield r
    finally:
        await r.shutdown()


@pytest.fixture
def registry(tmp_path):
    return TaskRegistry(session_dir=tmp_path, session_id="sess-1")


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp", session_id="sess-1")


def _make_spawn(
    repl: RLMRepl,
    registry: TaskRegistry,
    *,
    parent_role: Role = BUILTIN_ROLES["coordinator"],
    worker_runner=None,
) -> Spawn:
    return Spawn(
        repl=repl,
        registry=registry,
        parent_role=parent_role,
        session_id="sess-1",
        worker_runner=worker_runner,
    )


# ---------------------------------------------------------------------------
# Spawn-depth gate
# ---------------------------------------------------------------------------


class TestSpawnDepthGate:
    async def test_worker_role_spawn_returns_error_result(self, repl, registry, ctx):
        # Worker role has spawn_depth=0; Spawn must refuse without raising.
        spawn = _make_spawn(repl, registry, parent_role=BUILTIN_ROLES["worker"])
        result = await spawn.call(
            {"prompt": "do x", "bind_as": "out"},
            ctx,
        )
        assert result.is_error is True
        assert "spawn budget" in result.output

    async def test_check_permissions_denies_when_no_budget(self, repl, registry, ctx):
        spawn = _make_spawn(repl, registry, parent_role=BUILTIN_ROLES["worker"])
        verdict = await spawn.check_permissions({"prompt": "x", "bind_as": "y"}, ctx)
        assert verdict["allowed"] is False
        assert "spawn budget" in verdict["reason"]


# ---------------------------------------------------------------------------
# Missing runner
# ---------------------------------------------------------------------------


class TestMissingRunner:
    async def test_no_runner_attached_returns_error(self, repl, registry, ctx):
        spawn = _make_spawn(repl, registry, worker_runner=None)
        result = await spawn.call(
            {"prompt": "do x", "bind_as": "out"},
            ctx,
        )
        assert result.is_error is True
        assert "no worker runner attached" in result.output

    async def test_no_runner_marks_task_failed(self, repl, registry, ctx):
        spawn = _make_spawn(repl, registry, worker_runner=None)
        await spawn.call({"prompt": "do x", "bind_as": "out"}, ctx)
        # Exactly one task got registered, and it is FAILED.
        tasks = registry.list()
        assert len(tasks) == 1
        from duh.duhwave.task.registry import TaskStatus
        assert tasks[0].status is TaskStatus.FAILED
        assert "no worker runner" in (tasks[0].error or "")


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    async def test_missing_prompt_rejected(self, repl, registry, ctx):
        async def runner(task: Task, view: RLMHandleView) -> str:
            return "never called"
        spawn = _make_spawn(repl, registry, worker_runner=runner)
        result = await spawn.call({"bind_as": "out", "prompt": ""}, ctx)
        assert result.is_error is True
        assert "prompt" in result.output

    async def test_missing_bind_as_rejected(self, repl, registry, ctx):
        async def runner(task: Task, view: RLMHandleView) -> str:
            return "never called"
        spawn = _make_spawn(repl, registry, worker_runner=runner)
        result = await spawn.call({"prompt": "do x", "bind_as": ""}, ctx)
        assert result.is_error is True
        assert "bind_as" in result.output

    async def test_unknown_expose_handle_rejected(self, repl, registry, ctx):
        async def runner(task: Task, view: RLMHandleView) -> str:
            return "never called"
        spawn = _make_spawn(repl, registry, worker_runner=runner)
        result = await spawn.call(
            {
                "prompt": "do x",
                "bind_as": "out",
                "expose": ["does_not_exist"],
            },
            ctx,
        )
        assert result.is_error is True
        assert "not bound" in result.output

    async def test_bind_as_collision_rejected(self, repl, registry, ctx):
        await repl.bind("taken", "existing content")

        async def runner(task: Task, view: RLMHandleView) -> str:
            return "never called"
        spawn = _make_spawn(repl, registry, worker_runner=runner)
        result = await spawn.call({"prompt": "x", "bind_as": "taken"}, ctx)
        assert result.is_error is True
        assert "already bound" in result.output


# ---------------------------------------------------------------------------
# Happy path: worker completes, result binds back
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_completed_worker_binds_result(self, repl, registry, ctx):
        await repl.bind("source_doc", "alpha beta gamma\ndelta epsilon\n")

        async def runner(task: Task, view: RLMHandleView) -> str:
            # Sanity: the worker's view sees the exposed handle.
            assert view.list_exposed() == ["source_doc"]
            content = await view.peek("source_doc", end=20)
            return f"worker-saw: {content[:5]}"

        spawn = _make_spawn(repl, registry, worker_runner=runner)
        result = await spawn.call(
            {
                "prompt": "summarise the doc",
                "bind_as": "summary",
                "expose": ["source_doc"],
            },
            ctx,
        )
        assert result.is_error is False
        # Result text was bound into the coordinator REPL.
        h = repl.handles.get("summary")
        assert h is not None
        # The actual content is readable via peek.
        peeked = await repl.peek("summary")
        assert peeked.startswith("worker-saw: alpha")

    async def test_metadata_payload_contains_required_fields(self, repl, registry, ctx):
        async def runner(task: Task, view: RLMHandleView) -> str:
            return "ok"

        spawn = _make_spawn(repl, registry, worker_runner=runner)
        result = await spawn.call({"prompt": "x", "bind_as": "out"}, ctx)
        assert result.is_error is False
        meta = result.metadata
        assert "task_id" in meta
        assert meta["bind_as"] == "out"
        assert meta["status"] == "completed"
        assert "summary" in meta
        # task_id is session-prefixed.
        assert meta["task_id"].startswith("sess-1:")


# ---------------------------------------------------------------------------
# Worker FAILED — partial bind path (ADR-029 §"Failure handling")
# ---------------------------------------------------------------------------


class TestFailedWorker:
    async def test_failed_worker_does_not_bind_to_bind_as(self, repl, registry, ctx):
        async def runner(task: Task, view: RLMHandleView) -> str:
            raise RuntimeError("worker crashed")

        spawn = _make_spawn(repl, registry, worker_runner=runner)
        result = await spawn.call({"prompt": "x", "bind_as": "out"}, ctx)
        assert result.is_error is True
        # The bind_as handle is NOT created in the coordinator REPL on failure.
        assert repl.handles.get("out") is None

    async def test_failed_worker_partial_bound_under_partial_name(self, repl, registry, ctx):
        # When the runner raises, completed.error becomes the "result_text"
        # surface, and Spawn binds it under bind_as__partial.
        async def runner(task: Task, view: RLMHandleView) -> str:
            raise RuntimeError("worker crashed mid-thought")

        spawn = _make_spawn(repl, registry, worker_runner=runner)
        await spawn.call({"prompt": "x", "bind_as": "out"}, ctx)
        partial = repl.handles.get("out__partial")
        assert partial is not None
        # And the partial content includes the error text.
        peeked = await repl.peek("out__partial")
        assert "worker crashed mid-thought" in peeked

    async def test_failed_worker_metadata_status(self, repl, registry, ctx):
        async def runner(task: Task, view: RLMHandleView) -> str:
            raise RuntimeError("nope")

        spawn = _make_spawn(repl, registry, worker_runner=runner)
        result = await spawn.call({"prompt": "x", "bind_as": "out"}, ctx)
        assert result.metadata["status"] == "failed"
        assert result.is_error is True


# ---------------------------------------------------------------------------
# attach_runner seam
# ---------------------------------------------------------------------------


class TestAttachRunner:
    async def test_attach_runner_after_construction(self, repl, registry, ctx):
        spawn = _make_spawn(repl, registry, worker_runner=None)

        async def runner(task: Task, view: RLMHandleView) -> str:
            return "attached-ok"

        spawn.attach_runner(runner)
        result = await spawn.call({"prompt": "x", "bind_as": "out"}, ctx)
        assert result.is_error is False
        assert repl.handles.get("out") is not None
