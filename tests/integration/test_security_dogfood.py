"""End-to-end dogfood: run `duh security scan` against D.U.H.'s own
source tree with the 9 Minimal-tier scanners and assert zero blocking
findings. Also assert that the committed CI files reference the Phase 5
generator output (SHA-pinned) and that publish.yml uses Trusted Publishing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from duh.security.cli import main as security_main


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_ci_yml_has_security_job_with_pinned_actions() -> None:
    body = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "security:" in body, "ci.yml must define a security job"
    # Every `uses:` in the security job should be SHA-pinned. Scan all
    # `uses: owner/name@ref` occurrences and require 40-char SHAs.
    sha_re = re.compile(r"uses:\s+([^@\s]+)@([^\s]+)")
    for match in sha_re.finditer(body):
        ref = match.group(2)
        if ref == "TODO":
            assert "zizmor" in match.group(1)
            continue
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"unpinned in ci.yml: {match.group(0)}"


def test_publish_yml_uses_trusted_publishing() -> None:
    body = (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "id-token: write" in body
    assert "pypa/gh-action-pypi-publish@" in body
    # Trusted Publishing means no long-lived token reference anywhere.
    assert "PYPI_API_TOKEN" not in body
    # SHA-pinned to v1.14.0+ from the pin registry.
    assert "6733eb7d741f0b11ec6a39b58540dab7590f9b7d" in body


def test_dependabot_yml_is_committed() -> None:
    body = (REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert "version: 2" in body
    assert "package-ecosystem: \"pip\"" in body
    assert "package-ecosystem: \"github-actions\"" in body


def test_security_md_is_committed() -> None:
    body = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "# Security Policy" in body
    assert "Reporting a Vulnerability" in body
    assert "Safe Harbor" in body


def test_dogfood_scan_on_self_has_no_blocking_findings(tmp_path: Path) -> None:
    # Run `duh security scan` against D.U.H.'s own source tree (duh/ package
    # only — tests/ and fixtures are excluded to avoid false-positives from
    # intentional CVE replay code and scanner self-reference patterns).
    out = tmp_path / "self.sarif"
    src_root = REPO_ROOT / "duh"
    exit_code = security_main([
        "scan",
        "--project-root", str(src_root),
        "--sarif-out", str(out),
        "--fail-on", "critical,high",
    ])
    assert exit_code == 0, (
        f"D.U.H. source has blocking security findings. "
        f"Inspect: {out}"
    )
    # Additionally confirm SARIF was produced.
    assert out.exists()
    import json as _json
    sarif = _json.loads(out.read_text(encoding="utf-8"))
    assert sarif["$schema"].startswith("https://")
