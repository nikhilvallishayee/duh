"""CLI entry point for D.U.H.

Usage:
    duh -p "fix the bug"              # print mode
    duh --version                      # show version
    duh doctor                         # diagnostics
    duh -p "prompt" --debug            # full event tracing
    duh -p "prompt" --model opus       # specify model
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

import duh
from duh.adapters.anthropic import AnthropicProvider
from duh.adapters.native_executor import NativeExecutor
from duh.adapters.approvers import AutoApprover, InteractiveApprover
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.tools.registry import get_all_tools

logger = logging.getLogger("duh")

SYSTEM_PROMPT = (
    "You are D.U.H. (D.U.H. is a Universal Harness), an AI coding assistant. "
    "You have access to tools for reading, writing, editing files, running "
    "bash commands, globbing, and grepping. Use them to help the user with "
    "their coding tasks. Be concise and direct."
)

# ---------------------------------------------------------------------------
# Error interpretation — translate API errors into human-friendly messages
# ---------------------------------------------------------------------------

_ERROR_HINTS: dict[str, str] = {
    "credit balance is too low": (
        "Your API key has no credits. Go to console.anthropic.com "
        "→ Plans & Billing to add credits."
    ),
    "invalid x-api-key": (
        "Your API key is invalid. Check ANTHROPIC_API_KEY is set correctly."
    ),
    "authentication_error": (
        "Authentication failed. Verify your ANTHROPIC_API_KEY."
    ),
    "rate_limit": (
        "Rate limited. Wait a moment and try again."
    ),
    "overloaded": (
        "The API is overloaded. Try again in a few seconds, "
        "or use --model claude-haiku-4-5-20251001 for lower latency."
    ),
    "prompt is too long": (
        "Your conversation is too long for the model's context window. "
        "Try a shorter prompt or start a new session."
    ),
    "Could not resolve authentication": (
        "No API key found. Set ANTHROPIC_API_KEY:\n"
        "  export ANTHROPIC_API_KEY=sk-ant-..."
    ),
}


def _interpret_error(error_text: str) -> str:
    """Translate raw API errors into actionable user messages."""
    for pattern, hint in _ERROR_HINTS.items():
        if pattern.lower() in error_text.lower():
            return hint
    return error_text


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def run_doctor() -> int:
    checks: list[tuple[str, bool, str]] = []

    py_version = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 12)
    checks.append(("Python version", py_ok,
                    f"{py_version} {'(>= 3.12)' if py_ok else '(need >= 3.12)'}"))

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    checks.append(("ANTHROPIC_API_KEY", bool(api_key), "set" if api_key else "not set"))

    config_dir = os.path.expanduser("~/.config/duh")
    checks.append(("Config directory", True,
                    f"{config_dir} {'(exists)' if os.path.isdir(config_dir) else '(not created yet)'}"))

    try:
        import anthropic  # noqa: F401
        checks.append(("anthropic SDK", True, "installed"))
    except ImportError:
        checks.append(("anthropic SDK", False, "not installed (pip install anthropic)"))

    tools = get_all_tools()
    checks.append(("Tools available", len(tools) > 0,
                    ", ".join(getattr(t, "name", "?") for t in tools)))

    all_ok = True
    for name, ok, detail in checks:
        status = "ok" if ok else "FAIL"
        if not ok:
            all_ok = False
        sys.stdout.write(f"  [{status:>4}] {name}: {detail}\n")

    sys.stdout.write(f"\n{'All checks passed.' if all_ok else 'Some checks failed.'}\n")
    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# Print mode
# ---------------------------------------------------------------------------

async def run_print_mode(args: argparse.Namespace) -> int:
    debug = args.debug
    if debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                            format="[%(levelname)s] %(name)s: %(message)s")

    # Resolve provider: explicit flag > env detection > Ollama fallback
    provider_name = args.provider
    if not provider_name:
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider_name = "anthropic"
        else:
            # Try Ollama as fallback
            try:
                import httpx
                r = httpx.get("http://localhost:11434/api/tags", timeout=2)
                if r.status_code == 200:
                    provider_name = "ollama"
            except Exception:
                pass

    if not provider_name:
        sys.stderr.write(
            "Error: No provider available.\n"
            "  Option 1: export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  Option 2: start Ollama (ollama serve)\n"
            "  Option 3: duh --provider ollama --model qwen2.5-coder:1.5b\n"
        )
        return 1

    # Build provider
    if provider_name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            sys.stderr.write("Error: ANTHROPIC_API_KEY not set.\n")
            return 1
        model = args.model or "claude-sonnet-4-6"
        call_model = AnthropicProvider(api_key=api_key, model=model).stream
    elif provider_name == "ollama":
        from duh.adapters.ollama import OllamaProvider
        model = args.model or "qwen2.5-coder:1.5b"
        call_model = OllamaProvider(model=model).stream
    else:
        sys.stderr.write(f"Error: Unknown provider: {provider_name}\n")
        return 1

    if debug:
        sys.stderr.write(f"[DEBUG] provider={provider_name} model={model}\n")

    cwd = os.getcwd()
    tools = list(get_all_tools())

    # --- Wire DUH.md / AGENTS.md instructions ---
    from duh.config import load_instructions
    instruction_list = load_instructions(cwd)
    system_prompt_parts = [args.system_prompt or SYSTEM_PROMPT]
    if instruction_list:
        system_prompt_parts.extend(instruction_list if isinstance(instruction_list, list) else [instruction_list])

    # --- Wire plugins (discover and merge tools) ---
    from duh.plugins import discover_plugins, PluginRegistry
    plugin_specs = discover_plugins()
    plugin_registry = PluginRegistry()
    for spec in plugin_specs:
        plugin_registry.load(spec)
    tools.extend(plugin_registry.plugin_tools)

    # --- Wire MCP (connect servers, merge tools) ---
    mcp_executor = None
    from duh.config import Config
    try:
        config = Config.load(cwd)
        if config.mcp_servers:
            from duh.adapters.mcp_executor import MCPExecutor
            mcp_executor = MCPExecutor.from_config(config.mcp_servers)
            # MCP tools will be discovered after connect
    except Exception:
        pass  # MCP is optional

    # --- Wire hooks ---
    from duh.hooks import HookRegistry
    hook_registry = HookRegistry()
    try:
        config = Config.load(cwd)
        if config.hooks:
            hook_registry = HookRegistry.from_config(config.hooks)
    except Exception:
        pass

    # --- Wire compactor ---
    from duh.adapters.simple_compactor import SimpleCompactor
    compactor = SimpleCompactor()

    # --- Wire session store ---
    from duh.adapters.file_store import FileStore
    store = FileStore()

    # --- Build executor and approver ---
    executor = NativeExecutor(tools=tools, cwd=cwd)
    approver: Any = AutoApprover() if args.dangerously_skip_permissions else InteractiveApprover()

    deps = Deps(
        call_model=call_model,
        run_tool=executor.run,
        approve=approver.check,
        compact=compactor.compact,
    )
    config = EngineConfig(
        model=model,
        system_prompt="\n\n".join(system_prompt_parts),
        tools=tools,
        max_turns=args.max_turns,
        tool_choice=args.tool_choice,
    )
    engine = Engine(deps=deps, config=config)

    # --- Resume session if --continue or --resume ---
    if getattr(args, "continue_session", False) or args.resume:
        try:
            if args.resume:
                prev = await store.load(args.resume)
            else:
                sessions = await store.list_sessions()
                if sessions:
                    latest = sorted(sessions, key=lambda s: s.get("modified", ""), reverse=True)[0]
                    prev = await store.load(latest.get("session_id") or latest.get("id", ""))
                else:
                    prev = None
            if prev:
                from duh.kernel.messages import Message as Msg
                for m in prev:
                    role = m.get("role", "user") if isinstance(m, dict) else getattr(m, "role", "user")
                    content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
                    engine._messages.append(Msg(role=role, content=content))
                if debug:
                    logger.debug("resumed %d messages", len(prev))
            elif debug:
                logger.debug("no session to resume")
        except Exception as e:
            import traceback
            logger.debug("resume failed: %s", e)
            if debug:
                traceback.print_exc(file=sys.stderr)

    # --- Session start hooks ---
    try:
        from duh.hooks import HookEvent
        await hook_registry.execute(HookEvent.SESSION_START, {"session_id": engine.session_id})
    except Exception:
        pass

    json_events: list[dict[str, Any]] = []
    had_output = False
    had_error = False

    async for event in engine.run(args.prompt):
        event_type = event.get("type", "")

        if debug:
            logger.debug("event: %s", _summarize_event(event))

        if args.output_format == "json":
            json_events.append(_make_serializable(event))
        else:
            if event_type == "text_delta":
                sys.stdout.write(event.get("text", ""))
                sys.stdout.flush()
                had_output = True

            elif event_type == "thinking_delta":
                if debug:
                    sys.stderr.write(f"\033[2;3m{event.get('text', '')}\033[0m")
                    sys.stderr.flush()

            elif event_type == "tool_use":
                name = event.get("name", "?")
                inp = event.get("input", {})
                summary = ", ".join(f"{k}={v!r}" for k, v in list(inp.items())[:2])
                sys.stderr.write(f"  \033[33m> {name}\033[0m({summary})\n")
                sys.stderr.flush()

            elif event_type == "tool_result":
                if event.get("is_error"):
                    sys.stderr.write(f"  \033[31m! {event.get('output', '')[:200]}\033[0m\n")
                elif debug:
                    sys.stderr.write(f"  \033[32m< {str(event.get('output', ''))[:100]}\033[0m\n")

            elif event_type == "assistant":
                # Check for API errors in the assistant message
                msg = event.get("message")
                if isinstance(msg, Message) and msg.metadata.get("is_error"):
                    error_text = msg.text
                    hint = _interpret_error(error_text)
                    sys.stderr.write(f"\n\033[31mError: {hint}\033[0m\n")
                    had_error = True

            elif event_type == "error":
                hint = _interpret_error(event.get("error", "unknown"))
                sys.stderr.write(f"\n\033[31mError: {hint}\033[0m\n")
                had_error = True

            elif event_type == "done":
                if debug:
                    logger.debug("done: turns=%s reason=%s",
                                 event.get("turns"), event.get("stop_reason"))

    if args.output_format == "json":
        sys.stdout.write(json.dumps(json_events, indent=2, default=str))
        sys.stdout.write("\n")
    elif had_output:
        print()  # final newline after streaming

    # --- Save session ---
    try:
        await store.save(engine.session_id, engine.messages)
        if debug:
            logger.debug("session saved: %s (%d messages)", engine.session_id, len(engine.messages))
    except Exception as e:
        if debug:
            logger.debug("session save failed: %s", e)

    # --- Session end hooks ---
    from duh.hooks import HookEvent
    try:
        await hook_registry.execute(HookEvent.SESSION_END, {"session_id": engine.session_id})
    except Exception:
        pass

    # --- Disconnect MCP ---
    if mcp_executor:
        try:
            await mcp_executor.disconnect_all()
        except Exception:
            pass

    return 1 if had_error else 0


def _summarize_event(event: dict[str, Any]) -> str:
    """One-line summary of an event for debug output."""
    t = event.get("type", "?")
    if t == "text_delta":
        return f"text_delta: {event.get('text', '')[:40]!r}"
    if t == "tool_use":
        return f"tool_use: {event.get('name', '?')}({event.get('input', {})})"
    if t == "tool_result":
        return f"tool_result: err={event.get('is_error')} out={str(event.get('output', ''))[:60]!r}"
    if t == "assistant":
        msg = event.get("message")
        text = msg.text[:60] if isinstance(msg, Message) else "?"
        return f"assistant: {text!r}"
    if t == "error":
        return f"error: {event.get('error', '')[:80]}"
    return f"{t}: {str(event)[:80]}"


def _make_serializable(event: dict[str, Any]) -> dict[str, Any]:
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
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return run_doctor()

    if args.prompt is not None:
        return asyncio.run(run_print_mode(args))

    parser.print_help()
    return 0
