"""Tests for ScannerRegistry, Runner, FindingStore."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from duh.security.config import ScannerConfig, SecurityPolicy
from duh.security.engine import (
    FindingStore,
    Runner,
    ScannerRegistry,
    ScannerResult,
)
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import InProcessScanner, Tier


class _OkScanner(InProcessScanner):
    name = "ok"
    tier: Tier = "minimal"
    _module_name = "json"

    async def _scan_impl(self, target, cfg, *, changed_files):
        return [
            Finding.create(
                id="OK-1",
                aliases=(),
                scanner=self.name,
                severity=Severity.MEDIUM,
                message="ok finding",
                description="",
                location=Location(file="a.py", line_start=1, line_end=1, snippet=""),
            )
        ]


class _CrashScanner(InProcessScanner):
    name = "crash"
    tier: Tier = "minimal"
    _module_name = "json"

    async def _scan_impl(self, target, cfg, *, changed_files):
        raise RuntimeError("boom")


class _SlowScanner(InProcessScanner):
    name = "slow"
    tier: Tier = "minimal"
    _module_name = "json"

    async def _scan_impl(self, target, cfg, *, changed_files):
        await asyncio.sleep(5.0)
        return []


class _UnavailableScanner(InProcessScanner):
    name = "nope"
    tier: Tier = "extended"
    _module_name = "definitely_not_a_real_module_xyz"

    async def _scan_impl(self, target, cfg, *, changed_files):
        return []


def test_scanner_registry_register_and_get() -> None:
    reg = ScannerRegistry()
    reg.register(_OkScanner())
    assert "ok" in reg.names()
    assert reg.get("ok").name == "ok"


def test_scanner_registry_duplicate_raises() -> None:
    reg = ScannerRegistry()
    reg.register(_OkScanner())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_OkScanner())


def test_runner_ok_scanner_returns_ok_result() -> None:
    reg = ScannerRegistry()
    reg.register(_OkScanner())
    runner = Runner(registry=reg, policy=SecurityPolicy())
    results = asyncio.run(runner.run(Path("."), scanners=["ok"]))
    assert len(results) == 1
    assert results[0].status == "ok"
    assert len(results[0].findings) == 1


def test_runner_crash_scanner_isolated() -> None:
    reg = ScannerRegistry()
    reg.register(_OkScanner())
    reg.register(_CrashScanner())
    runner = Runner(registry=reg, policy=SecurityPolicy())
    results = asyncio.run(runner.run(Path("."), scanners=["ok", "crash"]))
    by_name = {r.scanner: r for r in results}
    assert by_name["ok"].status == "ok"
    assert by_name["crash"].status == "error"
    assert "boom" in by_name["crash"].reason


def test_runner_timeout_scanner() -> None:
    reg = ScannerRegistry()
    reg.register(_SlowScanner())
    runner = Runner(registry=reg, policy=SecurityPolicy(), per_scanner_timeout_s=0.1)
    results = asyncio.run(runner.run(Path("."), scanners=["slow"]))
    assert results[0].status == "timeout"


def test_runner_unavailable_scanner_skipped() -> None:
    reg = ScannerRegistry()
    reg.register(_UnavailableScanner())
    runner = Runner(registry=reg, policy=SecurityPolicy())
    results = asyncio.run(runner.run(Path("."), scanners=["nope"]))
    assert results[0].status == "skipped"


def test_runner_on_scanner_error_fail_raises() -> None:
    reg = ScannerRegistry()
    reg.register(_CrashScanner())
    runner = Runner(registry=reg, policy=SecurityPolicy(mode="paranoid"))
    with pytest.raises(RuntimeError):
        asyncio.run(runner.run(Path("."), scanners=["crash"]))


def test_finding_store_persists_and_reloads(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    store = FindingStore(path=cache)
    f = Finding.create(
        id="X-1",
        aliases=(),
        scanner="ok",
        severity=Severity.HIGH,
        message="m",
        description="",
        location=Location(file="a.py", line_start=1, line_end=1, snippet=""),
    )
    store.add(f)
    store.save()

    reloaded = FindingStore.load(cache)
    assert len(reloaded.all()) == 1
    assert reloaded.all()[0].id == "X-1"


def test_finding_store_deduplicates_by_fingerprint() -> None:
    store = FindingStore(path=Path("/tmp/unused.json"))
    f = Finding.create(
        id="X-1",
        aliases=(),
        scanner="ok",
        severity=Severity.HIGH,
        message="m",
        description="",
        location=Location(file="a.py", line_start=1, line_end=1, snippet=""),
    )
    store.add(f)
    store.add(f)
    assert len(store.all()) == 1


def test_finding_store_load_missing_file_empty(tmp_path: Path) -> None:
    store = FindingStore.load(tmp_path / "nonexistent.json")
    assert store.all() == []


def test_scanner_result_is_frozen() -> None:
    r = ScannerResult(scanner="x", status="ok", findings=(), reason="", duration_ms=0)
    with pytest.raises(Exception):
        r.status = "error"  # type: ignore[misc]
