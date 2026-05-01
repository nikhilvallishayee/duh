"""Remote Task execution server — companion to :class:`RemoteExecutor`. ADR-030.

A tiny ``aiohttp`` application that exposes the four endpoints the
:class:`~duh.duhwave.task.remote.RemoteExecutor` client expects:

* ``POST   /v1/tasks``                  — submit a Task
* ``GET    /v1/tasks/{id}``             — read current state
* ``DELETE /v1/tasks/{id}``             — kill
* ``GET    /v1/tasks/{id}/events``      — server-sent stream of lifecycle events

This server is suitable for unit + integration testing. It is also a
viable starting point for a real deployment — multi-tenancy, persistent
storage across restarts, and rate limiting are explicit non-goals here
and live in a follow-up ADR.

Auth: the server compares the ``Authorization`` header against the
configured ``auth_token`` using :func:`hmac.compare_digest` to keep the
comparison constant-time. The expected scheme is the literal string
``"Bearer "`` followed by the token; anything else returns 401.

Tasks accepted via POST run in-process via
:class:`~duh.duhwave.task.executors.InProcessExecutor` against the
runner supplied at construction time.
"""
from __future__ import annotations

import asyncio
import hmac
import json
from dataclasses import dataclass
from typing import Any

from duh.duhwave.task.executors import AgentRunner, InProcessExecutor
from duh.duhwave.task.registry import (
    Task,
    TaskRegistry,
    TaskStatus,
    TaskSurface,
)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _EventSubscriber:
    """One subscriber to the lifecycle event stream of a single Task."""

    queue: "asyncio.Queue[dict[str, Any]]"


class RemoteTaskServer:
    """aiohttp app exposing the ``/v1/tasks`` API.

    Construct, then call :meth:`build_app` to obtain an
    :class:`aiohttp.web.Application` you can run via the standard aiohttp
    runner pattern. :meth:`start` and :meth:`stop` provide a convenience
    bind-and-serve loop suitable for tests.
    """

    def __init__(
        self,
        registry: TaskRegistry,
        *,
        runner: AgentRunner,
        auth_token: str,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._auth_token = auth_token
        # Server-side executor wraps the user-supplied runner so we get
        # state-machine + persistence for free.
        self._executor = InProcessExecutor(registry, runner)
        # Per-task subscriber queues for the events endpoint.
        self._subscribers: dict[str, list[_EventSubscriber]] = {}
        # AppRunner / TCPSite only set once start() has been called.
        self._app_runner: Any = None
        self._site: Any = None
        self._port: int | None = None

    # ----- lifecycle ----------------------------------------------------

    def build_app(self) -> Any:
        from aiohttp import web

        @web.middleware
        async def auth_middleware(request: Any, handler: Any) -> Any:
            if not self._check_auth(request):
                return self._bad_token_response()
            return await handler(request)

        app = web.Application(middlewares=[auth_middleware])
        app.router.add_post("/v1/tasks", self._handle_create)
        app.router.add_get("/v1/tasks/{task_id}", self._handle_get)
        app.router.add_delete("/v1/tasks/{task_id}", self._handle_delete)
        app.router.add_get("/v1/tasks/{task_id}/events", self._handle_events)
        return app

    async def start(self, *, host: str = "127.0.0.1", port: int = 0) -> int:
        """Bind to ``host:port`` and start serving. Returns the actual port."""
        from aiohttp import web

        app = self.build_app()
        self._app_runner = web.AppRunner(app)
        await self._app_runner.setup()
        self._site = web.TCPSite(self._app_runner, host=host, port=port)
        await self._site.start()
        # Discover the actual bound port (port=0 → ephemeral).
        sockets = self._site._server.sockets if self._site._server else []  # type: ignore[attr-defined]
        if sockets:
            self._port = sockets[0].getsockname()[1]
        else:
            self._port = port
        return int(self._port)

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._app_runner is not None:
            await self._app_runner.cleanup()
            self._app_runner = None

    @property
    def port(self) -> int | None:
        return self._port

    # ----- middleware ---------------------------------------------------

    @staticmethod
    def _bad_token_response() -> Any:
        from aiohttp import web

        return web.json_response(
            {"error": "unauthorized: invalid or missing bearer token"},
            status=401,
        )

    def _check_auth(self, request: Any) -> bool:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        token = header[len("Bearer ") :]
        # Constant-time comparison; both sides must be bytes/str of same kind.
        return hmac.compare_digest(token, self._auth_token)

    # ----- handlers -----------------------------------------------------

    async def _handle_create(self, request: Any) -> Any:
        from aiohttp import web

        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response(
                {"error": "bad request: body is not JSON"}, status=400
            )
        if not isinstance(body, dict):
            return web.json_response({"error": "bad request: body must be object"}, status=400)

        prompt = str(body.get("prompt") or "")
        if not prompt:
            return web.json_response({"error": "bad request: prompt is required"}, status=400)
        model = str(body.get("model") or "")
        tools = tuple(body.get("tools") or ())
        expose = tuple(body.get("expose") or ())
        metadata = dict(body.get("metadata") or {})

        task_id = self._registry.new_id()
        task = Task(
            task_id=task_id,
            session_id=task_id.split(":", 1)[0],
            parent_id=None,
            surface=TaskSurface.IN_PROCESS,  # remote server runs in-process
            prompt=prompt,
            model=model,
            tools_allowlist=tools,  # type: ignore[arg-type]
            expose_handles=expose,  # type: ignore[arg-type]
            metadata=metadata,
        )
        self._registry.register(task)

        # Wrap the executor to fan terminal events to subscribers.
        await self._executor.submit(task)
        # Spawn a watcher that publishes lifecycle events when the
        # underlying asyncio.Task finishes. We also publish a "started"
        # event immediately for clients that connect right after create.
        asyncio.create_task(self._watch_and_publish(task_id))

        return web.json_response(
            {"task_id": task_id, "status": TaskStatus.PENDING.value},
            status=201,
        )

    async def _handle_get(self, request: Any) -> Any:
        from aiohttp import web

        task_id = request.match_info["task_id"]
        t = self._registry.get(task_id)
        if t is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(
            {
                "task_id": t.task_id,
                "status": t.status.value,
                "result": t.result,
                "error": t.error,
                "started_at": t.started_at,
                "terminated_at": t.terminated_at,
            },
            status=200,
        )

    async def _handle_delete(self, request: Any) -> Any:
        from aiohttp import web

        task_id = request.match_info["task_id"]
        t = self._registry.get(task_id)
        if t is None:
            return web.json_response({"error": "not found"}, status=404)
        await self._executor.kill(task_id)
        return web.json_response({"ok": True}, status=200)

    async def _handle_events(self, request: Any) -> Any:
        """Stream lifecycle events as JSON-per-line (text/event-stream)."""
        from aiohttp import web

        task_id = request.match_info["task_id"]
        t = self._registry.get(task_id)
        if t is None:
            return web.json_response({"error": "not found"}, status=404)

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
            },
        )
        await response.prepare(request)
        sub = _EventSubscriber(queue=asyncio.Queue())
        self._subscribers.setdefault(task_id, []).append(sub)

        # Emit a synthetic "started" event reflecting the current state so
        # late subscribers know what state we're in.
        await sub.queue.put({"event": "started", "status": t.status.value})

        try:
            while True:
                event = await sub.queue.get()
                payload = json.dumps(event).encode("utf-8") + b"\n"
                await response.write(payload)
                if event.get("event") in {"completed", "failed", "killed"}:
                    break
        finally:
            try:
                self._subscribers.get(task_id, []).remove(sub)
            except ValueError:
                pass
        return response

    # ----- watcher ------------------------------------------------------

    async def _watch_and_publish(self, task_id: str) -> None:
        """Poll the registry for terminal state and fan to subscribers."""
        while True:
            await asyncio.sleep(0.05)
            t = self._registry.get(task_id)
            if t is None:
                return
            if not t.status.terminal:
                continue
            event = self._terminal_event(t)
            for sub in list(self._subscribers.get(task_id, [])):
                await sub.queue.put(event)
            return

    @staticmethod
    def _terminal_event(t: Task) -> dict[str, Any]:
        if t.status is TaskStatus.COMPLETED:
            return {"event": "completed", "status": t.status.value, "result": t.result}
        if t.status is TaskStatus.FAILED:
            return {"event": "failed", "status": t.status.value, "error": t.error}
        return {"event": "killed", "status": t.status.value, "error": t.error}
