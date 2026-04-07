"""CLI entry point for D.U.H.

Usage:
    duh -p "fix the bug"              # print mode (non-interactive)
    duh                                # interactive REPL (future)
    duh --version                      # show version
    duh --help                         # show help
    duh --model claude-opus-4-6        # specify model
    duh --max-turns 5                  # limit turns
    duh --output-format json           # JSON output
    duh --dangerously-skip-permissions # bypass approval
    duh doctor                         # diagnostics
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import duh
from duh.adapters.anthropic import AnthropicProvider
from duh.adapters.native_executor import NativeExecutor
from duh.adapters.approvers import AutoApprover, InteractiveApprover
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.tools.registry import get_all_tools


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are D.U.H. (Duh is a Universal Harness), an AI coding assistant. "
    "You have access to tools for reading, writing, editing files, running "
    "bash commands, globbing, and grepping. Use them to help the user with "
    "their coding tasks. Be concise and direct."
)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="duh",
        description="D.U.H. -- Duh is a Universal Harness. Provider-agnostic AI coding agent.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"duh {duh.__version__}",
    )
    parser.add_argument(
        "-p", "--prompt",
        type=str,
        default=None,
        help="Run in print mode: execute a single prompt and exit.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="claude-sonnet-4-6",
        help="Model to use (default: claude-sonnet-4-6).",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=10,
        help="Maximum number of agentic turns (default: 10).",
    )
    parser.add_argument(
        "--output-format",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        default=False,
        help="Skip permission prompts (auto-approve all tool calls).",
    )
    parser.add_argument(
        "--system-prompt",
        type=str,
        default=None,
        help="Override the default system prompt.",
    )

    # Subcommands
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="Run diagnostics and health checks.")

    return parser


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def run_doctor() -> int:
    """Run diagnostic checks and print results."""
    checks: list[tuple[str, bool, str]] = []

    # Python version
    py_version = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 12)
    checks.append((
        "Python version",
        py_ok,
        f"{py_version} {'(>= 3.12)' if py_ok else '(need >= 3.12)'}",
    ))

    # ANTHROPIC_API_KEY
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    key_ok = bool(api_key)
    checks.append((
        "ANTHROPIC_API_KEY",
        key_ok,
        "set" if key_ok else "not set",
    ))

    # Config directory
    config_dir = os.path.expanduser("~/.config/duh")
    config_exists = os.path.isdir(config_dir)
    checks.append((
        "Config directory",
        True,  # not a hard failure
        f"{config_dir} {'(exists)' if config_exists else '(not created yet)'}",
    ))

    # anthropic SDK
    try:
        import anthropic  # noqa: F401
        sdk_ok = True
        sdk_msg = "installed"
    except ImportError:
        sdk_ok = False
        sdk_msg = "not installed"
    checks.append(("anthropic SDK", sdk_ok, sdk_msg))

    # Available tools
    from duh.tools.registry import get_all_tools
    tools = get_all_tools()
    tool_names = [getattr(t, "name", "?") for t in tools]
    checks.append((
        "Tools available",
        len(tools) > 0,
        ", ".join(tool_names) if tool_names else "none",
    ))

    # Print results
    all_ok = True
    for name, ok, detail in checks:
        status = "ok" if ok else "FAIL"
        if not ok:
            all_ok = False
        sys.stdout.write(f"  [{status:>4}] {name}: {detail}\n")

    sys.stdout.write("\n")
    if all_ok:
        sys.stdout.write("All checks passed.\n")
    else:
        sys.stdout.write("Some checks failed. See above.\n")

    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# Print mode
# ---------------------------------------------------------------------------

async def run_print_mode(args: argparse.Namespace) -> int:
    """Execute a single prompt in print mode, stream output, and exit."""

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        sys.stderr.write("Error: ANTHROPIC_API_KEY environment variable is not set.\n")
        return 1

    tools = get_all_tools()
    provider = AnthropicProvider(api_key=api_key)
    executor = NativeExecutor(tools=tools, cwd=os.getcwd())

    if args.dangerously_skip_permissions:
        approver: Any = AutoApprover()
    else:
        approver = InteractiveApprover()

    system_prompt = args.system_prompt or SYSTEM_PROMPT

    deps = Deps(
        call_model=provider.stream,
        run_tool=executor.run,
        approve=approver.check,
    )

    config = EngineConfig(
        model=args.model,
        system_prompt=system_prompt,
        tools=tools,
        max_turns=args.max_turns,
    )

    engine = Engine(deps=deps, config=config)

    json_events: list[dict[str, Any]] = []

    async for event in engine.run(args.prompt):
        event_type = event.get("type", "")

        if args.output_format == "json":
            # Collect events for JSON output (skip non-serializable)
            serializable = _make_serializable(event)
            json_events.append(serializable)
        else:
            # Text streaming mode
            if event_type == "text_delta":
                sys.stdout.write(event.get("text", ""))
                sys.stdout.flush()
            elif event_type == "error":
                sys.stderr.write(f"Error: {event.get('error', 'unknown')}\n")

    if args.output_format == "json":
        sys.stdout.write(json.dumps(json_events, indent=2))
    else:
        print()  # final newline after streaming

    return 0


def _make_serializable(event: dict[str, Any]) -> dict[str, Any]:
    """Convert an event dict to a JSON-serializable form."""
    out: dict[str, Any] = {}
    for k, v in event.items():
        if hasattr(v, "__dataclass_fields__"):
            from dataclasses import asdict
            out[k] = asdict(v)
        elif isinstance(v, (str, int, float, bool, type(None), list, dict)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Subcommand: doctor
    if args.command == "doctor":
        return run_doctor()

    # Print mode
    if args.prompt is not None:
        return asyncio.run(run_print_mode(args))

    # No prompt and no subcommand → show help (REPL is future)
    parser.print_help()
    return 0
