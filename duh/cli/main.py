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
import signal
import sys

from duh.cli.parser import build_parser
from duh.cli.doctor import run_doctor
from duh.cli.runner import run_print_mode


def _setup_signal_handlers() -> None:
    """Install graceful shutdown handlers for SIGINT/SIGTERM."""
    def _handle_signal(signum: int, frame: object) -> None:
        # Raise KeyboardInterrupt to trigger cleanup in asyncio
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_signal)
    # SIGINT is already handled by Python (raises KeyboardInterrupt)


def main(argv: list[str] | None = None) -> int:
    _setup_signal_handlers()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return run_doctor()

    # SDK mode: stream-json on both input and output
    if getattr(args, "input_format", "text") == "stream-json":
        from duh.cli.sdk_runner import run_stream_json_mode
        try:
            return asyncio.run(run_stream_json_mode(args))
        except KeyboardInterrupt:
            sys.stderr.write("\nInterrupted.\n")
            return 130

    if args.prompt is not None:
        try:
            return asyncio.run(run_print_mode(args))
        except KeyboardInterrupt:
            sys.stderr.write("\nInterrupted.\n")
            return 130

    # No prompt and no SDK mode → interactive REPL
    from duh.cli.repl import run_repl
    try:
        return asyncio.run(run_repl(args))
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        return 0
