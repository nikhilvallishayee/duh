"""Print-mode runner for D.U.H. CLI."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

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

BRIEF_INSTRUCTION = (
    "Be extremely concise. Use short sentences. Skip explanations unless asked. "
    "Prefer code over prose. Maximum 3 sentences for non-code responses."
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
# Print mode
# ---------------------------------------------------------------------------

async def run_print_mode(args: argparse.Namespace) -> int:
    debug = args.debug
    if debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                            format="[%(levelname)s] %(name)s: %(message)s")

    # Resolve provider: explicit flag > model name hint > env detection > Ollama fallback
    provider_name = args.provider
    if not provider_name and args.model:
        # Infer provider from model name
        m = args.model.lower()
        if any(k in m for k in ("claude", "haiku", "sonnet", "opus")):
            provider_name = "anthropic"
        elif any(k in m for k in ("gpt", "o1", "o3", "davinci")):
            provider_name = "openai"
    if not provider_name:
        if os.environ.get("ANTHROPIC_API_KEY"):
            provider_name = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            provider_name = "openai"
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
            "  Option 2: export OPENAI_API_KEY=sk-...\n"
            "  Option 3: start Ollama (ollama serve)\n"
            "  Option 4: duh --provider ollama --model qwen2.5-coder:1.5b\n"
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
    elif provider_name == "openai":
        from duh.adapters.openai import OpenAIProvider
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            sys.stderr.write("Error: OPENAI_API_KEY not set.\n")
            return 1
        model = args.model or "gpt-4o"
        call_model = OpenAIProvider(api_key=api_key, model=model).stream
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

    # --- Load skills (ADR-017) ---
    from duh.kernel.skill import load_all_skills
    loaded_skills = load_all_skills(cwd)

    # --- Wire plugins (discover and merge tools) ---
    from duh.plugins import discover_plugins, PluginRegistry
    plugin_specs = discover_plugins()
    plugin_registry = PluginRegistry()
    for spec in plugin_specs:
        plugin_registry.load(spec)

    # --- Build deferred tools from plugin tools (ADR-018) ---
    from duh.tools.tool_search import DeferredTool
    deferred_tools: list[DeferredTool] = []
    for pt in plugin_registry.plugin_tools:
        if hasattr(pt, "input_schema") and hasattr(pt, "name"):
            deferred_tools.append(DeferredTool(
                name=pt.name,
                description=getattr(pt, "description", ""),
                input_schema=getattr(pt, "input_schema", {}),
                source="plugin",
            ))

    tools = list(get_all_tools(skills=loaded_skills, deferred_tools=deferred_tools))

    # --- Filter tools by --allowedTools / --disallowedTools ---
    allowed = getattr(args, "allowedTools", None)
    disallowed = getattr(args, "disallowedTools", None)
    if allowed:
        allowed_set = {t.strip() for t in allowed.split(",")}
        tools = [t for t in tools if getattr(t, "name", "") in allowed_set]
    if disallowed:
        disallowed_set = {t.strip() for t in disallowed.split(",")}
        tools = [t for t in tools if getattr(t, "name", "") not in disallowed_set]

    # --- Resolve system prompt (string > file > default) ---
    from duh.config import load_instructions
    instruction_list = load_instructions(cwd)
    base_prompt = args.system_prompt or SYSTEM_PROMPT
    if not args.system_prompt and getattr(args, "system_prompt_file", None):
        try:
            base_prompt = open(args.system_prompt_file, encoding="utf-8").read()
        except Exception as e:
            sys.stderr.write(f"Warning: Could not read system prompt file: {e}\n")
    system_prompt_parts = [base_prompt]
    if getattr(args, "brief", False):
        system_prompt_parts.append(BRIEF_INSTRUCTION)
    if instruction_list:
        system_prompt_parts.extend(instruction_list if isinstance(instruction_list, list) else [instruction_list])

    # --- Wire per-project memory ---
    from duh.adapters.memory_store import FileMemoryStore
    from duh.kernel.memory import build_memory_prompt
    memory_store = FileMemoryStore(cwd=cwd)
    memory_prompt = build_memory_prompt(memory_store)
    if memory_prompt:
        system_prompt_parts.append(memory_prompt)

    # --- Inject environment context (cwd, platform, shell) ---
    import platform as _platform
    _shell = os.environ.get("SHELL", "unknown").rsplit("/", 1)[-1]
    system_prompt_parts.append(
        f"<environment>\n"
        f"cwd: {cwd}\n"
        f"platform: {_platform.system().lower()}\n"
        f"shell: {_shell}\n"
        f"python: {_platform.python_version()}\n"
        f"</environment>"
    )

    # --- Inject git context ---
    from duh.kernel.git_context import get_git_context, get_git_warnings
    git_ctx = get_git_context(cwd)
    if git_ctx:
        system_prompt_parts.append(git_ctx)

    # --- Print git safety warnings ---
    for warning in get_git_warnings(cwd):
        sys.stderr.write(f"\033[33mWARNING: {warning}\033[0m\n")

    # --- Inject skill descriptions into system prompt (ADR-017) ---
    if loaded_skills:
        skill_lines = [
            "\nAvailable skills (invoke via the Skill tool):"
        ]
        for s in loaded_skills:
            hint = f" ({s.argument_hint})" if s.argument_hint else ""
            skill_lines.append(f"- {s.name}: {s.description}{hint}")
        system_prompt_parts.append("\n".join(skill_lines))

    # --- Inject template descriptions into system prompt ---
    from duh.kernel.templates import load_all_templates
    loaded_templates = load_all_templates(cwd)
    if loaded_templates:
        tmpl_lines = ["\nAvailable prompt templates (invoke via /template):"]
        for t in loaded_templates:
            tmpl_lines.append(f"- {t.name}: {t.description}")
        system_prompt_parts.append("\n".join(tmpl_lines))

    # --- Inject deferred tools into system prompt (ADR-018) ---
    if deferred_tools:
        dt_lines = [
            "\n<deferred-tools>",
            "The following tools are available but their schemas are not yet loaded.",
            "Use the ToolSearch tool to load a tool's full schema before calling it.",
            "",
        ]
        for dt in deferred_tools:
            dt_lines.append(f"- {dt.name}: {dt.description}")
        dt_lines.append("</deferred-tools>")
        system_prompt_parts.append("\n".join(dt_lines))

    # --- Load config once (MCP + hooks + settings) ---
    from duh.config import load_config
    mcp_executor = None
    from duh.hooks import HookRegistry
    hook_registry = HookRegistry()
    try:
        app_config = load_config(cwd=cwd)
        # --mcp-config CLI flag overrides project config
        cli_mcp = getattr(args, "mcp_config", None)
        if cli_mcp:
            import json as _json
            try:
                if cli_mcp.strip().startswith("{"):
                    mcp_data = _json.loads(cli_mcp)
                else:
                    mcp_data = _json.loads(open(cli_mcp).read())
                app_config.mcp_servers = mcp_data
            except Exception:
                logger.debug("Failed to parse --mcp-config", exc_info=True)
        if app_config.mcp_servers:
            from duh.adapters.mcp_executor import MCPExecutor
            mcp_executor = MCPExecutor.from_config(app_config.mcp_servers)
        if app_config.hooks:
            hook_registry = HookRegistry.from_config(app_config.hooks)
    except Exception:
        logger.debug("Config loading failed, using defaults", exc_info=True)

    # --- Connect to MCP servers and wrap tools ---
    if mcp_executor:
        try:
            discovered = await mcp_executor.connect_all()
            from duh.tools.mcp_tool import MCPToolWrapper
            for server_name, mcp_tools in discovered.items():
                for info in mcp_tools:
                    wrapper = MCPToolWrapper(info=info, executor=mcp_executor)
                    tools.append(wrapper)
                    if debug:
                        logger.debug("MCP tool registered: %s", wrapper.name)
            total_mcp = sum(len(t) for t in discovered.values())
            if total_mcp:
                logger.info("Loaded %d MCP tools from %d servers",
                            total_mcp, len(discovered))
        except Exception:
            logger.debug("MCP connection failed, continuing without MCP tools",
                         exc_info=True)

    # --- Wire compactor ---
    from duh.adapters.simple_compactor import SimpleCompactor
    compactor = SimpleCompactor()

    # --- Wire session store ---
    from duh.adapters.file_store import FileStore
    store = FileStore()

    # --- Build executor and approver ---
    executor = NativeExecutor(tools=tools, cwd=cwd)
    skip_perms = args.dangerously_skip_permissions or getattr(args, "permission_mode", None) in ("bypassPermissions", "dontAsk")
    approver: Any = AutoApprover() if skip_perms else InteractiveApprover()

    deps = Deps(
        call_model=call_model,
        run_tool=executor.run,
        approve=approver.check,
        compact=compactor.compact,
    )
    # Resolve max_cost: CLI flag > env var > None
    max_cost = getattr(args, "max_cost", None)
    if max_cost is None:
        env_cost = os.environ.get("DUH_MAX_COST")
        if env_cost is not None:
            try:
                max_cost = float(env_cost)
            except (ValueError, TypeError):
                pass

    # Build thinking config from --max-thinking-tokens
    thinking = None
    mtt = getattr(args, "max_thinking_tokens", None)
    if mtt is not None:
        thinking = {"type": "enabled", "budget_tokens": mtt} if mtt > 0 else {"type": "disabled"}

    engine_config = EngineConfig(
        model=model,
        fallback_model=getattr(args, "fallback_model", None),
        system_prompt="\n\n".join(system_prompt_parts),
        tools=tools,
        max_turns=args.max_turns,
        max_cost=max_cost,
        tool_choice=args.tool_choice,
        thinking=thinking,
    )
    # --- Wire structured JSON logger ---
    structured_logger = None
    if getattr(args, "log_json", False) or os.environ.get("DUH_LOG_JSON", "") == "1":
        from duh.adapters.structured_logging import StructuredLogger
        structured_logger = StructuredLogger()

    engine = Engine(deps=deps, config=engine_config, session_store=store,
                    structured_logger=structured_logger)

    # --- Override session ID if --session-id provided ---
    session_id = getattr(args, "session_id", None)
    if session_id:
        engine._session_id = session_id

    # --- Resume session if --continue, --resume, or --session-id ---
    should_resume = getattr(args, "continue_session", False) or args.resume or session_id
    if should_resume:
        try:
            resume_id = args.resume or session_id
            if resume_id:
                prev = await store.load(resume_id)
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
        from duh.hooks import HookEvent, execute_hooks
        await execute_hooks(hook_registry, HookEvent.SESSION_START, {"session_id": engine.session_id})
    except Exception:
        logger.debug("Session start hooks failed", exc_info=True)

    json_events: list[dict[str, Any]] = []
    had_output = False
    had_error = False

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
                had_error = True
            elif event_type == "assistant":
                msg = event.get("message")
                if isinstance(msg, Message) and msg.metadata.get("is_error"):
                    had_error = True
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

    # --- Session end hooks ---
    from duh.hooks import HookEvent, execute_hooks
    try:
        await execute_hooks(hook_registry, HookEvent.SESSION_END, {"session_id": engine.session_id})
    except Exception:
        logger.debug("Session end hooks failed", exc_info=True)

    # --- Close structured logger ---
    if structured_logger:
        structured_logger.session_end(
            turns=engine.turn_count,
            input_tokens=engine.total_input_tokens,
            output_tokens=engine.total_output_tokens,
        )
        structured_logger.close()

    # --- Disconnect MCP ---
    if mcp_executor:
        try:
            await mcp_executor.disconnect_all()
        except Exception:
            logger.debug("MCP disconnect failed", exc_info=True)

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
