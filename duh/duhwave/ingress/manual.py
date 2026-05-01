"""Manual ingress seam — ADR-031 §B.2.

A Unix-domain-socket server at ``<host_dir>/manual.sock`` accepting
newline-delimited JSON. Each well-formed line becomes one
:class:`Trigger`::

    {"source": "test:rapid-refresh", "payload": {"foo": "bar"}}

Used by integration tests and by power users who want to fire a
trigger from a shell script::

    echo '{"source":"deploy:rolled","payload":{}}' | nc -U manual.sock

Lines that fail JSON parsing or lack a ``source`` field are logged and
dropped — the seam errs on the side of staying alive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from duh.duhwave.ingress.triggers import Trigger, TriggerKind, TriggerLog

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ServerState:
    server: asyncio.AbstractServer
    socket_path: Path


class ManualSeam:
    """Unix-socket listener for manually fired triggers.

    Parameters
    ----------
    log:
        Append-only trigger log.
    host_dir:
        Directory to place ``manual.sock`` in. Created if missing.
    """

    def __init__(self, log: TriggerLog, host_dir: Path) -> None:
        self._log = log
        self._host_dir = Path(host_dir)
        self._socket_path = self._host_dir / "manual.sock"
        self._state: _ServerState | None = None
        self._client_tasks: set[asyncio.Task[None]] = set()

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    async def start(self) -> None:
        """Bind the Unix socket. Idempotent."""
        if self._state is not None:
            return

        self._host_dir.mkdir(parents=True, exist_ok=True)
        # Stale socket from a prior crash would prevent bind.
        if self._socket_path.exists():
            try:
                self._socket_path.unlink()
            except OSError:  # pragma: no cover
                logger.exception("could not unlink stale socket %s", self._socket_path)

        server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._socket_path),
        )
        # Restrict to owner — the socket carries unauthenticated triggers.
        try:
            os.chmod(self._socket_path, 0o600)
        except OSError:  # pragma: no cover
            logger.warning("could not chmod manual socket")

        self._state = _ServerState(server=server, socket_path=self._socket_path)
        logger.info("ManualSeam listening on %s", self._socket_path)

    async def stop(self) -> None:
        """Close the server and unlink the socket. Idempotent."""
        state = self._state
        if state is None:
            return
        self._state = None

        # Cancel any in-flight client handlers.
        for task in list(self._client_tasks):
            task.cancel()
        for task in list(self._client_tasks):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._client_tasks.clear()

        state.server.close()
        try:
            await state.server.wait_closed()
        except Exception:  # pragma: no cover
            logger.exception("error closing manual seam server")

        try:
            if state.socket_path.exists():
                state.socket_path.unlink()
        except OSError:  # pragma: no cover
            logger.exception("error unlinking manual socket")
        logger.info("ManualSeam stopped")

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Read newline-delimited JSON until the peer closes."""
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                self._process_line(line)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # pragma: no cover
            logger.exception("manual seam client crashed")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # pragma: no cover
                pass
            if task is not None:
                self._client_tasks.discard(task)

    def _process_line(self, raw: bytes) -> None:
        """Parse one line, validate, append a Trigger. Errors are logged."""
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            obj: Any = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("manual seam: dropping malformed JSON line")
            return
        if not isinstance(obj, dict):
            logger.warning("manual seam: dropping non-object line")
            return

        source = obj.get("source")
        if not isinstance(source, str) or not source:
            logger.warning("manual seam: dropping line missing 'source'")
            return

        raw_payload = obj.get("payload", {})
        payload: dict[str, object]
        if isinstance(raw_payload, dict):
            payload = dict(raw_payload)
        else:
            payload = {"value": raw_payload}

        trigger = Trigger(
            kind=TriggerKind.MANUAL,
            source=source,
            payload=payload,
        )
        try:
            self._log.append(trigger)
        except Exception:  # pragma: no cover
            logger.exception("failed to append manual trigger")
