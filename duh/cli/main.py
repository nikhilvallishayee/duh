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

    if args.command == "constitution":
        from duh.constitution import build_system_prompt, ConstitutionConfig
        agent = getattr(args, "agent_type", "general") or "general"
        cfg = ConstitutionConfig(agent_type=agent)
        sys.stdout.write(build_system_prompt(cfg))
        sys.stdout.write("\n")
        return 0

    if args.command == "security":
        from duh.security.cli import main as security_main
        return security_main(args.security_args)

    if args.command == "audit":
        import json as _json
        from duh.security.audit import AuditLogger
        logger = AuditLogger()
        entries = logger.read_entries(limit=args.limit)
        if not entries:
            sys.stdout.write("No audit entries found.\n")
            return 0
        if getattr(args, "audit_json", False):
            for e in entries:
                sys.stdout.write(_json.dumps(e) + "\n")
        else:
            sys.stdout.write(f"Last {len(entries)} audit entries:\n")
            for e in entries:
                ts = e.get("ts", "?")
                tool = e.get("tool", "?")
                status = e.get("status", "?")
                ms = e.get("ms", 0)
                sid = e.get("sid", "?")[:8]
                sys.stdout.write(f"  {ts}  {sid}  {tool:20s}  {status:7s}  {ms}ms\n")
        return 0

    if args.command == "review":
        from duh.cli.review import run_review
        try:
            return asyncio.run(run_review(args))
        except KeyboardInterrupt:
            sys.stderr.write("\nInterrupted.\n")
            return 130

    if args.command == "bridge":
        from duh.bridge.server import BridgeServer

        async def _run_bridge() -> int:
            server = BridgeServer(
                host=args.host,
                port=args.port,
                token=args.token,
            )
            await server.start()
            print(f"Bridge server running on ws://{args.host}:{args.port}")
            print("Press Ctrl+C to stop.")
            try:
                await asyncio.Future()  # run forever
            except asyncio.CancelledError:
                pass
            finally:
                await server.stop()
            return 0

        try:
            return asyncio.run(_run_bridge())
        except KeyboardInterrupt:
            sys.stderr.write("\nBridge server stopped.\n")
            return 0

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

    # TUI mode: --tui flag launches the Textual full-widget-tree interface
    if getattr(args, "tui", False):
        from duh.ui import run_tui
        return run_tui(args)

    # No prompt and no SDK mode → interactive REPL
    from duh.cli.repl import run_repl
    try:
        return asyncio.run(run_repl(args))
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        return 0
