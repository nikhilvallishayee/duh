"""Tests for :mod:`duh.duhwave.task.remote` — RemoteExecutor.

The tests use a small in-process fake HTTP transport rather than spinning
up a real socket server (the e2e tests in
``tests/integration/test_duhwave_remote_e2e.py`` cover real-socket flow).

Tests under :class:`TestSubmitFlow`, :class:`TestAuth`, :class:`TestKill`
need a real ``aiohttp`` server, so they're gated with
``pytest.importorskip("aiohttp")`` at module top — they skip cleanly on
environments where the optional dep isn't installed (matches the
``croniter`` / ``watchfiles`` listener-test pattern).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

import duh.duhwave.task.remote as remote_mod
from duh.duhwave.task.registry import (
    Task,
    TaskRegistry,
    TaskStatus,
    TaskSurface,
)
from duh.duhwave.task.remote import (
    RemoteExecutor,
    RemoteExecutorError,
    _HTTPResponse,
    _HTTPTransport,
)


# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------


@dataclass
class _FakeRoute:
    """One scripted response for a method+path. Reused as many times as set."""

    status: int
    body: dict[str, Any] | None = None
    raises: BaseException | None = None
    delay_s: float = 0.0


@dataclass
class _FakeTransport(_HTTPTransport):
    """Subclass of :class:`_HTTPTransport` that returns scripted responses."""

    # NOTE: dataclass=False semantics — we deliberately bypass the parent
    # __init__ by overriding it.
    routes: dict[tuple[str, str], list[_FakeRoute]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    has_aiohttp_override: bool = True

    def __init__(self) -> None:
        # Skip _HTTPTransport.__init__ — we don't import aiohttp here.
        self.routes = {}
        self.calls = []
        self.has_aiohttp_override = True
        self._timeout_s = 30.0  # match parent attr the executor reads
        self._aiohttp = object()  # truthy so .has_aiohttp returns True

    @property
    def has_aiohttp(self) -> bool:  # type: ignore[override]
        return self.has_aiohttp_override

    def script(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        body: dict[str, Any] | None = None,
        raises: BaseException | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self.routes.setdefault((method, path), []).append(
            _FakeRoute(status=status, body=body, raises=raises, delay_s=delay_s)
        )

    async def request(  # type: ignore[override]
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: Any | None = None,
    ) -> _HTTPResponse:
        # Strip the base url; route on path only.
        # url shape: "http://host/v1/tasks/<id>" → "/v1/tasks/<id>"
        path = url.split("/", 3)[-1]
        if not path.startswith("/"):
            path = "/" + path
        self.calls.append((method, path))
        # Bearer header sanity — the executor must always send one.
        assert headers.get("Authorization", "").startswith("Bearer ")

        # Match exact path; fall back to the trailing-segment match for
        # the {id} routes ("/v1/tasks/<id>" → "/v1/tasks/{id}").
        scripted = self.routes.get((method, path))
        if scripted is None:
            scripted = self.routes.get((method, _generic_path(path)))
        if not scripted:
            return _HTTPResponse(status=500, body=b'{"error":"unrouted"}')

        route = scripted[0] if len(scripted) == 1 else scripted.pop(0)
        if route.delay_s:
            await asyncio.sleep(route.delay_s)
        if route.raises is not None:
            raise route.raises
        body_bytes = b""
        if route.body is not None:
            body_bytes = json.dumps(route.body).encode("utf-8")
        return _HTTPResponse(status=route.status, body=body_bytes)


def _generic_path(path: str) -> str:
    """Map ``/v1/tasks/abc:000001`` → ``/v1/tasks/{id}``-shaped key."""
    parts = path.split("/")
    if len(parts) >= 4 and parts[1] == "v1" and parts[2] == "tasks":
        if len(parts) == 4:
            return "/v1/tasks/{id}"
        if len(parts) == 5 and parts[4] == "events":
            return "/v1/tasks/{id}/events"
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(reg: TaskRegistry) -> Task:
    return Task(
        task_id=reg.new_id(),
        session_id="s1",
        parent_id=None,
        surface=TaskSurface.REMOTE,
        prompt="hello",
        model="haiku",
        tools_allowlist=(),
    )


@pytest.fixture
def registry(tmp_path):
    return TaskRegistry(session_dir=tmp_path, session_id="s1")


# ---------------------------------------------------------------------------
# 1. Unit tests against the fake transport
# ---------------------------------------------------------------------------


class TestSubmitFlow:
    async def test_post_creates_remote_task_and_completes(self, registry):
        pytest.importorskip("aiohttp")
        # Spin up a tiny in-memory "server" via a real RemoteTaskServer so
        # the create endpoint exercises both registry + executor paths.
        from duh.duhwave.task.remote_server import RemoteTaskServer

        server_registry = TaskRegistry(
            session_dir=registry._session_dir / "server", session_id="server"
        )
        async def runner(task: Task) -> str:
            return "ok"

        server = RemoteTaskServer(
            server_registry, runner=runner, auth_token="t0p"
        )
        port = await server.start(port=0)
        try:
            executor = RemoteExecutor(
                registry,
                base_url=f"http://127.0.0.1:{port}",
                auth_token="t0p",
            )
            local = _make_task(registry)
            registry.register(local)
            # Tighten poll for snappier test.
            executor._POLL_INTERVAL_S = 0.05  # type: ignore[attr-defined]
            await executor.submit(local)

            # Wait for terminal locally.
            for _ in range(200):
                t = registry.get(local.task_id)
                if t and t.status.terminal:
                    break
                await asyncio.sleep(0.05)

            assert registry.get(local.task_id).status is TaskStatus.COMPLETED
            assert registry.get(local.task_id).result == "ok"
            # The server-side registry has a task too.
            assert len(server_registry.list()) == 1
            srv_task = server_registry.list()[0]
            assert srv_task.status is TaskStatus.COMPLETED
        finally:
            await server.stop()

    async def test_get_returns_terminal_status_after_run(self, registry):
        pytest.importorskip("aiohttp")
        """Direct GET on the server registry surfaces terminal state."""
        from duh.duhwave.task.remote_server import RemoteTaskServer

        async def runner(task: Task) -> str:
            return "done"

        server = RemoteTaskServer(registry, runner=runner, auth_token="t0p")
        port = await server.start(port=0)
        try:
            # Submit one Task by hitting the endpoint directly.
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{port}/v1/tasks",
                    json={"prompt": "x", "model": "haiku", "tools": []},
                    headers={"Authorization": "Bearer t0p"},
                ) as resp:
                    payload = await resp.json()
                    assert resp.status == 201
                    task_id = payload["task_id"]

                # Poll until terminal.
                for _ in range(200):
                    async with session.get(
                        f"http://127.0.0.1:{port}/v1/tasks/{task_id}",
                        headers={"Authorization": "Bearer t0p"},
                    ) as r:
                        body = await r.json()
                        if body["status"] in {"completed", "failed", "killed"}:
                            break
                    await asyncio.sleep(0.05)
                assert body["status"] == "completed"
                assert body["result"] == "done"
        finally:
            await server.stop()


class TestAuth:
    async def test_server_rejects_bad_token(self, registry):
        pytest.importorskip("aiohttp")
        from duh.duhwave.task.remote_server import RemoteTaskServer

        async def runner(task: Task) -> str:
            return "x"

        server = RemoteTaskServer(registry, runner=runner, auth_token="real")
        port = await server.start(port=0)
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{port}/v1/tasks",
                    json={"prompt": "x", "model": "haiku"},
                    headers={"Authorization": "Bearer wrong"},
                ) as resp:
                    assert resp.status == 401
                async with session.post(
                    f"http://127.0.0.1:{port}/v1/tasks",
                    json={"prompt": "x", "model": "haiku"},
                    # No header at all.
                ) as resp:
                    assert resp.status == 401
        finally:
            await server.stop()

    async def test_client_raises_on_bad_token(self, registry):
        transport = _FakeTransport()
        # Server replies 401 — RemoteExecutor must surface as RemoteExecutorError.
        transport.script("POST", "/v1/tasks", status=401, body={"error": "nope"})
        executor = RemoteExecutor(
            registry, "http://h", "wrong", http_client=transport
        )
        local = _make_task(registry)
        registry.register(local)
        with pytest.raises(RemoteExecutorError) as ei:
            await executor.submit(local)
        assert ei.value.status == 401


class TestRetry:
    async def test_5xx_retries_succeeds_on_third_attempt(self, registry):
        transport = _FakeTransport()
        transport.script("POST", "/v1/tasks", status=503, body={"error": "down"})
        transport.script("POST", "/v1/tasks", status=502, body={"error": "down"})
        transport.script(
            "POST", "/v1/tasks", status=201, body={"task_id": "remote-1", "status": "pending"}
        )
        # GET reaper path — reply terminal immediately so reaper returns.
        transport.script(
            "GET",
            "/v1/tasks/{id}",
            status=200,
            body={"task_id": "remote-1", "status": "completed", "result": "ok"},
        )
        # Compress backoff so the test is fast.
        executor = RemoteExecutor(
            registry, "http://h", "t", http_client=transport
        )
        executor._RETRY_BACKOFFS_S = (0.0, 0.0, 0.0)  # type: ignore[attr-defined]
        executor._POLL_INTERVAL_S = 0.0  # type: ignore[attr-defined]

        local = _make_task(registry)
        registry.register(local)
        await executor.submit(local)
        # Wait for reaper.
        for _ in range(200):
            t = registry.get(local.task_id)
            if t and t.status.terminal:
                break
            await asyncio.sleep(0.01)
        assert registry.get(local.task_id).status is TaskStatus.COMPLETED
        # 3 POST + at least 1 GET
        post_calls = [c for c in transport.calls if c[0] == "POST"]
        assert len(post_calls) == 3

    async def test_4xx_does_not_retry(self, registry):
        transport = _FakeTransport()
        transport.script("POST", "/v1/tasks", status=400, body={"error": "bad"})

        executor = RemoteExecutor(
            registry, "http://h", "t", http_client=transport
        )
        executor._RETRY_BACKOFFS_S = (0.0, 0.0, 0.0)  # type: ignore[attr-defined]
        local = _make_task(registry)
        registry.register(local)
        with pytest.raises(RemoteExecutorError) as ei:
            await executor.submit(local)
        assert ei.value.status == 400
        # Single attempt only.
        post_calls = [c for c in transport.calls if c[0] == "POST"]
        assert len(post_calls) == 1


class TestKill:
    async def test_delete_marks_killed_on_server(self, registry):
        pytest.importorskip("aiohttp")
        from duh.duhwave.task.remote_server import RemoteTaskServer

        started = asyncio.Event()
        unblock = asyncio.Event()

        async def runner(task: Task) -> str:
            started.set()
            await unblock.wait()
            return "never"

        server_reg = TaskRegistry(
            session_dir=registry._session_dir / "srv", session_id="srv"
        )
        server = RemoteTaskServer(server_reg, runner=runner, auth_token="t")
        port = await server.start(port=0)
        try:
            executor = RemoteExecutor(
                registry, f"http://127.0.0.1:{port}", "t"
            )
            executor._POLL_INTERVAL_S = 0.05  # type: ignore[attr-defined]
            local = _make_task(registry)
            registry.register(local)
            await executor.submit(local)

            await asyncio.wait_for(started.wait(), timeout=2.0)
            await executor.kill(local.task_id)

            # Server-side Task transitions to KILLED.
            for _ in range(40):
                if server_reg.list() and server_reg.list()[0].status.terminal:
                    break
                await asyncio.sleep(0.05)
            assert server_reg.list()[0].status is TaskStatus.KILLED
            # Local Task is also KILLED.
            assert registry.get(local.task_id).status is TaskStatus.KILLED
        finally:
            unblock.set()
            await server.stop()


class TestTimeouts:
    async def test_per_request_timeout_enforced(self, registry):
        transport = _FakeTransport()
        # Simulate an upstream that never responds → all retries time out.
        transport.script(
            "POST", "/v1/tasks",
            raises=asyncio.TimeoutError(),
        )
        transport.script(
            "POST", "/v1/tasks",
            raises=asyncio.TimeoutError(),
        )
        transport.script(
            "POST", "/v1/tasks",
            raises=asyncio.TimeoutError(),
        )
        executor = RemoteExecutor(
            registry, "http://h", "t", http_client=transport
        )
        executor._RETRY_BACKOFFS_S = (0.0, 0.0, 0.0)  # type: ignore[attr-defined]
        local = _make_task(registry)
        registry.register(local)
        with pytest.raises(RemoteExecutorError, match="transport error"):
            await executor.submit(local)


class TestMaxWait:
    async def test_max_wait_exhaustion_marks_failed(self, registry):
        transport = _FakeTransport()
        transport.script(
            "POST", "/v1/tasks",
            status=201, body={"task_id": "r", "status": "pending"},
        )
        # Reply "running" forever — the reaper's max_wait_s should fire.
        for _ in range(200):
            transport.script(
                "GET", "/v1/tasks/{id}",
                status=200, body={"task_id": "r", "status": "running"},
            )
        executor = RemoteExecutor(
            registry,
            "http://h",
            "t",
            http_client=transport,
            max_wait_s=0.05,
        )
        executor._RETRY_BACKOFFS_S = (0.0, 0.0, 0.0)  # type: ignore[attr-defined]
        executor._POLL_INTERVAL_S = 0.01  # type: ignore[attr-defined]
        local = _make_task(registry)
        registry.register(local)
        await executor.submit(local)

        for _ in range(200):
            t = registry.get(local.task_id)
            if t and t.status.terminal:
                break
            await asyncio.sleep(0.02)
        loaded = registry.get(local.task_id)
        assert loaded.status is TaskStatus.FAILED
        assert "remote task wait exceeded" in (loaded.error or "")


class TestAiohttpUnavailable:
    async def test_no_aiohttp_no_urllib_raises(self, registry, monkeypatch):
        # Force has_aiohttp -> False and patch urllib detection so the
        # executor must raise at submit time.
        from duh.duhwave.task import remote as remote_module

        class _NoTransport(_HTTPTransport):
            def __init__(self) -> None:
                self._timeout_s = 30.0
                self._aiohttp = None

            @property
            def has_aiohttp(self) -> bool:  # type: ignore[override]
                return False

        monkeypatch.setattr(remote_module, "_urllib_available", lambda: False)
        executor = RemoteExecutor(
            registry, "http://h", "t", http_client=_NoTransport()
        )
        local = _make_task(registry)
        registry.register(local)
        with pytest.raises(RemoteExecutorError, match="aiohttp or urllib"):
            await executor.submit(local)
