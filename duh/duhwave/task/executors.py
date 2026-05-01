"""Task executors — three surfaces, one interface. ADR-030.

The :class:`TaskExecutor` Protocol is the boundary between the generic
:class:`~duh.duhwave.task.registry.Task` lifecycle and the specifics of
*where* a task actually runs:

- :class:`InProcessExecutor`  — asyncio.Task in the host process.
- :class:`SubprocessExecutor` — fresh ``python3 -I`` subprocess.
- :class:`RemoteExecutor` (in :mod:`duh.duhwave.task.remote`) — HTTP+bearer.

Each executor enforces the Task's ``tools_allowlist`` capability boundary
and streams output to the registry's ``output_path_for(task_id)``.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from duh.duhwave.task.registry import Task, TaskRegistry, TaskStatus, TaskSurface


# Type alias for the function that actually runs an agent loop.
# Real wiring drives :class:`duh.kernel.engine.Engine`; the executor
# stays decoupled from that import to keep the boundary clean.
AgentRunner = Callable[[Task], Awaitable[str]]


class TaskExecutor(Protocol):
    """Run a Task to terminal state. Implementations must be async-safe."""

    surface: TaskSurface

    async def submit(self, task: Task) -> None: ...

    async def kill(self, task_id: str) -> None: ...


class InProcessExecutor:
    """Runs the agent loop as an asyncio.Task in the host process.

    Lowest latency. Crashes in the agent loop are caught and surfaced as
    Task FAILED transitions; crashes in the host process orphan the task
    (transition to FAILED on next host start with reason="orphaned").
    """

    surface = TaskSurface.IN_PROCESS

    def __init__(self, registry: TaskRegistry, runner: AgentRunner) -> None:
        self._registry = registry
        self._runner = runner
        self._asyncio_tasks: dict[str, asyncio.Task[None]] = {}

    async def submit(self, task: Task) -> None:
        if task.surface is not TaskSurface.IN_PROCESS:
            raise ValueError(f"executor surface mismatch: {task.surface}")
        self._registry.transition(task.task_id, TaskStatus.RUNNING)
        coro = self._run_to_completion(task)
        self._asyncio_tasks[task.task_id] = asyncio.create_task(
            coro, name=f"task:{task.task_id}"
        )

    async def kill(self, task_id: str) -> None:
        atask = self._asyncio_tasks.get(task_id)
        if atask is None or atask.done():
            return
        atask.cancel()
        try:
            await atask
        except (asyncio.CancelledError, Exception):
            pass
        # Defensive: only transition if not already terminal.
        t = self._registry.get(task_id)
        if t is not None and not t.status.terminal:
            self._registry.transition(task_id, TaskStatus.KILLED, error="killed by host")

    async def _run_to_completion(self, task: Task) -> None:
        try:
            result = await self._runner(task)
        except asyncio.CancelledError:
            self._registry.transition(task.task_id, TaskStatus.KILLED, error="cancelled")
            raise
        except Exception as e:
            self._registry.transition(task.task_id, TaskStatus.FAILED, error=f"{type(e).__name__}: {e}")
            return
        self._registry.transition(task.task_id, TaskStatus.COMPLETED, result=result)


class SubprocessExecutor:
    """Runs the agent loop in a fresh ``python3 -I`` subprocess.

    Real isolation; survives parent crashes via state on disk. Talks to
    the parent over stdin/stdout JSON for permission round-trips and
    output streaming. The subprocess script lives at
    ``duh/duhwave/task/_subprocess_runner.py`` (stub for now).
    """

    surface = TaskSurface.SUBPROCESS

    def __init__(self, registry: TaskRegistry) -> None:
        self._registry = registry
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        # task_ids whose termination was initiated by .kill() — the reaper
        # treats these as user-killed rather than crashed, regardless of
        # the exit code returned by SIGTERM/SIGKILL.
        self._killed: set[str] = set()

    async def submit(self, task: Task) -> None:
        if task.surface is not TaskSurface.SUBPROCESS:
            raise ValueError(f"executor surface mismatch: {task.surface}")
        runner_path = Path(__file__).parent / "_subprocess_runner.py"
        out_path = self._registry.output_path_for(task.task_id)
        log = out_path.open("ab", buffering=0)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(runner_path),
            task.task_id,
            stdin=asyncio.subprocess.PIPE,
            stdout=log,
            stderr=log,
        )
        self._procs[task.task_id] = proc
        # Persist pid before RUNNING so orphan-recovery on restart can
        # probe liveness via os.kill(pid, 0). The transition() call
        # writes the record, picking up metadata["pid"].
        task.metadata["pid"] = proc.pid
        self._registry.transition(task.task_id, TaskStatus.RUNNING)
        # Reaper runs in the background.
        asyncio.create_task(self._reap(task.task_id, proc, log))

    async def kill(self, task_id: str) -> None:
        proc = self._procs.get(task_id)
        if proc is None or proc.returncode is not None:
            return
        # Mark as user-killed *before* signalling the process so the reaper
        # — which may run concurrently — does not racily attribute the
        # SIGTERM-induced non-zero exit to a crash.
        self._killed.add(task_id)
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        t = self._registry.get(task_id)
        if t is not None and not t.status.terminal:
            self._registry.transition(task_id, TaskStatus.KILLED, error="killed by host")

    async def _reap(
        self,
        task_id: str,
        proc: asyncio.subprocess.Process,
        log,
    ) -> None:
        try:
            rc = await proc.wait()
        finally:
            log.close()
            self._procs.pop(task_id, None)
        t = self._registry.get(task_id)
        if t is None or t.status.terminal:
            self._killed.discard(task_id)
            return
        if task_id in self._killed:
            self._killed.discard(task_id)
            self._registry.transition(task_id, TaskStatus.KILLED, error="killed by host")
        elif rc == 0:
            self._registry.transition(task_id, TaskStatus.COMPLETED, result="(see output log)")
        else:
            self._registry.transition(task_id, TaskStatus.FAILED, error=f"exit code {rc}")
