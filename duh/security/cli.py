"""duh security subcommand entry point.

Dispatches `init`, `scan`, `diff`, `exception`, `db`, `doctor`, `hook`.
Phase 1 implements `scan` as a stub that emits a SARIF document.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence

from duh.security.config import load_policy
from duh.security.engine import FindingStore, Runner, ScannerRegistry
from duh.security.finding import Finding


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="duh security")
    subs = parser.add_subparsers(dest="cmd", required=True)

    scan = subs.add_parser("scan", help="Run enabled scanners once")
    scan.add_argument("--project-root", default=".", type=Path)
    scan.add_argument("--sarif-out", default=None, help="path or '-' for stdout")
    scan.add_argument("--scanner", action="append", default=None)
    scan.add_argument("--baseline", default=None)
    scan.add_argument("--fail-on", default=None)
    scan.add_argument("--quiet", action="store_true")

    subs.add_parser("init", help="Interactive wizard (phase 3)")
    subs.add_parser("diff", help="Delta against baseline (phase 4)")
    subs.add_parser("exception", help="Exception CRUD (phase 2)")
    subs.add_parser("db", help="Advisory DB management (phase 4)")
    subs.add_parser("doctor", help="Diagnose scanner install + CI (phase 5)")

    hook = subs.add_parser("hook", help="Install/uninstall git hooks")
    hook_sub = hook.add_subparsers(dest="hook_cmd", required=True)
    for verb in ("install", "uninstall"):
        sp = hook_sub.add_parser(verb)
        sp.add_argument("kind", choices=["git"])
        sp.add_argument("--project-root", default=".", type=Path)

    return parser


def _to_sarif(findings: list[Finding]) -> dict:
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {"driver": {"name": "duh-security", "version": "0.1.0"}},
                "results": [f.to_sarif() for f in findings],
            }
        ],
    }


async def _run_scan(project_root: Path, scanner_filter: list[str] | None) -> list[Finding]:
    policy = load_policy(project_root=project_root)
    registry = ScannerRegistry()
    registry.load_entry_points()
    candidate_names = scanner_filter or [
        name for name in registry.names() if name in policy.scanners or not policy.scanners
    ]
    runner = Runner(registry=registry, policy=policy)
    results = await runner.run(project_root, scanners=candidate_names)
    findings: list[Finding] = []
    for r in results:
        findings.extend(r.findings)
    return findings


def _checkout_baseline(ref: str, project_root: Path) -> Path:
    """Check out the baseline ref into a temp worktree; return its path."""
    import subprocess
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="duh-sec-baseline-"))
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(tmp), ref],
        cwd=str(project_root), check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return tmp


def _delta(head: list[Finding], base: list[Finding]) -> list[Finding]:
    base_fps = {f.fingerprint for f in base}
    return [f for f in head if f.fingerprint not in base_fps]


_PRE_PUSH_BODY = """#!/usr/bin/env sh
#
# Installed by `duh security init`.
# To disable once: git push --no-verify
# To remove entirely: duh security hook uninstall git
#
if ! duh security scan --baseline "@{upstream}" --fail-on=high --quiet; then
    echo ""
    echo "duh-sec: push blocked by security findings."
    echo "  Inspect:  duh security scan --baseline @{upstream}"
    echo "  Bypass:   git push --no-verify"
    echo "  Disable:  duh security hook uninstall git"
    exit 1
fi
"""


def _dispatch_hook(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root)
    hook_path = project_root / ".git" / "hooks" / "pre-push"
    if args.hook_cmd == "install":
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(_PRE_PUSH_BODY, encoding="utf-8")
        hook_path.chmod(0o755)
        sys.stdout.write(
            "duh-sec: pre-push hook installed.\n"
            "  To disable once:  git push --no-verify\n"
            "  To remove:        duh security hook uninstall git\n"
        )
        return 0
    if args.hook_cmd == "uninstall":
        if hook_path.exists():
            hook_path.unlink()
        return 0
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)

    if args.cmd == "scan":
        head_findings = asyncio.run(_run_scan(args.project_root, args.scanner))
        findings = head_findings
        if args.baseline:
            base_root = _checkout_baseline(args.baseline, args.project_root)
            base_findings = asyncio.run(_run_scan(base_root, args.scanner))
            findings = _delta(head_findings, base_findings)
        sarif = _to_sarif(findings)
        payload = json.dumps(sarif, indent=2)
        if args.sarif_out == "-" or args.sarif_out is None:
            sys.stdout.write(payload + "\n")
        else:
            Path(args.sarif_out).write_text(payload, encoding="utf-8")
        if args.fail_on:
            threshold = {s.strip() for s in args.fail_on.split(",")}
            if any(f.severity.value in threshold for f in findings):
                return 1
        return 0

    if args.cmd == "hook":
        return _dispatch_hook(args)

    sys.stderr.write(f"duh security: {args.cmd} is not yet implemented\n")
    return 3
