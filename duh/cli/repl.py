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
    /compact  — compact conversation (summarize older messages)
    /exit     — exit the REPL (also Ctrl-D)
"""

from __future__ import annotations

import argparse
import logging
import os
import readline
import sys
from typing import Any

from duh.adapters.anthropic import AnthropicProvider
from duh.adapters.native_executor import NativeExecutor
from duh.adapters.approvers import ApprovalMode, AutoApprover, InteractiveApprover, TieredApprover
from duh.cli.runner import BRIEF_INSTRUCTION, SYSTEM_PROMPT, _interpret_error
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.kernel.plan_mode import PlanMode
from duh.tools.registry import get_all_tools

logger = logging.getLogger("duh")

PROMPT = "\033[1;36mduh>\033[0m "  # bold cyan

# ---------------------------------------------------------------------------
# Rich-aware renderer (graceful fallback when rich is absent)
# ---------------------------------------------------------------------------

_HAS_RICH = False
try:
    from rich.console import Console
    from rich.markdown import Markdown as RichMarkdown
    from rich.panel import Panel
    from rich.text import Text
    from rich.theme import Theme
    _HAS_RICH = True
except ImportError:
    pass


class _PlainRenderer:
    """Fallback renderer that uses raw ANSI escape codes."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._buf: list[str] = []  # accumulates text_delta chunks

    # -- prompt --------------------------------------------------------
    @staticmethod
    def prompt() -> str:
        return PROMPT

    # -- streaming text ------------------------------------------------
    def text_delta(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()
        self._buf.append(text)

    # -- markdown flush (no-op for plain) ------------------------------
    def flush_response(self) -> None:
        self._buf.clear()

    # -- thinking ------------------------------------------------------
    def thinking_delta(self, text: str) -> None:
        if self.debug:
            sys.stderr.write(f"\033[2;3m{text}\033[0m")
            sys.stderr.flush()

    # -- tool use & results --------------------------------------------
    def tool_use(self, name: str, inp: dict[str, Any]) -> None:
        summary = ", ".join(f"{k}={v!r}" for k, v in list(inp.items())[:2])
        sys.stderr.write(f"  \033[33m> {name}\033[0m({summary})\n")
        sys.stderr.flush()

    def tool_result(self, output: str, is_error: bool) -> None:
        if is_error:
            sys.stderr.write(f"  \033[31m! {output[:200]}\033[0m\n")
        elif self.debug:
            sys.stderr.write(f"  \033[32m< {output[:100]}\033[0m\n")

    # -- errors --------------------------------------------------------
    def error(self, hint: str) -> None:
        sys.stderr.write(f"\n\033[31mError: {hint}\033[0m\n")

    # -- end of turn separator -----------------------------------------
    def turn_end(self) -> None:
        sys.stdout.write("\n\n")

    # -- banner --------------------------------------------------------
    def banner(self, model: str) -> None:
        sys.stdout.write(
            f"D.U.H. interactive mode ({model}). "
            "Type /help for commands, /exit or Ctrl-D to quit.\n\n"
        )

    # -- status bar (no-op for plain) ----------------------------------
    def status_bar(self, model: str, turns: int) -> None:
        pass


class _RichRenderer:
    """Renderer that uses the Rich library for styled terminal output."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self._buf: list[str] = []
        theme = Theme({
            "tool": "bold yellow",
            "tool.ok": "green",
            "tool.err": "bold red",
            "thinking": "dim italic",
            "err": "bold red",
            "status": "dim",
        })
        self._console = Console(theme=theme, stderr=False)
        self._err_console = Console(theme=theme, stderr=True)

    # -- prompt --------------------------------------------------------
    @staticmethod
    def prompt() -> str:
        # Rich can style prompts, but readline integration is tricky.
        # We keep the ANSI prompt so readline calculates width correctly.
        return PROMPT

    # -- streaming text ------------------------------------------------
    def text_delta(self, text: str) -> None:
        # Stream tokens to stdout immediately so the user sees them live.
        sys.stdout.write(text)
        sys.stdout.flush()
        self._buf.append(text)

    # -- markdown flush ------------------------------------------------
    def flush_response(self) -> None:
        """Re-render the full response as Rich Markdown after streaming."""
        full = "".join(self._buf)
        self._buf.clear()
        if not full.strip():
            return
        # Heuristic: only use Markdown renderer when content looks like it
        # has markdown constructs (headers, code fences, lists, bold, etc.)
        md_indicators = ("```", "##", "**", "* ", "- ", "1. ", "> ", "| ")
        if any(ind in full for ind in md_indicators):
            # Move cursor up and overwrite the raw streamed text.
            # Count how many lines were streamed.
            lines = full.count("\n") + 1
            # Clear those lines
            sys.stdout.write(f"\033[{lines}A\033[J")
            sys.stdout.flush()
            self._console.print(RichMarkdown(full))

    # -- thinking ------------------------------------------------------
    def thinking_delta(self, text: str) -> None:
        if self.debug:
            self._err_console.print(Text(text, style="thinking"), end="")

    # -- tool use & results --------------------------------------------
    def tool_use(self, name: str, inp: dict[str, Any]) -> None:
        summary = ", ".join(f"{k}={v!r}" for k, v in list(inp.items())[:2])
        self._err_console.print(
            Text.assemble(
                ("  > ", "tool"),
                (name, "bold yellow"),
                (f"({summary})", ""),
            )
        )

    def tool_result(self, output: str, is_error: bool) -> None:
        if is_error:
            self._err_console.print(
                Panel(
                    output[:300],
                    title="tool error",
                    border_style="tool.err",
                    expand=False,
                )
            )
        elif self.debug:
            self._err_console.print(
                Text(f"  < {output[:100]}", style="tool.ok")
            )

    # -- errors --------------------------------------------------------
    def error(self, hint: str) -> None:
        self._err_console.print(
            Panel(hint, title="Error", border_style="err", expand=False)
        )

    # -- end of turn separator -----------------------------------------
    def turn_end(self) -> None:
        sys.stdout.write("\n\n")

    # -- banner --------------------------------------------------------
    def banner(self, model: str) -> None:
        self._console.print(
            Panel(
                f"[bold cyan]D.U.H.[/bold cyan] interactive mode\n"
                f"Model: [bold]{model}[/bold]  |  "
                "Type [bold]/help[/bold] for commands, "
                "[bold]/exit[/bold] or [bold]Ctrl-D[/bold] to quit.",
                border_style="cyan",
                expand=False,
            )
        )
        self._console.print()

    # -- status bar ----------------------------------------------------
    def status_bar(self, model: str, turns: int) -> None:
        self._err_console.print(
            Text(f"  [{model}] turn {turns}", style="status")
        )


def _make_renderer(debug: bool = False) -> _PlainRenderer | Any:
    """Return a Rich renderer if available, else a plain one."""
    if _HAS_RICH:
        return _RichRenderer(debug=debug)
    return _PlainRenderer(debug=debug)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

SLASH_COMMANDS = {
    "/help": "Show available commands",
    "/model": "Show or set model (/model <name>)",
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
    "/snapshot": "Ghost snapshot (/snapshot, /snapshot apply, /snapshot discard)",
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

    template_state is a mutable dict with keys:
        'templates': dict[str, TemplateDef] -- loaded templates
        'active': str | None -- currently active template name
    Modified in place by /template commands.
    """
    parts = cmd.strip().split(None, 1)
    name = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if name == "/help":
        for k, v in SLASH_COMMANDS.items():
            sys.stdout.write(f"  {k:12s} {v}\n")
        return True, model

    if name == "/model":
        if arg:
            sys.stdout.write(f"  Model changed to: {arg}\n")
            return True, arg
        sys.stdout.write(f"  Current model: {model}\n")
        return True, model

    if name == "/brief":
        # Detect current state by checking if BRIEF_INSTRUCTION is in the system prompt
        sp = engine._config.system_prompt
        current_text = "\n\n".join(sp) if isinstance(sp, list) else sp
        is_on = BRIEF_INSTRUCTION in current_text

        arg_lower = arg.strip().lower()
        if arg_lower == "on":
            want_on = True
        elif arg_lower == "off":
            want_on = False
        else:
            # bare /brief → toggle
            want_on = not is_on

        if want_on and not is_on:
            new_prompt = current_text + "\n\n" + BRIEF_INSTRUCTION
            engine._config.system_prompt = new_prompt
            sys.stdout.write("  Brief mode: ON\n")
        elif not want_on and is_on:
            new_prompt = current_text.replace("\n\n" + BRIEF_INSTRUCTION, "")
            engine._config.system_prompt = new_prompt
            sys.stdout.write("  Brief mode: OFF\n")
        else:
            state = "ON" if is_on else "OFF"
            sys.stdout.write(f"  Brief mode: {state} (no change)\n")
        return True, model

    if name == "/cost":
        sys.stdout.write(f"  {engine.cost_summary(model)}\n")
        return True, model

    if name == "/status":
        sys.stdout.write(
            f"  Session: {engine.session_id[:8]}...\n"
            f"  Turns:   {engine.turn_count}\n"
            f"  Messages: {len(engine.messages)}\n"
            f"  Model:   {model}\n"
        )
        return True, model

    if name == "/context":
        sys.stdout.write(context_breakdown(engine, model) + "\n")
        return True, model

    if name == "/changes":
        if executor is not None:
            text = executor.file_tracker.summary()
            # Also show git diff --stat when available
            diff_stat = executor.file_tracker.diff_summary(cwd=executor._cwd)
            if diff_stat and diff_stat != "No files modified.":
                text += f"\n\n  Git diff:\n{diff_stat}"
        else:
            text = "No file tracker available."
        sys.stdout.write(f"  {text}\n")
        return True, model

    if name == "/git":
        from duh.kernel.git_context import get_git_context
        import os
        cwd = os.getcwd()
        ctx = get_git_context(cwd)
        if ctx:
            # Strip the XML-like tags for display
            display = ctx.replace("<git-context>", "").replace("</git-context>", "").strip()
            sys.stdout.write(f"  {display}\n")
        else:
            sys.stdout.write("  Not in a git repository.\n")
        return True, model

    if name == "/tasks":
        if task_manager is not None:
            sys.stdout.write(f"  {task_manager.summary()}\n")
        else:
            sys.stdout.write("  No tasks.\n")
        return True, model

    if name == "/jobs":
        from duh.tools.bash import get_job_queue
        queue = get_job_queue()
        if arg:
            # /jobs <id> — show result of a specific job
            try:
                result = queue.results(arg.strip())
                sys.stdout.write(f"  {result}\n")
            except KeyError:
                sys.stdout.write(f"  Unknown job id: {arg.strip()}\n")
            except ValueError as exc:
                sys.stdout.write(f"  {exc}\n")
        else:
            # /jobs — list all jobs
            jobs = queue.list_jobs()
            if not jobs:
                sys.stdout.write("  No background jobs.\n")
            else:
                for j in jobs:
                    elapsed = f" ({j['elapsed_s']}s)" if j.get("elapsed_s") is not None else ""
                    sys.stdout.write(
                        f"  [{j['id']}] {j['state']:10s} {j['name']}{elapsed}\n"
                    )
        return True, model

    if name == "/search":
        if not arg:
            sys.stdout.write("  Usage: /search <query>\n")
            return True, model
        _search_messages(engine.messages, arg)
        return True, model

    if name == "/template":
        _handle_template_command(arg, template_state or {})
        return True, model

    if name == "/plan":
        if plan_mode is None:
            sys.stdout.write("  Plan mode not available.\n")
            return True, model
        sub = arg.strip().lower()
        if sub == "show":
            sys.stdout.write(f"  {plan_mode.format_plan()}\n")
            return True, model
        if sub == "clear":
            plan_mode.clear()
            sys.stdout.write("  Plan cleared.\n")
            return True, model
        if not arg.strip():
            sys.stdout.write(
                "  Usage: /plan <description>  — propose a plan\n"
                "         /plan show           — display current plan\n"
                "         /plan clear          — clear current plan\n"
            )
            return True, model
        # /plan <description> — signal handled by REPL loop (see run_repl)
        # Return a sentinel: model prefixed with \x00 signals plan request
        return True, f"\x00plan\x00{arg.strip()}"

    if name == "/pr":
        _handle_pr_command(arg)
        return True, model

    if name == "/undo":
        if executor is None:
            sys.stdout.write("  No executor available.\n")
            return True, model
        try:
            path, msg = executor.undo_stack.undo()
            sys.stdout.write(f"  {msg}\n")
        except IndexError:
            sys.stdout.write("  Nothing to undo.\n")
        return True, model

    if name == "/health":
        from duh.kernel.health_check import HealthChecker
        from duh.cli.doctor import _format_latency
        checker = HealthChecker(timeout=5.0)

        sys.stdout.write("  Running health checks...\n")

        # Check providers
        providers_to_check = ["anthropic", "openai", "ollama"]
        disabled: list[str] = []
        for pname in providers_to_check:
            result = checker.check_provider(pname)
            latency = _format_latency(result["latency_ms"])
            if result["healthy"]:
                status = f"healthy ({latency})"
            else:
                status = f"UNHEALTHY ({result['error']}, {latency})"
                disabled.append(pname)
            sys.stdout.write(f"    {pname:12s} {status}\n")

        # Check MCP servers
        if mcp_executor is not None:
            connections = getattr(mcp_executor, "_connections", {})
            configs = getattr(mcp_executor, "_servers", {})
            if connections or configs:
                sys.stdout.write("  MCP servers:\n")
                all_names = set(configs.keys()) | set(connections.keys())
                for sname in sorted(all_names):
                    result = checker.check_mcp(sname, connections=connections)
                    if result["healthy"]:
                        status = f"healthy ({result['tools']} tools)"
                    else:
                        status = "UNHEALTHY (disconnected)"
                        disabled.append(f"mcp:{sname}")
                    sys.stdout.write(f"    {sname:12s} {status}\n")

        # Report disabled items
        if disabled:
            sys.stdout.write(f"  Unhealthy: {', '.join(disabled)}\n")
        else:
            sys.stdout.write("  All checks passed.\n")
        return True, model

    if name == "/clear":
        engine._messages.clear()
        sys.stdout.write("  Conversation cleared.\n")
        return True, model

    if name == "/compact":
        if deps.compact:
            import asyncio
            try:
                asyncio.get_event_loop().run_until_complete(
                    deps.compact(engine._messages)
                )
                sys.stdout.write(f"  Compacted to {len(engine.messages)} messages.\n")
            except Exception as e:
                sys.stdout.write(f"  Compact failed: {e}\n")
        else:
            sys.stdout.write("  No compactor configured.\n")
        return True, model

    if name == "/snapshot":
        # Handled by REPL loop (see run_repl) -- return sentinel
        return True, f"\x00snapshot\x00{arg.strip()}"

    if name == "/exit":
        return False, model

    sys.stdout.write(f"  Unknown command: {name}. Type /help for commands.\n")
    return True, model


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

    # --- Build renderer (Rich when available, plain ANSI otherwise) ---
    renderer = _make_renderer(debug=debug)

    # --- Resolve provider ---
    provider_name = args.provider
    if not provider_name and getattr(args, "model", None):
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
        )
        return 1

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

    cwd = os.getcwd()
    tools = list(get_all_tools())

    # --- Load config and connect MCP servers ---
    mcp_executor = None
    try:
        from duh.config import load_config
        app_config = load_config(cwd=cwd)
        if app_config.mcp_servers:
            from duh.adapters.mcp_executor import MCPExecutor
            mcp_executor = MCPExecutor.from_config(app_config.mcp_servers)
            discovered = await mcp_executor.connect_all()
            from duh.tools.mcp_tool import MCPToolWrapper
            for _server_name, mcp_tools in discovered.items():
                for info in mcp_tools:
                    wrapper = MCPToolWrapper(info=info, executor=mcp_executor)
                    tools.append(wrapper)
                    if debug:
                        logger.debug("MCP tool registered: %s", wrapper.name)
    except Exception:
        logger.debug("MCP loading failed in REPL, continuing without MCP", exc_info=True)

    # --- Build system prompt with git context ---
    system_prompt_parts = [args.system_prompt or SYSTEM_PROMPT]
    if getattr(args, "brief", False):
        system_prompt_parts.append(BRIEF_INSTRUCTION)

    from duh.kernel.git_context import get_git_context, get_git_warnings
    git_ctx = get_git_context(cwd)
    if git_ctx:
        system_prompt_parts.append(git_ctx)

    # --- Print git safety warnings ---
    for warning in get_git_warnings(cwd):
        sys.stderr.write(f"\033[33mWARNING: {warning}\033[0m\n")

    system_prompt = "\n\n".join(system_prompt_parts)

    # --- Locate TaskTool's manager for /tasks slash command ---
    _task_manager = None
    for _t in tools:
        if getattr(_t, "name", None) == "Task" and hasattr(_t, "task_manager"):
            _task_manager = _t.task_manager
            break

    executor = NativeExecutor(tools=tools, cwd=cwd)

    # --- Approval mode selection ---
    approval_mode_str = getattr(args, "approval_mode", None)
    if approval_mode_str:
        mode = ApprovalMode(approval_mode_str)
        approver: Any = TieredApprover(mode=mode, cwd=cwd)
    elif args.dangerously_skip_permissions:
        approver = AutoApprover()
    else:
        approver = InteractiveApprover()

    # --- Wire compactor ---
    from duh.adapters.simple_compactor import SimpleCompactor
    compactor = SimpleCompactor()

    # --- Wire session store (auto-save after each turn) ---
    from duh.adapters.file_store import FileStore
    store = FileStore()

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

    engine_config = EngineConfig(
        model=model,
        fallback_model=getattr(args, "fallback_model", None),
        system_prompt=system_prompt,
        tools=tools,
        max_turns=args.max_turns,
        max_cost=max_cost,
    )
    # --- Wire structured JSON logger ---
    structured_logger = None
    if getattr(args, "log_json", False) or os.environ.get("DUH_LOG_JSON", "") == "1":
        from duh.adapters.structured_logging import StructuredLogger
        structured_logger = StructuredLogger()

    engine = Engine(deps=deps, config=engine_config, session_store=store,
                    structured_logger=structured_logger)
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

            continue

        # Apply active template to user input
        effective_input = user_input
        _active_tmpl_name = _template_state.get("active")
        if _active_tmpl_name and _active_tmpl_name in _template_state["templates"]:
            effective_input = _template_state["templates"][_active_tmpl_name].render(user_input)

        # Show status bar before each turn (model + turn count)
        renderer.status_bar(model, engine.turn_count + 1)

        # Run the prompt through the engine
        async for event in engine.run(effective_input):
            event_type = event.get("type", "")

            if event_type == "text_delta":
                renderer.text_delta(event.get("text", ""))

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

        # Re-render accumulated text as Rich Markdown (no-op for plain)
        renderer.flush_response()
        renderer.turn_end()

    # --- Save readline history on exit ---
    _save_history()

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
            logger.debug("MCP disconnect failed in REPL", exc_info=True)

    return 0
