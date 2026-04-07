"""CLI entry point for D.U.H.

Usage:
    duh -p "fix the bug"              # print mode
    duh --version                      # show version
    duh doctor                         # diagnostics
    duh -p "prompt" --debug            # full event tracing
    duh -p "prompt" --model opus       # specify model
    duh --output-format stream-json --input-format stream-json  # SDK mode
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

    # SDK mode: stream-json on both input and output
    if getattr(args, "input_format", "text") == "stream-json":
        from duh.cli.sdk_runner import run_stream_json_mode
        return asyncio.run(run_stream_json_mode(args))

    if args.prompt is not None:
        return asyncio.run(run_print_mode(args))

    # No prompt and no SDK mode → interactive REPL
    from duh.cli.repl import run_repl
    return asyncio.run(run_repl(args))
