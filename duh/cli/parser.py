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
    parser.add_argument("--debug", "-d", action="store_true", default=False,
                        help="Enable debug output (full event tracing to stderr).")
    parser.add_argument("--verbose", action="store_true", default=False,
                        help="Enable verbose output (used by SDK mode).")
    parser.add_argument("--brief", action="store_true", default=False,
                        help="Enable brief mode: shorter, more concise responses.")
    parser.add_argument("--log-json", action="store_true", default=False,
                        help="Enable structured JSON logging to ~/.config/duh/logs/duh.jsonl.")

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

    bridge_parser = subparsers.add_parser("bridge", help="Start the remote bridge server.")
    bridge_sub = bridge_parser.add_subparsers(dest="bridge_command", required=True)
    start_parser = bridge_sub.add_parser("start", help="Start the WebSocket bridge server.")
    start_parser.add_argument("--host", type=str, default="localhost",
                              help="Host to bind to (default: localhost).")
    start_parser.add_argument("--port", type=int, default=8765,
                              help="Port to bind to (default: 8765).")
    start_parser.add_argument("--token", type=str, default="",
                              help="Bearer token for authentication (empty = open).")

    return parser
