"""Slash command dispatch for the D.U.H. REPL.

Extracts slash command handling from the monolithic ``_handle_slash`` function
into a table-driven dispatcher with one method per command.  Each handler is
kept under ~30 lines and receives a ``SlashContext`` that bundles all the
state a handler might need.

Usage from the REPL::

    ctx = SlashContext(engine=engine, model=model, deps=deps, ...)
    dispatcher = SlashDispatcher(ctx)
    keep_going, new_model = dispatcher.dispatch(name, arg)

The original ``_handle_slash`` function in ``repl.py`` delegates here.
"""

from __future__ import annotations

import getpass
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from duh.adapters.native_executor import NativeExecutor
from duh.cli.runner import BRIEF_INSTRUCTION
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine
from duh.kernel.plan_mode import PlanMode

logger = logging.getLogger("duh")


@dataclass
class SlashContext:
    """All state a slash command handler may need.

    Bundles the parameter cluster that was previously threaded through
    ``_handle_slash`` as positional + keyword arguments.
    """

    engine: Engine
    model: str
    deps: Deps
    executor: NativeExecutor | None = None
    task_manager: Any | None = None
    template_state: dict[str, Any] = field(default_factory=dict)
    plan_mode: PlanMode | None = None
    mcp_executor: Any | None = None
    provider_name: str = ""


# Type alias for handler methods.  Each returns (should_continue, new_model).
_HandlerResult = tuple[bool, str]


# ---------------------------------------------------------------------------
# Cost-delta warning for /model switches
# ---------------------------------------------------------------------------

# Threshold: warn when the new model's input price is at least this many
# multiples of the current model's input price.
_COST_WARN_RATIO = 10.0


def _short_name(model: str) -> str:
    """Strip dates/sizes for a friendly short label (haiku/sonnet/opus/...)."""
    lower = model.lower()
    for tag in ("haiku", "sonnet", "opus"):
        if tag in lower:
            return tag
    if "gpt-4o-mini" in lower:
        return "gpt-4o-mini"
    if "gpt-4o" in lower:
        return "gpt-4o"
    if "ollama" in lower or "llama" in lower or "qwen" in lower:
        return "local"
    return model


def _format_cost_delta_warning(current: str, target: str) -> str:
    """Return a one-line warning when switching to a much pricier model.

    Returns ``""`` when the switch is to a cheaper or comparable model, or
    when either model has unknown pricing (free/local models price 0 are
    handled explicitly).
    """
    from duh.kernel.tokens import _resolve_pricing

    cur_in, cur_out = _resolve_pricing(current)
    new_in, new_out = _resolve_pricing(target)

    def _fmt(price: float) -> str:
        # 15.0 -> "15", 0.25 -> "0.25", 0.6 -> "0.60" -- choose the shortest
        # representation that's still unambiguous.
        if price == int(price):
            return f"{int(price)}"
        if price < 1.0:
            return f"{price:.2f}"
        return f"{price:g}"

    # Free model -> paid model: always warn.
    if cur_in == 0.0 and new_in > 0.0:
        return (
            f"  ⚠️  Switching from {_short_name(current)} (free) to "
            f"{_short_name(target)} (${_fmt(new_in)}/M in, ${_fmt(new_out)}/M out)"
        )

    # Avoid division by zero / no pricing info; nothing useful to say.
    if cur_in <= 0.0 or new_in <= 0.0:
        return ""

    ratio = new_in / cur_in
    if ratio < _COST_WARN_RATIO:
        return ""

    return (
        f"  ⚠️  Switching from {_short_name(current)} "
        f"(${_fmt(cur_in)}/M in, ${_fmt(cur_out)}/M out) "
        f"to {_short_name(target)} "
        f"(${_fmt(new_in)}/M in, ${_fmt(new_out)}/M out) "
        f"— {ratio:.0f}x cost increase"
    )


class SlashDispatcher:
    """Table-driven dispatcher for REPL slash commands."""

    def __init__(self, ctx: SlashContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------
    # Dispatch table — maps command name (with leading /) to a method.
    # Populated at the bottom of this class via _register().
    # ------------------------------------------------------------------
    _HANDLERS: dict[str, Callable[[SlashDispatcher, str], _HandlerResult]] = {}

    def dispatch(self, name: str, arg: str) -> _HandlerResult:
        """Dispatch *name* (e.g. ``"/help"``) to the matching handler.

        Returns ``(should_continue, new_model)`` — the same contract as the
        original ``_handle_slash``.
        """
        handler = self._HANDLERS.get(name)
        if handler is not None:
            return handler(self, arg)
        sys.stdout.write(f"  Unknown command: {name}. Type /help for commands.\n")
        return True, self.ctx.model

    # ------------------------------------------------------------------
    # Private helpers shared across handlers
    # ------------------------------------------------------------------

    def _connected_providers(self) -> list[str]:
        """Probe which providers look authenticated right now.

        Looks up ``has_anthropic_available``, ``has_openai_available``, and
        ``has_openai_chatgpt_oauth`` through the ``duh.cli.repl`` module
        namespace so that unit tests can monkeypatch them there — the same
        strategy the original inline ``_connected_providers`` closure used.
        """
        import duh.cli.repl as _repl_mod

        def _check_ollama() -> bool:
            try:
                import httpx
                r = httpx.get("http://localhost:11434/api/tags", timeout=2)
                return r.status_code == 200
            except Exception:
                return False

        providers: list[str] = []
        if _repl_mod.has_anthropic_available():
            providers.append("anthropic")
        if _repl_mod.has_openai_available() or _repl_mod.has_openai_chatgpt_oauth():
            providers.append("openai")
        if _check_ollama():
            providers.append("ollama")
        if not providers and self.ctx.provider_name:
            providers.append(self.ctx.provider_name)
        return list(dict.fromkeys(providers))

    def _switch_backend_for_model(self, target_model: str) -> tuple[bool, str]:
        """Try to swap the underlying provider to match *target_model*.

        Always updates the engine's model string.  Provider swap is
        best-effort: if the new provider cannot be built we keep the
        existing call_model and return a warning string.

        Uses ``duh.cli.repl`` namespace for ``infer_provider_from_model``
        and ``build_model_backend`` so tests can monkeypatch on repl_mod.
        """
        import duh.cli.repl as _repl_mod

        self.ctx.engine._config.model = target_model
        resolved_provider = _repl_mod.infer_provider_from_model(target_model)
        if not resolved_provider:
            return False, (
                f"Model set to '{target_model}'. Provider unknown — "
                "use a model name like claude-*, gpt-*, o1/o3, or *codex*."
            )

        backend = _repl_mod.build_model_backend(resolved_provider, target_model)
        if not backend.ok:
            return False, backend.error or f"Provider {resolved_provider} not configured"
        self.ctx.deps.call_model = backend.call_model
        self.ctx.engine._config.model = backend.model
        return True, backend.provider

    # ------------------------------------------------------------------
    # Individual handlers — one per slash command
    # ------------------------------------------------------------------

    def _handle_help(self, arg: str) -> _HandlerResult:
        from duh.cli.repl import SLASH_COMMANDS
        for k, v in SLASH_COMMANDS.items():
            sys.stdout.write(f"  {k:12s} {v}\n")
        return True, self.ctx.model

    def _handle_model(self, arg: str) -> _HandlerResult:
        import duh.cli.repl as _repl_mod
        from duh.kernel.model_caps import rebuild_system_prompt

        model = self.ctx.model
        if arg:
            requested = arg.strip()
            if requested.lower() == "codex":
                requested = "gpt-5.2-codex"

            # Cost warning when jumping to a much pricier model.
            warning = _format_cost_delta_warning(model, requested)
            if warning:
                sys.stdout.write(warning + "\n")

            ok, result = self._switch_backend_for_model(requested)
            self.ctx.engine._config.system_prompt = rebuild_system_prompt(
                self.ctx.engine._config.system_prompt, model, requested,
            )
            if ok:
                sys.stdout.write(f"  Model changed to: {requested} ({result})\n")
            else:
                sys.stdout.write(f"  Model changed to: {requested} ({result})\n")
            return True, requested
        inferred = _repl_mod.infer_provider_from_model(model) or self.ctx.provider_name or "unknown"
        sys.stdout.write(f"  Current model: {model} ({inferred})\n")
        return True, model

    def _handle_connect(self, arg: str) -> _HandlerResult:
        """Handle /connect openai|anthropic [...].

        Uses ``duh.cli.repl`` namespace for ``connect_openai_chatgpt_subscription``,
        ``connect_openai_api_key``, and ``connect_anthropic_api_key`` so tests
        can monkeypatch on repl_mod.
        """
        import duh.cli.repl as _repl_mod

        model = self.ctx.model
        parts = arg.strip().split()
        provider = (parts[0].lower() if parts else "openai")
        if provider not in ("openai", "anthropic"):
            sys.stdout.write("  Supported: /connect openai | /connect anthropic\n")
            return True, model

        method = parts[1].lower() if len(parts) > 1 else ""
        if provider == "openai" and not method:
            sys.stdout.write(
                "  Select auth method:\n"
                "    1) ChatGPT Plus/Pro login\n"
                "    2) API key\n"
                "  Choice [1/2]: "
            )
            sys.stdout.flush()
            try:
                choice = input().strip()
            except (EOFError, KeyboardInterrupt):
                sys.stdout.write("\n")
                return True, model
            method = "chatgpt" if choice in ("", "1") else "api-key"

        if provider == "openai":
            if method in ("chatgpt", "oauth", "plus", "pro"):
                ok, msg = _repl_mod.connect_openai_chatgpt_subscription(input_fn=input)
                sys.stdout.write(f"  {msg}\n")
                return True, model
            if method in ("api-key", "apikey", "key"):
                ok, msg = _repl_mod.connect_openai_api_key(input_fn=getpass.getpass)
                sys.stdout.write(f"  {msg}\n")
                return True, model
            sys.stdout.write("  Usage: /connect openai [chatgpt|api-key]\n")
            return True, model

        if provider == "anthropic":
            ok, msg = _repl_mod.connect_anthropic_api_key(input_fn=getpass.getpass)
            sys.stdout.write(f"  {msg}\n")
            return True, model

        return True, model

    def _handle_models(self, arg: str) -> _HandlerResult:
        """Handle /models and /models use <name>.

        Uses ``duh.cli.repl`` namespace for ``resolve_openai_auth_mode``,
        ``has_openai_chatgpt_oauth``, ``available_models_for_provider``, and
        ``infer_provider_from_model`` so tests can monkeypatch on repl_mod.
        """
        import duh.cli.repl as _repl_mod
        from duh.providers.registry import get_openai_api_key

        model = self.ctx.model
        sub = arg.strip().split(None, 1)
        connected = self._connected_providers()
        if sub and sub[0].lower() == "use":
            if len(sub) < 2 or not sub[1].strip():
                sys.stdout.write("  Usage: /models use <name>\n")
                return True, model
            target = sub[1].strip()
            if target.lower() == "codex":
                target = "gpt-5.2-codex"
            ok, result = self._switch_backend_for_model(target)
            sys.stdout.write(f"  Model changed to: {target} ({result})\n")
            return True, target

        effective_provider = _repl_mod.infer_provider_from_model(model) or self.ctx.provider_name or "unknown"
        sys.stdout.write(f"  Current model: {model}\n")
        sys.stdout.write(f"  Current provider: {effective_provider}\n")

        if "openai" in connected:
            has_key = bool(get_openai_api_key())
            has_oauth = _repl_mod.has_openai_chatgpt_oauth()
            mode = _repl_mod.resolve_openai_auth_mode(model)
            if mode == "chatgpt":
                mode_label = "ChatGPT subscription"
            elif has_key:
                mode_label = "API key"
            elif has_oauth:
                mode_label = "ChatGPT subscription (available for Codex models)"
            else:
                mode_label = "not connected"
            sys.stdout.write(f"  OpenAI auth: {mode_label}\n")

        if not connected:
            sys.stdout.write("  No connected providers.\n")
            return True, model

        for pname in connected:
            models = _repl_mod.available_models_for_provider(
                pname,
                current_model=model if pname == effective_provider else None,
            )
            sys.stdout.write(f"  [{pname}]\n")
            if not models:
                sys.stdout.write("    (no models found)\n")
                continue
            for m in models:
                marker = "*" if m == model else " "
                sys.stdout.write(f"  {marker} {m}\n")
        return True, model

    def _handle_brief(self, arg: str) -> _HandlerResult:
        engine = self.ctx.engine
        model = self.ctx.model
        sp = engine._config.system_prompt
        current_text = "\n\n".join(sp) if isinstance(sp, list) else sp
        is_on = BRIEF_INSTRUCTION in current_text

        arg_lower = arg.strip().lower()
        if arg_lower == "on":
            want_on = True
        elif arg_lower == "off":
            want_on = False
        else:
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

    def _handle_cost(self, arg: str) -> _HandlerResult:
        sys.stdout.write(f"  {self.ctx.engine.cost_summary(self.ctx.model)}\n")
        return True, self.ctx.model

    def _handle_status(self, arg: str) -> _HandlerResult:
        engine = self.ctx.engine
        model = self.ctx.model
        sys.stdout.write(
            f"  Session: {engine.session_id[:8]}...\n"
            f"  Turns:   {engine.turn_count}\n"
            f"  Messages: {len(engine.messages)}\n"
            f"  Model:   {model}\n"
        )
        return True, model

    def _handle_context(self, arg: str) -> _HandlerResult:
        from duh.cli.repl import context_breakdown
        sys.stdout.write(context_breakdown(self.ctx.engine, self.ctx.model) + "\n")
        return True, self.ctx.model

    def _handle_changes(self, arg: str) -> _HandlerResult:
        executor = self.ctx.executor
        model = self.ctx.model
        if executor is not None:
            text = executor.file_tracker.summary()
            diff_stat = executor.file_tracker.diff_summary_sync(cwd=executor._cwd)
            if diff_stat and diff_stat != "No files modified.":
                text += f"\n\n  Git diff:\n{diff_stat}"
        else:
            text = "No file tracker available."
        sys.stdout.write(f"  {text}\n")
        return True, model

    def _handle_git(self, arg: str) -> _HandlerResult:
        from duh.kernel.git_context import get_git_context
        cwd = os.getcwd()
        ctx = get_git_context(cwd)
        if ctx:
            display = ctx.replace("<git-context>", "").replace("</git-context>", "").strip()
            sys.stdout.write(f"  {display}\n")
        else:
            sys.stdout.write("  Not in a git repository.\n")
        return True, self.ctx.model

    def _handle_tasks(self, arg: str) -> _HandlerResult:
        if self.ctx.task_manager is not None:
            sys.stdout.write(f"  {self.ctx.task_manager.summary()}\n")
        else:
            sys.stdout.write("  No tasks.\n")
        return True, self.ctx.model

    def _handle_jobs(self, arg: str) -> _HandlerResult:
        from duh.tools.bash import get_job_queue
        queue = get_job_queue()
        model = self.ctx.model
        if arg:
            try:
                result = queue.results(arg.strip())
                sys.stdout.write(f"  {result}\n")
            except KeyError:
                sys.stdout.write(f"  Unknown job id: {arg.strip()}\n")
            except ValueError as exc:
                sys.stdout.write(f"  {exc}\n")
        else:
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

    def _handle_search(self, arg: str) -> _HandlerResult:
        from duh.cli.repl import _search_messages
        model = self.ctx.model
        if not arg:
            sys.stdout.write("  Usage: /search <query>\n")
            return True, model
        _search_messages(self.ctx.engine.messages, arg)
        return True, model

    def _handle_template(self, arg: str) -> _HandlerResult:
        from duh.cli.repl import _handle_template_command
        _handle_template_command(arg, self.ctx.template_state or {})
        return True, self.ctx.model

    def _handle_plan(self, arg: str) -> _HandlerResult:
        model = self.ctx.model
        plan_mode = self.ctx.plan_mode
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
        return True, f"\x00plan\x00{arg.strip()}"

    def _handle_pr(self, arg: str) -> _HandlerResult:
        from duh.cli.repl import _handle_pr_command
        _handle_pr_command(arg)
        return True, self.ctx.model

    def _handle_undo(self, arg: str) -> _HandlerResult:
        executor = self.ctx.executor
        model = self.ctx.model
        if executor is None:
            sys.stdout.write("  No executor available.\n")
            return True, model
        try:
            path, msg = executor.undo_stack.undo()
            sys.stdout.write(f"  {msg}\n")
        except IndexError:
            sys.stdout.write("  Nothing to undo.\n")
        return True, model

    def _handle_health(self, arg: str) -> _HandlerResult:
        from duh.cli.doctor import _format_latency
        from duh.kernel.health_check import HealthChecker

        model = self.ctx.model
        checker = HealthChecker(timeout=5.0)

        sys.stdout.write("  Running health checks...\n")

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

        mcp_executor = self.ctx.mcp_executor
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

        if disabled:
            sys.stdout.write(f"  Unhealthy: {', '.join(disabled)}\n")
        else:
            sys.stdout.write("  All checks passed.\n")
        return True, model

    def _handle_audit(self, arg: str) -> _HandlerResult:
        from duh.security.audit import AuditLogger

        model = self.ctx.model
        limit = 20
        if arg.strip().isdigit():
            limit = int(arg.strip())
        logger_instance = getattr(self.ctx.deps, "audit_logger", None)
        if logger_instance is None:
            logger_instance = AuditLogger()
        entries = logger_instance.read_entries(limit=limit)
        if not entries:
            sys.stdout.write("  No audit entries found.\n")
        else:
            sys.stdout.write(f"  Last {len(entries)} audit entries:\n")
            for e in entries:
                ts = e.get("ts", "?")
                tool = e.get("tool", "?")
                status = e.get("status", "?")
                ms = e.get("ms", 0)
                sys.stdout.write(f"    {ts}  {tool:20s}  {status:7s}  {ms}ms\n")
        return True, model

    def _handle_clear(self, arg: str) -> _HandlerResult:
        self.ctx.engine._messages.clear()
        sys.stdout.write("  Conversation cleared.\n")
        return True, self.ctx.model

    def _handle_compact(self, arg: str) -> _HandlerResult:
        model = self.ctx.model
        if self.ctx.deps.compact:
            return True, "\x00compact\x00"
        else:
            sys.stdout.write("  No compactor configured.\n")
        return True, model

    def _handle_compact_stats(self, arg: str) -> _HandlerResult:
        sys.stdout.write(f"  {self.ctx.engine.compact_stats.summary()}\n")
        return True, self.ctx.model

    def _handle_snapshot(self, arg: str) -> _HandlerResult:
        return True, f"\x00snapshot\x00{arg.strip()}"

    def _handle_attach(self, arg: str) -> _HandlerResult:
        model = self.ctx.model
        engine = self.ctx.engine
        if not arg.strip():
            sys.stdout.write(
                "  Usage: /attach <path>  — attach a file to the next message\n"
                "  Example: /attach screenshot.png\n"
            )
            return True, model
        from duh.kernel.attachments import AttachmentManager
        mgr = AttachmentManager()
        try:
            att = mgr.read_file(arg.strip())
            if not hasattr(engine, "_pending_attachments"):
                engine._pending_attachments = []
            engine._pending_attachments.append(att)
            sys.stdout.write(
                f"  Attachment queued: {att.name} "
                f"({att.content_type}, {att.size:,} bytes)\n"
                "  It will be included with your next message.\n"
            )
        except FileNotFoundError as exc:
            sys.stdout.write(f"  Error: {exc}\n")
        except ValueError as exc:
            sys.stdout.write(f"  Error: {exc}\n")
        return True, model

    def _handle_memory(self, arg: str) -> _HandlerResult:
        from duh.adapters.memory_store import FileMemoryStore

        model = self.ctx.model
        mem_store = FileMemoryStore(cwd=os.getcwd())
        sub_parts = arg.strip().split(None, 1)
        sub = sub_parts[0].lower() if sub_parts else "list"
        sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""

        if sub == "list":
            facts = mem_store.list_facts()
            if not facts:
                sys.stdout.write("  No memory facts stored.\n")
            else:
                sys.stdout.write(f"  {len(facts)} fact(s):\n")
                for f in facts:
                    key = f.get("key", "?")
                    value = f.get("value", "")
                    tags = f.get("tags", [])
                    tag_str = f" [{', '.join(tags)}]" if tags else ""
                    sys.stdout.write(f"    {key}: {value}{tag_str}\n")

        elif sub == "search":
            if not sub_arg:
                sys.stdout.write("  Usage: /memory search <query>\n")
            else:
                results = mem_store.recall_facts(sub_arg)
                if not results:
                    sys.stdout.write(f"  No facts matching '{sub_arg}'.\n")
                else:
                    sys.stdout.write(f"  {len(results)} match(es):\n")
                    for f in results:
                        key = f.get("key", "?")
                        value = f.get("value", "")
                        tags = f.get("tags", [])
                        tag_str = f" [{', '.join(tags)}]" if tags else ""
                        sys.stdout.write(f"    {key}: {value}{tag_str}\n")

        elif sub == "show":
            if not sub_arg:
                sys.stdout.write("  Usage: /memory show <key>\n")
            else:
                facts = mem_store.list_facts()
                match = [f for f in facts if f.get("key") == sub_arg]
                if not match:
                    sys.stdout.write(f"  No fact with key '{sub_arg}'.\n")
                else:
                    f = match[0]
                    tags = f.get("tags", [])
                    tag_str = f"  Tags: {', '.join(tags)}" if tags else ""
                    ts = f.get("timestamp", "")
                    ts_str = f"  Saved: {ts}" if ts else ""
                    sys.stdout.write(
                        f"  Key:   {f.get('key', '?')}\n"
                        f"  Value: {f.get('value', '')}\n"
                        f"{tag_str}\n{ts_str}\n"
                    )

        elif sub == "delete":
            if not sub_arg:
                sys.stdout.write("  Usage: /memory delete <key>\n")
            else:
                deleted = mem_store.delete_fact(sub_arg)
                if deleted:
                    sys.stdout.write(f"  Deleted fact '{sub_arg}'.\n")
                else:
                    sys.stdout.write(f"  No fact with key '{sub_arg}'.\n")

        elif sub == "gc":
            from duh.kernel.memory_decay import gc_memories

            max_facts = 200
            if sub_arg:
                try:
                    max_facts = int(sub_arg)
                except ValueError:
                    sys.stdout.write("  Usage: /memory gc [max_facts]\n")
                    return True, model
            removed = gc_memories(mem_store, max_facts=max_facts)
            remaining = len(mem_store.list_facts())
            sys.stdout.write(
                f"  Memory GC: removed {removed} stale fact(s), "
                f"{remaining} remaining.\n"
            )

        else:
            sys.stdout.write(
                "  Usage:\n"
                "    /memory              — list all facts\n"
                "    /memory list         — list all facts\n"
                "    /memory search <q>   — search facts by keyword\n"
                "    /memory show <key>   — show a specific fact\n"
                "    /memory delete <key> — delete a fact\n"
                "    /memory gc [max]     — garbage-collect stale facts\n"
            )
        return True, model

    def _handle_sessions(self, arg: str) -> _HandlerResult:
        model = self.ctx.model
        store = getattr(self.ctx.engine, "_session_store", None)
        if store is None:
            sys.stdout.write("  No session store configured.\n")
            return True, model
        import asyncio as _sessions_aio
        try:
            sessions = _sessions_aio.run(store.list_sessions())
        except RuntimeError:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                sessions = pool.submit(
                    _sessions_aio.run, store.list_sessions()
                ).result()
        except Exception as exc:
            sys.stdout.write(f"  Error listing sessions: {exc}\n")
            return True, model
        if not sessions:
            sys.stdout.write("  No sessions for this project.\n")
            return True, model
        sessions.sort(key=lambda s: s.get("modified", ""), reverse=True)
        sys.stdout.write(f"  {'ID':10s} {'Messages':>8s}  {'Last Modified'}\n")
        sys.stdout.write(f"  {'─' * 10} {'─' * 8}  {'─' * 20}\n")
        for s in sessions:
            sid = s["session_id"][:8]
            count = s.get("message_count", 0)
            modified = s.get("modified", "?")
            if modified != "?" and "T" in modified:
                modified = modified.replace("T", " ").split("+")[0][:19]
            sys.stdout.write(f"  {sid:10s} {count:>8d}  {modified}\n")
        return True, model

    def _handle_exit(self, arg: str) -> _HandlerResult:
        return False, self.ctx.model


# ------------------------------------------------------------------
# Populate the dispatch table
# ------------------------------------------------------------------
SlashDispatcher._HANDLERS = {
    "/help": SlashDispatcher._handle_help,
    "/model": SlashDispatcher._handle_model,
    "/connect": SlashDispatcher._handle_connect,
    "/models": SlashDispatcher._handle_models,
    "/brief": SlashDispatcher._handle_brief,
    "/cost": SlashDispatcher._handle_cost,
    "/status": SlashDispatcher._handle_status,
    "/context": SlashDispatcher._handle_context,
    "/changes": SlashDispatcher._handle_changes,
    "/git": SlashDispatcher._handle_git,
    "/tasks": SlashDispatcher._handle_tasks,
    "/jobs": SlashDispatcher._handle_jobs,
    "/search": SlashDispatcher._handle_search,
    "/template": SlashDispatcher._handle_template,
    "/plan": SlashDispatcher._handle_plan,
    "/pr": SlashDispatcher._handle_pr,
    "/undo": SlashDispatcher._handle_undo,
    "/health": SlashDispatcher._handle_health,
    "/audit": SlashDispatcher._handle_audit,
    "/clear": SlashDispatcher._handle_clear,
    "/compact": SlashDispatcher._handle_compact,
    "/compact-stats": SlashDispatcher._handle_compact_stats,
    "/snapshot": SlashDispatcher._handle_snapshot,
    "/attach": SlashDispatcher._handle_attach,
    "/memory": SlashDispatcher._handle_memory,
    "/sessions": SlashDispatcher._handle_sessions,
    "/exit": SlashDispatcher._handle_exit,
}
