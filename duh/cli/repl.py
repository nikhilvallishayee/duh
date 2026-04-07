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
    /cost     — show session cost estimate
    /status   — show session status (turns, messages, model)
    /changes  — show files touched in this session
    /clear    — clear conversation history
    /compact  — compact conversation (summarize older messages)
    /exit     — exit the REPL (also Ctrl-D)
"""

from __future__ import annotations

import argparse
import logging
import os
import readline  # noqa: F401 — enables line editing in input()
import sys
from typing import Any

from duh.adapters.anthropic import AnthropicProvider
from duh.adapters.native_executor import NativeExecutor
from duh.adapters.approvers import AutoApprover, InteractiveApprover
from duh.cli.runner import SYSTEM_PROMPT, _interpret_error
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
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
    "/changes": "Show files touched in this session",
    "/clear": "Clear conversation history",
    "/compact": "Compact older messages",
    "/exit": "Exit the REPL",
}


def _handle_slash(
    cmd: str,
    engine: Engine,
    model: str,
    deps: Deps,
    *,
    executor: NativeExecutor | None = None,
) -> tuple[bool, str]:
    """Handle a slash command. Returns (should_continue, new_model)."""
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

    if name == "/changes":
        if executor is not None:
            text = executor.file_tracker.summary()
        else:
            text = "No file tracker available."
        sys.stdout.write(f"  {text}\n")
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

    if name == "/exit":
        return False, model

    sys.stdout.write(f"  Unknown command: {name}. Type /help for commands.\n")
    return True, model


# ---------------------------------------------------------------------------
# REPL loop
# ---------------------------------------------------------------------------

async def run_repl(args: argparse.Namespace) -> int:
    """Run the interactive REPL."""
    debug = args.debug
    if debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr,
                            format="[%(levelname)s] %(name)s: %(message)s")

    # --- Resolve provider ---
    provider_name = args.provider
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

    system_prompt = args.system_prompt or SYSTEM_PROMPT

    executor = NativeExecutor(tools=tools, cwd=cwd)
    approver: Any = AutoApprover() if args.dangerously_skip_permissions else InteractiveApprover()

    # --- Wire compactor ---
    from duh.adapters.simple_compactor import SimpleCompactor
    compactor = SimpleCompactor()

    deps = Deps(
        call_model=call_model,
        run_tool=executor.run,
        approve=approver.check,
        compact=compactor.compact,
    )
    engine_config = EngineConfig(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        max_turns=args.max_turns,
    )
    engine = Engine(deps=deps, config=engine_config)

    sys.stdout.write(f"D.U.H. interactive mode ({model}). Type /help for commands, /exit or Ctrl-D to quit.\n\n")

    while True:
        try:
            user_input = input(PROMPT)
        except (EOFError, KeyboardInterrupt):
            sys.stdout.write("\n")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Slash commands
        if user_input.startswith("/"):
            keep_going, model = _handle_slash(user_input, engine, model, deps, executor=executor)
            if not keep_going:
                break
            continue

        # Run the prompt through the engine
        async for event in engine.run(user_input):
            event_type = event.get("type", "")

            if event_type == "text_delta":
                sys.stdout.write(event.get("text", ""))
                sys.stdout.flush()

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
                msg = event.get("message")
                if isinstance(msg, Message) and msg.metadata.get("is_error"):
                    hint = _interpret_error(msg.text)
                    sys.stderr.write(f"\n\033[31mError: {hint}\033[0m\n")

            elif event_type == "error":
                hint = _interpret_error(event.get("error", "unknown"))
                sys.stderr.write(f"\n\033[31mError: {hint}\033[0m\n")

        sys.stdout.write("\n\n")  # blank line after response

    # --- Disconnect MCP ---
    if mcp_executor:
        try:
            await mcp_executor.disconnect_all()
        except Exception:
            logger.debug("MCP disconnect failed in REPL", exc_info=True)

    return 0
