"""Scanner Protocol and base classes.

Two concrete base classes:
  - InProcessScanner: pure-Python, imports its implementation
  - SubprocessScanner: shells out to a binary and parses stdout
"""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
from pathlib import Path
from typing import Callable, Literal, Protocol, runtime_checkable

from duh.security.config import ScannerConfig
from duh.security.finding import Finding, Severity

Tier = Literal["minimal", "extended", "paranoid", "custom"]


@runtime_checkable
class Scanner(Protocol):
    name: str
    tier: Tier
    default_severity: tuple[Severity, ...]

    def available(self) -> bool: ...

    async def scan(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None = None,
    ) -> list[Finding]: ...


class InProcessScanner:
    """Base class for pure-Python scanners."""

    name: str = ""
    tier: Tier = "minimal"
    default_severity: tuple[Severity, ...] = (Severity.HIGH,)
    _module_name: str = ""

    def available(self) -> bool:
        if not self._module_name:
            return True
        return importlib.util.find_spec(self._module_name) is not None

    async def scan(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None = None,
    ) -> list[Finding]:
        return await self._scan_impl(target, cfg, changed_files=changed_files)

    async def _scan_impl(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None,
    ) -> list[Finding]:
        raise NotImplementedError


class SubprocessScanner:
    """Base class for scanners that shell out to a binary."""

    name: str = ""
    tier: Tier = "extended"
    default_severity: tuple[Severity, ...] = (Severity.HIGH,)
    _binary: str = ""
    _argv_template: list[str] = []
    _parser: Callable[[bytes], list[Finding]] = staticmethod(lambda _b: [])  # type: ignore[assignment]

    def available(self) -> bool:
        return shutil.which(self._binary) is not None

    async def scan(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None = None,
    ) -> list[Finding]:
        argv = list(self._argv_template)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(target) if target.is_dir() else None,
        )
        stdout, _stderr = await proc.communicate()
        return type(self)._parser(stdout)
