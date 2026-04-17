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
import time
from typing import Any

from dataclasses import dataclass

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Footer, Header, Input, Label, Static, TextArea

from duh.ui.styles import OutputStyle
from duh.ui.theme import APP_CSS
from duh.ui.logo import LOGO_COMPACT
from duh.ui.file_tree import RecentFilesWidget
from duh.ui.widgets import MessageWidget, ThinkingWidget, ToolCallWidget
from duh.kernel.model_caps import model_context_block, rebuild_system_prompt


# ---------------------------------------------------------------------------
# SubmittableTextArea — multi-line prompt input (ADR-073 Wave 1)
# ---------------------------------------------------------------------------


class SubmittableTextArea(TextArea):
    """A ``TextArea`` that treats ``Enter`` as *submit* and ``Shift+Enter`` /
    ``Ctrl+J`` as *newline*.

    Rationale (ADR-073 Wave 1 #4):
        Textual's default ``Input`` is single-line.  Users composing code
        snippets or multi-paragraph questions need a multi-line editor —
        but with chat-style submission semantics (Enter = send).

    Behaviour:
        * ``enter``           → posts :class:`Submitted`.  No newline inserted.
        * ``shift+enter``     → inserts ``\\n`` at the cursor.
        * ``ctrl+j``          → inserts ``\\n`` (terminal fallback for terminals
                                 that do not distinguish shift+enter from enter).
        * Every other key    → default TextArea behaviour.
    """

    @dataclass
    class Submitted(Message):
        """Posted when the user presses Enter (without modifiers)."""

        text_area: "SubmittableTextArea"
        value: str

        @property
        def control(self) -> "SubmittableTextArea":
            return self.text_area

    async def _on_key(self, event: events.Key) -> None:  # type: ignore[override]
        key = event.key
        # Enter (no modifiers) → submit.
        if key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(text_area=self, value=self.text))
            return
        # Shift+Enter or Ctrl+J → newline (treated identically by default, but
        # some terminals only emit one or the other, so both are supported).
        if key in ("shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            if self.read_only:
                return
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        # All other keys: default behaviour (printable → insert, arrows, etc.)
        await super()._on_key(event)

# Tools whose input contains a file path we want to track.
_FILE_TOOLS = frozenset({"Read", "Write", "Edit", "MultiEdit", "Glob", "Grep"})


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
        cwd: str = "",
        approval_label: str = "auto-approve",
    ) -> None:
        super().__init__()
        self._engine = engine
        self._model = model
        self._session_id = session_id
        self._debug = debug
        self._approval_label = approval_label
        self._resumed_messages = resumed_messages or []
        self._cwd = cwd
        self._output_style: OutputStyle = OutputStyle.DEFAULT
        self._coordinator_mode: bool = False

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

        # Progress indicators (ADR-067 P1): elapsed time per tool call
        self._tool_start: float = 0.0

        # Recent files sidebar (ADR-067 P2)
        self._recent_files: list[str] = []
        self._recent_files_widget: RecentFilesWidget | None = None

    # ---------------------------------------------------------------- compose

    def compose(self) -> ComposeResult:
        yield Static(self._header_text(), id="header")
        with Horizontal(id="body"):
            yield self._make_sidebar()
            yield ScrollableContainer(id="message-log")
        with Horizontal(id="input-area"):
            # Multi-line input (ADR-073 Wave 1 #4). ``Enter`` submits,
            # ``Shift+Enter`` / ``Ctrl+J`` insert a newline.
            yield SubmittableTextArea(
                id="prompt-input",
                soft_wrap=True,
                placeholder="Type a message… (Enter to send, Shift+Enter for newline)",
            )
            yield Button("Send", id="send-button", variant="primary")
        yield Static(self._status_text(), id="statusbar")

    # ----------------------------------------------------------------- sidebar

    def _make_sidebar(self) -> Vertical:
        self._recent_files_widget = RecentFilesWidget(id="recent-files")
        sidebar = Vertical(
            Static(
                "[bold magenta]D[/].U.[bold magenta]H[/].\n"
                "[dim]Universal Harness[/]",
                id="sidebar-logo",
            ),
            self._recent_files_widget,
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

    def _rebuild_system_prompt(self, old_model: str, new_model: str) -> None:
        """Rebuild the system prompt after a model switch.

        Replaces the ``<model-context>`` block with updated capabilities
        for *new_model*.  If no block exists yet (sessions started before
        this fix) the block is appended.
        """
        self._engine._config.system_prompt = rebuild_system_prompt(
            self._engine._config.system_prompt, old_model, new_model,
        )

    # ----------------------------------------------------------------- sidebar toggle

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar")
        if "visible" in sidebar.classes:
            sidebar.remove_class("visible")
        else:
            sidebar.add_class("visible")

    # ----------------------------------------------------------------- recent files

    def _track_recent_file(self, path: str) -> None:
        """Add *path* to the recent-files list (deduped, max 10)."""
        if path in self._recent_files:
            self._recent_files.remove(path)
        self._recent_files.insert(0, path)
        self._recent_files = self._recent_files[:10]
        if self._recent_files_widget is not None:
            self._recent_files_widget.set_files(self._recent_files)

    # ----------------------------------------------------------------- on_mount

    async def on_mount(self) -> None:
        """Show welcome banner and restored session messages."""
        log = self.query_one("#message-log", ScrollableContainer)

        # Welcome banner with project awareness
        sid_short = self._session_id[:8] if self._session_id else "new"

        # Count sessions for this project
        session_count = 0
        store = getattr(self._engine, "_session_store", None)
        if store:
            try:
                sessions = await store.list_sessions()
                session_count = len(sessions)
            except Exception:
                pass

        project_line = ""
        if self._cwd:
            project_line = f"[dim]Project:[/] {self._cwd}  "
            project_line += f"[dim]Sessions:[/] {session_count}\n"

        banner = (
            f"[bold magenta]D[/].U.[bold magenta]H[/]. — "
            f"[bold magenta]D[/].U.[bold magenta]H[/]. is a Universal Harness\n\n"
            f"{project_line}"
            f"[dim]Model:[/] {self._model}  "
            f"[dim]Session:[/] {sid_short}  "
            f"[dim]Permissions:[/] {self._approval_label}\n"
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
        self.query_one("#prompt-input", SubmittableTextArea).focus()

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

    async def _new_thinking_widget(self, collapsed: bool = True) -> ThinkingWidget:
        widget = ThinkingWidget(collapsed=collapsed)
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

    @on(SubmittableTextArea.Submitted, "#prompt-input")
    async def handle_textarea_submitted(
        self, _event: SubmittableTextArea.Submitted,
    ) -> None:
        await self._submit()

    async def _submit(self) -> None:
        inp = self.query_one("#prompt-input", SubmittableTextArea)
        text = inp.text.strip()
        if not text:
            return
        inp.clear()

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

    def _build_slash_context(self):
        """Build a SlashContext for delegating to SlashDispatcher.

        The TUI does not yet own a full ``SessionBuild`` (it constructs its
        engine inline in ``run_tui``), so we assemble a minimal context from
        the attributes we have.  ``executor`` / ``task_manager`` / ``plan_mode``
        are ``None`` until ``run_tui`` is migrated to ``SessionBuilder``.
        """
        from duh.cli.slash_commands import SlashContext

        deps = getattr(self._engine, "_deps", None)
        return SlashContext(
            engine=self._engine,
            model=self._model,
            deps=deps,
            executor=getattr(self, "_executor", None),
            task_manager=getattr(self, "_task_manager", None),
            template_state=getattr(self, "_template_state", {}),
            plan_mode=getattr(self, "_plan_mode", None),
            mcp_executor=getattr(self, "_mcp_executor", None),
            provider_name=getattr(self, "_provider_name", ""),
        )

    async def _add_system_message(self, text: str) -> None:
        """Render *text* (captured-stdout-style) into the message log."""
        # Strip the leading two-space indent that sync handlers emit for the
        # readline REPL — looks awkward inside a bordered widget.
        cleaned = "\n".join(
            line[2:] if line.startswith("  ") else line
            for line in text.splitlines()
        ).rstrip()
        if not cleaned:
            return
        await self._add_widget(Static(cleaned, classes="welcome-banner"))

    async def _handle_slash(self, text: str) -> bool:
        """Handle /commands locally. Returns True if handled.

        Delegates to :class:`SlashDispatcher.async_dispatch` for every
        command that is not TUI-specific — see ADR-073 Wave 1 tasks 1 & 2.
        Only ``/style``, ``/mode``, ``/session`` (a TUI-only info panel
        distinct from the REPL's ``/status``), ``/clear`` (needs to wipe
        visible widgets) and the ``/quit``/``/q`` aliases stay local.
        """
        from duh.cli.slash_commands import SlashDispatcher

        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        # --- TUI-local commands (routed BEFORE the shared dispatcher) -----
        if cmd == "/style":
            await self._handle_style_local(arg)
            return True

        if cmd == "/mode":
            await self._handle_mode_local(arg)
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

        if cmd in ("/quit", "/q"):
            self.exit(0)
            return True

        # --- /clear: also wipes the visible message log (TUI-only extra).
        if cmd == "/clear":
            log = self.query_one("#message-log", ScrollableContainer)
            await log.remove_children()
            self._engine._messages.clear()
            await self._add_widget(Static(
                "[dim]Messages cleared. Context retained in engine.[/]",
                classes="session-divider",
            ))
            return True

        # --- /model: TUI updates header/statusbar; delegate, then refresh.
        if cmd == "/model" and arg.strip():
            old_model = self._model
            ctx = self._build_slash_context()
            dispatcher = SlashDispatcher(ctx)
            output, new_model = await dispatcher.async_dispatch(cmd, arg)
            if new_model and not new_model.startswith("\x00"):
                self._model = new_model
                self._rebuild_system_prompt(old_model, new_model)
            self._refresh_status()
            await self._add_system_message(output)
            return True

        # --- /compact: sentinel from shared handler signals "run compaction".
        if cmd == "/compact":
            await self._handle_compact_local()
            return True

        # --- Delegate everything else to SlashDispatcher.async_dispatch.
        ctx = self._build_slash_context()
        dispatcher = SlashDispatcher(ctx)
        try:
            output, new_model = await dispatcher.async_dispatch(cmd, arg)
        except Exception as exc:  # noqa: BLE001 — surface the failure
            await self._add_error_message(f"{cmd} failed: {exc}")
            return True

        # --- /help: append TUI-local commands to the shared help output.
        if cmd == "/help" and output:
            output = (
                output
                + "\n[TUI-local]\n"
                + "/style       Toggle output style (default|concise|verbose)\n"
                + "/mode        Toggle coordinator mode (normal|coordinator)\n"
                + "/session     Show TUI session info panel\n"
                + "/quit, /q    Exit the TUI\n"
            )

        # Sentinel returns — /plan and /snapshot still need interactive UI
        # support (tracked separately in Wave 1 task 2 notes).
        if isinstance(new_model, str) and new_model.startswith("\x00"):
            if new_model.startswith("\x00plan\x00"):
                plan_desc = new_model[len("\x00plan\x00"):]
                await self._add_widget(Static(
                    f"[bold]Proposed plan:[/]\n{plan_desc}\n\n"
                    "[dim]Plan mode approval not yet supported in TUI "
                    "(see ADR-073 Wave 1 task 4).[/]",
                    classes="welcome-banner",
                ))
                return True
            if new_model.startswith("\x00snapshot\x00"):
                snap_arg = new_model[len("\x00snapshot\x00"):]
                await self._add_widget(Static(
                    f"[bold]Snapshot:[/] {snap_arg or '(none)'}\n"
                    "[dim]Interactive snapshot apply/discard not yet "
                    "supported in TUI (see ADR-073 Wave 1 task 2).[/]",
                    classes="welcome-banner",
                ))
                return True

        # /exit from the shared dispatcher maps to quitting the TUI.
        if cmd == "/exit":
            self.exit(0)
            return True

        if output:
            await self._add_system_message(output)
        return True

    async def _handle_style_local(self, arg: str) -> None:
        """TUI-local `/style` handler (output style is TUI rendering state)."""
        if arg:
            arg_lower = arg.strip().lower()
            try:
                new_style = OutputStyle(arg_lower)
            except ValueError:
                await self._add_error_message(
                    f"Unknown style '{arg.strip()}'. "
                    f"Choose from: default, concise, verbose"
                )
                return
            self._output_style = new_style
            await self._add_widget(Static(
                f"[green]Output style set to:[/] {new_style.value}",
                classes="session-divider",
            ))
        else:
            await self._add_widget(Static(
                f"[dim]Current output style:[/] {self._output_style.value}",
                classes="session-divider",
            ))

    async def _handle_mode_local(self, arg: str) -> None:
        """TUI-local `/mode` handler (flips coordinator system-prompt prefix)."""
        if arg:
            mode_arg = arg.strip().lower()
            if mode_arg == "coordinator":
                if not self._coordinator_mode:
                    from duh.kernel.coordinator import COORDINATOR_SYSTEM_PROMPT
                    self._engine._config.system_prompt = (
                        COORDINATOR_SYSTEM_PROMPT + "\n\n" + self._engine._config.system_prompt
                    )
                self._coordinator_mode = True
                if self._engine._messages:
                    self._engine._messages[0].metadata["coordinator_mode"] = True
                await self._add_widget(Static(
                    "[green]Mode switched to:[/] coordinator",
                    classes="session-divider",
                ))
            elif mode_arg == "normal":
                if self._coordinator_mode:
                    from duh.kernel.coordinator import COORDINATOR_SYSTEM_PROMPT
                    prompt = self._engine._config.system_prompt
                    prefix = COORDINATOR_SYSTEM_PROMPT + "\n\n"
                    if prompt.startswith(prefix):
                        self._engine._config.system_prompt = prompt[len(prefix):]
                self._coordinator_mode = False
                if self._engine._messages:
                    self._engine._messages[0].metadata["coordinator_mode"] = False
                await self._add_widget(Static(
                    "[green]Mode switched to:[/] normal",
                    classes="session-divider",
                ))
            else:
                await self._add_error_message(
                    f"Unknown mode '{arg.strip()}'. Choose from: normal, coordinator"
                )
        else:
            current = "coordinator" if self._coordinator_mode else "normal"
            await self._add_widget(Static(
                f"[dim]Current mode:[/] {current}",
                classes="session-divider",
            ))

    async def _handle_compact_local(self) -> None:
        """TUI-local `/compact` — runs compaction and reports result."""
        deps = getattr(self._engine, "_deps", None)
        compact_fn = getattr(deps, "compact", None) if deps else None
        if not compact_fn:
            await self._add_error_message("No compactor configured")
            return
        try:
            from duh.kernel.tokens import get_context_limit
            before = len(self._engine._messages)
            limit = int(get_context_limit(self._model) * 0.50)
            self._engine._messages = await compact_fn(
                self._engine._messages, token_limit=limit,
            )
            after = len(self._engine._messages)
            await self._add_widget(Static(
                f"[green]Compacted:[/] {before} → {after} messages",
                classes="session-divider",
            ))
        except Exception as exc:  # noqa: BLE001
            await self._add_error_message(f"Compact failed: {exc}")

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
                    # VERBOSE/debug: expanded. DEFAULT: collapsed. CONCISE: hidden.
                    if self._output_style != OutputStyle.CONCISE:
                        text = event.get("text", "")
                        expanded = self._debug or self._output_style == OutputStyle.VERBOSE
                        if self._active_thinking is None:
                            self._active_thinking = await self._new_thinking_widget(
                                collapsed=not expanded,
                            )
                        self._active_thinking.append(text)

                elif event_type == "tool_use":
                    # Finish the current assistant message first
                    self._active_assistant = None
                    name = event.get("name", "?")
                    inp = event.get("input", {})
                    self._tool_start = time.monotonic()
                    self._active_tool = await self._new_tool_widget(name, inp)

                elif event_type == "tool_result":
                    if self._active_tool is not None:
                        elapsed_ms = (time.monotonic() - self._tool_start) * 1000
                        output = str(event.get("output", ""))
                        is_error = bool(event.get("is_error"))
                        self._active_tool.set_result(
                            output, is_error, elapsed_ms=elapsed_ms,
                        )
                        # Track file paths for recent-files sidebar (ADR-067 P2)
                        tool_name = self._active_tool._tool_name
                        if tool_name in _FILE_TOOLS:
                            tool_input = self._active_tool._input
                            path = (
                                tool_input.get("file_path")
                                or tool_input.get("path")
                                or tool_input.get("pattern")
                                or ""
                            )
                            if path:
                                self._track_recent_file(path)
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

                elif event_type == "context_blocked":
                    msg = event.get("message", "")
                    await self._add_error_message(f"Context blocked: {msg}")

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
            inp = self.query_one("#prompt-input", SubmittableTextArea)
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
    if getattr(args, "coordinator", False):
        from duh.kernel.coordinator import COORDINATOR_SYSTEM_PROMPT
        system_prompt_parts.insert(0, COORDINATOR_SYSTEM_PROMPT)
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

    # Model context block -- rebuilt on /model switch (ADR-070)
    system_prompt_parts.append(model_context_block(model))

    system_prompt = "\n\n".join(system_prompt_parts)

    executor = NativeExecutor(tools=tools, cwd=cwd)

    # TUI mode: InteractiveApprover blocks on stdin (impossible in Textual).
    # When --approval-mode full-auto is set, skip prompts entirely.
    # When a tiered mode is set, use TieredApprover for rule-based gating.
    # Otherwise use TUIApprover (ADR-066 P1) which shows a modal dialog.
    # TUIApprover is wired after the DuhApp is constructed (it needs the app ref).
    approval_mode_str = getattr(args, "approval_mode", None)
    if approval_mode_str and approval_mode_str == "full-auto":
        approver: Any = AutoApprover()
        _approval_label = "full-auto"
    elif approval_mode_str:
        approver = TieredApprover(mode=ApprovalMode(approval_mode_str), cwd=cwd)
        _approval_label = approval_mode_str
    else:
        # Placeholder — replaced with TUIApprover after DuhApp is constructed
        approver = AutoApprover()
        _approval_label = "interactive"

    compactor = SimpleCompactor()
    store = FileStore(cwd=cwd)
    hook_registry = HookRegistry()

    deps = Deps(
        call_model=backend.call_model,
        run_tool=executor.run,
        approve=approver.check,
        compact=compactor.compact,
        hook_registry=hook_registry,
    )

    # Wire AgentTool and SwarmTool now that Deps and tools are both built.
    for t in tools:
        if getattr(t, "name", "") in ("Agent", "Swarm"):
            t._parent_deps = deps
            t._parent_tools = tools

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
                    meta = raw.get("metadata", {}) if isinstance(raw, dict) else getattr(raw, "metadata", {})
                    engine._messages.append(
                        Msg(role=raw.get("role", "user"), content=raw.get("content", ""), metadata=meta or {})
                    )
                engine._session_id = session_id_to_load
                logger.info("Resumed session %s with %d messages", session_id_to_load, len(loaded))

                # --- ADR-063: Restore coordinator mode from session metadata ---
                if engine._messages and engine._messages[0].metadata.get("coordinator_mode"):
                    from duh.kernel.coordinator import COORDINATOR_SYSTEM_PROMPT
                    if not engine._config.system_prompt.startswith(COORDINATOR_SYSTEM_PROMPT):
                        engine._config.system_prompt = (
                            COORDINATOR_SYSTEM_PROMPT + "\n\n" + engine._config.system_prompt
                        )

                # ADR-057: No post-resume force-compact needed — sessions
                # now have correct alternation (including tool_result messages)
                # so they load at the right size. Auto-compact in engine.run()
                # handles context limits if needed.

                # --- ADR-058 Phase 3: --summarize compacts on resume ---
                if getattr(args, "summarize", False) and engine._messages:
                    compact_fn = deps.compact
                    if compact_fn:
                        before_count = len(engine._messages)
                        engine._messages = _aio.run(
                            compact_fn(engine._messages, token_limit=compactor.default_limit // 2)
                        )
                        after_count = len(engine._messages)
                        logger.info(
                            "summarize: compacted %d -> %d messages",
                            before_count, after_count,
                        )

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
        cwd=cwd,
        approval_label=_approval_label,
    )

    # ADR-066 P1: Wire TUIApprover now that we have the app instance.
    # TUIApprover needs the app to push_screen_wait, so it can only be
    # created after the DuhApp is instantiated.
    # ADR-073 Wave 1 task 3: honour approval_timeout_seconds from config.
    if _approval_label == "interactive":
        from duh.kernel.permission_cache import SessionPermissionCache
        from duh.ui.tui_approver import TUIApprover

        _tui_cache = SessionPermissionCache()
        tui_approver = TUIApprover(
            app=app,
            permission_cache=_tui_cache,
            timeout_seconds=app_config.approval_timeout_seconds,
        )
        deps.approve = tui_approver.check

    # Restore coordinator mode from CLI flag or session metadata (ADR-063)
    app._coordinator_mode = getattr(args, "coordinator", False)
    if not app._coordinator_mode and engine._messages:
        if engine._messages[0].metadata.get("coordinator_mode"):
            app._coordinator_mode = True
    return app.run() or 0
