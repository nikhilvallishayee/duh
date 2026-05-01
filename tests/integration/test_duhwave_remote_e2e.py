"""End-to-end tests for the Remote Task surface — ADR-030.

These tests bind a :class:`RemoteTaskServer` on an ephemeral port, drive
it from a real :class:`RemoteExecutor` over the loopback interface, and
verify the lifecycle round-trips both for the happy path and the kill
path.
"""

from __future__ import annotations

import asyncio

import pytest

from duh.duhwave.task.registry import (
    Task,
    TaskRegistry,
    TaskStatus,
    TaskSurface,
)
from duh.duhwave.task.remote import RemoteExecutor
from duh.duhwave.task.remote_server import RemoteTaskServer


# Treat everything in this file as integration; pyproject already adds the
# marker.
pytestmark = pytest.mark.integration


def _make_task(reg: TaskRegistry) -> Task:
    return Task(
        task_id=reg.new_id(),
        session_id="client",
        parent_id=None,
        surface=TaskSurface.REMOTE,
        prompt="hello world",
        model="haiku",
        tools_allowlist=(),
    )


@pytest.fixture
def client_registry(tmp_path):
    return TaskRegistry(session_dir=tmp_path / "client", session_id="client")


@pytest.fixture
def server_registry(tmp_path):
    return TaskRegistry(session_dir=tmp_path / "server", session_id="server")


class TestHappyPath:
    async def test_submit_runs_and_round_trips_result(
        self, client_registry, server_registry
    ):
        async def runner(task: Task) -> str:
            return f"echo:{task.prompt}"

        server = RemoteTaskServer(
            server_registry, runner=runner, auth_token="secret"
        )
        port = await server.start(port=0)
        try:
            executor = RemoteExecutor(
                client_registry,
                base_url=f"http://127.0.0.1:{port}",
                auth_token="secret",
            )
            executor._POLL_INTERVAL_S = 0.05  # type: ignore[attr-defined]
            client_task = _make_task(client_registry)
            client_registry.register(client_task)
            await executor.submit(client_task)

            for _ in range(200):
                t = client_registry.get(client_task.task_id)
                if t and t.status.terminal:
                    break
                await asyncio.sleep(0.05)

            loaded = client_registry.get(client_task.task_id)
            assert loaded.status is TaskStatus.COMPLETED
            assert loaded.result == "echo:hello world"
            # Server-side Task ran in-process and reached terminal.
            srv = server_registry.list()
            assert len(srv) == 1
            assert srv[0].status is TaskStatus.COMPLETED
        finally:
            await server.stop()


class TestKillMidFlight:
    async def test_kill_propagates_to_server(
        self, client_registry, server_registry
    ):
        started = asyncio.Event()
        unblock = asyncio.Event()

        async def runner(task: Task) -> str:
            started.set()
            await unblock.wait()
            return "never"

        server = RemoteTaskServer(
            server_registry, runner=runner, auth_token="secret"
        )
        port = await server.start(port=0)
        try:
            executor = RemoteExecutor(
                client_registry,
                base_url=f"http://127.0.0.1:{port}",
                auth_token="secret",
            )
            executor._POLL_INTERVAL_S = 0.05  # type: ignore[attr-defined]
            client_task = _make_task(client_registry)
            client_registry.register(client_task)
            await executor.submit(client_task)

            await asyncio.wait_for(started.wait(), timeout=5.0)
            await executor.kill(client_task.task_id)

            # Server-side transitions to KILLED.
            for _ in range(80):
                srv = server_registry.list()
                if srv and srv[0].status.terminal:
                    break
                await asyncio.sleep(0.05)
            srv_tasks = server_registry.list()
            assert len(srv_tasks) == 1
            assert srv_tasks[0].status is TaskStatus.KILLED
            # Client local Task too.
            assert (
                client_registry.get(client_task.task_id).status
                is TaskStatus.KILLED
            )
        finally:
            unblock.set()
            await server.stop()
