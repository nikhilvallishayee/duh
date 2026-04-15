"""D.U.H. Textual TUI — full widget-tree frontend (ADR-011 Tier 2).

Architecture
------------
The app is a *pure frontend*: it consumes the same async event stream that
the readline REPL uses (``engine.run(prompt)``).  The kernel (``loop.py``,
``engine.py``) is unchanged.  This is the TextualRenderer described in
ADR-011's architecture diagram.

Layout
------
┌─ Header ──────────────────────────────────────────────────────────────┐
│ model name  |  session id  |  tokens  |  cost                         │
├─ Sidebar ──┬─ Message log (ScrollableContainer) ─────────────────────┤
│ session    │                                                            │
│ info       │  [user]  …                                                │
│ active     │  [assistant]  …                                           │
│ tools      │    ┌─ tool call ─────────────────────────────────────┐   │
│ recent     │    │ Bash(command='ls /')   OK: total 64             │   │
│ files      │    └─────────────────────────────────────────────────┘   │
│            │                                                            │
├────────────┴───────────────────────────────────────────────────────────┤
│ Input > _______________________________________________  [Send]        │
├────────────────────────────────────────────────────────────────────────┤
│ model  turn N  in=NNN out=NNN  $0.0000  connected                     │
└────────────────────────────────────────────────────────────────────────┘

Keyboard shortcuts
------------------
Ctrl+B  — toggle sidebar
Ctrl+Q / Escape  — quit
Enter   — send message (same as clicking Send)
"""

from __future__ import annotations

import asyncio
import argparse
import sys
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, Static

from duh.ui.theme import APP_CSS
from duh.ui.logo import LOGO_COMPACT
from duh.ui.widgets import MessageWidget, ThinkingWidget, ToolCallWidget


# ---------------------------------------------------------------------------
# DuhApp
# ---------------------------------------------------------------------------


class DuhApp(App[int]):
    """D.U.H. Textual TUI application.

    Parameters
    ----------
    engine:
        A fully-configured ``duh.kernel.engine.Engine`` instance.
    model:
        Display name of the active model.
    session_id:
        Session identifier string for display.
    debug:
        When *True*, thinking blocks are visible.
    """

    CSS = APP_CSS
    ENABLE_COMMAND_PALETTE = False
    ALLOW_SELECT = True  # Enable native text selection with mouse

    BINDINGS = [
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("escape", "quit", "Quit", show=False),
    ]

    # ------------------------------------------------------------------ init

    def __init__(
        self,
        engine: Any,
        model: str = "unknown",
        session_id: str = "",
        debug: bool = False,
        resumed_messages: list | None = None,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._model = model
        self._session_id = session_id
        self._debug = debug
        self._resumed_messages = resumed_messages or []

        # Running counters
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._cost: float = 0.0
        self._turn: int = 0
        self._connected: bool = True

        # Track the "current" assistant message being streamed
        self._active_assistant: MessageWidget | None = None
        self._active_thinking: ThinkingWidget | None = None
        self._active_tool: ToolCallWidget | None = None

    # ---------------------------------------------------------------- compose

    def compose(self) -> ComposeResult:
        yield Static(self._header_text(), id="header")
        with Horizontal(id="body"):
            yield self._make_sidebar()
            yield ScrollableContainer(id="message-log")
        with Horizontal(id="input-area"):
            yield Input(placeholder="Type a message… (Enter to send)", id="prompt-input")
            yield Button("Send", id="send-button", variant="primary")
        yield Static(self._status_text(), id="statusbar")

    # ----------------------------------------------------------------- sidebar

    def _make_sidebar(self) -> Vertical:
        sidebar = Vertical(
            Static(
                "[bold magenta]D[/].U.[bold magenta]H[/].\n"
                "[dim]Universal Harness[/]",
                id="sidebar-logo",
            ),
            id="sidebar",
        )
        return sidebar

    # ----------------------------------------------------------------- header / status

    def _header_text(self) -> str:
        sid = f"  [{self._session_id[:8]}]" if self._session_id else ""
        return f" [bold magenta]D[/].U.[bold magenta]H[/]. | {self._model}{sid}"

    def _status_text(self) -> str:
        tok = ""
        if self._input_tokens or self._output_tokens:
            tok = f"  in={self._input_tokens:,} out={self._output_tokens:,}"
        cost = f"  ${self._cost:.4f}" if self._cost else ""
        conn = "[green]connected[/]" if self._connected else "[red]disconnected[/]"
        return f" [bold magenta]D[/].[bold magenta]U[/].[bold magenta]H[/]. [{self._model}] turn {self._turn}{tok}{cost}  {conn}"

    def _refresh_status(self) -> None:
        self.query_one("#header", Static).update(self._header_text())
        self.query_one("#statusbar", Static).update(self._status_text())

    # ----------------------------------------------------------------- sidebar toggle

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar")
        if "visible" in sidebar.classes:
            sidebar.remove_class("visible")
        else:
            sidebar.add_class("visible")

    # ----------------------------------------------------------------- on_mount

    async def on_mount(self) -> None:
        """Show welcome banner and restored session messages."""
        log = self.query_one("#message-log", ScrollableContainer)

        # Welcome banner
        sid_short = self._session_id[:8] if self._session_id else "new"
        banner = (
            f"[bold magenta]D[/].U.[bold magenta]H[/]. — "
            f"[bold magenta]D[/].U.[bold magenta]H[/]. is a Universal Harness\n\n"
            f"[dim]Model:[/] {self._model}  "
            f"[dim]Session:[/] {sid_short}  "
            f"[dim]Permissions:[/] auto-approve\n"
            f"[dim]Type a message below. Ctrl+Q to quit. Ctrl+B for sidebar.[/]\n"
            f"[dim]Select text with mouse, Ctrl+C to copy.[/]"
        )
        await log.mount(Static(banner, classes="welcome-banner"))

        # Show restored session messages (skip empty ones)
        if self._resumed_messages:
            shown = 0
            for raw in self._resumed_messages:
                role = raw.get("role", "user") if isinstance(raw, dict) else getattr(raw, "role", "user")
                content = raw.get("content", "") if isinstance(raw, dict) else getattr(raw, "text", str(raw))
                if isinstance(content, list):
                    # Render each content block type appropriately
                    parts = []
                    for b in content:
                        if not isinstance(b, dict):
                            parts.append(str(b))
                            continue
                        btype = b.get("type", "")
                        if btype == "text":
                            parts.append(b.get("text", ""))
                        elif btype == "tool_use":
                            name = b.get("name", "?")
                            parts.append(f"[Used {name}]")
                        elif btype == "tool_result":
                            tr = str(b.get("content", ""))[:80]
                            parts.append(f"[Result: {tr}]")
                        elif btype == "thinking":
                            parts.append("[thinking]")
                        # skip unknown block types
                    content = " ".join(p for p in parts if p).strip()
                text = str(content).strip()
                if not text:
                    continue  # skip empty messages
                text = text[:500]  # truncate for display
                widget = MessageWidget(role=role, text=text)
                await log.mount(widget)
                shown += 1

            if shown > 0:
                await log.mount(Static(
                    f"[dim]--- Restored {shown} messages ---[/]",
                    classes="session-divider",
                ))

        log.scroll_end(animate=False)
        self.query_one("#prompt-input", Input).focus()

    # ----------------------------------------------------------------- message helpers

    async def _add_widget(self, widget: Any) -> None:
        """Mount a widget into the message log and scroll to bottom."""
        log = self.query_one("#message-log", ScrollableContainer)
        await log.mount(widget)
        log.scroll_end(animate=False)

    async def _new_user_message(self, text: str) -> MessageWidget:
        widget = MessageWidget(role="user", text=text)
        await self._add_widget(widget)
        return widget

    async def _new_assistant_message(self) -> MessageWidget:
        widget = MessageWidget(role="assistant", text="")
        await self._add_widget(widget)
        return widget

    async def _new_thinking_widget(self) -> ThinkingWidget:
        widget = ThinkingWidget()
        await self._add_widget(widget)
        return widget

    async def _new_tool_widget(self, name: str, inp: dict) -> ToolCallWidget:
        widget = ToolCallWidget(tool_name=name, input=inp)
        await self._add_widget(widget)
        return widget

    async def _add_error_message(self, text: str) -> None:
        widget = Static(f"[red]Error:[/red] {text}", classes="message-assistant")
        await self._add_widget(widget)

    # ----------------------------------------------------------------- send message

    @on(Button.Pressed, "#send-button")
    async def handle_send_button(self, _event: Button.Pressed) -> None:
        await self._submit()

    @on(Input.Submitted, "#prompt-input")
    async def handle_input_submitted(self, _event: Input.Submitted) -> None:
        await self._submit()

    async def _submit(self) -> None:
        inp = self.query_one("#prompt-input", Input)
        text = inp.value.strip()
        if not text:
            return
        inp.value = ""

        # Slash command dispatch — handle locally, don't send to model
        if text.startswith("/"):
            handled = await self._handle_slash(text)
            if handled:
                return

        inp.disabled = True
        self.query_one("#send-button", Button).disabled = True

        await self._new_user_message(text)
        self._run_query(text)

    # ----------------------------------------------------------------- slash commands

    async def _handle_slash(self, text: str) -> bool:
        """Handle /commands locally. Returns True if handled."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "/help":
            await self._add_widget(Static(
                "[bold]Available commands:[/]\n"
                "  [cyan]/help[/]          — Show this help\n"
                "  [cyan]/model[/] [name]  — Show or switch model\n"
                "  [cyan]/cost[/]          — Show token usage and cost\n"
                "  [cyan]/compact[/]       — Force context compaction\n"
                "  [cyan]/clear[/]         — Clear message display\n"
                "  [cyan]/session[/]       — Show session info\n"
                "  [cyan]/quit[/]          — Exit D.U.H.\n",
                classes="welcome-banner",
            ))
            return True

        if cmd == "/model":
            if arg:
                self._model = arg
                self._engine._config.model = arg
                self._refresh_status()
                await self._add_widget(Static(
                    f"[green]Model switched to:[/] {arg}", classes="session-divider",
                ))
            else:
                await self._add_widget(Static(
                    f"[dim]Current model:[/] {self._model}", classes="session-divider",
                ))
            return True

        if cmd == "/cost":
            from duh.kernel.tokens import estimate_cost
            cost = estimate_cost(self._model, self._input_tokens, self._output_tokens)
            await self._add_widget(Static(
                f"[bold]Session cost:[/]\n"
                f"  Input tokens:  {self._input_tokens:,}\n"
                f"  Output tokens: {self._output_tokens:,}\n"
                f"  Estimated cost: ${cost:.4f}\n"
                f"  Turn: {self._turn}\n"
                f"  Model: {self._model}",
                classes="welcome-banner",
            ))
            return True

        if cmd == "/compact":
            try:
                compact_fn = self._engine._deps.compact
                if compact_fn:
                    import asyncio
                    before = len(self._engine._messages)
                    from duh.kernel.tokens import get_context_limit
                    limit = int(get_context_limit(self._model) * 0.50)
                    self._engine._messages = await compact_fn(
                        self._engine._messages, token_limit=limit,
                    )
                    after = len(self._engine._messages)
                    await self._add_widget(Static(
                        f"[green]Compacted:[/] {before} → {after} messages",
                        classes="session-divider",
                    ))
                else:
                    await self._add_error_message("No compactor configured")
            except Exception as e:
                await self._add_error_message(f"Compact failed: {e}")
            return True

        if cmd == "/clear":
            log = self.query_one("#message-log", ScrollableContainer)
            await log.remove_children()
            await self._add_widget(Static(
                "[dim]Messages cleared. Context retained in engine.[/]",
                classes="session-divider",
            ))
            return True

        if cmd == "/session":
            msg_count = len(getattr(self._engine, "_messages", []))
            await self._add_widget(Static(
                f"[bold]Session info:[/]\n"
                f"  ID: {self._session_id}\n"
                f"  Messages: {msg_count}\n"
                f"  Turn: {self._turn}\n"
                f"  Model: {self._model}",
                classes="welcome-banner",
            ))
            return True

        if cmd in ("/quit", "/exit", "/q"):
            self.exit(0)
            return True

        # Unknown slash command — let the model handle it
        return False

    # ----------------------------------------------------------------- worker

    @work(exclusive=True, thread=False)
    async def _run_query(self, prompt: str) -> None:
        """Stream engine events and update the TUI reactively."""
        self._turn += 1
        self._active_assistant = None
        self._active_thinking = None
        self._active_tool = None

        import logging
        _log = logging.getLogger("duh.tui.query")
        _log.info("Starting query: %s", prompt[:80])

        try:
            async for event in self._engine.run(prompt):
                event_type = event.get("type", "")
                _log.debug("Event: %s", event_type)

                if event_type == "text_delta":
                    text = event.get("text", "")
                    if self._active_assistant is None:
                        self._active_assistant = await self._new_assistant_message()
                    self._active_assistant.append(text)
                    # Scroll log to bottom
                    log = self.query_one("#message-log", ScrollableContainer)
                    log.scroll_end(animate=False)

                elif event_type == "thinking_delta":
                    if self._debug:
                        text = event.get("text", "")
                        if self._active_thinking is None:
                            self._active_thinking = await self._new_thinking_widget()
                        self._active_thinking.append(text)

                elif event_type == "tool_use":
                    # Finish the current assistant message first
                    self._active_assistant = None
                    name = event.get("name", "?")
                    inp = event.get("input", {})
                    self._active_tool = await self._new_tool_widget(name, inp)

                elif event_type == "tool_result":
                    if self._active_tool is not None:
                        output = str(event.get("output", ""))
                        is_error = bool(event.get("is_error"))
                        self._active_tool.set_result(output, is_error)
                        self._active_tool = None

                elif event_type == "assistant":
                    # Full assistant message arrived — final markdown render
                    if self._active_assistant is not None:
                        self._active_assistant.finish()
                    self._active_assistant = None

                elif event_type == "error":
                    error_text = str(event.get("error", "unknown error"))
                    await self._add_error_message(error_text)

                elif event_type == "done":
                    stop = event.get("stop_reason", "")
                    turns = event.get("turns", 0)
                    if stop == "max_turns":
                        await self._add_error_message(
                            f"Reached {turns}-turn limit. Use --max-turns to increase."
                        )
                    if self._active_assistant is not None:
                        self._active_assistant.finish()
                        self._active_assistant = None

                elif event_type == "budget_warning":
                    msg = event.get("message", "")
                    await self._add_error_message(f"Budget warning: {msg}")

                elif event_type == "budget_exceeded":
                    msg = event.get("message", "")
                    await self._add_error_message(f"Budget exceeded: {msg}")

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            _log.exception("Query error: %s", exc)
            # Show the error prominently — don't swallow it
            error_msg = str(exc)
            if "400" in error_msg or "Bad Request" in error_msg:
                error_msg = f"API Error (400 Bad Request): {error_msg}\n\nThis usually means the conversation context is too long. Try starting a new session or use /compact."
            elif "401" in error_msg or "Unauthorized" in error_msg:
                error_msg = f"API Error (401): Check your API key. {error_msg}"
            elif "429" in error_msg or "rate" in error_msg.lower():
                error_msg = f"Rate limited: {error_msg}\n\nWait a moment and try again."
            elif "timeout" in error_msg.lower():
                error_msg = f"Request timed out: {error_msg}\n\nThe API took too long to respond. Try again."
            await self._add_error_message(error_msg)
        finally:
            # Update token counts from engine
            try:
                from duh.kernel.tokens import estimate_cost

                self._input_tokens = getattr(self._engine, "total_input_tokens", 0)
                self._output_tokens = getattr(self._engine, "total_output_tokens", 0)
                self._cost = estimate_cost(
                    self._model,
                    self._input_tokens,
                    self._output_tokens,
                )
            except Exception:
                pass

            self._refresh_status()

            # Save session to disk so --continue can resume it
            try:
                store = getattr(self._engine, "_session_store", None)
                sid = getattr(self._engine, "_session_id", None)
                msgs = getattr(self._engine, "_messages", [])
                if store and sid and msgs:
                    import asyncio as _save_aio
                    _save_aio.get_event_loop().create_task(store.save(sid, msgs))
                    _log.info("Saved session %s (%d messages)", sid, len(msgs))
            except Exception as save_err:
                _log.warning("Session save failed: %s", save_err)

            # Re-enable input
            inp = self.query_one("#prompt-input", Input)
            inp.disabled = False
            inp.focus()
            self.query_one("#send-button", Button).disabled = False

    # ----------------------------------------------------------------- quit

    def action_quit(self) -> int:
        self.exit(0)
        return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_tui(args: argparse.Namespace) -> int:
    """Build the engine from *args* and launch the Textual TUI.

    This mirrors the structure of ``run_repl`` in ``duh/cli/repl.py``:
    same provider resolution, same engine setup, different frontend.
    """
    import logging
    import os

    # Enable logging to file for TUI debugging
    log_file = os.path.expanduser("~/.config/duh/logs/tui.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logging.basicConfig(
        filename=log_file,
        level=logging.DEBUG if getattr(args, "debug", False) else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("duh.tui")
    logger.info("TUI starting")

    from duh.adapters.approvers import ApprovalMode, AutoApprover, InteractiveApprover, TieredApprover
    from duh.adapters.native_executor import NativeExecutor
    from duh.adapters.simple_compactor import SimpleCompactor
    from duh.adapters.file_store import FileStore
    from duh.cli.runner import SYSTEM_PROMPT, BRIEF_INSTRUCTION
    from duh.hooks import HookRegistry
    from duh.kernel.deps import Deps
    from duh.kernel.engine import Engine, EngineConfig
    from duh.kernel.git_context import get_git_context, get_git_warnings
    from duh.providers.registry import (
        build_model_backend,
        resolve_provider_name,
    )
    from duh.tools.registry import get_all_tools

    def _check_ollama() -> bool:
        try:
            import httpx

            r = httpx.get("http://localhost:11434/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    provider_name = resolve_provider_name(
        explicit_provider=getattr(args, "provider", None),
        model=getattr(args, "model", None),
        check_ollama=_check_ollama,
    )

    if not provider_name:
        sys.stderr.write(
            "Error: No provider available.\n"
            "  Option 1: export ANTHROPIC_API_KEY=sk-ant-...\n"
            "  Option 2: start Ollama (ollama serve)\n"
        )
        return 1

    backend = build_model_backend(provider_name, getattr(args, "model", None))
    if not backend.ok:
        sys.stderr.write(f"Error: {backend.error}\n")
        return 1

    model = backend.model
    cwd = os.getcwd()

    # Load skills (CC-compatible: ~/.claude/skills, .claude/skills, .duh/skills)
    from duh.kernel.skill import load_all_skills
    loaded_skills = load_all_skills(cwd)
    tools = list(get_all_tools(skills=loaded_skills))

    # Load project config for trifecta and MCP settings
    from duh.config import load_config
    app_config = load_config(cwd=cwd)

    system_prompt_parts = [getattr(args, "system_prompt", None) or SYSTEM_PROMPT]
    if getattr(args, "brief", False):
        system_prompt_parts.append(BRIEF_INSTRUCTION)

    # Load project instructions (DUH.md, CLAUDE.md, AGENTS.md, rules)
    from duh.config import load_instructions
    for instruction in load_instructions(cwd):
        system_prompt_parts.append(instruction)

    # Environment context — tell the model where it is
    import platform as _platform
    system_prompt_parts.append(
        f"## Environment\n\n"
        f"- Working directory: {cwd}\n"
        f"- Platform: {sys.platform}\n"
        f"- Shell: {os.environ.get('SHELL', 'unknown')}\n"
        f"- Python: {_platform.python_version()}\n"
    )

    git_ctx = get_git_context(cwd)
    if git_ctx:
        system_prompt_parts.append(git_ctx)

    for warning in get_git_warnings(cwd):
        sys.stderr.write(f"\033[33mWARNING: {warning}\033[0m\n")

    system_prompt = "\n\n".join(system_prompt_parts)

    executor = NativeExecutor(tools=tools, cwd=cwd)

    # TUI mode: InteractiveApprover blocks on stdin (impossible in Textual).
    # Default to AutoApprover; user can still restrict via --approval-mode.
    approval_mode_str = getattr(args, "approval_mode", None)
    if approval_mode_str:
        approver: Any = TieredApprover(mode=ApprovalMode(approval_mode_str), cwd=cwd)
    else:
        approver = AutoApprover()

    compactor = SimpleCompactor()
    store = FileStore()
    hook_registry = HookRegistry()

    deps = Deps(
        call_model=backend.call_model,
        run_tool=executor.run,
        approve=approver.check,
        compact=compactor.compact,
        hook_registry=hook_registry,
    )

    # Wire AgentTool now that Deps and tools are both built.
    for t in tools:
        if getattr(t, "name", "") == "Agent":
            t._parent_deps = deps
            t._parent_tools = tools
            break

    max_cost = getattr(args, "max_cost", None)
    if max_cost is None:
        env_cost = os.environ.get("DUH_MAX_COST")
        if env_cost is not None:
            try:
                max_cost = float(env_cost)
            except (ValueError, TypeError):
                pass

    trifecta_ack = getattr(args, "i_understand_the_lethal_trifecta", False)
    if not trifecta_ack:
        try:
            trifecta_ack = app_config.trifecta_acknowledged
        except (NameError, AttributeError):
            pass

    engine_config = EngineConfig(
        model=model,
        fallback_model=getattr(args, "fallback_model", None),
        system_prompt=system_prompt,
        tools=tools,
        max_turns=getattr(app_config, "max_turns", None) or getattr(args, "max_turns", 100),
        max_cost=max_cost,
        trifecta_acknowledged=trifecta_ack,
    )

    engine = Engine(deps=deps, config=engine_config, session_store=store)

    # --- Session resume (--continue / --resume) ---
    resume_id = getattr(args, "resume", None)
    continue_session = getattr(args, "continue_session", False)

    if continue_session or resume_id:
        import asyncio as _aio
        session_id_to_load = None

        if resume_id:
            session_id_to_load = resume_id
        else:
            sessions = _aio.run(store.list_sessions())
            if sessions:
                sessions.sort(key=lambda s: s.get("modified", ""), reverse=True)
                session_id_to_load = sessions[0]["session_id"]
                logger.info("Continuing most recent session: %s", session_id_to_load)
            else:
                sys.stderr.write("No sessions found to continue.\n")

        if session_id_to_load:
            loaded = _aio.run(store.load(session_id_to_load))
            if loaded:
                from duh.kernel.messages import Message as Msg
                for raw in loaded:
                    engine._messages.append(
                        Msg(role=raw.get("role", "user"), content=raw.get("content", ""))
                    )
                engine._session_id = session_id_to_load
                logger.info("Resumed session %s with %d messages", session_id_to_load, len(loaded))
                # ADR-057: No post-resume force-compact needed — sessions
                # now have correct alternation (including tool_result messages)
                # so they load at the right size. Auto-compact in engine.run()
                # handles context limits if needed.

    # Collect resumed messages for display
    _resumed_for_display = []
    if (continue_session or resume_id) and session_id_to_load:
        loaded_for_display = _aio.run(store.load(session_id_to_load))
        if loaded_for_display:
            _resumed_for_display = loaded_for_display

    app = DuhApp(
        engine=engine,
        model=model,
        session_id=engine.session_id,
        debug=getattr(args, "debug", False),
        resumed_messages=_resumed_for_display,
    )
    return app.run() or 0
