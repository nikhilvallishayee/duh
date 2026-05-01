"""duhwave host daemon.

Runs as a single asyncio process. Serves Unix-socket RPC requests from
the ``duh wave`` CLI. Hosts the persistent agent runtime: per-swarm
:class:`HostState` (with a :class:`TaskRegistry` and event log),
ingress listeners, subscription matcher.

ADR-032 §C control-plane ops implemented here:

- ``ping``       — liveness check
- ``ls_tasks``   — flatten task lists across all installed swarms
- ``inspect``    — topology + state snapshot for one swarm
- ``pause``      — idempotent flag-file marker; trigger spawns honour it
- ``resume``     — unset the pause flag
- ``logs``       — tail the swarm's event log
- ``web``        — optional aiohttp read-only JSON server (degrades
                   cleanly if aiohttp is not installed)
- ``shutdown``   — clean asyncio loop exit

Trigger spawning + agent execution wires through ADR-031's coordinator
in a follow-up step; this module owns the RPC surface only.

Usage::

    python -m duh.duhwave.cli.daemon <waves_root> [<swarm_name>]
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

from duh.duhwave.bundle.installer import BundleInstaller
from duh.duhwave.cli.dispatcher import Dispatcher
from duh.duhwave.cli.host_state import HostState
from duh.duhwave.cli.rpc import host_pid_path, host_socket_path
from duh.duhwave.cli.runner import HostRunner, disabled_runner, openai_text_runner
from duh.duhwave.ingress.triggers import TriggerKind, TriggerLog
from duh.duhwave.spec.parser import SwarmSpec, SwarmSpecError, parse_swarm

logger = logging.getLogger(__name__)


# ── streaming logs cadence (test seam) ────────────────────────────────
# The streaming-logs implementation polls the event-log file at
# :data:`LOGS_FOLLOW_POLL_S` and emits a heartbeat every
# :data:`LOGS_FOLLOW_HEARTBEAT_S`. Tests override these via
# monkeypatch to keep wall-clock waits short.
LOGS_FOLLOW_POLL_S: float = 0.5
LOGS_FOLLOW_HEARTBEAT_S: float = 30.0


async def _emit_stream_line(
    writer: asyncio.StreamWriter, payload: dict[str, Any]
) -> bool:
    """Write one ``:``-prefixed JSON line to ``writer``.

    Returns ``True`` on success, ``False`` if the client has gone away
    (connection reset, broken pipe). The caller uses the return value
    to abandon the follow loop cleanly without raising.
    """
    try:
        writer.write(b":" + json.dumps(payload).encode("utf-8") + b"\n")
        await writer.drain()
    except (ConnectionError, OSError):
        return False
    return True


class _Host:
    """Per-process host harness for one or more installed swarms.

    On startup the host walks ``BundleInstaller(waves_root)``'s install
    index, parses each ``swarm.toml``, and builds one
    :class:`HostState` per swarm. The states sit in
    ``self.swarms[name]``; every control-plane op resolves a
    ``swarm_id`` against this dict.

    Bundles whose ``swarm.toml`` fails to parse are skipped with a
    warning rather than aborting host startup — the user can still
    ``ls`` / ``uninstall`` a broken bundle while the rest of the swarms
    keep running.
    """

    def __init__(self, waves_root: Path, swarm_name: str | None) -> None:
        self.waves_root = waves_root
        self.swarm_name = swarm_name
        self.trigger_log = TriggerLog(waves_root / "triggers.jsonl")
        self._stopping = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._web_runner: Any = None  # aiohttp AppRunner, when started
        # Per-swarm runtime state, keyed by SwarmSpec.name.
        self.swarms: dict[str, HostState] = {}
        self._load_installed_swarms()
        # Dispatcher attached on run(). Pick OpenAI runner if a key is
        # present at startup; otherwise fall back to the disabled
        # runner so triggers are still observable in the event log.
        self._runner: HostRunner = (
            openai_text_runner if os.environ.get("OPENAI_API_KEY") else disabled_runner
        )
        self._dispatcher: Dispatcher | None = None
        # Topology-driven ingress listeners (webhook / filewatch / cron /
        # mcp_push). Booted in :meth:`run` after the dispatcher is up
        # and torn down in the same finally block.
        self._listeners: list[Any] = []

    def _load_installed_swarms(self) -> None:
        """Discover installed bundles, parse each swarm.toml, build state.

        Defensive: a bundle missing/corrupt swarm.toml is skipped, not
        fatal, so an operator can ``duh wave uninstall`` it.
        """
        installer = BundleInstaller(root=self.waves_root)
        for entry in installer.list_installed():
            install_dir = Path(entry.path)
            spec_path = install_dir / "swarm.toml"
            if not spec_path.exists():
                logger.warning("installed bundle missing swarm.toml: %s", install_dir)
                continue
            try:
                spec: SwarmSpec = parse_swarm(spec_path)
            except (SwarmSpecError, OSError) as e:
                logger.warning("failed to parse %s: %s", spec_path, e)
                continue
            # If a single-swarm host was requested via CLI, skip the rest.
            if self.swarm_name is not None and spec.name != self.swarm_name:
                continue
            state = HostState(install_dir=install_dir, spec=spec)
            state.append_event("host.start", f"loaded swarm {spec.name} {spec.version}")
            self.swarms[spec.name] = state

    async def run(self) -> int:
        sock_path = host_socket_path(self.waves_root)
        sock_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._handle_client, path=str(sock_path))
        os.chmod(sock_path, 0o600)
        # Persist PID for the CLI.
        host_pid_path(self.waves_root).write_text(str(os.getpid()))

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: self._stopping.set())

        # Start the trigger-to-spawn dispatcher (ADR-031 §B + ADR-032 §C).
        # Only meaningful when at least one swarm is installed.
        if self.swarms:
            self._dispatcher = Dispatcher(
                log=self.trigger_log,
                swarms=self.swarms,
                runner=self._runner,
            )
            await self._dispatcher.start()
            for state in self.swarms.values():
                state.append_event(
                    "host.dispatcher_ready",
                    f"runner={self._runner.__name__}",
                )
            # Listener auto-boot lives behind the dispatcher: the
            # dispatcher feeds off the trigger log, listeners write to
            # it. Order matters — dispatcher first so the very first
            # ingressed event has somewhere to land.
            await self._start_listeners()

        try:
            async with server:
                await self._stopping.wait()
        finally:
            await self._stop_listeners()
            if self._dispatcher is not None:
                with contextlib.suppress(Exception):
                    await self._dispatcher.stop()
            for t in self._tasks:
                t.cancel()
            # Gracefully shut down the web runner if it's running.
            if self._web_runner is not None:
                with contextlib.suppress(Exception):
                    await self._web_runner.cleanup()
            with contextlib.suppress(Exception):
                sock_path.unlink()
            host_pid_path(self.waves_root).unlink(missing_ok=True)
        return 0

    # ── ingress lifecycle ──────────────────────────────────────────────

    async def _start_listeners(self) -> None:
        """Boot one listener per :class:`TriggerKind` declared by any swarm.

        Topology layout drives this:

        * ``webhook`` — one shared :class:`WebhookListener` for all
          swarms; per-swarm secrets are merged into a ``{prefix → secret}``
          map keyed off each webhook trigger's ``source``.
        * ``filewatch`` — one :class:`FileWatchListener` per unique
          watch path across all swarms.
        * ``cron`` — one shared :class:`CronListener` whose entries
          aggregate every swarm's cron trigger.
        * ``mcp_push`` — TODO(adr-031-mcp-push), skipped for now.
        * ``manual`` — the daemon already exposes its RPC socket; the
          manual seam is on by default and not booted here.

        Optional dependencies (``aiohttp`` / ``watchfiles`` / ``croniter``)
        are imported lazily; if missing, the listener is skipped with a
        warning rather than crashing the daemon.
        """
        # ── group triggers by kind ──
        webhook_secrets: dict[str, str] = {}
        webhook_port = 8728
        webhook_host = "127.0.0.1"
        webhook_port_explicit = False
        any_webhook = False

        filewatch_paths: set[str] = set()
        cron_entries: list[tuple[str, str]] = []
        mcp_skipped = False

        for state in self.swarms.values():
            spec: SwarmSpec = state.spec
            for trigger in spec.triggers:
                try:
                    kind = TriggerKind(trigger.kind)
                except ValueError:
                    continue

                if kind is TriggerKind.WEBHOOK:
                    any_webhook = True
                    if spec.ingress.secret is not None:
                        webhook_secrets[trigger.source] = spec.ingress.secret
                    # First swarm with a non-default port wins; later
                    # explicit ports against the same listener log a
                    # warning so the operator notices the conflict.
                    has_explicit_port = spec.ingress.webhook_port != 8728
                    has_explicit_host = spec.ingress.webhook_host != "127.0.0.1"
                    if has_explicit_port or has_explicit_host:
                        if not webhook_port_explicit:
                            webhook_port = spec.ingress.webhook_port
                            webhook_host = spec.ingress.webhook_host
                            webhook_port_explicit = True
                        elif (
                            spec.ingress.webhook_port != webhook_port
                            or spec.ingress.webhook_host != webhook_host
                        ):
                            logger.warning(
                                "swarm %s requests %s:%d; already bound to %s:%d",
                                spec.name,
                                spec.ingress.webhook_host,
                                spec.ingress.webhook_port,
                                webhook_host,
                                webhook_port,
                            )
                elif kind is TriggerKind.FILEWATCH:
                    filewatch_paths.add(trigger.source)
                elif kind is TriggerKind.CRON:
                    # Cron triggers' ``source`` doubles as a label and
                    # the cron expression lives in
                    # ``options['expr']`` — fall back to ``source`` if
                    # the topology is using source-as-expression.
                    expr = str(trigger.options.get("expr") or trigger.source)
                    cron_entries.append((expr, trigger.source))
                elif kind is TriggerKind.MCP_PUSH:
                    # TODO(adr-031-mcp-push): wire once the MCP client
                    # exposes a notification subscription API.
                    mcp_skipped = True

        # ── webhook ──
        if any_webhook:
            await self._boot_webhook_listener(
                host=webhook_host,
                port=webhook_port,
                secrets=webhook_secrets,
            )

        # ── filewatch ──
        for path in sorted(filewatch_paths):
            await self._boot_filewatch_listener(path)

        # ── cron ──
        if cron_entries:
            await self._boot_cron_listener(cron_entries)

        # ── mcp_push ──
        if mcp_skipped:
            for state in self.swarms.values():
                state.append_event(
                    "host.listener_skipped",
                    "kind=mcp_push reason=adr-031-follow-up",
                )

    async def _boot_webhook_listener(
        self, *, host: str, port: int, secrets: dict[str, str]
    ) -> None:
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            logger.warning(
                "aiohttp not installed — webhook listener disabled "
                "(install with: pip install 'aiohttp')"
            )
            for state in self.swarms.values():
                state.append_event(
                    "host.listener_skipped",
                    f"kind=webhook reason=missing_aiohttp",
                )
            return

        # Lazy import: the listener module imports aiohttp at start()
        # time itself, but we want a clean error path if it's absent.
        from duh.duhwave.ingress.webhook import WebhookListener

        listener = WebhookListener(
            log=self.trigger_log,
            port=port,
            host=host,
            secrets=secrets or None,
        )
        try:
            await listener.start()
        except OSError as e:
            logger.warning(
                "webhook listener failed to bind %s:%d — %s", host, port, e
            )
            for state in self.swarms.values():
                state.append_event(
                    "host.listener_failed",
                    f"kind=webhook host={host} port={port} err={e}",
                )
            return
        self._listeners.append(listener)
        for state in self.swarms.values():
            state.append_event(
                "host.listener_started",
                f"kind=webhook port={port} secrets={len(secrets)}",
            )

    async def _boot_filewatch_listener(self, path: str) -> None:
        try:
            import watchfiles  # noqa: F401
        except ImportError:
            logger.warning(
                "watchfiles not installed — filewatch listener for %s disabled",
                path,
            )
            for state in self.swarms.values():
                state.append_event(
                    "host.listener_skipped",
                    f"kind=filewatch path={path} reason=missing_watchfiles",
                )
            return

        from duh.duhwave.ingress.filewatch import FileWatchListener

        listener = FileWatchListener(
            log=self.trigger_log,
            paths=[Path(path)],
        )
        try:
            await listener.start()
        except Exception as e:
            logger.warning("filewatch listener failed for %s — %s", path, e)
            for state in self.swarms.values():
                state.append_event(
                    "host.listener_failed",
                    f"kind=filewatch path={path} err={type(e).__name__}: {e}",
                )
            return
        self._listeners.append(listener)
        for state in self.swarms.values():
            state.append_event(
                "host.listener_started",
                f"kind=filewatch path={path}",
            )

    async def _boot_cron_listener(self, entries: list[tuple[str, str]]) -> None:
        try:
            import croniter  # noqa: F401
        except ImportError:
            logger.warning(
                "croniter not installed — cron listener disabled "
                "(install with: pip install 'croniter')"
            )
            for state in self.swarms.values():
                state.append_event(
                    "host.listener_skipped",
                    f"kind=cron entries={len(entries)} reason=missing_croniter",
                )
            return

        from duh.duhwave.ingress.cron import CronListener

        listener = CronListener(log=self.trigger_log, entries=entries)
        try:
            await listener.start()
        except Exception as e:
            logger.warning(
                "cron listener failed to start — %s entries=%d",
                e,
                len(entries),
            )
            for state in self.swarms.values():
                state.append_event(
                    "host.listener_failed",
                    f"kind=cron entries={len(entries)} err={type(e).__name__}: {e}",
                )
            return
        self._listeners.append(listener)
        for state in self.swarms.values():
            state.append_event(
                "host.listener_started",
                f"kind=cron entries={len(entries)}",
            )

    async def _stop_listeners(self) -> None:
        """Tear down every booted listener. Idempotent + best-effort."""
        for listener in self._listeners:
            with contextlib.suppress(Exception):
                await listener.stop()
            kind = _listener_kind(listener)
            for state in self.swarms.values():
                state.append_event(
                    "host.listener_stopped",
                    f"kind={kind}",
                )
        self._listeners.clear()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        line = await reader.readline()
        if not line:
            writer.close()
            return
        try:
            req = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as e:
            writer.write(
                (json.dumps({"error": f"bad json: {e}"}) + "\n").encode("utf-8")
            )
            await writer.drain()
            writer.close()
            return

        # ``logs follow=true`` is the one streaming op. The wire format
        # is documented on :meth:`_stream_logs`. Every other op (and
        # ``logs`` without ``follow``) keeps the legacy
        # one-request/one-response shape.
        if req.get("op") == "logs" and bool(req.get("follow", False)):
            await self._stream_logs(req, reader, writer)
            return

        resp = await self._dispatch(req)
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()

    # ── streaming logs (op="logs", follow=true) ─────────────────────

    async def _stream_logs(
        self,
        req: dict[str, Any],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Stream a swarm's event log to the client until disconnect.

        Wire framing (newline-delimited JSON over the same socket the
        unary RPCs use)::

            :{"line": "<unix_ts>\\t<kind>\\t<msg>", "offset": <int>}
            :{"heartbeat": <unix_ts>}
            ...
            {"done": true}

        Lines prefixed with ``:`` are stream items. The unprefixed
        ``{"done": true}`` is the clean terminator the server emits
        when its loop exits (daemon shutdown, broken pipe, etc.). The
        client stops a follow by closing the socket.

        Initial response sends the last ``lines`` snapshot lines, then
        the loop polls the event-log file every
        :data:`LOGS_FOLLOW_POLL_S` seconds; on growth, the new bytes
        are read from the prior offset and emitted as one
        ``:{"line", "offset"}`` frame per complete log line. A
        heartbeat goes out every :data:`LOGS_FOLLOW_HEARTBEAT_S`
        seconds to keep the connection live on quiet logs.
        """
        swarm_id = req.get("swarm_id")
        state = self._lookup(swarm_id) if isinstance(swarm_id, str) else None
        if state is None:
            # Error responses on the streaming path use the same
            # one-line shape as unary ops, then close. Clients
            # detecting an unprefixed line treat it as a terminal
            # response.
            writer.write(
                (
                    json.dumps({"error": f"swarm not installed: {swarm_id}"}) + "\n"
                ).encode("utf-8")
            )
            try:
                await writer.drain()
            except (ConnectionError, OSError):
                pass
            writer.close()
            return

        try:
            initial_lines = max(0, int(req.get("lines", 200) or 0))
        except (TypeError, ValueError):
            initial_lines = 200

        path = state.event_log_path

        # 1. Initial snapshot: emit the last N lines from the existing
        #    file so a fresh ``--follow`` shows recent context, not
        #    just new arrivals.
        try:
            initial_size = path.stat().st_size if path.exists() else 0
        except OSError:
            initial_size = 0

        tail, _ = state.tail_event_log(initial_lines)
        # Pin the offset to the size we observed when we read; new
        # lines appended after this point are what the follow loop
        # streams. We don't compute per-line offsets for the snapshot
        # — the value is "everything up to here" and the live phase
        # reports exact byte offsets thereafter.
        offset = initial_size
        for tail_line in tail:
            if not await _emit_stream_line(
                writer, {"line": tail_line, "offset": offset}
            ):
                writer.close()
                return

        # 2. Follow loop: poll the file size; on growth, read the new
        #    bytes from the prior offset and emit one frame per
        #    complete (newline-terminated) line. The trailing
        #    fragment, if any, carries over to the next poll so we
        #    never emit half a line.
        loop = asyncio.get_running_loop()
        last_heartbeat = loop.time()
        carry = b""
        try:
            while True:
                # Honour a clean client close. ``reader.at_eof()`` is
                # the cheapest probe; combined with the drain failure
                # in :func:`_emit_stream_line` it covers both half-
                # close and abrupt reset.
                if reader.at_eof():
                    break
                if self._stopping.is_set():
                    break

                await asyncio.sleep(LOGS_FOLLOW_POLL_S)

                try:
                    size = path.stat().st_size if path.exists() else 0
                except OSError:
                    size = offset  # transient stat failure → no-op

                if size > offset:
                    try:
                        with path.open("rb") as f:
                            f.seek(offset)
                            chunk = f.read(size - offset)
                    except OSError:
                        chunk = b""
                    if chunk:
                        carry += chunk
                        parts = carry.split(b"\n")
                        carry = parts.pop()  # incomplete tail fragment
                        for raw in parts:
                            new_offset = offset + len(raw) + 1
                            line_text = raw.decode("utf-8", errors="replace")
                            if not await _emit_stream_line(
                                writer,
                                {"line": line_text, "offset": new_offset},
                            ):
                                return
                            offset = new_offset
                    # If chunk read empty (file truncated under us)
                    # we silently skip; offset stays where it was.

                # 3. Heartbeats keep the connection live on quiet
                #    logs. Cadence is monotonic-clock-based so a wall-
                #    clock jump doesn't double-emit.
                now = loop.time()
                if now - last_heartbeat >= LOGS_FOLLOW_HEARTBEAT_S:
                    if not await _emit_stream_line(writer, {"heartbeat": now}):
                        return
                    last_heartbeat = now
        except asyncio.CancelledError:
            # Daemon is shutting down. Fall through to send the
            # terminator if we still can.
            pass
        finally:
            # Best-effort terminator. If the client is already gone
            # the write/drain raises — swallow it.
            try:
                writer.write((json.dumps({"done": True}) + "\n").encode("utf-8"))
                await writer.drain()
            except (ConnectionError, OSError, asyncio.CancelledError):
                pass
            try:
                writer.close()
            except OSError:
                pass

    # ── op dispatch ─────────────────────────────────────────────────

    async def _dispatch(self, req: dict[str, Any]) -> dict[str, Any]:
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "pong": True}
        if op == "ls_tasks":
            return self._op_ls_tasks()
        if op == "inspect":
            return self._op_inspect(req.get("swarm_id"))
        if op == "pause":
            return self._op_pause(req.get("swarm_id"))
        if op == "resume":
            return self._op_resume(req.get("swarm_id"))
        if op == "logs":
            return self._op_logs(
                req.get("swarm_id"),
                lines=int(req.get("lines", 200) or 0),
                follow=bool(req.get("follow", False)),
            )
        if op == "web":
            return await self._op_web(int(req.get("port", 8729)))
        if op == "shutdown":
            self._stopping.set()
            return {"ok": True, "shutting_down": True}
        return {"error": f"unknown op: {op}"}

    # ── ls_tasks ────────────────────────────────────────────────────

    def _op_ls_tasks(self) -> dict[str, Any]:
        """Flatten every swarm's TaskRegistry into one list."""
        flat: list[dict[str, Any]] = []
        for name, state in self.swarms.items():
            for task in state.registry.list():
                flat.append(
                    {
                        "task_id": task.task_id,
                        "swarm": name,
                        "status": task.status.value,
                        "prompt": task.prompt,
                        "started_at": task.started_at,
                        "created_at": task.created_at,
                        "model": task.model,
                    }
                )
        return {"ok": True, "tasks": flat}

    # ── inspect ─────────────────────────────────────────────────────

    def _op_inspect(self, swarm_id: str | None) -> dict[str, Any]:
        state = self._lookup(swarm_id)
        if state is None:
            return {"error": f"swarm not installed: {swarm_id}"}
        spec = state.spec
        counts = state.task_counts()
        return {
            "ok": True,
            "swarm": {
                "name": spec.name,
                "version": spec.version,
                "description": spec.description,
                "agents": [
                    {"id": a.id, "role": a.role, "model": a.model, "tools": list(a.tools)}
                    for a in spec.agents
                ],
                "triggers": [
                    {
                        "kind": t.kind,
                        "source": t.source,
                        "target_agent_id": t.target_agent_id,
                    }
                    for t in spec.triggers
                ],
                "edges": [
                    {
                        "from_agent_id": e.from_agent_id,
                        "to_agent_id": e.to_agent_id,
                        "kind": e.kind,
                    }
                    for e in spec.edges
                ],
                "budget": {
                    "max_tokens_per_hour": spec.budget.max_tokens_per_hour,
                    "max_usd_per_day": spec.budget.max_usd_per_day,
                    "max_concurrent_tasks": spec.budget.max_concurrent_tasks,
                },
            },
            "state": {
                "active_tasks": counts.active,
                "completed_tasks": counts.completed,
                "failed_tasks": counts.failed,
                "paused": state.is_paused(),
                "trigger_log_size": state.trigger_log_size(self.waves_root),
            },
        }

    # ── pause / resume ──────────────────────────────────────────────

    def _op_pause(self, swarm_id: str | None) -> dict[str, Any]:
        state = self._lookup(swarm_id)
        if state is None:
            return {"error": f"swarm not installed: {swarm_id}"}
        state.mark_paused()
        return {"ok": True, "paused": True, "swarm": state.spec.name}

    def _op_resume(self, swarm_id: str | None) -> dict[str, Any]:
        state = self._lookup(swarm_id)
        if state is None:
            return {"error": f"swarm not installed: {swarm_id}"}
        state.mark_resumed()
        return {"ok": True, "paused": False, "swarm": state.spec.name}

    # ── logs ────────────────────────────────────────────────────────

    def _op_logs(
        self,
        swarm_id: str | None,
        *,
        lines: int,
        follow: bool,
    ) -> dict[str, Any]:
        state = self._lookup(swarm_id)
        if state is None:
            return {"error": f"swarm not installed: {swarm_id}"}
        # ``follow`` is accepted but the v1 control plane returns a
        # snapshot. Streaming tail needs a framed/streaming RPC; that
        # arrives with the SSE wiring in a follow-up. Document the
        # stub so callers know what they're getting.
        tail, total = state.tail_event_log(lines if lines > 0 else 200)
        return {
            "ok": True,
            "swarm": state.spec.name,
            "lines": tail,
            "total_size_bytes": total,
            "follow_supported": False,
        }

    # ── optional web UI ─────────────────────────────────────────────

    async def _op_web(self, port: int) -> dict[str, Any]:
        """Bring up a tiny read-only JSON server.

        The web endpoint is deliberately minimal: a health probe and
        two list endpoints reading from the same in-memory state the
        RPC ops use. No write paths, no auth — bound to 127.0.0.1
        only because the sole v1 trust model is local-only (ADR-032
        §C "auth deferred — local-only is the v1 trust model").
        """
        if self._web_runner is not None:
            return {"error": "web ui already running"}
        try:
            from aiohttp import web  # noqa: F401
        except ImportError:
            return {"error": "web ui requires aiohttp"}

        from aiohttp import web

        async def health(_request: web.Request) -> web.Response:
            return web.json_response({"ok": True, "swarms": list(self.swarms.keys())})

        async def list_swarms(_request: web.Request) -> web.Response:
            payload = [
                {
                    "name": s.spec.name,
                    "version": s.spec.version,
                    "paused": s.is_paused(),
                    "agents": len(s.spec.agents),
                }
                for s in self.swarms.values()
            ]
            return web.json_response(payload)

        async def list_tasks(request: web.Request) -> web.Response:
            sid = request.match_info["swarm_id"]
            state = self.swarms.get(sid)
            if state is None:
                return web.json_response(
                    {"error": f"swarm not installed: {sid}"}, status=404
                )
            return web.json_response(
                [
                    {
                        "task_id": t.task_id,
                        "status": t.status.value,
                        "prompt": t.prompt,
                        "model": t.model,
                    }
                    for t in state.registry.list()
                ]
            )

        app = web.Application()
        app.router.add_get("/health", health)
        app.router.add_get("/swarms", list_swarms)
        app.router.add_get("/swarms/{swarm_id}/tasks", list_tasks)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="127.0.0.1", port=port)
        try:
            await site.start()
        except OSError as e:
            await runner.cleanup()
            return {"error": f"web ui failed to bind: {e}"}
        self._web_runner = runner
        return {"ok": True, "url": f"http://127.0.0.1:{port}"}

    # ── helpers ─────────────────────────────────────────────────────

    def _lookup(self, swarm_id: str | None) -> HostState | None:
        if not isinstance(swarm_id, str):
            return None
        return self.swarms.get(swarm_id)


def _listener_kind(listener: Any) -> str:
    """Map a listener instance to a stable event-log kind label."""
    cls_name = type(listener).__name__
    if cls_name == "WebhookListener":
        return "webhook"
    if cls_name == "FileWatchListener":
        return "filewatch"
    if cls_name == "CronListener":
        return "cron"
    if cls_name == "MCPPushListener":
        return "mcp_push"
    if cls_name == "ManualSeam":
        return "manual"
    return cls_name.lower()


def run_foreground(waves_root: Path, *, swarm_name: str | None = None) -> int:
    """Run the host in the foreground — used by ``duh wave start --foreground``."""
    host = _Host(waves_root, swarm_name)
    return asyncio.run(host.run())


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: python -m duh.duhwave.cli.daemon <waves_root> [<swarm>]\n")
        return 2
    waves_root = Path(argv[1])
    swarm_name = argv[2] if len(argv) > 2 else None
    waves_root.mkdir(parents=True, exist_ok=True)
    return run_foreground(waves_root, swarm_name=swarm_name)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
