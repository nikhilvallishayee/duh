"""Cron ingress listener — ADR-031 §B.2.

Each ``(cron_expr, source_label)`` tuple gets its own task that:

1. Computes the next fire time via :class:`croniter.croniter`.
2. Sleeps until that wall-clock instant (NOT interval arithmetic — this
   means a paused host does not fire a backlog of skipped ticks).
3. Emits one :class:`Trigger`.
4. Schedules the *next* fire from the just-fired instant.

If a previous fire has not finished its log append by the time the next
fire is due (very unusual — log appends are sub-millisecond), the new
fire is skipped and a warning is logged. Overrun behaviour is by design:
we'd rather drop a tick than queue them up.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from duh.duhwave.ingress.triggers import Trigger, TriggerKind, TriggerLog

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Schedule:
    """Per-entry runtime state."""

    expr: str
    label: str
    task: asyncio.Task[None]
    fire_in_progress: bool = False
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)


class CronListener:
    """Cron-driven trigger emitter.

    Parameters
    ----------
    log:
        Append-only trigger log.
    entries:
        ``[(cron_expr, source_label), ...]``. The expression is a
        standard 5-field crontab spec. The label is the trigger
        ``source`` so topologies can subscribe by label glob.
    """

    def __init__(
        self,
        log: TriggerLog,
        entries: list[tuple[str, str]],
    ) -> None:
        self._log = log
        self._entries: list[tuple[str, str]] = list(entries)
        self._schedules: list[_Schedule] = []
        self._running = False

    async def start(self) -> None:
        """Validate every expression, then spawn one task per entry."""
        if self._running:
            return

        # Lazy import — keeps croniter optional at module-import time.
        from croniter import croniter

        # Validate all expressions up front so a bad spec fails fast.
        for expr, label in self._entries:
            if not croniter.is_valid(expr):
                raise ValueError(f"invalid cron expression for {label!r}: {expr!r}")

        self._running = True
        for expr, label in self._entries:
            stop_event = asyncio.Event()
            schedule = _Schedule(
                expr=expr,
                label=label,
                task=asyncio.create_task(
                    self._run_one(expr, label, stop_event),
                    name=f"cron:{label}",
                ),
                stop_event=stop_event,
            )
            self._schedules.append(schedule)
        logger.info("CronListener scheduled %d entrie(s)", len(self._entries))

    async def stop(self) -> None:
        """Cancel every scheduled task and wait for completion."""
        if not self._running:
            return
        self._running = False
        for s in self._schedules:
            s.stop_event.set()
            s.task.cancel()
        for s in self._schedules:
            try:
                await s.task
            except (asyncio.CancelledError, Exception):
                pass
        self._schedules.clear()
        logger.info("CronListener stopped")

    async def _run_one(
        self,
        expr: str,
        label: str,
        stop_event: asyncio.Event,
    ) -> None:
        """Sleep-fire-reschedule loop for one cron entry."""
        from croniter import croniter

        # Anchor on wall clock so absolute fire times are stable across drift.
        anchor = time.time()
        itr = croniter(expr, anchor)

        try:
            while not stop_event.is_set():
                next_fire = itr.get_next(float)
                delay = next_fire - time.time()
                if delay > 0:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=delay)
                        # If wait_for did not raise, stop_event was set.
                        return
                    except asyncio.TimeoutError:
                        pass  # fall through to fire

                # Find the schedule entry to track overlap.
                schedule = self._find_schedule(label)
                if schedule is not None and schedule.fire_in_progress:
                    logger.warning(
                        "cron fire overlap for %r — skipping this tick",
                        label,
                    )
                    continue

                if schedule is not None:
                    schedule.fire_in_progress = True
                try:
                    trigger = Trigger(
                        kind=TriggerKind.CRON,
                        source=label,
                        payload={"expr": expr, "fired_at": next_fire},
                    )
                    try:
                        self._log.append(trigger)
                    except Exception:  # pragma: no cover
                        logger.exception(
                            "failed to append cron trigger for %r", label
                        )
                finally:
                    if schedule is not None:
                        schedule.fire_in_progress = False
        except asyncio.CancelledError:  # pragma: no cover
            return

    def _find_schedule(self, label: str) -> _Schedule | None:
        for s in self._schedules:
            if s.label == label:
                return s
        return None
