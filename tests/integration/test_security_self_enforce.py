"""D.U.H. self-enforcement: sandbox-lint + oauth-lint MUST block
on their own CVE replays and MUST be marked enforce=True in
D.U.H.'s committed `.duh/security.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from duh.security.config import SecurityPolicy
from duh.security.engine import ScannerRegistry, Runner
from duh.security.finding import Severity


REPO_ROOT = Path(__file__).resolve().parents[2]
SECURITY_JSON = REPO_ROOT / ".duh" / "security.json"


def test_duh_commits_security_json() -> None:
    assert SECURITY_JSON.exists(), (
        f"D.U.H. must ship its own .duh/security.json; missing at {SECURITY_JSON}"
    )
    data = json.loads(SECURITY_JSON.read_text(encoding="utf-8"))
    assert "scanners" in data


def test_sandbox_lint_is_enforced_on_self() -> None:
    data = json.loads(SECURITY_JSON.read_text(encoding="utf-8"))
    sandbox = data["scanners"].get("duh-sandbox-lint", {})
    assert sandbox.get("enforce") is True, (
        "D.U.H. must enforce duh-sandbox-lint on its own codebase"
    )


def test_oauth_lint_is_enforced_on_self() -> None:
    data = json.loads(SECURITY_JSON.read_text(encoding="utf-8"))
    oauth = data["scanners"].get("duh-oauth-lint", {})
    assert oauth.get("enforce") is True, (
        "D.U.H. must enforce duh-oauth-lint on its own codebase"
    )


@pytest.mark.asyncio
async def test_sandbox_lint_blocks_replay_fixture(tmp_path: Path) -> None:
    # Copy the CVE-2025-59532 replay into tmp_path, then scan.
    replay = REPO_ROOT / "tests" / "fixtures" / "security" / "cve_replays" / "CVE-2025-59532"
    if not replay.exists():
        pytest.skip("replay fixture not available in this checkout")
    target = tmp_path / "project"
    target.mkdir()
    for f in replay.rglob("*"):
        if f.is_file():
            rel = f.relative_to(replay)
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(f.read_bytes())

    policy = SecurityPolicy(mode="strict")
    registry = ScannerRegistry()
    registry.load_entry_points()
    runner = Runner(registry=registry, policy=policy)
    results = await runner.run(target, scanners=("duh-sandbox-lint",))
    # At least one high-severity finding must appear — that is the whole
    # point of the replay fixture.
    findings = [f for r in results for f in r.findings]
    assert any(f.severity == Severity.HIGH for f in findings), (
        "duh-sandbox-lint must detect the CVE-2025-59532 replay fixture"
    )
