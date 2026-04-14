"""ScannerRegistry, Runner, FindingStore, ScannerResult."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Iterable, Literal

from duh.security.config import ScannerConfig, SecurityPolicy
from duh.security.finding import Finding
from duh.security.scanners import Scanner

ENTRY_POINT_GROUP = "duh.security.scanners"


@dataclass(frozen=True, slots=True)
class ScannerResult:
    scanner: str
    status: Literal["ok", "error", "timeout", "skipped"]
    findings: tuple[Finding, ...]
    reason: str
    duration_ms: int


class ScannerRegistry:
    """Holds registered scanners; supports entry-point discovery."""

    def __init__(self) -> None:
        self._scanners: dict[str, Scanner] = {}

    def register(self, scanner: Scanner) -> None:
        if scanner.name in self._scanners:
            raise ValueError(f"scanner {scanner.name!r} already registered")
        self._scanners[scanner.name] = scanner

    def get(self, name: str) -> Scanner:
        return self._scanners[name]

    def names(self) -> list[str]:
        return list(self._scanners.keys())

    def load_entry_points(self) -> None:
        try:
            eps = importlib_metadata.entry_points(group=ENTRY_POINT_GROUP)
        except Exception:
            return
        for ep in eps:
            try:
                cls = ep.load()
                instance = cls()
            except Exception:
                continue
            if instance.name not in self._scanners:
                self._scanners[instance.name] = instance


class Runner:
    """Runs scanners with isolation, timeout, and on_scanner_error handling."""

    def __init__(
        self,
        *,
        registry: ScannerRegistry,
        policy: SecurityPolicy,
        per_scanner_timeout_s: float = 60.0,
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._timeout = per_scanner_timeout_s

    async def run(
        self,
        target: Path,
        *,
        scanners: Iterable[str],
        changed_files: list[Path] | None = None,
    ) -> list[ScannerResult]:
        results: list[ScannerResult] = []
        for name in scanners:
            scanner = self._registry.get(name)
            cfg = self._policy.scanners.get(name, ScannerConfig())
            result = await self._run_one(scanner, target, cfg, changed_files)
            results.append(result)
            if result.status == "error" and self._policy.on_scanner_error == "fail":
                raise RuntimeError(f"scanner {name} failed: {result.reason}")
        return results

    async def _run_one(
        self,
        scanner: Scanner,
        target: Path,
        cfg: ScannerConfig,
        changed_files: list[Path] | None,
    ) -> ScannerResult:
        if not scanner.available():
            return ScannerResult(
                scanner=scanner.name,
                status="skipped",
                findings=(),
                reason=f"{scanner.name} not installed",
                duration_ms=0,
            )
        t0 = time.monotonic()
        try:
            findings = await asyncio.wait_for(
                scanner.scan(target, cfg, changed_files=changed_files),
                timeout=self._timeout,
            )
            return ScannerResult(
                scanner=scanner.name,
                status="ok",
                findings=tuple(findings),
                reason="",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except asyncio.TimeoutError:
            return ScannerResult(
                scanner=scanner.name,
                status="timeout",
                findings=(),
                reason=f"exceeded {self._timeout}s",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as exc:
            return ScannerResult(
                scanner=scanner.name,
                status="error",
                findings=(),
                reason=repr(exc),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )


class FindingStore:
    """Append-only per-session cache of findings, keyed by fingerprint."""

    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._by_fp: dict[str, Finding] = {}

    def add(self, finding: Finding) -> None:
        self._by_fp[finding.fingerprint] = finding

    def extend(self, findings: Iterable[Finding]) -> None:
        for f in findings:
            self.add(f)

    def all(self) -> list[Finding]:
        return list(self._by_fp.values())

    def active(self, *, scope: Path | None = None) -> list[Finding]:
        return self.all()

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "findings": [f.to_json() for f in self.all()]}
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "FindingStore":
        store = cls(path=path)
        if not path.exists():
            return store
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw in data.get("findings", []):
            store.add(Finding.from_json(raw))
        return store

    def snapshot_for_session(self, session_id: str) -> None:
        return None

    def since_session_start(self, session_id: str) -> list[Finding]:
        return self.all()
