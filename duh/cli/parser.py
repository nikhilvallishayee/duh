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
    parser.add_argument("--provider", type=str, choices=["anthropic", "litellm", "ollama", "openai"],
                        default=None,
                        help="LLM provider (default: auto-detect from API keys or Ollama).")
    parser.add_argument("--max-turns", type=int, default=100,
                        help="Maximum agentic turns (default: 100).")
    parser.add_argument("--max-cost", type=float, default=None,
                        help="Maximum cost in USD for this session.")
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="Maximum response tokens per turn.")
    parser.add_argument("--max-thinking-tokens", type=int, default=None,
                        help="Budget for extended thinking tokens.")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Sampling temperature (0.0-1.0).")
    parser.add_argument("--output-format", type=str, choices=["text", "json", "stream-json"],
                        default="text", help="Output format (default: text).")
    parser.add_argument("--output-style", choices=["default", "concise", "verbose"],
                        default="default", help="Output verbosity style (default: default).")
    parser.add_argument("--input-format", type=str, choices=["text", "stream-json"],
                        default="text", help="Input format (default: text).")
    parser.add_argument("--dangerously-skip-permissions", action="store_true",
                        default=False, help="Auto-approve all tool calls.")
    parser.add_argument("--approval-mode", type=str, default=None,
                        choices=["suggest", "auto-edit", "full-auto"],
                        help="Approval mode: suggest (reads auto-approved), "
                             "auto-edit (reads+writes auto-approved), "
                             "full-auto (all auto-approved).")
    parser.add_argument("--permission-mode", type=str, default=None,
                        choices=["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"],
                        help="Permission mode (SDK compat). bypassPermissions = auto-approve.")
    parser.add_argument("--system-prompt", type=str, default=None,
                        help="Override the default system prompt.")
    parser.add_argument("--system-prompt-file", type=str, default=None,
                        help="Load system prompt from a file.")
    parser.add_argument("--tool-choice", type=str, default=None,
                        help="Control tool use: auto (default), none (text only), any (force tool), or a tool name.")
    parser.add_argument("--allowedTools", type=str, default=None,
                        help="Comma-separated list of allowed tools.")
    parser.add_argument("--disallowedTools", type=str, default=None,
                        help="Comma-separated list of disallowed tools.")
    parser.add_argument("--add-dir", action="append", default=None,
                        help="Additional directories to include in context (can be repeated).")
    parser.add_argument("--mcp-config", type=str, default=None,
                        help="MCP server config (JSON string or file path).")
    parser.add_argument("-c", "--continue", action="store_true", dest="continue_session",
                        default=False, help="Continue the most recent session.")
    parser.add_argument("--resume", type=str, default=None,
                        help="Resume a specific session by ID.")
    parser.add_argument("--session-id", type=str, default=None,
                        help="Use a specific session ID.")
    parser.add_argument("--fork-session", action="store_true", default=False,
                        help="Fork from the resumed session into a new session.")
    parser.add_argument("--summarize", action="store_true", default=False,
                        help="Summarize older messages on resume (use with --continue or --resume).")
    parser.add_argument("--debug", "-d", action="store_true", default=False,
                        help="Enable debug output (full event tracing to stderr).")
    parser.add_argument("--verbose", action="store_true", default=False,
                        help="Enable verbose output (used by SDK mode).")
    parser.add_argument("--brief", action="store_true", default=False,
                        help="Enable brief mode: shorter, more concise responses.")
    parser.add_argument("--log-json", action="store_true", default=False,
                        help="Enable structured JSON logging to ~/.config/duh/logs/duh.jsonl.")
    parser.add_argument("--tui", action="store_true", default=False,
                        help="Launch the full Textual TUI (ADR-011 Tier 2) instead of the readline REPL.")
    parser.add_argument("--coordinator", action="store_true", default=False,
                        help="Run in coordinator mode — delegates all tasks to subagents.")
    parser.add_argument(
        "--i-understand-the-lethal-trifecta",
        action="store_true",
        default=False,
        help=(
            "Acknowledge the risk of running with READ_PRIVATE + READ_UNTRUSTED + "
            "NETWORK_EGRESS simultaneously (Simon Willison's exfiltration trifecta). "
            "Required when all three capabilities are enabled at once."
        ),
    )

    # SDK compat: accept flags the SDK may pass that we don't use
    parser.add_argument("--print", action="store_true", default=False,
                        help=argparse.SUPPRESS)
    parser.add_argument("--include-partial-messages", action="store_true", default=False,
                        help=argparse.SUPPRESS)
    parser.add_argument("--tools", type=str, default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--settings", type=str, default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--setting-sources", type=str, default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--permission-prompt-tool", type=str, default=None,
                        help=argparse.SUPPRESS)

    subparsers = parser.add_subparsers(dest="command", required=False)
    subparsers.add_parser("doctor", help="Run diagnostics and health checks.")

    _constitution = subparsers.add_parser("constitution",
        help="Print the full system prompt (constitution) for human review.")
    _constitution.add_argument("--agent-type", type=str, default="general",
        choices=["general", "coder", "researcher", "planner", "reviewer"],
        help="Show constitution for a specific agent type.")

    _security = subparsers.add_parser("security", help="Vulnerability monitoring (ADR-053)")
    _security.add_argument("security_args", nargs=argparse.REMAINDER)

    _audit = subparsers.add_parser("audit", help="Show recent audit log entries (ADR-072)")
    _audit.add_argument("-n", "--limit", type=int, default=20,
                        help="Number of entries to show (default: 20).")
    _audit.add_argument("--json", action="store_true", default=False, dest="audit_json",
                        help="Output as raw JSONL.")

    review_parser = subparsers.add_parser("review", help="Review a pull request")
    review_parser.add_argument("--pr", type=int, required=True, help="PR number to review")
    review_parser.add_argument("--repo", type=str, default=None,
                               help="Repository (owner/repo). Default: auto-detect from git remote.")

    batch_parser = subparsers.add_parser("batch", help="Process multiple prompts from a file")
    batch_parser.add_argument("file", type=str, help="Path to file with one prompt per line")
    batch_parser.add_argument("--model", type=str, default=None)
    batch_parser.add_argument("--max-turns", type=int, default=10)
    batch_parser.add_argument("--output-dir", type=str, default=None,
                              help="Write results to files in this directory")

    bridge_parser = subparsers.add_parser("bridge", help="Start the remote bridge server.")
    bridge_sub = bridge_parser.add_subparsers(dest="bridge_command", required=True)
    start_parser = bridge_sub.add_parser("start", help="Start the WebSocket bridge server.")
    start_parser.add_argument("--host", type=str, default="localhost",
                              help="Host to bind to (default: localhost).")
    start_parser.add_argument("--port", type=int, default=9120,
                              help="Port to bind to (default: 9120).")
    start_parser.add_argument("--token", type=str, default="",
                              help="Bearer token for authentication (empty = open).")

    return parser
