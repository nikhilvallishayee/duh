"""Print-mode runner for D.U.H. CLI."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from duh.cli import exit_codes
from duh.kernel.untrusted import TaintSource, UntrustedStr


def wrap_prompt_flag(value: str) -> UntrustedStr:
    """Tag the -p/--prompt CLI flag value as USER_INPUT."""
    if isinstance(value, UntrustedStr):
        return value
    return UntrustedStr(value, TaintSource.USER_INPUT)

from duh.adapters.anthropic import AnthropicProvider  # noqa: F401 (test/mocking compatibility)
from duh.adapters.native_executor import NativeExecutor
from duh.adapters.approvers import AutoApprover, InteractiveApprover
from duh.kernel.permission_cache import SessionPermissionCache
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.tools.registry import get_all_tools
from duh.providers.registry import (
    build_model_backend,
    get_anthropic_api_key,
    resolve_provider_name,
)

logger = logging.getLogger("duh")

from duh.constitution import build_system_prompt as _build_constitution, BRIEF, ConstitutionConfig

# Legacy aliases — kept for backward compat with tests that import these
SYSTEM_PROMPT = _build_constitution()
BRIEF_INSTRUCTION = BRIEF

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


# Per-provider env-var documentation surfaced to first-time users when no
# provider can be detected.  Keep the labels short -- the error message is
# already verbose.
_PROVIDER_ENV_HELP: tuple[tuple[str, str], ...] = (
    ("Anthropic", "ANTHROPIC_API_KEY=sk-ant-..."),
    ("OpenAI",    "OPENAI_API_KEY=sk-..."),
    ("Google",    "GEMINI_API_KEY=..."),
    ("Ollama",    "(start a local server: ollama serve)"),
)


def _no_provider_message() -> str:
    """Build the first-run-friendly 'No provider available' error text.

    Includes:
      * a `duh doctor` suggestion for first-time users,
      * env-var names per provider,
      * a link to the getting-started docs.
    """
    lines = [
        "Error: No provider available.\n",
        "  First time? Try `duh doctor` to diagnose your setup.\n",
        "\n",
        "  Set one of the following environment variables:\n",
    ]
    for label, hint in _PROVIDER_ENV_HELP:
        lines.append(f"    {label:<10s} export {hint}\n")
    lines.append(
        "\n  Docs: https://nikhilvallishayee.github.io/duh/site/getting-started.html\n"
    )
    return "".join(lines)


# ---------------------------------------------------------------------------
# Print mode
# ---------------------------------------------------------------------------

async def run_print_mode(args: argparse.Namespace) -> int:
    debug = args.debug
    if debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                            format="[%(levelname)s] %(name)s: %(message)s")

    # --- Build the shared session (provider, tools, MCP, prompt, engine, resume) ---
    # Duplication with the REPL runner was extracted into SessionBuilder
    # (issue #18 / CQ-4).  The print-mode-specific options live here; the
    # builder does the heavy lifting.
    from duh.cli.session_builder import (
        ProviderResolutionError,
        SessionBuilder,
        SessionBuilderOptions,
        _BuilderPatchTargets,
    )

    options = SessionBuilderOptions(
        include_skills_in_tools=True,
        include_deferred_tools=True,
        include_memory_prompt=True,
        include_env_block=True,
        include_templates_hint=True,
        include_model_context_block=False,
        honour_tool_filters=True,
        approver_mode="print_mode",
        wire_hook_registry_in_deps=False,
        wire_audit_logger_in_deps=True,
        honour_tool_choice=True,
        honour_thinking=True,
        allow_session_id_override=True,
        log_skip_perms_warning=True,
        default_system_prompt=SYSTEM_PROMPT,
        brief_instruction=BRIEF_INSTRUCTION,
    )
    # Keep the legacy runner-level patch targets stable for unit tests that
    # do things like ``patch("duh.cli.runner.Engine")``.
    patch_targets = _BuilderPatchTargets(
        engine_cls=Engine,
        engine_config_cls=EngineConfig,
        deps_cls=Deps,
        native_executor_cls=NativeExecutor,
        get_all_tools_fn=get_all_tools,
    )

    builder = SessionBuilder(
        args, options, cwd=os.getcwd(), debug=debug, patch_targets=patch_targets,
    )
    try:
        build = await builder.build(
            provider_factories={
                "anthropic": lambda m: AnthropicProvider(
                    api_key=get_anthropic_api_key(), model=m
                ),
            }
        )
    except ProviderResolutionError as exc:
        if exc.provider_name is None:
            # "No provider available" — keep the enriched first-run message.
            sys.stderr.write(_no_provider_message())
        else:
            # Backend build error (unknown provider, missing API key, etc).
            sys.stderr.write(f"Error: {exc.message}\n")
        return exit_codes.PROVIDER_ERROR

    engine = build.engine
    deps = build.deps
    model = build.model
    mcp_executor = build.mcp_executor
    hook_registry = build.hook_registry
    structured_logger = build.structured_logger

    # --- Session start hooks ---
    try:
        from duh.hooks import HookEvent, execute_hooks
        await execute_hooks(hook_registry, HookEvent.SESSION_START, {"session_id": engine.session_id})
    except Exception:
        logger.debug("Session start hooks failed", exc_info=True)

    json_events: list[dict[str, Any]] = []
    had_output = False
    exit_code = exit_codes.SUCCESS
    last_stop_reason: str | None = None

    async for event in engine.run(args.prompt):
        event_type = event.get("type", "")

        if debug:
            logger.debug("event: %s", _summarize_event(event))

        if args.output_format == "json":
            json_events.append(_make_serializable(event))
        elif args.output_format == "stream-json":
            from duh.cli.ndjson import ndjson_write
            ndjson_write(_make_serializable(event))
            if event_type == "text_delta":
                had_output = True
            elif event_type == "error":
                error_text = event.get("error", "")
                exit_code = exit_codes.classify_error(error_text)
            elif event_type == "assistant":
                msg = event.get("message")
                if isinstance(msg, Message) and msg.metadata.get("is_error"):
                    exit_code = exit_codes.classify_error(msg.text)
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
                    output_text = event.get("output", "")
                    sys.stderr.write(f"  \033[31m! {output_text[:200]}\033[0m\n")
                    # Track permission denials for NEEDS_HUMAN
                    if "denied" in output_text.lower():
                        exit_code = exit_codes.NEEDS_HUMAN
                elif debug:
                    sys.stderr.write(f"  \033[32m< {str(event.get('output', ''))[:100]}\033[0m\n")

            elif event_type == "assistant":
                # Check for API errors in the assistant message
                msg = event.get("message")
                if isinstance(msg, Message) and msg.metadata.get("is_error"):
                    error_text = msg.text
                    hint = _interpret_error(error_text)
                    sys.stderr.write(f"\n\033[31mError: {hint}\033[0m\n")
                    exit_code = exit_codes.classify_error(error_text)

            elif event_type == "error":
                error_text = event.get("error", "unknown")
                hint = _interpret_error(error_text)
                sys.stderr.write(f"\n\033[31mError: {hint}\033[0m\n")
                exit_code = exit_codes.classify_error(error_text)

            elif event_type == "done":
                last_stop_reason = event.get("stop_reason")
                if debug:
                    logger.debug("done: turns=%s reason=%s",
                                 event.get("turns"), last_stop_reason)

        # budget_exceeded is emitted as a separate event by the engine
        if event_type == "budget_exceeded":
            exit_code = exit_codes.BUDGET_EXCEEDED

    # --- Map stop_reason to exit code (only if no error already recorded) ---
    if exit_code == exit_codes.SUCCESS and last_stop_reason:
        if last_stop_reason == "max_turns":
            exit_code = exit_codes.TIMEOUT

    if args.output_format == "json":
        sys.stdout.write(json.dumps(json_events, indent=2, default=str))
        sys.stdout.write("\n")
    elif had_output:
        print()  # final newline after streaming

    # --- Session end hooks ---
    from duh.hooks import HookEvent, execute_hooks
    try:
        await execute_hooks(hook_registry, HookEvent.SESSION_END, {"session_id": engine.session_id})
    except Exception:
        logger.debug("Session end hooks failed", exc_info=True)

    # --- Close structured logger + disconnect MCP ---
    build.close_structured_logger()
    await build.teardown_mcp()

    return exit_code


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
