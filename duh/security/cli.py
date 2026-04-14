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
    subs.add_parser("hook", help="Install/uninstall pre-push git hook (phase 4)")

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


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)

    if args.cmd == "scan":
        findings = asyncio.run(_run_scan(args.project_root, args.scanner))
        sarif = _to_sarif(findings)
        payload = json.dumps(sarif, indent=2)
        if args.sarif_out == "-" or args.sarif_out is None:
            sys.stdout.write(payload + "\n")
        else:
            Path(args.sarif_out).write_text(payload, encoding="utf-8")
        return 0

    sys.stderr.write(f"duh security: {args.cmd} is not yet implemented\n")
    return 3
