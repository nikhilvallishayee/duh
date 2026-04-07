"""Argument parser for D.U.H. CLI."""

from __future__ import annotations

import argparse

import duh


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="duh",
        description="D.U.H. -- D.U.H. is a Universal Harness. Provider-agnostic AI coding agent.",
    )
    parser.add_argument("--version", action="version", version=f"duh {duh.__version__}")
    parser.add_argument("-p", "--prompt", type=str, default=None,
                        help="Run in print mode: execute a single prompt and exit.")
    parser.add_argument("--model", type=str, default=None,
                        help="Model to use (default: auto-detect from provider).")
    parser.add_argument("--fallback-model", type=str, default=None,
                        help="Fallback model if primary is overloaded.")
    parser.add_argument("--provider", type=str, choices=["anthropic", "ollama", "openai"],
                        default=None,
                        help="LLM provider (default: auto-detect from API keys or Ollama).")
    parser.add_argument("--max-turns", type=int, default=10,
                        help="Maximum agentic turns (default: 10).")
    parser.add_argument("--output-format", type=str, choices=["text", "json", "stream-json"],
                        default="text", help="Output format (default: text).")
    parser.add_argument("--input-format", type=str, choices=["text", "stream-json"],
                        default="text", help="Input format (default: text).")
    parser.add_argument("--dangerously-skip-permissions", action="store_true",
                        default=False, help="Auto-approve all tool calls.")
    parser.add_argument("--permission-mode", type=str, default=None,
                        choices=["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"],
                        help="Permission mode (SDK compat). bypassPermissions = auto-approve.")
    parser.add_argument("--system-prompt", type=str, default=None,
                        help="Override the default system prompt.")
    parser.add_argument("--tool-choice", type=str, default=None,
                        help="Control tool use: auto (default), none (text only), any (force tool), or a tool name.")
    parser.add_argument("-c", "--continue", action="store_true", dest="continue_session",
                        default=False, help="Continue the most recent session.")
    parser.add_argument("--resume", type=str, default=None,
                        help="Resume a specific session by ID.")
    parser.add_argument("--debug", "-d", action="store_true", default=False,
                        help="Enable debug output (full event tracing to stderr).")
    parser.add_argument("--verbose", action="store_true", default=False,
                        help="Enable verbose output (used by SDK mode).")

    # SDK compat: accept (and ignore) unknown flags the SDK may pass
    parser.add_argument("--print", action="store_true", default=False,
                        help=argparse.SUPPRESS)  # SDK compat
    parser.add_argument("--allowedTools", type=str, default=None,
                        help=argparse.SUPPRESS)  # SDK compat
    parser.add_argument("--disallowedTools", type=str, default=None,
                        help=argparse.SUPPRESS)  # SDK compat
    parser.add_argument("--max-thinking-tokens", type=int, default=None,
                        help=argparse.SUPPRESS)  # SDK compat
    parser.add_argument("--include-partial-messages", action="store_true", default=False,
                        help=argparse.SUPPRESS)  # SDK compat
    parser.add_argument("--tools", type=str, default=None,
                        help=argparse.SUPPRESS)  # SDK compat
    parser.add_argument("--session-id", type=str, default=None,
                        help=argparse.SUPPRESS)  # SDK compat
    parser.add_argument("--settings", type=str, default=None,
                        help=argparse.SUPPRESS)  # SDK compat
    parser.add_argument("--setting-sources", type=str, default=None,
                        help=argparse.SUPPRESS)  # SDK compat
    parser.add_argument("--permission-prompt-tool", type=str, default=None,
                        help=argparse.SUPPRESS)  # SDK compat
    parser.add_argument("--mcp-config", type=str, default=None,
                        help=argparse.SUPPRESS)  # SDK compat

    subparsers = parser.add_subparsers(dest="command", required=False)
    subparsers.add_parser("doctor", help="Run diagnostics and health checks.")
    return parser
