"""Filesystem ingress listener — ADR-031 §B.2.

Wraps :func:`watchfiles.awatch`. Each top-level path passed to the
constructor gets its own watcher task so unrelated trees do not cross-
contaminate each other's debounce windows.

A single debounce batch (default 500 ms via watchfiles' ``step``) emits
one :class:`Trigger` whose ``payload["changes"]`` lists ``{type, path}``
entries. The change ``type`` is the lower-case watchfiles ``Change``
name (``added`` / ``modified`` / ``deleted``).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from duh.duhwave.ingress.triggers import Trigger, TriggerKind, TriggerLog

logger = logging.getLogger(__name__)


_CHANGE_NAMES: dict[int, str] = {
    1: "added",
    2: "modified",
    3: "deleted",
}


@dataclass(slots=True)
class _Watch:
    """Per-path bookkeeping for a single ``awatch`` task."""

    path: Path
    task: asyncio.Task[None]
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)


class FileWatchListener:
    """Debounced filesystem watcher.

    Parameters
    ----------
    log:
        Append-only trigger log.
    paths:
        Top-level filesystem paths to watch. Each gets its own watcher.
    debounce_ms:
        Coalescing window. Forwards to ``watchfiles.awatch(step=…)``.
    """

    def __init__(
        self,
        log: TriggerLog,
        paths: list[Path],
        debounce_ms: int = 500,
    ) -> None:
        self._log = log
        self._paths: list[Path] = [Path(p) for p in paths]
        self._debounce_ms = debounce_ms
        self._watches: list[_Watch] = []
        self._running = False

    async def start(self) -> None:
        """Spawn one watcher task per top-level path. Idempotent."""
        if self._running:
            return

        # Lazy import — module import must not require watchfiles.
        from watchfiles import awatch  # noqa: F401  # imported for availability check

        self._running = True
        for path in self._paths:
            stop_event = asyncio.Event()
            task = asyncio.create_task(
                self._watch_one(path, stop_event),
                name=f"filewatch:{path}",
            )
            self._watches.append(_Watch(path=path, task=task, stop_event=stop_event))
        logger.info("FileWatchListener watching %d path(s)", len(self._paths))

    async def stop(self) -> None:
        """Signal every watcher to exit and await completion. Idempotent."""
        if not self._running:
            return
        self._running = False
        for w in self._watches:
            w.stop_event.set()
        for w in self._watches:
            try:
                await asyncio.wait_for(w.task, timeout=2.0)
            except asyncio.TimeoutError:
                w.task.cancel()
                try:
                    await w.task
                except (asyncio.CancelledError, Exception):
                    pass
        self._watches.clear()
        logger.info("FileWatchListener stopped")

    async def _watch_one(self, path: Path, stop_event: asyncio.Event) -> None:
        """Drive one ``awatch`` loop until ``stop_event`` fires."""
        from watchfiles import awatch

        try:
            async for batch in awatch(
                str(path),
                step=self._debounce_ms,
                stop_event=stop_event,
            ):
                changes = [
                    {
                        "type": _change_name(change),
                        "path": str(change_path),
                    }
                    for change, change_path in batch
                ]
                if not changes:
                    continue
                trigger = Trigger(
                    kind=TriggerKind.FILEWATCH,
                    source=str(path),
                    payload={"changes": changes},
                )
                try:
                    self._log.append(trigger)
                except Exception:  # pragma: no cover
                    logger.exception(
                        "failed to append filewatch trigger for %s", path
                    )
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:  # pragma: no cover
            logger.exception("filewatch loop crashed for %s", path)


def _change_name(change: object) -> str:
    """Map a ``watchfiles.Change`` enum to ``added|modified|deleted``."""
    # ``Change`` is an IntEnum in watchfiles; fall back to ``.name``.
    try:
        as_int = int(change)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return getattr(change, "name", "modified").lower()
    return _CHANGE_NAMES.get(as_int, "modified")
