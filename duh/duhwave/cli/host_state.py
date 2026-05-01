"""Per-swarm runtime state for the duhwave host — ADR-032 §C.

Each installed bundle on disk lives at::

    <waves_root>/<name>/<version>/

Inside that directory the host needs a writable ``state/`` subtree to
hold per-swarm runtime artefacts: the persistent task registry's
``tasks/`` directory (ADR-030), an event log read by ``duh wave logs``,
and a single-byte ``paused.flag`` file used by ``pause``/``resume``.

:class:`HostState` is the small wrapper that owns those paths and the
:class:`TaskRegistry` instance. The host's RPC dispatcher
(:mod:`duh.duhwave.cli.daemon`) instantiates one ``HostState`` per
installed swarm at startup and uses them to answer ``inspect``,
``pause``, ``resume``, ``logs``, and ``ls_tasks`` requests.

The class is deliberately small. It does not own any asyncio state, no
sockets, no subprocess handles — those live one layer up in ``_Host``.
What it does own is the on-disk layout: paths, file existence checks,
the registry, and a few helpers (``mark_paused``, ``mark_resumed``,
``append_event``, ``tail_event_log``) that keep the daemon's
``_dispatch`` method short.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from duh.duhwave.spec.parser import SwarmSpec
from duh.duhwave.task.registry import TaskRegistry, TaskStatus


# ``state/event.log`` line format. One line per event, plain-text:
#   "<unix_ts>\t<event_kind>\t<message>\n"
# Keeps the file human-tail-able with `tail -f`. ``duh wave logs``
# returns the last N raw lines; ``--follow`` streams new lines as
# they're appended via the daemon's streaming RPC shape.
EVENT_LOG_FILENAME = "event.log"
PAUSED_FLAG_FILENAME = "paused.flag"
TASKS_SUBDIR = "tasks"


@dataclass(slots=True)
class StateCounts:
    """Snapshot of task statuses for ``inspect``."""

    active: int
    completed: int
    failed: int


class HostState:
    """One-swarm view over its on-disk state directory.

    Args:
        install_dir: ``<waves_root>/<name>/<version>/`` — the bundle's
            installed root. The constructor creates ``state/`` under
            it on demand.
        spec: parsed ``swarm.toml`` for the bundle.
    """

    def __init__(self, install_dir: Path, spec: SwarmSpec) -> None:
        self.install_dir = Path(install_dir)
        self.spec = spec
        self.state_dir = self.install_dir / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.registry = TaskRegistry(
            session_dir=self.state_dir,
            session_id=f"{spec.name}-{spec.version}",
        )
        # Restore persisted tasks (no-op for a fresh install, useful
        # after a daemon restart).
        self.registry.restore_from_disk()
        # Seed the event log with a startup line so ``duh wave logs``
        # has something to show even before any triggers fire.
        self._ensure_event_log()

    # ── path accessors ──────────────────────────────────────────────

    @property
    def event_log_path(self) -> Path:
        return self.state_dir / EVENT_LOG_FILENAME

    @property
    def paused_flag_path(self) -> Path:
        return self.state_dir / PAUSED_FLAG_FILENAME

    # ── pause / resume ──────────────────────────────────────────────

    def is_paused(self) -> bool:
        return self.paused_flag_path.exists()

    def mark_paused(self) -> None:
        """Idempotent: creates the flag if absent."""
        if not self.paused_flag_path.exists():
            self.paused_flag_path.touch()
            self.append_event("swarm.paused", f"{self.spec.name}")

    def mark_resumed(self) -> None:
        """Idempotent: unlinks the flag if present."""
        if self.paused_flag_path.exists():
            self.paused_flag_path.unlink()
            self.append_event("swarm.resumed", f"{self.spec.name}")

    # ── event log ───────────────────────────────────────────────────

    def append_event(self, kind: str, message: str) -> None:
        """Append one line to ``state/event.log``.

        Plain-text, tab-delimited; ``duh wave logs`` returns these
        lines verbatim. Errors are swallowed — logging failures must
        not crash the host.
        """
        try:
            line = f"{time.time():.6f}\t{kind}\t{message}\n"
            with self.event_log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def tail_event_log(self, lines: int) -> tuple[list[str], int]:
        """Return the last ``lines`` lines of the event log + total bytes.

        Reads the file fully and slices via :class:`collections.deque`.
        Event logs are bounded by trigger-rate × runtime; even at
        thousands of lines the cost is negligible. The streaming
        ``--follow`` path lives in the daemon's RPC dispatcher (it
        builds the initial snapshot from this method, then tails the
        file for new lines).
        """
        path = self.event_log_path
        if not path.exists():
            return [], 0
        try:
            stat = path.stat()
        except OSError:
            return [], 0
        # ``deque(..., maxlen=N)`` is the cleanest "tail N lines" idiom
        # in stdlib without slurping the whole file into memory.
        with path.open("r", encoding="utf-8") as f:
            tail = deque(f, maxlen=max(0, int(lines)))
        return [line.rstrip("\n") for line in tail], int(stat.st_size)

    # ── task-state snapshot ─────────────────────────────────────────

    def task_counts(self) -> StateCounts:
        """Counts of tasks by lifecycle group, for ``inspect``."""
        active = completed = failed = 0
        for t in self.registry.list():
            s = t.status
            if s == TaskStatus.COMPLETED:
                completed += 1
            elif s in (TaskStatus.FAILED, TaskStatus.KILLED):
                failed += 1
            elif s in (TaskStatus.PENDING, TaskStatus.RUNNING):
                active += 1
        return StateCounts(active=active, completed=completed, failed=failed)

    def trigger_log_size(self, waves_root: Path) -> int:
        """Bytes of ``<waves_root>/triggers.jsonl``, or 0 if absent.

        The trigger log is currently shared across swarms (one host =
        one file). Per-swarm logs are a follow-up; for now we return
        the global size so ``inspect`` has a non-zero number to show.
        """
        path = Path(waves_root) / "triggers.jsonl"
        try:
            return int(path.stat().st_size) if path.exists() else 0
        except OSError:
            return 0

    # ── helpers ─────────────────────────────────────────────────────

    def _ensure_event_log(self) -> None:
        """Create the event log if absent; do not write a header (lets
        ``logs`` of a fresh swarm return ``[]`` cleanly)."""
        try:
            self.event_log_path.touch(exist_ok=True)
        except OSError:
            pass


__all__ = [
    "EVENT_LOG_FILENAME",
    "PAUSED_FLAG_FILENAME",
    "TASKS_SUBDIR",
    "HostState",
    "StateCounts",
]
