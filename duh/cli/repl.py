"""Interactive REPL for D.U.H.

Provides a readline-based interactive session with slash commands,
streaming text output, and tool use indicators.

When the ``rich`` package is installed, output is rendered with Rich:
- Markdown responses get proper formatting (headers, code blocks, etc.)
- Tool calls, results, and errors use Rich Panels / styled text
- Thinking blocks are shown in dim italic
- A status bar shows model name and turn count

If ``rich`` is not installed the REPL falls back to plain ANSI escapes
(the original behaviour).

Slash commands:
    /help     — show available commands
    /model    — show or change the current model
    /connect  — connect provider auth (OpenAI API key / ChatGPT subscription)
    /models   — list available models for current provider
    /brief    — toggle brief mode (shorter responses)
    /cost     — show session cost estimate
    /status   — show session status (turns, messages, model)
    /context  — show context window token breakdown
    /changes  — show files touched in this session
    /tasks    — show task checklist
    /jobs     — list background jobs (/jobs <id> for result)
    /search   — search messages in the current session
    /plan     — plan mode (/plan <desc>, /plan show, /plan clear)
    /pr       — GitHub PRs (/pr list, /pr view <n>, /pr diff <n>, /pr checks <n>)
    /undo     — undo the last file modification (Write or Edit)
    /health   — run provider and MCP health checks
    /clear    — clear conversation history
    /memory   — memory facts (list, search, show, delete)
    /compact  — compact conversation (summarize older messages)
    /exit     — exit the REPL (also Ctrl-D)
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import readline
import sys
from typing import Any

from duh.kernel.confirmation import ConfirmationMinter
from duh.kernel.untrusted import TaintSource, UntrustedStr


def _mint_continue_token(
    minter: ConfirmationMinter, session_id: str, tool: str, input_obj: dict
) -> str:
    """Mint a confirmation token when the user types /continue."""
    return minter.mint(session_id, tool, input_obj)


def _wrap_user_input(raw: str) -> UntrustedStr:
    """Tag raw REPL input as USER_INPUT taint-source."""
    if isinstance(raw, UntrustedStr):
        return raw
    return UntrustedStr(raw, TaintSource.USER_INPUT)

from duh.adapters.anthropic import AnthropicProvider  # noqa: F401 (test/mocking compatibility)
from duh.adapters.native_executor import NativeExecutor
from duh.adapters.approvers import ApprovalMode, AutoApprover, InteractiveApprover, TieredApprover
from duh.kernel.permission_cache import SessionPermissionCache
from duh.cli.runner import BRIEF_INSTRUCTION, SYSTEM_PROMPT, _interpret_error
from duh.hooks import HookEvent, HookRegistry, execute_hooks
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.kernel.plan_mode import PlanMode
from duh.kernel.query_guard import QueryGuard
from duh.kernel.model_caps import model_context_block, rebuild_system_prompt
from duh.tools.registry import get_all_tools
from duh.auth.openai_chatgpt import (
    connect_openai_api_key,
    connect_openai_chatgpt_subscription,
    has_openai_chatgpt_oauth,
)
from duh.auth.anthropic import connect_anthropic_api_key
from duh.providers.registry import (
    available_models_for_provider,
    build_model_backend,
    connected_providers,
    has_anthropic_available,
    has_openai_available,
    infer_provider_from_model,
    resolve_openai_auth_mode,
    resolve_provider_name,
)

logger = logging.getLogger("duh")

# ---------------------------------------------------------------------------
# Renderers live in duh.cli.repl_renderers (issue #26 extraction).
#
# We re-export the public symbols under their legacy names so existing tests
# and external callers that import ``duh.cli.repl._PlainRenderer`` /
# ``_RichRenderer`` / ``_HAS_RICH`` / ``PROMPT`` continue to work unchanged.
# ---------------------------------------------------------------------------

from duh.cli.repl_renderers import (
    HAS_RICH as _HAS_RICH,
    PROMPT,
    PlainRenderer as _PlainRenderer,
    RichRenderer as _RichRenderer,
)


def _make_renderer(
    debug: bool = False,
    output_style: Any = None,
) -> _PlainRenderer | Any:
    """Return a Rich renderer if available, else a plain one.

    ``output_style`` is passed through to :class:`RichRenderer` so the unified
    :class:`OutputTruncationPolicy` (ADR-073) picks the right thresholds.
    """
    if _HAS_RICH:
        if output_style is None:
            return _RichRenderer(debug=debug)
        return _RichRenderer(debug=debug, output_style=output_style)
    return _PlainRenderer(debug=debug)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

SLASH_COMMANDS = {
    "/help": "Show available commands",
    "/model": "Show or set model (/model <name>)",
    "/connect": "Connect provider auth (/connect openai|anthropic [...])",
    "/models": "List models for current provider (/models, /models use <name>)",
    "/cost": "Show estimated session cost",
    "/status": "Show session status",
    "/context": "Show context window token breakdown",
    "/changes": "Show files touched in this session (+ git diff --stat)",
    "/git": "Show git branch, status, and recent commits",
    "/tasks": "Show task checklist",
    "/brief": "Toggle brief mode (/brief on, /brief off, /brief)",
    "/search": "Search session messages (/search <query>)",
    "/template": "Prompt templates (/template list | use <name> | <name> <prompt>)",
    "/plan": "Plan mode (/plan <desc>, /plan show, /plan clear)",
    "/pr": "GitHub PRs (/pr list, /pr view <n>, /pr diff <n>, /pr checks <n>)",
    "/undo": "Undo the last file modification (Write or Edit)",
    "/jobs": "Background jobs (/jobs to list, /jobs <id> for result)",
    "/health": "Run provider and MCP health checks",
    "/clear": "Clear conversation history",
    "/compact": "Compact older messages",
    "/compact-stats": "Show compaction analytics for this session",
    "/snapshot": "Ghost snapshot (/snapshot, /snapshot apply, /snapshot discard)",
    "/attach": "Attach a file to the next message (/attach path/to/file)",
    "/memory": "Memory facts (/memory list|search <q>|show <key>|delete <key>|gc)",
    "/sessions": "List sessions for this project",
    "/audit": "Show recent audit log entries (/audit [N])",
    "/theme": "Switch TUI theme (/theme, /theme <name>) — TUI only",
    "/exit": "Exit the REPL",
}


# ---------------------------------------------------------------------------
# Readline history persistence + tab completion
# ---------------------------------------------------------------------------

HISTORY_DIR = os.path.expanduser("~/.config/duh")
HISTORY_FILE = os.path.join(HISTORY_DIR, "repl_history")
MAX_HISTORY = 1000


def _load_history() -> None:
    """Load readline history from disk. Creates the config dir if needed."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    try:
        readline.read_history_file(HISTORY_FILE)
    except (FileNotFoundError, PermissionError, OSError):
        pass  # first run, permission issue, or other OS error


def _save_history() -> None:
    """Save readline history to disk, truncating to MAX_HISTORY entries."""
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        readline.set_history_length(MAX_HISTORY)
        readline.write_history_file(HISTORY_FILE)
    except (PermissionError, OSError):
        pass  # can't save — not critical


class _SlashCompleter:
    """Tab-completer for /slash commands."""

    def __init__(self, commands: list[str]):
        self._commands = sorted(commands)
        self._matches: list[str] = []

    def complete(self, text: str, state: int) -> str | None:
        if state == 0:
            if text.startswith("/"):
                self._matches = [c for c in self._commands if c.startswith(text)]
            else:
                self._matches = []
        if state < len(self._matches):
            return self._matches[state]
        return None


def _setup_completion() -> None:
    """Configure readline tab completion for slash commands."""
    completer = _SlashCompleter(list(SLASH_COMMANDS.keys()))
    readline.set_completer(completer.complete)
    readline.set_completer_delims(" \t\n")
    # macOS uses libedit which needs a different parse_and_bind syntax
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def _search_messages(messages: list[Message], query: str) -> None:
    """Search through messages for *query* (case-insensitive).

    Prints matching messages with role, turn number, and a snippet with the
    match highlighted in bold yellow.
    """
    query_lower = query.lower()
    hits = 0
    # Turn numbering: each user message starts a new turn.
    turn = 0
    for msg in messages:
        if msg.role == "user":
            turn += 1
        text = msg.text
        if not text:
            continue
        if query_lower not in text.lower():
            continue
        hits += 1
        # Build a snippet around the first match
        idx = text.lower().index(query_lower)
        start = max(0, idx - 40)
        end = min(len(text), idx + len(query) + 40)
        snippet = text[start:end]
        # Replace newlines for compact display
        snippet = snippet.replace("\n", " ")
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."
        # Highlight the matched portion in bold yellow
        match_start = idx - start + (3 if start > 0 else 0)
        matched = snippet[match_start:match_start + len(query)]
        highlighted = (
            snippet[:match_start]
            + f"\033[1;33m{matched}\033[0m"
            + snippet[match_start + len(query):]
        )
        sys.stdout.write(f"  [turn {turn}] [{msg.role}] {highlighted}\n")
    if hits == 0:
        sys.stdout.write(f"  No matches for \"{query}\".\n")
    else:
        sys.stdout.write(f"  ({hits} match{'es' if hits != 1 else ''})\n")



def context_breakdown(
    engine: Engine,
    model: str,
) -> str:
    """Return a formatted table showing context window token usage.

    Breaks down: system prompt, conversation history, tool schemas,
    and available buffer.
    """
    import json
    from duh.kernel.tokens import count_tokens, get_context_limit

    context_limit = get_context_limit(model)

    # System prompt tokens
    sys_prompt = engine._config.system_prompt
    if isinstance(sys_prompt, list):
        sys_text = " ".join(sys_prompt)
    else:
        sys_text = sys_prompt or ""
    system_tokens = count_tokens(sys_text)

    # Conversation history tokens
    history_tokens = 0
    for msg in engine.messages:
        history_tokens += count_tokens(
            msg.text if isinstance(msg, Message) else str(msg)
        )

    # Tool schema tokens (name + description + input_schema JSON)
    tool_tokens = 0
    for tool in engine._config.tools:
        parts = []
        name = getattr(tool, "name", "")
        if name:
            parts.append(name)
        desc = getattr(tool, "description", "")
        if callable(desc):
            desc = desc()
        if desc:
            parts.append(str(desc))
        schema = getattr(tool, "input_schema", None)
        if schema:
            parts.append(json.dumps(schema))
        tool_tokens += count_tokens(" ".join(parts))

    used = system_tokens + history_tokens + tool_tokens
    available = max(0, context_limit - used)

    def _pct(n: int) -> str:
        if context_limit == 0:
            return "0.0%"
        return f"{n / context_limit * 100:.1f}%"

    def _fmt(n: int) -> str:
        return f"{n:,}"

    lines = [
        f"  Context window: {_fmt(context_limit)} tokens ({model})",
        f"",
        f"  {'Component':<22s} {'Tokens':>10s} {'%':>7s}",
        f"  {'-' * 22} {'-' * 10} {'-' * 7}",
        f"  {'System prompt':<22s} {_fmt(system_tokens):>10s} {_pct(system_tokens):>7s}",
        f"  {'Conversation history':<22s} {_fmt(history_tokens):>10s} {_pct(history_tokens):>7s}",
        f"  {'Tool schemas':<22s} {_fmt(tool_tokens):>10s} {_pct(tool_tokens):>7s}",
        f"  {'-' * 22} {'-' * 10} {'-' * 7}",
        f"  {'Used':<22s} {_fmt(used):>10s} {_pct(used):>7s}",
        f"  {'Available':<22s} {_fmt(available):>10s} {_pct(available):>7s}",
    ]

    # ADR-061 Phase 3: prompt cache stats
    cache_summary = engine.cache_tracker.summary()
    if cache_summary:
        lines.append(f"")
        lines.append(f"  {cache_summary}")

    # ADR-058: compact analytics
    if engine.compact_stats.total_compactions > 0:
        lines.append(f"")
        lines.append(f"  {engine.compact_stats.summary()}")

    return "\n".join(lines)

def _handle_slash(
    cmd: str,
    engine: Engine,
    model: str,
    deps: Deps,
    *,
    executor: NativeExecutor | None = None,
    task_manager: Any | None = None,
    template_state: dict[str, Any] | None = None,
    plan_mode: PlanMode | None = None,
    mcp_executor: Any | None = None,
    provider_name: str = "",
) -> tuple[bool, str]:
    """Handle a slash command. Returns (should_continue, new_model).

    Delegates to :class:`~duh.cli.slash_commands.SlashDispatcher` which
    holds one small method per command.  This thin wrapper preserves the
    original call-site contract so that all existing callers and tests
    continue to work without changes.

    template_state is a mutable dict with keys:
        'templates': dict[str, TemplateDef] -- loaded templates
        'active': str | None -- currently active template name
    Modified in place by /template commands.
    """
    from duh.cli.slash_commands import SlashContext, SlashDispatcher

    parts = cmd.strip().split(None, 1)
    name = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    ctx = SlashContext(
        engine=engine,
        model=model,
        deps=deps,
        executor=executor,
        task_manager=task_manager,
        template_state=template_state or {},
        plan_mode=plan_mode,
        mcp_executor=mcp_executor,
        provider_name=provider_name,
    )
    dispatcher = SlashDispatcher(ctx)
    return dispatcher.dispatch(name, arg)


def _handle_pr_command(arg: str) -> None:
    """Handle /pr subcommands by calling the gh CLI directly.

    Subcommands:
        /pr list [--state open|closed|merged|all]  -- list PRs
        /pr view <number>                          -- view PR details
        /pr diff <number>                          -- show PR diff
        /pr checks <number>                        -- show PR checks
    """
    from duh.tools.github_tool import _gh_available, _run_gh

    if not _gh_available():
        sys.stdout.write("  GitHub CLI (gh) not found. Install: brew install gh\n")
        return

    parts = arg.strip().split()
    if not parts:
        sys.stdout.write(
            "  Usage:\n"
            "    /pr list [--state open|closed|merged|all]\n"
            "    /pr view <number>\n"
            "    /pr diff <number>\n"
            "    /pr checks <number>\n"
        )
        return

    sub = parts[0].lower()

    if sub == "list":
        gh_args = ["pr", "list", "--json", "number,title,state,author"]
        # Pass through extra flags like --state
        if len(parts) > 1:
            gh_args.extend(parts[1:])
        stdout, stderr, rc = _run_gh(gh_args)
        if rc != 0:
            sys.stdout.write(f"  Error: {stderr.strip()}\n")
            return
        import json as _json
        try:
            prs = _json.loads(stdout)
        except _json.JSONDecodeError:
            sys.stdout.write(f"  {stdout}\n")
            return
        if not prs:
            sys.stdout.write("  No pull requests found.\n")
            return
        for pr in prs:
            author = pr.get("author", {})
            login = author.get("login", "?") if isinstance(author, dict) else str(author)
            sys.stdout.write(
                f"  #{pr.get('number', '?')} [{pr.get('state', '?')}] "
                f"{pr.get('title', '(no title)')} (by {login})\n"
            )
        return

    if sub in ("view", "diff", "checks"):
        if len(parts) < 2:
            sys.stdout.write(f"  Usage: /pr {sub} <number>\n")
            return
        number = parts[1]
        if sub == "view":
            gh_args = ["pr", "view", number, "--json", "title,body,state,reviews"]
        elif sub == "diff":
            gh_args = ["pr", "diff", number]
        else:  # checks
            gh_args = ["pr", "checks", number]
        stdout, stderr, rc = _run_gh(gh_args)
        if rc != 0:
            sys.stdout.write(f"  Error: {stderr.strip()}\n")
            return
        sys.stdout.write(f"  {stdout.strip()}\n" if stdout.strip() else "  (no output)\n")
        return

    sys.stdout.write(f"  Unknown /pr subcommand: {sub}\n")


def _handle_template_command(arg: str, state: dict[str, Any]) -> None:
    """Handle /template subcommands, mutating *state* in place.

    Subcommands:
        /template list             -- list available templates
        /template use <name>       -- set active template for future prompts
        /template use              -- clear active template
        /template <name> <prompt>  -- apply template to prompt (one-shot, printed)
    """
    templates: dict[str, Any] = state.get("templates", {})
    active: str | None = state.get("active")

    sub_parts = arg.strip().split(None, 1)
    sub = sub_parts[0] if sub_parts else ""
    sub_arg = sub_parts[1] if len(sub_parts) > 1 else ""

    if not sub or sub == "list":
        if not templates:
            sys.stdout.write("  No templates loaded.\n")
        else:
            for tname, tmpl in sorted(templates.items()):
                marker = " (active)" if tname == active else ""
                sys.stdout.write(f"  {tname:20s} {tmpl.description}{marker}\n")
        return

    if sub == "use":
        tname = sub_arg.strip()
        if not tname:
            if active:
                sys.stdout.write(f"  Template cleared (was: {active}).\n")
            else:
                sys.stdout.write("  No active template.\n")
            state["active"] = None
            return
        if tname not in templates:
            sys.stdout.write(f"  Template not found: {tname!r}. Use /template list.\n")
            return
        sys.stdout.write(f"  Active template set to: {tname}\n")
        state["active"] = tname
        return

    # /template <name> <prompt> -- one-shot render
    tname = sub
    if tname not in templates:
        sys.stdout.write(f"  Template not found: {tname!r}. Use /template list.\n")
        return

    rendered = templates[tname].render(sub_arg)
    sys.stdout.write(f"  [template: {tname}]\n")
    sys.stdout.write(f"  {rendered}\n")


# ---------------------------------------------------------------------------
# REPL loop
# ---------------------------------------------------------------------------

async def run_repl(args: argparse.Namespace) -> int:
    """Run the interactive REPL."""
    debug = args.debug
    if debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                            format="[%(levelname)s] %(name)s: %(message)s")

    # --- Print startup logo ---
    from duh.ui.logo import print_logo
    print_logo("compact", color=True)

    # --- Build renderer (Rich when available, plain ANSI otherwise) ---
    renderer = _make_renderer(debug=debug)

    # --- Build the shared session via SessionBuilder (issue #18 / CQ-4) ---
    # REPL-specific bits (renderer, prewarm, slash commands, plan mode) stay
    # in this function; provider resolution, tool loading, MCP connection,
    # system-prompt assembly, deps wiring and session resume are shared.
    from duh.cli.session_builder import (
        ProviderResolutionError,
        SessionBuilder,
        SessionBuilderOptions,
        _BuilderPatchTargets,
    )

    options = SessionBuilderOptions(
        # The legacy REPL only loaded base tools (no skills / deferred / memory).
        include_skills_in_tools=False,
        include_deferred_tools=False,
        include_memory_prompt=False,
        include_env_block=False,
        include_templates_hint=False,
        include_model_context_block=True,
        honour_tool_filters=False,
        approver_mode="repl",
        wire_hook_registry_in_deps=True,
        wire_audit_logger_in_deps=False,
        honour_tool_choice=False,
        honour_thinking=False,
        allow_session_id_override=False,
        log_skip_perms_warning=True,
        default_system_prompt=SYSTEM_PROMPT,
        brief_instruction=BRIEF_INSTRUCTION,
    )
    # Use lambdas so monkeypatch on the repl module (e.g. tests that set
    # ``repl.build_model_backend = fake``) is picked up at call time.
    patch_targets = _BuilderPatchTargets(
        engine_cls=Engine,
        engine_config_cls=EngineConfig,
        deps_cls=Deps,
        native_executor_cls=NativeExecutor,
        get_all_tools_fn=get_all_tools,
        build_model_backend_fn=lambda *a, **kw: build_model_backend(*a, **kw),
        resolve_provider_name_fn=lambda **kw: resolve_provider_name(**kw),
    )

    cwd = os.getcwd()
    builder = SessionBuilder(
        args, options, cwd=cwd, debug=debug, patch_targets=patch_targets,
    )
    try:
        build = await builder.build()
    except ProviderResolutionError as exc:
        if exc.provider_name is None:
            sys.stderr.write(
                "Error: No provider available.\n"
                "  Option 1: export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  Option 2: start Ollama (ollama serve)\n"
            )
        else:
            sys.stderr.write(f"Error: {exc.message}\n")
        return 1

    provider_name = build.provider_name
    model = build.model
    call_model = build.call_model
    tools = build.tools
    executor = build.executor
    deps = build.deps
    engine = build.engine
    mcp_executor = build.mcp_executor
    compactor = build.compactor
    store = build.store
    structured_logger = build.structured_logger
    _hook_registry = build.hook_registry
    _task_manager = build.task_manager

    # --- Pre-warm the model connection in background (REPL-only) ---
    import asyncio
    from duh.cli.prewarm import prewarm_connection
    _prewarm_task = asyncio.ensure_future(prewarm_connection(call_model))

    # --- Load prompt templates for /template commands (REPL state) ---
    _template_state: dict[str, Any] = {
        "templates": {t.name: t for t in build.loaded_templates},
        "active": None,
    }
    if not build.loaded_templates:
        try:
            from duh.kernel.templates import load_all_templates
            loaded_templates = load_all_templates(cwd)
            _template_state["templates"] = {t.name: t for t in loaded_templates}
        except Exception:
            logger.debug("Template loading failed in REPL", exc_info=True)

    _query_guard = QueryGuard()
    _plan_mode = PlanMode(engine)

    # --- Load readline history & set up tab completion ---
    _load_history()
    _setup_completion()

    renderer.banner(model)

    while True:
        try:
            user_input = input(renderer.prompt())
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Convenience: allow slash commands without the leading "/".
        # Example: "model gpt-5.2-codex" -> "/model gpt-5.2-codex"
        if not user_input.startswith("/"):
            first = user_input.split(None, 1)[0].lower()
            candidate = f"/{first}"
            if candidate in SLASH_COMMANDS:
                user_input = "/" + user_input

        # Slash commands
        if user_input.startswith("/"):
            keep_going, model = _handle_slash(
                user_input, engine, model, deps,
                executor=executor,
                task_manager=_task_manager,
                template_state=_template_state,
                plan_mode=_plan_mode,
                mcp_executor=mcp_executor,
                provider_name=provider_name,
            )
            if not keep_going:
                break

            # Check for plan request signal from _handle_slash
            if model.startswith("\x00plan\x00"):
                plan_desc = model[len("\x00plan\x00"):]
                model = engine.model or model  # restore actual model

                sys.stdout.write(f"  Planning: {plan_desc}\n")
                renderer.status_bar(model, engine.turn_count + 1)

                async for event in _plan_mode.plan(plan_desc):
                    event_type = event.get("type", "")
                    if event_type == "text_delta":
                        renderer.text_delta(event.get("text", ""))
                    elif event_type == "error":
                        hint = _interpret_error(event.get("error", "unknown"))
                        renderer.error(hint)

                renderer.flush_response()
                renderer.turn_end()

                if _plan_mode.steps:
                    sys.stdout.write(f"  {_plan_mode.format_plan()}\n\n")
                    sys.stdout.write(
                        "  [a]pprove  [r]eject  [m]odify > "
                    )
                    sys.stdout.flush()
                    try:
                        choice = input().strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        sys.stdout.write("\n")
                        _plan_mode.clear()
                        continue

                    if choice in ("a", "approve"):
                        sys.stdout.write("  Executing plan...\n")
                        renderer.status_bar(model, engine.turn_count + 1)

                        async for event in _plan_mode.execute():
                            event_type = event.get("type", "")
                            if event_type == "text_delta":
                                renderer.text_delta(event.get("text", ""))
                            elif event_type == "thinking_delta":
                                renderer.thinking_delta(event.get("text", ""))
                            elif event_type == "tool_use":
                                renderer.tool_use(
                                    event.get("name", "?"),
                                    event.get("input", {}),
                                )
                            elif event_type == "tool_result":
                                renderer.tool_result(
                                    str(event.get("output", "")),
                                    bool(event.get("is_error")),
                                )
                            elif event_type == "error":
                                hint = _interpret_error(
                                    event.get("error", "unknown")
                                )
                                renderer.error(hint)

                        renderer.flush_response()
                        renderer.turn_end()
                    elif choice in ("m", "modify"):
                        sys.stdout.write(
                            "  Edit the plan with /plan show, then "
                            "/plan <new description> to re-plan.\n"
                        )
                    else:
                        _plan_mode.clear()
                        sys.stdout.write("  Plan rejected.\n")
                else:
                    sys.stdout.write(
                        "  Could not parse a plan from the response.\n"
                    )
                    _plan_mode.clear()

            # Check for compact request signal from _handle_slash
            if model == "\x00compact\x00":
                model = engine.model or model  # restore actual model
                try:
                    await deps.compact(engine._messages)
                    sys.stdout.write(
                        f"  Compacted to {len(engine.messages)} messages.\n"
                    )
                except Exception as e:
                    sys.stdout.write(f"  Compact failed: {e}\n")

            continue

        # Apply active template to user input
        effective_input = user_input
        _active_tmpl_name = _template_state.get("active")
        if _active_tmpl_name and _active_tmpl_name in _template_state["templates"]:
            effective_input = _template_state["templates"][_active_tmpl_name].render(user_input)

        # Show status bar before each turn (model + turn count)
        renderer.status_bar(model, engine.turn_count + 1)

        # Emit STATUS_LINE hook
        if _hook_registry:
            await execute_hooks(
                _hook_registry,
                HookEvent.STATUS_LINE,
                {"model": model, "turn": engine.turn_count + 1},
            )

        # Emit USER_PROMPT_SUBMIT hook
        if _hook_registry:
            await execute_hooks(
                _hook_registry,
                HookEvent.USER_PROMPT_SUBMIT,
                {"prompt": effective_input, "session_id": engine.session_id},
            )

        # --- QueryGuard: reserve slot before dispatching ---
        try:
            _qg_gen = _query_guard.reserve()
        except RuntimeError:
            renderer.error("A query is already in progress.")
            continue

        try:
            _qg_started = _query_guard.try_start(_qg_gen)
            if _qg_started is None:
                renderer.error("Query generation became stale.")
                continue

            # Run the prompt through the engine
            async for event in engine.run(effective_input):
                event_type = event.get("type", "")

                if event_type == "text_delta":
                    renderer.text_delta(event.get("text", ""))

                elif event_type == "usage_delta":
                    # ADR-073 Task 8: live token counter — best-effort
                    # update of the status line mid-stream. Old renderers
                    # without this method stay backward-compatible.
                    if hasattr(renderer, "usage_delta"):
                        renderer.usage_delta(
                            input_tokens=event.get("input_tokens", 0),
                            output_tokens=event.get("output_tokens", 0),
                        )

                elif event_type == "thinking_delta":
                    renderer.thinking_delta(event.get("text", ""))

                elif event_type == "tool_use":
                    name = event.get("name", "?")
                    inp = event.get("input", {})
                    renderer.tool_use(name, inp)

                elif event_type == "tool_result":
                    renderer.tool_result(
                        str(event.get("output", "")),
                        bool(event.get("is_error")),
                    )

                elif event_type == "assistant":
                    msg = event.get("message")
                    if isinstance(msg, Message) and msg.metadata.get("is_error"):
                        hint = _interpret_error(msg.text)
                        renderer.error(hint)

                elif event_type == "error":
                    hint = _interpret_error(event.get("error", "unknown"))
                    renderer.error(hint)

                elif event_type == "budget_warning":
                    # Display budget warnings in yellow
                    sys.stderr.write(
                        f"\033[33mWarning: {event.get('message', '')}\033[0m\n"
                    )

                elif event_type == "budget_exceeded":
                    # Display budget exceeded in bold yellow
                    sys.stderr.write(
                        f"\033[1;33m{event.get('message', '')}\033[0m\n"
                    )

        except (KeyboardInterrupt, EOFError):
            # User aborted mid-query
            _query_guard.force_end()
            sys.stdout.write("\n  (query aborted)\n")
            continue
        finally:
            # Always return to idle
            _query_guard.end(_qg_gen)

        # Re-render accumulated text as Rich Markdown (no-op for plain)
        renderer.flush_response()

        # Update status bar with current token counts and cost estimate.
        from duh.kernel.tokens import estimate_cost as _estimate_cost
        renderer.update_stats(
            input_tokens=engine.total_input_tokens,
            output_tokens=engine.total_output_tokens,
            cost=_estimate_cost(model, engine.total_input_tokens, engine.total_output_tokens),
        )

        renderer.turn_end()

    # --- Save readline history on exit ---
    _save_history()

    # --- Close structured logger + disconnect MCP ---
    build.close_structured_logger()
    await build.teardown_mcp()

    return 0
