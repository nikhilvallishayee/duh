"""ExceptionStore — alias-expanded, scope-narrowed, expiring exceptions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from duh.security.finding import Finding


@dataclass(frozen=True, slots=True)
class Exception:
    id: str
    aliases: tuple[str, ...]
    scope: dict[str, Any]
    reason: str
    added_by: str
    added_at: datetime
    expires_at: datetime
    ticket: str | None
    permanent: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "aliases": list(self.aliases),
            "scope": dict(self.scope),
            "reason": self.reason,
            "added_by": self.added_by,
            "added_at": self.added_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "ticket": self.ticket,
            "permanent": self.permanent,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Exception":
        return cls(
            id=data["id"],
            aliases=tuple(data.get("aliases", [])),
            scope=dict(data.get("scope", {})),
            reason=data["reason"],
            added_by=data["added_by"],
            added_at=datetime.fromisoformat(data["added_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            ticket=data.get("ticket"),
            permanent=bool(data.get("permanent", False)),
        )


@dataclass(frozen=True, slots=True)
class AuditReport:
    expired: tuple[str, ...]
    unused: tuple[str, ...]
    expiring_soon: tuple[str, ...]


class ExceptionStore:
    _MAX_DAYS_DEFAULT = 90
    _MAX_DAYS_LONG_TERM = 365

    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._exceptions: dict[str, Exception] = {}

    def add(
        self,
        *,
        id: str,
        reason: str,
        expires_at: datetime,
        added_by: str,
        added_at: datetime,
        aliases: tuple[str, ...] = (),
        scope: dict[str, Any] | None = None,
        ticket: str | None = None,
        permanent: bool = False,
        long_term: bool = False,
    ) -> None:
        if not reason.strip():
            raise ValueError("exception requires a non-empty reason")
        if expires_at <= added_at:
            raise ValueError("expires_at is in the past")
        cap = self._MAX_DAYS_LONG_TERM if long_term else self._MAX_DAYS_DEFAULT
        if (expires_at - added_at) > timedelta(days=cap):
            raise ValueError(f"expires_at exceeds {cap}-day cap")
        self._exceptions[id] = Exception(
            id=id,
            aliases=tuple(aliases),
            scope=dict(scope or {}),
            reason=reason,
            added_by=added_by,
            added_at=added_at,
            expires_at=expires_at,
            ticket=ticket,
            permanent=permanent,
        )

    def remove(self, id: str) -> bool:
        return self._exceptions.pop(id, None) is not None

    def renew(self, id: str, new_expires_at: datetime) -> None:
        existing = self._exceptions[id]
        self._exceptions[id] = Exception(
            id=existing.id,
            aliases=existing.aliases,
            scope=existing.scope,
            reason=existing.reason,
            added_by=existing.added_by,
            added_at=existing.added_at,
            expires_at=new_expires_at,
            ticket=existing.ticket,
            permanent=existing.permanent,
        )

    def get(self, id: str) -> Exception:
        return self._exceptions[id]

    def all(self) -> list[Exception]:
        return list(self._exceptions.values())

    def covers(self, finding: Finding, *, at: datetime) -> bool:
        candidate_ids = {finding.id, *finding.aliases}
        for exc in self._exceptions.values():
            ids = {exc.id, *exc.aliases}
            if not (candidate_ids & ids):
                continue
            if not exc.permanent and exc.expires_at <= at:
                continue
            if not self._scope_matches(exc.scope, finding):
                continue
            return True
        return False

    @staticmethod
    def _scope_matches(scope: dict[str, Any], finding: Finding) -> bool:
        pkg = scope.get("package")
        if pkg is not None and pkg != finding.package:
            return False
        return True

    def audit(self, *, at: datetime) -> AuditReport:
        expired: list[str] = []
        expiring: list[str] = []
        for exc in self._exceptions.values():
            if exc.permanent:
                continue
            if exc.expires_at <= at:
                expired.append(exc.id)
            elif (exc.expires_at - at) <= timedelta(days=7):
                expiring.append(exc.id)
        return AuditReport(
            expired=tuple(expired),
            unused=(),
            expiring_soon=tuple(expiring),
        )

    def expiring_within(self, *, days: int, at: datetime | None = None) -> list[Exception]:
        now = at or datetime.now(tz=timezone.utc)
        out = []
        for exc in self._exceptions.values():
            if exc.permanent:
                continue
            delta = exc.expires_at - now
            if timedelta(0) < delta <= timedelta(days=days):
                out.append(exc)
        return out

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "exceptions": [e.to_json() for e in self._exceptions.values()],
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ExceptionStore":
        store = cls(path=path)
        if not path.exists():
            return store
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw in data.get("exceptions", []):
            exc = Exception.from_json(raw)
            store._exceptions[exc.id] = exc
        return store
