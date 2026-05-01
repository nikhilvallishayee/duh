"""Tests for duh.duhwave.task.executors — InProcessExecutor + SubprocessExecutor.

These tests check that executors maintain the registry's state machine
correctly across success, failure, and kill paths.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from duh.duhwave.task.executors import InProcessExecutor, SubprocessExecutor
from duh.duhwave.task.registry import (
    Task,
    TaskRegistry,
    TaskStatus,
    TaskSurface,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(reg: TaskRegistry, *, surface: TaskSurface) -> Task:
    return Task(
        task_id=reg.new_id(),
        session_id="s1",
        parent_id=None,
        surface=surface,
        prompt="x",
        model="haiku",
        tools_allowlist=(),
    )


@pytest.fixture
def registry(tmp_path):
    return TaskRegistry(session_dir=tmp_path, session_id="s1")


# ---------------------------------------------------------------------------
# InProcessExecutor
# ---------------------------------------------------------------------------


class TestInProcessSuccess:
    async def test_runner_returning_string_completes(self, registry):
        async def runner(task: Task) -> str:
            return "the answer"

        executor = InProcessExecutor(registry, runner)
        task = _make_task(registry, surface=TaskSurface.IN_PROCESS)
        registry.register(task)

        await executor.submit(task)
        # Wait for the asyncio.Task spawned by the executor.
        atask = executor._asyncio_tasks[task.task_id]
        await asyncio.wait_for(atask, timeout=2.0)

        loaded = registry.get(task.task_id)
        assert loaded.status is TaskStatus.COMPLETED
        assert loaded.result == "the answer"
        assert loaded.error is None
        assert loaded.started_at is not None
        assert loaded.terminated_at is not None


class TestInProcessFailure:
    async def test_runner_raising_marks_failed(self, registry):
        async def runner(task: Task) -> str:
            raise RuntimeError("boom")

        executor = InProcessExecutor(registry, runner)
        task = _make_task(registry, surface=TaskSurface.IN_PROCESS)
        registry.register(task)

        await executor.submit(task)
        atask = executor._asyncio_tasks[task.task_id]
        await asyncio.wait_for(atask, timeout=2.0)

        loaded = registry.get(task.task_id)
        assert loaded.status is TaskStatus.FAILED
        assert "RuntimeError" in (loaded.error or "")
        assert "boom" in (loaded.error or "")


class TestInProcessKill:
    async def test_kill_before_completion_marks_killed(self, registry):
        started = asyncio.Event()
        unblock = asyncio.Event()

        async def runner(task: Task) -> str:
            started.set()
            await unblock.wait()
            return "never reached"

        executor = InProcessExecutor(registry, runner)
        task = _make_task(registry, surface=TaskSurface.IN_PROCESS)
        registry.register(task)

        await executor.submit(task)
        await asyncio.wait_for(started.wait(), timeout=2.0)

        await executor.kill(task.task_id)

        loaded = registry.get(task.task_id)
        assert loaded.status is TaskStatus.KILLED
        # The asyncio task was cancelled.
        atask = executor._asyncio_tasks[task.task_id]
        assert atask.done()

    async def test_kill_unknown_task_is_noop(self, registry):
        async def runner(task: Task) -> str:
            return ""

        executor = InProcessExecutor(registry, runner)
        # Should not raise.
        await executor.kill("not-a-task")

    async def test_kill_already_done_is_noop(self, registry):
        async def runner(task: Task) -> str:
            return "fast"

        executor = InProcessExecutor(registry, runner)
        task = _make_task(registry, surface=TaskSurface.IN_PROCESS)
        registry.register(task)
        await executor.submit(task)
        await asyncio.wait_for(executor._asyncio_tasks[task.task_id], timeout=2.0)
        # Already terminal — kill must not bump it again.
        before = registry.get(task.task_id).status
        await executor.kill(task.task_id)
        after = registry.get(task.task_id).status
        assert before is after is TaskStatus.COMPLETED


class TestInProcessSurfaceMismatch:
    async def test_subprocess_task_rejected(self, registry):
        async def runner(task: Task) -> str:
            return ""

        executor = InProcessExecutor(registry, runner)
        wrong_task = _make_task(registry, surface=TaskSurface.SUBPROCESS)
        registry.register(wrong_task)

        with pytest.raises(ValueError, match="surface mismatch"):
            await executor.submit(wrong_task)


# ---------------------------------------------------------------------------
# SubprocessExecutor
# ---------------------------------------------------------------------------


class TestSubprocessSuccess:
    async def test_stub_runner_exits_zero_completes(self, registry):
        executor = SubprocessExecutor(registry)
        task = _make_task(registry, surface=TaskSurface.SUBPROCESS)
        registry.register(task)

        await executor.submit(task)
        # Reaper runs as a background task; poll the registry until terminal.
        for _ in range(200):  # 200 * 50ms = 10s max
            if registry.get(task.task_id).status.terminal:
                break
            await asyncio.sleep(0.05)

        loaded = registry.get(task.task_id)
        assert loaded.status is TaskStatus.COMPLETED
        assert "(see output log)" in (loaded.result or "")
        # The stub writes a line to the log.
        log_path = registry.output_path_for(task.task_id)
        assert log_path.exists()
        contents = log_path.read_text()
        assert task.task_id in contents


class TestSubprocessFailure:
    async def test_nonzero_exit_marks_failed(self, registry, tmp_path, monkeypatch):
        # Point the runner at a script that exits 1.
        bogus_runner = tmp_path / "_bogus_runner.py"
        bogus_runner.write_text(
            "import sys\nsys.stderr.write('crashed\\n')\nraise SystemExit(7)\n"
        )

        executor = SubprocessExecutor(registry)
        task = _make_task(registry, surface=TaskSurface.SUBPROCESS)
        registry.register(task)

        # Patch the runner_path lookup inside .submit by monkey-patching the
        # _subprocess_runner.py path resolution. The cleanest seam is to
        # subclass and override.
        class _CustomExecutor(SubprocessExecutor):
            async def submit(self, t: Task) -> None:
                if t.surface is not TaskSurface.SUBPROCESS:
                    raise ValueError(f"surface mismatch: {t.surface}")
                out_path = self._registry.output_path_for(t.task_id)
                log = out_path.open("ab", buffering=0)
                proc = await asyncio.create_subprocess_exec(
                    __import__("sys").executable,
                    "-I",
                    str(bogus_runner),
                    t.task_id,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=log,
                    stderr=log,
                )
                self._procs[t.task_id] = proc
                self._registry.transition(t.task_id, TaskStatus.RUNNING)
                asyncio.create_task(self._reap(t.task_id, proc, log))

        custom = _CustomExecutor(registry)
        await custom.submit(task)

        for _ in range(200):
            if registry.get(task.task_id).status.terminal:
                break
            await asyncio.sleep(0.05)

        loaded = registry.get(task.task_id)
        assert loaded.status is TaskStatus.FAILED
        assert "exit code 7" in (loaded.error or "")


class TestSubprocessKill:
    async def test_kill_terminates_subprocess(self, registry, tmp_path):
        # Use a runner that sleeps so we can kill it mid-flight.
        sleeper = tmp_path / "_sleeper_runner.py"
        sleeper.write_text(
            "import sys, time\n"
            "sys.stdout.write('starting\\n'); sys.stdout.flush()\n"
            "time.sleep(60)\n"
        )

        class _CustomExecutor(SubprocessExecutor):
            async def submit(self, t: Task) -> None:
                if t.surface is not TaskSurface.SUBPROCESS:
                    raise ValueError(f"surface mismatch: {t.surface}")
                out_path = self._registry.output_path_for(t.task_id)
                log = out_path.open("ab", buffering=0)
                proc = await asyncio.create_subprocess_exec(
                    __import__("sys").executable,
                    "-I",
                    str(sleeper),
                    t.task_id,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=log,
                    stderr=log,
                )
                self._procs[t.task_id] = proc
                self._registry.transition(t.task_id, TaskStatus.RUNNING)
                asyncio.create_task(self._reap(t.task_id, proc, log))

        executor = _CustomExecutor(registry)
        task = _make_task(registry, surface=TaskSurface.SUBPROCESS)
        registry.register(task)

        await executor.submit(task)
        # Give the sleeper a chance to actually start.
        await asyncio.sleep(0.1)
        await executor.kill(task.task_id)

        # The reap path runs as a background task; allow it to finalise.
        for _ in range(40):
            if registry.get(task.task_id).status.terminal:
                break
            await asyncio.sleep(0.05)

        loaded = registry.get(task.task_id)
        assert loaded.status is TaskStatus.KILLED


class TestSubprocessSurfaceMismatch:
    async def test_in_process_task_rejected(self, registry):
        executor = SubprocessExecutor(registry)
        wrong_task = _make_task(registry, surface=TaskSurface.IN_PROCESS)
        registry.register(wrong_task)
        with pytest.raises(ValueError, match="surface mismatch"):
            await executor.submit(wrong_task)
