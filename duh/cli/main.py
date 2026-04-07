"""CLI entry point for D.U.H.

Usage:
    duh -p "fix the bug"              # print mode
    duh --version                      # show version
    duh doctor                         # diagnostics
    duh -p "prompt" --debug            # full event tracing
    duh -p "prompt" --model opus       # specify model
"""

from __future__ import annotations

import asyncio

from duh.cli.parser import build_parser
from duh.cli.doctor import run_doctor
from duh.cli.runner import run_print_mode


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return run_doctor()

    if args.prompt is not None:
        return asyncio.run(run_print_mode(args))

    parser.print_help()
    return 0
