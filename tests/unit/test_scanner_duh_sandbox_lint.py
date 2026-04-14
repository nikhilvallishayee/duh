"""Tests for SandboxLintScanner — CVE-2025-59532 sandbox bypass defense."""

from __future__ import annotations

import asyncio
from pathlib import Path

from duh.security.config import ScannerConfig
from duh.security.scanners.duh_sandbox_lint import SandboxLintScanner


_BAD = '''\
def handle(model_output: str) -> None:
    profile = f"(allow file-read-write {model_output})"
    with open("policy.sb", "w") as fh:
        fh.write(profile)
'''

_SAFE = '''\
def handle() -> None:
    with open("policy.sb", "w") as fh:
        fh.write("(allow file-read-write)")
'''


def test_flags_fstring_flowing_into_sb_write(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(_BAD)
    findings = asyncio.run(SandboxLintScanner().scan(tmp_path, ScannerConfig(), changed_files=None))
    assert any(f.id == "DUH-SANDBOX-UNTRUSTED" for f in findings)


def test_safe_sandbox_passes(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(_SAFE)
    findings = asyncio.run(SandboxLintScanner().scan(tmp_path, ScannerConfig(), changed_files=None))
    assert not any(f.id == "DUH-SANDBOX-UNTRUSTED" for f in findings)


def test_cve_2025_59532_replay() -> None:
    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "security" / "cve_replays" / "CVE-2025-59532"
    findings = asyncio.run(SandboxLintScanner().scan(fixture, ScannerConfig(), changed_files=None))
    assert any(f.id == "DUH-SANDBOX-UNTRUSTED" for f in findings), (
        f"CVE-2025-59532 replay not caught; got {[f.id for f in findings]}"
    )
