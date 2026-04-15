"""Finding, Severity, Location — the shared data model for the security module."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """Finding severity, ordered low → high."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {
            "info": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
            "critical": 4,
        }[self.value]


_SARIF_LEVEL = {
    Severity.INFO: "note",
    Severity.LOW: "note",
    Severity.MEDIUM: "warning",
    Severity.HIGH: "error",
    Severity.CRITICAL: "error",
}


@dataclass(frozen=True, slots=True)
class Location:
    file: str
    line_start: int
    line_end: int
    snippet: str

    def to_json(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "snippet": self.snippet,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Location":
        return cls(
            file=data["file"],
            line_start=int(data["line_start"]),
            line_end=int(data["line_end"]),
            snippet=data["snippet"],
        )


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    aliases: tuple[str, ...]
    scanner: str
    severity: Severity
    message: str
    description: str
    location: Location
    package: str | None
    version: str | None
    fixed_in: str | None
    cwe: tuple[int, ...]
    metadata: dict[str, Any]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        id: str,
        aliases: tuple[str, ...],
        scanner: str,
        severity: Severity,
        message: str,
        description: str,
        location: Location,
        package: str | None = None,
        version: str | None = None,
        fixed_in: str | None = None,
        cwe: tuple[int, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> "Finding":
        fp_source = f"{id}|{location.file}|{location.line_start}|{scanner}".encode("utf-8")
        fingerprint = hashlib.sha256(fp_source).hexdigest()
        return cls(
            id=id,
            aliases=tuple(aliases),
            scanner=scanner,
            severity=severity,
            message=message,
            description=description,
            location=location,
            package=package,
            version=version,
            fixed_in=fixed_in,
            cwe=tuple(cwe),
            metadata=dict(metadata or {}),
            fingerprint=fingerprint,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "aliases": list(self.aliases),
            "scanner": self.scanner,
            "severity": self.severity.value,
            "message": self.message,
            "description": self.description,
            "location": self.location.to_json(),
            "package": self.package,
            "version": self.version,
            "fixed_in": self.fixed_in,
            "cwe": list(self.cwe),
            "metadata": dict(self.metadata),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Finding":
        return cls(
            id=data["id"],
            aliases=tuple(data.get("aliases", [])),
            scanner=data["scanner"],
            severity=Severity(data["severity"]),
            message=data["message"],
            description=data["description"],
            location=Location.from_json(data["location"]),
            package=data.get("package"),
            version=data.get("version"),
            fixed_in=data.get("fixed_in"),
            cwe=tuple(data.get("cwe", [])),
            metadata=dict(data.get("metadata", {})),
            fingerprint=data["fingerprint"],
        )

    def to_sarif(self) -> dict[str, Any]:
        return {
            "ruleId": self.id,
            "level": _SARIF_LEVEL[self.severity],
            "message": {"text": self.message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": self.location.file},
                        "region": {
                            "startLine": max(1, self.location.line_start),
                            "endLine": max(1, self.location.line_end),
                            "snippet": {"text": self.location.snippet},
                        },
                    }
                }
            ],
            "partialFingerprints": {"primary": self.fingerprint},
        }
