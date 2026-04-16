"""duh security subcommand entry point.

Dispatches `init`, `scan`, `diff`, `exception`, `db`, `doctor`, `hook`.
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
from duh.security.finding import Finding, Severity


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="duh security")
    subs = parser.add_subparsers(dest="cmd", required=True)

    scan = subs.add_parser("scan", help="Run enabled scanners once")
    scan.add_argument("--project-root", default=".", type=Path)
    scan.add_argument(
        "--format",
        choices=["text", "sarif"],
        default="text",
        help="Output format: 'text' (human-readable table, default) or 'sarif' (raw JSON).",
    )
    scan.add_argument("--sarif-out", default=None, help="path or '-' for stdout (implies --format sarif)")
    scan.add_argument("--scanner", action="append", default=None)
    scan.add_argument("--baseline", default=None)
    scan.add_argument(
        "--fail-on",
        default=None,
        help=(
            "Comma-separated severity levels that cause a non-zero exit code. "
            "Valid values: critical, high, medium, low, info."
        ),
    )
    scan.add_argument("--quiet", action="store_true")

    init = subs.add_parser("init", help="Interactive wizard")
    init.add_argument("--non-interactive", action="store_true")
    init.add_argument("--mode", default="strict", choices=["advisory", "strict", "paranoid"])
    init.add_argument("--dry-run", action="store_true")
    init.add_argument("--project-root", default=".", type=Path)
    subs.add_parser("diff", help="Delta against baseline (phase 4)")

    exc = subs.add_parser("exception", help="Exception CRUD")
    exc_sub = exc.add_subparsers(dest="exc_cmd", required=True)

    add = exc_sub.add_parser("add")
    add.add_argument("id")
    add.add_argument("--reason", required=True)
    add.add_argument("--expires", required=True)
    add.add_argument("--aliases", default="")
    add.add_argument("--package", default=None)
    add.add_argument("--ticket", default=None)
    add.add_argument("--permanent", action="store_true")
    add.add_argument("--long-term", action="store_true")
    add.add_argument("--project-root", default=".", type=Path)

    lst = exc_sub.add_parser("list")
    lst.add_argument("--project-root", default=".", type=Path)

    rm = exc_sub.add_parser("remove")
    rm.add_argument("id")
    rm.add_argument("--project-root", default=".", type=Path)

    renew = exc_sub.add_parser("renew")
    renew.add_argument("id")
    renew.add_argument("--expires", required=True)
    renew.add_argument("--project-root", default=".", type=Path)

    audit_cmd = exc_sub.add_parser("audit")
    audit_cmd.add_argument("--project-root", default=".", type=Path)

    subs.add_parser("db", help="Advisory DB management (phase 4)")
    doc = subs.add_parser("doctor", help="Diagnose scanner installs")
    doc.add_argument("--project-root", default=".", type=Path)

    hook = subs.add_parser("hook", help="Install/uninstall git hooks")
    hook_sub = hook.add_subparsers(dest="hook_cmd", required=True)
    for verb in ("install", "uninstall"):
        sp = hook_sub.add_parser(verb)
        sp.add_argument("kind", choices=["git"])
        sp.add_argument("--project-root", default=".", type=Path)

    gen = subs.add_parser("generate", help="Generate CI templates and SECURITY.md")
    gen_sub = gen.add_subparsers(dest="gen_cmd", required=True)

    gen_wf = gen_sub.add_parser("workflow", help="Write .github/workflows/security.yml")
    gen_wf.add_argument(
        "--template",
        choices=["minimal", "standard", "paranoid"],
        default="standard",
    )
    gen_wf.add_argument("--output", type=Path, default=Path(".github/workflows/security.yml"))

    gen_db = gen_sub.add_parser("dependabot", help="Write .github/dependabot.yml")
    gen_db.add_argument("--output", type=Path, default=Path(".github/dependabot.yml"))

    gen_md = gen_sub.add_parser("security-md", help="Write SECURITY.md")
    gen_md.add_argument("--project-name", required=True)
    gen_md.add_argument("--latest-version", required=True)
    gen_md.add_argument("--output", type=Path, default=Path("SECURITY.md"))

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


# -- text output helpers -----------------------------------------------------

_SEVERITY_ORDER = [
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.INFO,
]

_ANSI_COLORS: dict[Severity, str] = {
    Severity.CRITICAL: "\033[1;31m",  # bold red
    Severity.HIGH: "\033[33m",        # yellow
    Severity.MEDIUM: "\033[36m",      # cyan
    Severity.LOW: "\033[34m",         # blue
    Severity.INFO: "\033[37m",        # white/grey
}
_ANSI_RESET = "\033[0m"


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    """Sort findings by severity descending (CRITICAL first)."""
    return sorted(findings, key=lambda f: f.severity.rank, reverse=True)


def _severity_label(sev: Severity, *, color: bool) -> str:
    label = sev.value.upper()
    if color:
        return f"{_ANSI_COLORS[sev]}{label}{_ANSI_RESET}"
    return label


def _build_summary(findings: list[Finding]) -> str:
    counts: dict[Severity, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    parts: list[str] = []
    for sev in _SEVERITY_ORDER:
        c = counts.get(sev, 0)
        if c:
            parts.append(f"{c} {sev.value}")
    total = len(findings)
    detail = ", ".join(parts) if parts else "none"
    return f"{total} findings ({detail})"


def format_text(findings: list[Finding], *, color: bool | None = None) -> str:
    """Render findings as a severity-sorted table with an optional ANSI palette.

    Parameters
    ----------
    findings:
        The list of :class:`Finding` objects to render.
    color:
        ``True`` to force ANSI colours, ``False`` to suppress them,
        ``None`` (default) to auto-detect based on whether stdout is a TTY.

    Returns
    -------
    str
        The formatted text block ready for ``sys.stdout.write()``.
    """
    if color is None:
        color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    sorted_findings = _sort_findings(findings)

    if not sorted_findings:
        return _build_summary(sorted_findings) + "\n"

    # Compute column widths (using raw labels for width, not ANSI-encoded ones).
    sev_width = max(len(f.severity.value) for f in sorted_findings)
    sev_width = max(sev_width, len("Severity"))
    scanner_width = max(len(f.scanner) for f in sorted_findings)
    scanner_width = max(scanner_width, len("Scanner"))
    finding_width = max(len(f.message) for f in sorted_findings)
    finding_width = max(finding_width, len("Finding"))
    loc_width = max(len(f"{f.location.file}:{f.location.line_start}") for f in sorted_findings)
    loc_width = max(loc_width, len("File:Line"))

    header = (
        f"{'Severity':<{sev_width}}  "
        f"{'Scanner':<{scanner_width}}  "
        f"{'Finding':<{finding_width}}  "
        f"{'File:Line':<{loc_width}}"
    )
    sep = (
        f"{'-' * sev_width}  "
        f"{'-' * scanner_width}  "
        f"{'-' * finding_width}  "
        f"{'-' * loc_width}"
    )

    lines: list[str] = [header, sep]
    for f in sorted_findings:
        sev_display = _severity_label(f.severity, color=color)
        # Pad after the label using raw length so alignment works with ANSI codes.
        raw_len = len(f.severity.value.upper())
        padding = " " * (sev_width - raw_len)
        loc_str = f"{f.location.file}:{f.location.line_start}"
        lines.append(
            f"{sev_display}{padding}  "
            f"{f.scanner:<{scanner_width}}  "
            f"{f.message:<{finding_width}}  "
            f"{loc_str:<{loc_width}}"
        )

    lines.append("")
    lines.append(_build_summary(sorted_findings))
    return "\n".join(lines) + "\n"


def _make_stderr_progress_callback(total_hint: int = 0):
    """Return a progress callback that paints a single live stderr line.

    Only used when stderr is a TTY and ``--quiet`` is not set.  The
    callback uses ``\\r`` to rewrite one line and emits a trailing newline
    once the final scanner finishes.
    """
    def _cb(name: str, current: int, total: int) -> None:
        _ = total_hint  # unused; signature stability for future extension
        sys.stderr.write(f"\rScanning [{current}/{total}] {name:<24s}")
        sys.stderr.flush()
        if current >= total:
            sys.stderr.write("\n")
            sys.stderr.flush()
    return _cb


async def _run_scan(
    project_root: Path,
    scanner_filter: list[str] | None,
    *,
    progress_cb=None,
) -> list[Finding]:
    policy = load_policy(project_root=project_root)
    registry = ScannerRegistry()
    registry.load_entry_points()
    candidate_names = scanner_filter or [
        name for name in registry.names() if name in policy.scanners or not policy.scanners
    ]
    runner = Runner(registry=registry, policy=policy)
    results = await runner.run(
        project_root, scanners=candidate_names, progress=progress_cb,
    )
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
        # QX: show a live progress indicator when stderr is a TTY and the
        # operator didn't ask for --quiet.  CI runs (non-TTY) stay silent.
        progress_cb = None
        if (
            not args.quiet
            and hasattr(sys.stderr, "isatty")
            and sys.stderr.isatty()
        ):
            progress_cb = _make_stderr_progress_callback()

        head_findings = asyncio.run(
            _run_scan(args.project_root, args.scanner, progress_cb=progress_cb)
        )
        findings = head_findings
        if args.baseline:
            base_root = _checkout_baseline(args.baseline, args.project_root)
            base_findings = asyncio.run(
                _run_scan(base_root, args.scanner, progress_cb=progress_cb)
            )
            findings = _delta(head_findings, base_findings)

        # --sarif-out implies sarif format for backward compatibility.
        fmt = args.format
        if args.sarif_out is not None:
            fmt = "sarif"

        if fmt == "sarif":
            sarif = _to_sarif(findings)
            payload = json.dumps(sarif, indent=2)
            if args.sarif_out == "-" or args.sarif_out is None:
                sys.stdout.write(payload + "\n")
            else:
                Path(args.sarif_out).write_text(payload, encoding="utf-8")
        else:
            # text (default)
            if not args.quiet:
                sys.stdout.write(format_text(findings))

        if args.fail_on:
            threshold = {s.strip() for s in args.fail_on.split(",")}
            if any(f.severity.value in threshold for f in findings):
                return 1
        return 0

    if args.cmd == "generate":
        return _dispatch_generate(args)

    if args.cmd == "init":
        return _dispatch_init(args)

    if args.cmd == "hook":
        return _dispatch_hook(args)

    if args.cmd == "exception":
        return _dispatch_exception(args)

    if args.cmd == "doctor":
        return _dispatch_doctor(args)

    sys.stderr.write(f"duh security: {args.cmd} is not yet implemented\n")
    return 3


def _dispatch_generate(args) -> int:
    from duh.security.ci_templates.github_actions import (
        WorkflowTemplate,
        generate_dependabot,
        generate_workflow,
    )
    from duh.security.ci_templates.security_md import generate as generate_security_md

    out: Path = args.output
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.gen_cmd == "workflow":
        body = generate_workflow(template=WorkflowTemplate(args.template))
    elif args.gen_cmd == "dependabot":
        body = generate_dependabot()
    elif args.gen_cmd == "security-md":
        body = generate_security_md(
            project_name=args.project_name,
            latest_version=args.latest_version,
        )
    else:
        return 2

    out.write_text(body, encoding="utf-8")
    sys.stdout.write(f"wrote {out}\n")
    return 0


def _dispatch_init(args) -> int:
    from duh.security.wizard import (
        Answers,
        detect,
        render_plan,
        run_interactive,
        write_plan,
    )

    project_root = Path(args.project_root)
    det = detect(project_root=project_root)
    if args.non_interactive:
        answers = Answers(
            mode=args.mode,
            enable_runtime=True,
            extended_scanners=(),
            generate_ci=False,
            ci_template="standard",
            install_git_hook=False,
            generate_security_md=False,
            import_legacy=False,
            pin_scanner_versions=True,
        )
    else:
        # Interactive wizard.  ``print``-style output keeps the prompts on
        # stdout so they show up in normal terminal sessions; tests inject
        # capturing callables.
        def _out(msg: str = "", end: str = "\n", flush: bool = False) -> None:
            sys.stdout.write(f"{msg}{end}")
            if flush:
                sys.stdout.flush()

        answers = run_interactive(
            project_root=project_root,
            detection=det,
            input_fn=input,
            output_fn=_out,
        )
        # Honour --mode if the operator passed it; the wizard otherwise
        # picks "strict" as a sensible default.
        if args.mode and args.mode != answers.mode:
            from dataclasses import replace
            answers = replace(answers, mode=args.mode)

    plan = render_plan(detection=det, answers=answers, project_root=project_root)
    result = write_plan(plan, dry_run=args.dry_run)
    if not args.dry_run and result.written:
        sys.stdout.write(f"  wrote {len(result.written)} file(s) under {project_root}\n")
    return 0


def _dispatch_doctor(args) -> int:
    from duh.security.engine import ScannerRegistry

    registry = ScannerRegistry()
    registry.load_entry_points()
    sys.stdout.write("duh security doctor\n")
    sys.stdout.write("  scanners discovered:\n")
    exit_code = 0
    for name in sorted(registry.names()):
        scanner = registry.get(name)
        status = "ok" if scanner.available() else "missing"
        if status == "missing":
            exit_code = 1
        sys.stdout.write(f"    {name:24s} {status}\n")
    return exit_code


def _dispatch_exception(args: argparse.Namespace) -> int:
    import os
    import socket
    from datetime import datetime

    from duh.security.exceptions import ExceptionStore

    project_root = Path(args.project_root)
    path = project_root / ".duh" / "security-exceptions.json"
    store = ExceptionStore.load(path)

    if args.exc_cmd == "add":
        now = datetime.now().astimezone()
        expires = datetime.fromisoformat(args.expires)
        store.add(
            id=args.id,
            reason=args.reason,
            expires_at=expires,
            added_by=f"{os.environ.get('USER', 'unknown')}@{socket.gethostname()}",
            added_at=now,
            aliases=tuple(args.aliases.split(",")) if args.aliases else (),
            scope={"package": args.package} if args.package else {},
            ticket=args.ticket,
            permanent=args.permanent,
            long_term=args.long_term,
        )
        store.save()
        return 0

    if args.exc_cmd == "list":
        for exc in store.all():
            sys.stdout.write(f"{exc.id}\texpires={exc.expires_at.isoformat()}\treason={exc.reason}\n")
        return 0

    if args.exc_cmd == "remove":
        removed = store.remove(args.id)
        store.save()
        return 0 if removed else 1

    if args.exc_cmd == "renew":
        new_expiry = datetime.fromisoformat(args.expires)
        store.renew(args.id, new_expiry)
        store.save()
        return 0

    if args.exc_cmd == "audit":
        report = store.audit(at=datetime.now().astimezone())
        sys.stdout.write(f"expired: {', '.join(report.expired) or '(none)'}\n")
        sys.stdout.write(f"expiring_soon: {', '.join(report.expiring_soon) or '(none)'}\n")
        return 0

    return 2
