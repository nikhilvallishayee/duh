"""Trigger schema + append-only log — ADR-031 §B.1, B.5.

Triggers are the normalised event type. Every listener (webhook,
filewatch, cron, MCP push, manual seam) produces :class:`Trigger`
records and appends them to a single :class:`TriggerLog`. The
dispatcher consumes from that log; subscriptions in the topology
match on ``(kind, source)``.

Public exports:

- :class:`TriggerKind` — the five recognised event sources.
- :class:`Trigger`     — the normalised record.
- :class:`TriggerLog`  — append-only JSONL store with at-least-once
                          replay semantics.
- :data:`MAX_PAYLOAD_BYTES` / :class:`TriggerOversizeError` /
  :func:`validate` — payload-size enforcement.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class TriggerKind(str, Enum):
    """The five sources a :class:`Trigger` may originate from."""

    WEBHOOK = "webhook"
    FILEWATCH = "filewatch"
    CRON = "cron"
    MCP_PUSH = "mcp_push"
    MANUAL = "manual"


# Hard cap on inline trigger payload — larger bodies must be addressed
# by URL/path via the ``raw_ref`` field.
MAX_PAYLOAD_BYTES = 64 * 1024


@dataclass(slots=True)
class Trigger:
    """Normalised external event.

    Subscriptions in the topology are matched against
    ``(kind, source)`` to spawn Tasks.
    """

    kind: TriggerKind
    source: str  # e.g. "github:nikhil/repo" or "/path/to/dir"
    payload: dict[str, object] = field(default_factory=dict)
    raw_ref: str | None = None  # URL/path for oversize bodies
    received_at: float = field(default_factory=time.time)
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-safe dict (Enum collapsed to its string value)."""
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> "Trigger":
        """Deserialise from a dict produced by :meth:`to_dict`."""
        return cls(
            kind=TriggerKind(d["kind"]),
            source=str(d["source"]),
            payload=dict(d.get("payload", {})),  # type: ignore[arg-type]
            raw_ref=d.get("raw_ref"),  # type: ignore[arg-type]
            received_at=float(d.get("received_at", time.time())),  # type: ignore[arg-type]
            correlation_id=str(d.get("correlation_id", uuid.uuid4().hex)),
        )


class TriggerOversizeError(ValueError):
    """Inline payload exceeded :data:`MAX_PAYLOAD_BYTES`."""


def validate(trigger: Trigger) -> None:
    """Validate a trigger before it enters the log."""
    encoded = json.dumps(trigger.payload).encode("utf-8")
    if len(encoded) > MAX_PAYLOAD_BYTES and trigger.raw_ref is None:
        raise TriggerOversizeError(
            f"payload {len(encoded)}B > {MAX_PAYLOAD_BYTES}B limit and no raw_ref"
        )


class TriggerLog:
    """Append-only JSONL store for triggers — ADR-031 §B.5.

    At-least-once delivery: every received trigger is recorded before
    spawn-routing fires, so a host crash mid-spawn replays cleanly on
    restart.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, trigger: Trigger) -> None:
        """Validate and append one trigger as a JSONL line. fsync-on-write
        is left to the OS — the host accepts a small at-most-one trigger
        loss on a hard kernel crash; the rare-event tradeoff."""
        validate(trigger)
        line = json.dumps(trigger.to_dict(), sort_keys=True) + "\n"
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)

    def replay(self) -> list[Trigger]:
        """Read every trigger ever recorded. Use on host restart."""
        if not self._path.exists():
            return []
        out: list[Trigger] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(Trigger.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return out
