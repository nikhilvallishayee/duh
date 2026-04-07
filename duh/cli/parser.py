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
    parser.add_argument("--provider", type=str, choices=["anthropic", "ollama"],
                        default=None,
                        help="LLM provider (default: auto-detect from ANTHROPIC_API_KEY or Ollama).")
    parser.add_argument("--max-turns", type=int, default=10,
                        help="Maximum agentic turns (default: 10).")
    parser.add_argument("--output-format", type=str, choices=["text", "json"],
                        default="text", help="Output format (default: text).")
    parser.add_argument("--dangerously-skip-permissions", action="store_true",
                        default=False, help="Auto-approve all tool calls.")
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

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="Run diagnostics and health checks.")
    return parser
