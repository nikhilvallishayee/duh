"""``duh wave`` argument parser + dispatch.

Ten subcommands grouped by concern:

- Lifecycle:     ``start`` ``stop``
- Inspect:       ``ls`` ``inspect`` ``logs``
- Control:       ``pause`` ``resume``
- Bundles:       ``install`` ``uninstall``
- Optional UI:   ``web``

Subcommands that need the running daemon (``inspect``, ``pause``,
``resume``, ``logs``, ``web``) talk to it over the host socket at
``~/.duh/waves/host.sock``. Subcommands that only touch on-disk state
(``ls``, ``install``, ``uninstall``) work without a running daemon.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from duh.duhwave.cli import commands


DEFAULT_WAVES_ROOT = Path.home() / ".duh" / "waves"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="duh wave",
        description="duhwave control plane (ADR-032).",
    )
    parser.add_argument(
        "--waves-root",
        type=Path,
        default=DEFAULT_WAVES_ROOT,
        help="Root directory for installed swarms (default: ~/.duh/waves)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="CMD")

    p_start = sub.add_parser("start", help="Start the duhwave host process")
    p_start.add_argument("name", nargs="?", help="Optional swarm to start by name")
    p_start.add_argument("--foreground", action="store_true", help="Run in foreground (default daemonises)")

    sub.add_parser("stop", help="Stop the host process")

    p_ls = sub.add_parser("ls", help="List installed swarms and running tasks")
    p_ls.add_argument("--json", action="store_true", help="JSON output")

    p_inspect = sub.add_parser("inspect", help="Show topology + live state for a swarm")
    p_inspect.add_argument("swarm_id")

    p_pause = sub.add_parser("pause", help="Suspend a running swarm without losing state")
    p_pause.add_argument("swarm_id")

    p_resume = sub.add_parser("resume", help="Resume a paused swarm")
    p_resume.add_argument("swarm_id")

    p_logs = sub.add_parser("logs", help="Tail a swarm's event log")
    p_logs.add_argument("swarm_id")
    p_logs.add_argument("--follow", "-f", action="store_true")
    p_logs.add_argument("--lines", "-n", type=int, default=200)

    p_install = sub.add_parser("install", help="Install a .duhwave bundle")
    p_install.add_argument("path", type=Path, help="Path to .duhwave file")
    p_install.add_argument(
        "--public-key",
        type=Path,
        default=None,
        help="Path to Ed25519 public key for signature verification",
    )
    p_install.add_argument(
        "--force",
        action="store_true",
        help="Skip permissions-diff confirmation",
    )

    p_uninstall = sub.add_parser("uninstall", help="Remove an installed swarm")
    p_uninstall.add_argument("name")

    p_web = sub.add_parser("web", help="Start the local web UI on http://localhost:8729")
    p_web.add_argument("--port", type=int, default=8729)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    waves_root: Path = args.waves_root
    waves_root.mkdir(parents=True, exist_ok=True)

    handler = getattr(commands, f"cmd_{args.cmd}", None)
    if handler is None:  # pragma: no cover - argparse guards this
        print(f"unknown subcommand: {args.cmd}", file=sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
