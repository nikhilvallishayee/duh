"""Tests for the Scanner Protocol, InProcessScanner, SubprocessScanner base classes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Location, Severity
from duh.security.scanners import (
    InProcessScanner,
    Scanner,
    SubprocessScanner,
    Tier,
)


class _FakeInProcess(InProcessScanner):
    name = "fake-inproc"
    tier: Tier = "minimal"
    _module_name = "json"  # always available

    async def _scan_impl(self, target, cfg, *, changed_files):
        return [
            Finding.create(
                id="FAKE-001",
                aliases=(),
                scanner=self.name,
                severity=Severity.LOW,
                message="synthetic",
                description="",
                location=Location(file=str(target), line_start=1, line_end=1, snippet=""),
            )
        ]


class _MissingModuleScanner(InProcessScanner):
    name = "fake-missing"
    tier: Tier = "extended"
    _module_name = "definitely_not_a_real_module_xyz"

    async def _scan_impl(self, target, cfg, *, changed_files):
        return []


def test_inprocess_available_true_when_module_importable() -> None:
    scanner = _FakeInProcess()
    assert scanner.available() is True


def test_inprocess_available_false_when_missing() -> None:
    scanner = _MissingModuleScanner()
    assert scanner.available() is False


def test_inprocess_scan_returns_findings() -> None:
    scanner = _FakeInProcess()
    cfg = ScannerConfig()
    findings = asyncio.run(scanner.scan(Path("x.py"), cfg, changed_files=None))
    assert len(findings) == 1
    assert findings[0].id == "FAKE-001"


class _FailingScanner(InProcessScanner):
    name = "boom"
    tier: Tier = "minimal"
    _module_name = "json"

    async def _scan_impl(self, target, cfg, *, changed_files):
        raise RuntimeError("scanner blew up")


def test_inprocess_scan_propagates_exception() -> None:
    scanner = _FailingScanner()
    cfg = ScannerConfig()
    with pytest.raises(RuntimeError, match="scanner blew up"):
        asyncio.run(scanner.scan(Path("x.py"), cfg, changed_files=None))


class _EchoSubprocess(SubprocessScanner):
    name = "echo-sub"
    tier: Tier = "extended"
    _binary = "echo"
    _argv_template = ["echo", "hello"]

    @staticmethod
    def _parser(stdout: bytes) -> list[Finding]:
        if b"hello" not in stdout:
            return []
        return [
            Finding.create(
                id="SUB-001",
                aliases=(),
                scanner="echo-sub",
                severity=Severity.INFO,
                message="echo reached",
                description="",
                location=Location(file="-", line_start=0, line_end=0, snippet=""),
            )
        ]


def test_subprocess_available_checks_binary() -> None:
    scanner = _EchoSubprocess()
    # echo is on PATH on every POSIX system we target
    assert scanner.available() is True


def test_subprocess_scan_parses_stdout() -> None:
    scanner = _EchoSubprocess()
    cfg = ScannerConfig()
    findings = asyncio.run(scanner.scan(Path("."), cfg, changed_files=None))
    assert len(findings) == 1
    assert findings[0].id == "SUB-001"


def test_scanner_protocol_is_runtime_checkable() -> None:
    # Protocol should be usable as a type marker
    assert issubclass(_FakeInProcess, InProcessScanner)
    fake: Scanner = _FakeInProcess()  # type: ignore[assignment]
    assert fake.name == "fake-inproc"
