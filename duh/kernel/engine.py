"""Engine — session lifecycle wrapper around the query loop.

The Engine owns the conversation state for a session:
- Message history
- Turn counting
- Usage tracking
- Session identity

It delegates the actual model calling to the query loop.

    engine = Engine(deps=my_deps, tools=my_tools)
    async for event in engine.run("fix the bug"):
        handle(event)
    # engine.messages now contains the full conversation
"""

from __future__ import annotations

import os
import uuid as _uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncGenerator, AsyncIterator

from duh.hooks import HookEvent, execute_hooks
from duh.kernel.cache_tracker import CacheTracker
from duh.kernel.compact_analytics import CompactStats
from duh.kernel.confirmation import ConfirmationMinter
from duh.kernel.deps import Deps
from duh.kernel.loop import query
from duh.kernel.messages import Message, UserMessage
from duh.kernel.context_gate import ContextGate
from duh.kernel.post_compact import (
    rebuild_post_compact_context,
    restore_plan_context,
    restore_skill_context,
)
from duh.kernel.tokens import (
    count_tokens,
    count_tokens_for_model,
    estimate_cost,
    format_cost,
    get_context_limit,
)
from duh.ports.store import SessionStore
from duh.security.trifecta import check_trifecta, compute_session_capabilities

if TYPE_CHECKING:
    from duh.adapters.structured_logging import StructuredLogger

import logging

logger = logging.getLogger(__name__)


_FALLBACK_TRIGGERS = ("overloaded", "rate_limit")


def _is_fallback_error(error_text: str) -> bool:
    """Return True if error_text contains an overload or rate-limit signal."""
    lower = error_text.lower()
    return any(trigger in lower for trigger in _FALLBACK_TRIGGERS)


MAX_PTL_RETRIES = 3

# Progressive compaction targets (ADR-031): 70% → 50% → 30% on successive retries.
_PTL_COMPACTION_TARGETS = [0.70, 0.50, 0.30]

_PTL_TRIGGERS = (
    "prompt is too long",
    "prompt_too_long",
    "prompttoolong",
    "context length exceeded",
    "maximum context length",
    "max_tokens",
    "too many tokens",
    "content too large",
    "request too large",
    "request entity too large",
    "input is too long",
)


def _is_ptl_error(error_text: str) -> bool:
    """Return True if error_text indicates a prompt-too-long condition."""
    lower = error_text.lower()
    return any(trigger in lower for trigger in _PTL_TRIGGERS)


@dataclass
class EngineConfig:
    """Configuration for an Engine session."""
    model: str = ""
    fallback_model: str | None = None
    max_fallback_retries: int = 1
    system_prompt: str | list[str] = ""
    tools: list[Any] = field(default_factory=list)
    thinking: dict[str, Any] | None = None
    tool_choice: str | dict[str, Any] | None = None
    max_turns: int = 1000
    max_cost: float | None = None
    cwd: str = "."
    trifecta_acknowledged: bool = False
    auto_memory: bool = False


@dataclass
class _TurnState:
    """Mutable per-turn token + control flow state.

    Shared between the public ``run()`` orchestrator and the helper
    methods that process query events. Using a dataclass instead of
    closure variables lets us decompose the work into small methods
    without losing the ability to mutate counters across them.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    # Set True when an event from the query loop indicates we should
    # discard the rest of this iteration and retry with compaction.
    ptl_detected: bool = False
    # Set True when an overload/rate-limit error appears and a fallback
    # model is configured.
    should_fallback: bool = False
    # Set True when the per-turn budget check fires a budget_exceeded
    # event — the orchestrator must stop the run.
    budget_exceeded: bool = False
    # ADR-073 Wave 2 / Task 8: running sum of characters streamed via
    # text_delta events this turn. Divided by USAGE_DELTA_CHARS_PER_TOKEN
    # to produce an estimated output-token count surfaced in
    # ``usage_delta`` events. Reset per turn.
    streaming_output_chars: int = 0


# ADR-073 Wave 2 / Task 8: rough char-to-token ratio used while streaming.
# The authoritative count still arrives on ``done`` from the provider; this
# is deliberately cheap (no tokenizer call per delta).
USAGE_DELTA_CHARS_PER_TOKEN = 4

# Emit a ``usage_delta`` event at most every N characters of streamed text
# so we don't churn the status line on every single delta.
USAGE_DELTA_EMIT_INTERVAL_CHARS = 40


class Engine:
    """Session lifecycle wrapper around the query loop.

    One Engine per conversation. Call run() for each user message.
    The engine maintains message history across runs.
    """

    def __init__(
        self,
        deps: Deps,
        config: EngineConfig | None = None,
        session_store: SessionStore | None = None,
        structured_logger: StructuredLogger | None = None,
        **kwargs: Any,
    ):
        self._deps = deps
        self._config = config or EngineConfig(**kwargs)
        self._messages: list[Message] = []
        self._session_id = str(_uuid.uuid4())
        self._turn_count = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        # Per-turn token history: list of (input_tokens, output_tokens) tuples
        self._turn_token_history: list[tuple[int, int]] = []
        self._session_store = session_store
        self._budget_warned_80 = False
        self._setup_emitted = False  # SETUP fires only once per session
        self._slog = structured_logger
        if self._slog:
            self._slog.session_id = self._session_id
        # QX: bounded in-session error buffer used by the ``/errors`` slash
        # command.  Populated by ``_record_session_error`` as error events
        # flow through ``_slog_event``.
        self._session_errors: list[dict[str, Any]] = []
        self._confirmation_minter = ConfirmationMinter(session_key=os.urandom(32))
        self._cache_tracker = CacheTracker()
        self._compact_stats = CompactStats()

        # Incremental token tracking (PERF-1): avoid O(N×M) re-scan every turn.
        # Messages are immutable once appended, so their token count is cached
        # by message id. Only compaction (which replaces the message list)
        # needs to invalidate the cache.
        self._msg_token_cache: dict[str, int] = {}
        self._cached_token_total: int = 0
        self._cache_model: str = ""  # model the cache was built for

        # SESSION_START: refuse sessions where all three trifecta capabilities
        # are simultaneously present without explicit acknowledgement.
        _caps = compute_session_capabilities(self._config.tools)
        check_trifecta(_caps, acknowledged=self._config.trifecta_acknowledged)

    @property
    def compact_stats(self) -> CompactStats:
        return self._compact_stats

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def cache_tracker(self) -> CacheTracker:
        return self._cache_tracker

    @property
    def max_cost(self) -> float | None:
        return self._config.max_cost

    def budget_remaining(self, model: str | None = None) -> float | None:
        """Return remaining budget in USD, or None if no budget is set."""
        if self._config.max_cost is None:
            return None
        return max(0.0, self._config.max_cost - self.estimated_cost(model))

    def estimated_cost(self, model: str | None = None) -> float:
        """Return estimated session cost in USD."""
        m = model or self._config.model
        return estimate_cost(m, self._total_input_tokens, self._total_output_tokens)

    def _check_budget(self, model: str | None = None) -> list[dict[str, Any]]:
        """Check budget and return any warning/stop events to yield.

        Returns a list of 0-2 events:
        - budget_warning at 80% usage
        - budget_exceeded at 100% (with stop signal)
        """
        max_cost = self._config.max_cost
        if max_cost is None or max_cost <= 0:
            return []
        cost = self.estimated_cost(model)
        events: list[dict[str, Any]] = []

        # 80% warning (once per session)
        if not self._budget_warned_80 and cost >= max_cost * 0.80:
            self._budget_warned_80 = True
            pct = cost / max_cost * 100
            events.append({
                "type": "budget_warning",
                "message": f"Approaching budget limit ({pct:.0f}% used)",
                "cost": cost,
                "max_cost": max_cost,
            })

        # 100% exceeded
        if cost >= max_cost:
            events.append({
                "type": "budget_exceeded",
                "message": f"Budget limit reached ({format_cost(cost)}). Session stopped.",
                "cost": cost,
                "max_cost": max_cost,
            })

        return events

    def cost_summary(self, model: str | None = None) -> str:
        """Return a human-readable cost summary string with per-turn breakdown."""
        m = model or self._config.model
        cost = self.estimated_cost(m)
        lines = [
            f"Input tokens:  {self._total_input_tokens:,}",
            f"Output tokens: {self._total_output_tokens:,}",
            f"Estimated cost: {format_cost(cost)} ({m})",
        ]
        # Per-turn breakdown (only shown when multiple turns exist)
        if self._turn_token_history:
            lines.append("")
            lines.append("Per-turn breakdown:")
            for i, (inp, out) in enumerate(self._turn_token_history, start=1):
                turn_cost = estimate_cost(m, inp, out)
                lines.append(
                    f"  Turn {i:>2d}: in={inp:>6,}  out={out:>6,}  cost={format_cost(turn_cost)}"
                )
        # ADR-061 Phase 3: cache stats
        cache_summary = self._cache_tracker.summary()
        if cache_summary:
            lines.append(cache_summary)
        remaining = self.budget_remaining(m)
        if remaining is not None:
            lines.append(f"Budget remaining: {format_cost(remaining)} of {format_cost(self._config.max_cost or 0.0)}")
        return "\n".join(lines)

    def _check_confirmation_gate(
        self,
        tool: str,
        input_obj: dict,
        chain: list,
        token: str | None,
    ) -> Any:
        """Check whether a tool call from `chain` requires a confirmation token."""
        from duh.security.policy import resolve_confirmation
        return resolve_confirmation(
            tool=tool,
            input_obj=input_obj,
            chain=chain,
            minter=self._confirmation_minter,
            session_id=self._session_id,
            token=token,
        )

    def _get_adaptive_compactor(self) -> Any:
        """Lazily create an AdaptiveCompactor for auto-compaction (ADR-056).

        Used when no explicit ``deps.compact`` is provided.  Cached on
        the engine instance so the circuit breaker state persists across
        turns.
        """
        if not hasattr(self, "_adaptive_compactor"):
            from duh.adapters.compact import AdaptiveCompactor
            self._adaptive_compactor = AdaptiveCompactor(
                call_model=self._deps.call_model,
            )
        return self._adaptive_compactor

    def _estimate_messages_tokens(self, model: str) -> int:
        """Estimate total tokens for all current messages using model calibration.

        Uses the incremental cache when the model matches; falls back to a
        full scan (and rebuilds the cache) otherwise.
        """
        if model == self._cache_model and self._msg_token_cache:
            # Validate the cache covers exactly the current message list.
            # Fast path: if every message id is cached, return the running total.
            if all(
                (m.id if isinstance(m, Message) else id(m)) in self._msg_token_cache
                for m in self._messages
            ):
                return self._cached_token_total
        # Cache miss or model changed — full rebuild.
        self._rebuild_token_cache(model)
        return self._cached_token_total

    def _token_count_for_message(self, msg: Message, model: str) -> int:
        """Return token count for a single message, using cache if available."""
        key = msg.id if isinstance(msg, Message) else id(msg)
        if model == self._cache_model and key in self._msg_token_cache:
            return self._msg_token_cache[key]
        text = msg.text if isinstance(msg, Message) else str(msg)
        tokens = count_tokens_for_model(text, model)
        # Only store if model matches the current cache model
        if model == self._cache_model:
            self._msg_token_cache[key] = tokens
            self._cached_token_total += tokens
        return tokens

    def _track_new_message(self, msg: Message, model: str) -> int:
        """Compute tokens for a newly appended message and add to running total.

        Call this right after appending a message to self._messages.
        Returns the token count for the message.
        """
        if model != self._cache_model:
            # Model changed — rebuild everything
            self._rebuild_token_cache(model)
            key = msg.id if isinstance(msg, Message) else id(msg)
            return self._msg_token_cache.get(key, 0)
        text = msg.text if isinstance(msg, Message) else str(msg)
        tokens = count_tokens_for_model(text, model)
        key = msg.id if isinstance(msg, Message) else id(msg)
        self._msg_token_cache[key] = tokens
        self._cached_token_total += tokens
        return tokens

    def _rebuild_token_cache(self, model: str) -> None:
        """Full rebuild of the token cache from scratch.

        Called on model change or after cache invalidation.
        """
        self._msg_token_cache.clear()
        self._cache_model = model
        total = 0
        for m in self._messages:
            key = m.id if isinstance(m, Message) else id(m)
            text = m.text if isinstance(m, Message) else str(m)
            tokens = count_tokens_for_model(text, model)
            self._msg_token_cache[key] = tokens
            total += tokens
        self._cached_token_total = total

    def _invalidate_token_cache(self) -> None:
        """Invalidate the token cache after compaction replaces the message list.

        The next call to _estimate_messages_tokens or _track_new_message will
        trigger a full rebuild.
        """
        self._msg_token_cache.clear()
        self._cached_token_total = 0
        self._cache_model = ""

    async def _run_auto_memory(self, model: str) -> list[dict[str, str]]:
        """Run auto-memory extraction and store results (ADR-069 P1).

        Returns the list of extracted facts (may be empty).
        """
        if self._deps.call_model is None:
            return []
        try:
            from duh.kernel.auto_memory import extract_memories
            from duh.adapters.memory_store import FileMemoryStore

            extracted = await extract_memories(
                self._messages,
                self._deps.call_model,
                model=model,
            )
            if extracted:
                store = FileMemoryStore(cwd=self._config.cwd)
                for fact in extracted:
                    store.store_fact(
                        key=fact["key"],
                        value=fact["value"],
                        tags=["auto-extracted"],
                    )
                logger.info(
                    "Auto-memory: extracted %d fact(s)", len(extracted),
                )
            return extracted
        except Exception:
            logger.warning("Auto-memory extraction failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Compaction helpers
    # ------------------------------------------------------------------

    async def _post_compact_rebuild(self, kind: str, count_before: int,
                                    tokens_before: int, model: str) -> None:
        """Run shared post-compaction housekeeping.

        Steps performed:
          1. Invalidate the token cache (compaction replaced the list).
          2. Notify the cache tracker (suppress false cache-break).
          3. ADR-058: rebuild file state when a file_tracker is present.
          4. ADR-058 Phase 4: restore plan/skill context.
          5. Record compact analytics.
          6. Emit POST_COMPACT hook.
        """
        # 1. Token cache invalidation.
        self._invalidate_token_cache()
        # 2. Cache tracker.
        self._cache_tracker.notify_compaction()

        # 3. ADR-058: post-compact file state rebuild.
        file_tracker = getattr(self._deps, "file_tracker", None)
        if file_tracker is not None:
            self._messages = await rebuild_post_compact_context(
                self._messages, file_tracker,
            )
            self._invalidate_token_cache()

        # 4. ADR-058 Phase 4: plan + skill context restoration.
        plan_ctx = restore_plan_context(self)
        if plan_ctx:
            self._messages.append(Message(
                role="system",
                content=f"[Post-compaction plan restoration]\n{plan_ctx}",
                metadata={"subtype": "post_compact_plan_restore"},
            ))
        skill_ctx = restore_skill_context(self)
        if skill_ctx:
            self._messages.append(Message(
                role="system",
                content=f"[Post-compaction skill restoration]\n{skill_ctx}",
                metadata={"subtype": "post_compact_skill_restore"},
            ))

        # 5. Analytics — _estimate_messages_tokens rebuilds the cache.
        tokens_after = self._estimate_messages_tokens(model)
        self._compact_stats.record(
            kind, tokens_freed=max(0, tokens_before - tokens_after),
        )

        # 6. POST_COMPACT hook.
        if self._deps.hook_registry:
            await execute_hooks(
                self._deps.hook_registry,
                HookEvent.POST_COMPACT,
                {
                    "message_count_before": count_before,
                    "message_count_after": len(self._messages),
                },
            )

    async def _auto_compact(
        self,
        input_estimate: int,
        effective_model: str,
        compact_fn: Any,
    ) -> None:
        """Trigger and run auto-compaction when context exceeds 80% threshold.

        Mutates ``self._messages`` in place. Returns silently when no
        compaction is needed. All hook emission, analytics, and cache
        invalidation happens here.
        """
        context_limit = get_context_limit(effective_model)
        threshold = int(context_limit * 0.80)
        if input_estimate <= threshold:
            return

        logger.info(
            "Auto-compacting: ~%d tokens exceeds 80%% threshold (%d) "
            "for %s (limit %d)",
            input_estimate, threshold, effective_model, context_limit,
        )
        # PRE_COMPACT hook.
        if self._deps.hook_registry:
            await execute_hooks(
                self._deps.hook_registry,
                HookEvent.PRE_COMPACT,
                {"message_count": len(self._messages),
                 "token_estimate": input_estimate},
            )

        count_before = len(self._messages)
        tokens_before = input_estimate
        self._messages = await compact_fn(
            self._messages, token_limit=threshold,
        )
        await self._post_compact_rebuild(
            "auto", count_before, tokens_before, effective_model,
        )

    async def _ptl_retry_compact(
        self,
        ptl_retries: int,
        input_estimate: int,
        effective_model: str,
        compact_fn: Any,
    ) -> None:
        """Compact aggressively when the model returned a prompt-too-long error.

        Uses the progressive compaction targets defined by ADR-031:
        70% → 50% → 30% on successive retries.
        """
        logger.info(
            "Prompt too long (retry %d/%d), compacting...",
            ptl_retries, MAX_PTL_RETRIES,
        )
        context_limit = get_context_limit(effective_model)
        target_ratio = _PTL_COMPACTION_TARGETS[
            min(ptl_retries - 1, len(_PTL_COMPACTION_TARGETS) - 1)
        ]
        target = int(context_limit * target_ratio)

        if self._deps.hook_registry:
            await execute_hooks(
                self._deps.hook_registry,
                HookEvent.PRE_COMPACT,
                {"message_count": len(self._messages),
                 "token_estimate": input_estimate},
            )

        count_before = len(self._messages)
        tokens_before_ptl = self._estimate_messages_tokens(effective_model)
        self._messages = await compact_fn(
            self._messages, token_limit=target,
        )
        await self._post_compact_rebuild(
            "ptl_retry", count_before, tokens_before_ptl, effective_model,
        )

    # ------------------------------------------------------------------
    # Shared event processing
    # ------------------------------------------------------------------

    def _track_assistant_message(
        self,
        msg: Message,
        effective_model: str,
        state: _TurnState,
        *,
        adjust_input: bool,
    ) -> None:
        """Record an assistant message in history and update token totals.

        Shared between the primary and fallback paths. When
        ``adjust_input`` is True (primary path), real input-token usage
        from the provider replaces the heuristic estimate. The fallback
        path skips that adjustment to mirror the original behavior.
        """
        self._messages.append(msg)
        self._track_new_message(msg, effective_model)
        usage = msg.metadata.get("usage", {}) if msg.metadata else {}
        real_input = usage.get("input_tokens", 0)
        real_output = usage.get("output_tokens", 0)
        if real_output > 0:
            out_tokens = real_output
            if adjust_input and real_input > 0:
                # Replace heuristic input estimate with real data.
                delta = real_input - state.input_tokens
                self._total_input_tokens += delta
                state.input_tokens = real_input
        else:
            out_tokens = count_tokens_for_model(msg.text, effective_model)
        self._total_output_tokens += out_tokens
        state.output_tokens += out_tokens
        if adjust_input:
            # ADR-061 Phase 3: only the primary path tracks cache hits.
            self._cache_tracker.record_usage(usage)

    def _capture_tool_result(self, msg: Message, effective_model: str) -> None:
        """Append a tool_result user message to history (ADR-057)."""
        self._messages.append(msg)
        self._track_new_message(msg, effective_model)

    def _slog_event(self, event: dict[str, Any]) -> None:
        """Forward tool/error events to the structured logger when present."""
        event_type = event.get("type", "")
        # QX: always mirror error-shaped events into the in-session buffer
        # that ``/errors`` reads from, regardless of whether a structured
        # logger is configured.
        if event_type == "error" or (
            event_type == "tool_result" and event.get("is_error", False)
        ):
            self._record_session_error(event)

        if not self._slog:
            return
        if event_type == "tool_use":
            self._slog.tool_call(
                name=event.get("name", ""),
                input=event.get("input"),
            )
        elif event_type == "tool_result":
            self._slog.tool_result(
                name=event.get("name", ""),
                output=str(event.get("output", "")),
                is_error=event.get("is_error", False),
            )
        elif event_type == "error":
            self._slog.error(error=event.get("error", ""))

    def _record_session_error(self, event: dict[str, Any]) -> None:
        """Append one entry to the in-session error buffer.

        Bounded to the last 100 entries so the buffer can't grow without
        limit on a long-running REPL session.  Each entry records an ISO-8601
        UTC timestamp, a short context tag, and a human-readable message.
        """
        from datetime import datetime, timezone

        if not hasattr(self, "_session_errors"):
            self._session_errors: list[dict[str, Any]] = []

        event_type = event.get("type", "")
        if event_type == "error":
            context = "error"
            message = str(event.get("error", "") or "(unknown error)")
        else:
            context = f"tool:{event.get('name', '?')}"
            message = str(event.get("output", "") or "(tool error)")

        self._session_errors.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "context": context,
                "message": message[:500],
                "turn": self._turn_count,
            }
        )
        # Cap to the last 100 entries.
        if len(self._session_errors) > 100:
            del self._session_errors[: len(self._session_errors) - 100]

    async def _on_done(
        self,
        event: dict[str, Any],
        effective_model: str,
        state: _TurnState,
        *,
        enable_hooks: bool,
        enable_auto_memory: bool,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run end-of-turn bookkeeping and yield any follow-up events.

        Performs (in order): TASK_COMPLETED hook (when enabled),
        per-turn token snapshot, budget check + yield budget events,
        session auto-save, and auto-memory extraction.

        Sets ``state.budget_exceeded`` when the budget cap is hit so
        the caller can return cleanly.
        """
        # TASK_COMPLETED hook — primary path only.
        if enable_hooks and self._deps.hook_registry:
            await execute_hooks(
                self._deps.hook_registry,
                HookEvent.TASK_COMPLETED,
                {
                    "session_id": self._session_id,
                    "turn": self._turn_count,
                    "stop_reason": event.get("stop_reason", "end_turn"),
                },
            )
        # Per-turn token snapshot.
        self._turn_token_history.append(
            (state.input_tokens, state.output_tokens),
        )
        # Budget enforcement.
        budget_events = self._check_budget(effective_model)
        for be in budget_events:
            yield be
        if any(be["type"] == "budget_exceeded" for be in budget_events):
            state.budget_exceeded = True
            return
        # Session auto-save.
        if self._session_store:
            try:
                await self._session_store.save(
                    self._session_id, self._messages,
                )
            except Exception:
                # Primary path warns; fallback path logs at debug. Mirror
                # primary semantics here — fallback distinguishes itself
                # via enable_auto_memory=False, but we keep the warn for
                # both paths since it's the same risk: lost history.
                logger.warning(
                    "Session auto-save failed; "
                    "conversation history may be lost",
                    exc_info=True,
                )
        # Auto-memory extraction (ADR-069 P1) — primary path only.
        if enable_auto_memory and self._config.auto_memory:
            extracted = await self._run_auto_memory(effective_model)
            if extracted:
                yield {"type": "auto_memory", "facts": extracted}

    def _classify_error(
        self,
        event: dict[str, Any],
        state: _TurnState,
        compact_fn: Any,
        fallback_model: str | None,
        ptl_retries: int,
    ) -> bool:
        """Inspect an error event and update ``state`` for retry routing.

        Returns True when the caller should suppress (not yield) the
        event because a PTL retry or fallback switch is queued.
        """
        if event.get("type", "") != "error":
            return False
        text = event.get("error", "")
        # PTL retry takes priority — disabled if no compact_fn or budget gone.
        if compact_fn is not None and ptl_retries < MAX_PTL_RETRIES \
                and _is_ptl_error(text):
            state.ptl_detected = True
            return True
        # Fallback route — only if a fallback model is configured.
        if fallback_model and _is_fallback_error(text):
            state.should_fallback = True
            return True
        return False

    def _ingest_message_event(
        self,
        event: dict[str, Any],
        effective_model: str,
        state: _TurnState,
        enable_hooks: bool,
    ) -> bool:
        """Capture assistant + tool_result messages into history.

        Returns True for ``tool_result_message`` events (which the caller
        must NOT yield to the consumer — they are internal to ADR-057).
        """
        event_type = event.get("type", "")
        if event_type == "assistant":
            msg = event.get("message")
            if isinstance(msg, Message):
                self._track_assistant_message(
                    msg, effective_model, state,
                    adjust_input=enable_hooks,
                )
            if enable_hooks and self._slog:
                self._slog.model_response(
                    model=effective_model, turn=self._turn_count,
                )
            return False
        if event_type == "tool_result_message":
            msg = event.get("message")
            if isinstance(msg, Message):
                self._capture_tool_result(msg, effective_model)
            return True
        return False

    async def _process_query_events(
        self,
        query_iter: AsyncIterator[dict[str, Any]],
        *,
        effective_model: str,
        state: _TurnState,
        compact_fn: Any,
        fallback_model: str | None,
        ptl_retries: int,
        enable_hooks: bool,
        enable_auto_memory: bool,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Iterate ``query_iter`` and yield user-visible events.

        Shared between primary and fallback paths. ``compact_fn=None``
        disables PTL detection; ``fallback_model=None`` disables
        fallback detection; ``enable_hooks`` controls TASK_COMPLETED +
        slog; ``enable_auto_memory`` controls ADR-069 P1 extraction.
        Mutates ``state`` to signal control flow back to the caller.
        """
        async for event in query_iter:
            # Capture assistant / tool_result messages into history.
            # tool_result events stay internal — never yielded.
            if self._ingest_message_event(
                event, effective_model, state, enable_hooks,
            ):
                continue

            if enable_hooks:
                self._slog_event(event)

            # Error routing — PTL retry or fallback switch.
            if self._classify_error(
                event, state, compact_fn, fallback_model, ptl_retries,
            ):
                continue

            event_type = event.get("type", "")
            # Defensive: don't yield done if PTL retry is queued.
            if event_type == "done" and state.ptl_detected:  # pragma: no cover - defensive; query() returns after error
                continue

            yield event

            # ADR-073 Wave 2 / Task 8: after a text_delta, surface an
            # incremental token estimate so renderers can update the
            # status line mid-stream. We use a cheap char-based heuristic
            # (no tokenizer call) and only emit every ~40 chars to keep
            # the event rate low. Authoritative counts still arrive on
            # the ``done`` event.
            if event_type == "text_delta":
                text = event.get("text", "") or ""
                if text:
                    prev_chars = state.streaming_output_chars
                    state.streaming_output_chars += len(text)
                    prev_bucket = prev_chars // USAGE_DELTA_EMIT_INTERVAL_CHARS
                    new_bucket = (
                        state.streaming_output_chars
                        // USAGE_DELTA_EMIT_INTERVAL_CHARS
                    )
                    if new_bucket > prev_bucket:
                        est_output = (
                            state.streaming_output_chars
                            // USAGE_DELTA_CHARS_PER_TOKEN
                        )
                        yield {
                            "type": "usage_delta",
                            "input_tokens": self._total_input_tokens,
                            "output_tokens": self._total_output_tokens + est_output,
                            "estimated": True,
                            "model": effective_model,
                        }

            if event_type == "done":
                async for follow_up in self._on_done(
                    event, effective_model, state,
                    enable_hooks=enable_hooks,
                    enable_auto_memory=enable_auto_memory,
                ):
                    yield follow_up
                if state.budget_exceeded:
                    return

    # ------------------------------------------------------------------
    # Orchestrator helpers — primary + fallback paths
    # ------------------------------------------------------------------

    async def _run_with_ptl_retry(
        self,
        max_turns: int | None,
        effective_model: str,
        fallback_model: str | None,
        compact_fn: Any,
        input_estimate: int,
        state: _TurnState,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run the primary query loop with PTL retry + progressive compaction.

        Yields user-visible events. Sets ``state.should_fallback`` when
        an overload/rate-limit error is observed; the orchestrator then
        invokes the fallback path.
        """
        ptl_retries = 0
        while True:
            state.ptl_detected = False
            # PERF-12: query() makes its own defensive copy of messages
            # before appending tool turns, so we pass self._messages
            # directly rather than building a redundant shallow copy
            # that is immediately discarded.
            query_iter = query(
                messages=self._messages,
                system_prompt=self._config.system_prompt,
                deps=self._deps,
                tools=self._config.tools,
                max_turns=max_turns or self._config.max_turns,
                model=effective_model,
                thinking=self._config.thinking,
                tool_choice=self._config.tool_choice,
            )
            async for event in self._process_query_events(
                query_iter,
                effective_model=effective_model,
                state=state,
                compact_fn=compact_fn,
                fallback_model=fallback_model,
                ptl_retries=ptl_retries,
                enable_hooks=True,
                enable_auto_memory=True,
            ):
                yield event
            if state.budget_exceeded:
                return
            if state.ptl_detected:
                ptl_retries += 1
                await self._ptl_retry_compact(
                    ptl_retries, input_estimate,
                    effective_model, compact_fn,
                )
                continue  # retry the query
            break  # query completed normally (or fallback flagged)

    async def _run_fallback(
        self,
        max_turns: int | None,
        fallback_model: str,
        primary_model: str,
        state: _TurnState,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Run the fallback model after primary raised an overload error.

        Reuses ``_process_query_events`` so the assistant/tool_result
        capture, budget enforcement, and session auto-save logic stays
        identical to the primary path. Hooks + auto-memory are disabled
        here to mirror the original behavior of the legacy fallback
        block.
        """
        logger.info(
            "Primary model overloaded, switching to fallback: %s",
            fallback_model,
        )
        # The model used for token tracking stays consistent with the
        # legacy code: ``fallback_model or effective_model``.
        track_model = fallback_model or primary_model
        query_iter = query(
            messages=self._messages,
            system_prompt=self._config.system_prompt,
            deps=self._deps,
            tools=self._config.tools,
            max_turns=max_turns or self._config.max_turns,
            model=fallback_model,
            thinking=self._config.thinking,
            tool_choice=self._config.tool_choice,
        )
        async for event in self._process_query_events(
            query_iter,
            effective_model=track_model,
            state=state,
            compact_fn=None,
            fallback_model=None,
            ptl_retries=MAX_PTL_RETRIES,  # disable PTL detection
            enable_hooks=False,
            enable_auto_memory=False,
        ):
            yield event

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def _begin_turn(self, prompt: str | list[Any], model: str | None) -> int:
        """Append the user message, bump the turn counter, and update
        the incremental token cache. Returns the input-token estimate
        used for budgeting + auto-compaction decisions.
        """
        user_msg = Message(
            role="user",
            content=prompt if isinstance(prompt, str) else prompt,
        )
        self._messages.append(user_msg)
        self._turn_count += 1
        effective_model = model or self._config.model
        sys_text = (
            self._config.system_prompt
            if isinstance(self._config.system_prompt, str)
            else " ".join(self._config.system_prompt)
        )
        if self._cache_model != effective_model:
            self._rebuild_token_cache(effective_model)
        else:
            self._track_new_message(user_msg, effective_model)
        input_estimate = (
            self._cached_token_total
            + count_tokens_for_model(sys_text, effective_model)
        )
        self._total_input_tokens += input_estimate
        return input_estimate

    async def _emit_session_lifecycle(self) -> None:
        """Fire SETUP (once per session) + TASK_CREATED (every turn) hooks
        and trigger structured logging at session start.
        """
        if self._slog and self._turn_count == 1:
            self._slog.session_start(model=self._config.model)
        if self._deps.hook_registry and not self._setup_emitted:
            self._setup_emitted = True
            await execute_hooks(
                self._deps.hook_registry,
                HookEvent.SETUP,
                {"session_id": self._session_id,
                 "model": self._config.model},
            )
        if self._deps.hook_registry:
            await execute_hooks(
                self._deps.hook_registry,
                HookEvent.TASK_CREATED,
                {"session_id": self._session_id,
                 "turn": self._turn_count},
            )

    async def run(
        self,
        prompt: str | list[Any],
        *,
        max_turns: int | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Submit a user message and stream the response.

        Yields the same events as kernel.query(), plus a synthetic
        ``{"type": "session", "session_id": "...", "turn": N}`` event.
        """
        # 1. Begin turn — append user, update token cache.
        input_estimate = self._begin_turn(prompt, model)
        state = _TurnState(input_tokens=input_estimate, output_tokens=0)

        # 2. Session event + lifecycle hooks.
        yield {"type": "session", "session_id": self._session_id,
               "turn": self._turn_count}
        await self._emit_session_lifecycle()

        # 3. Auto-compact when nearing the context window limit.
        compact_fn = self._deps.compact
        if compact_fn is None:
            compact_fn = self._get_adaptive_compactor().compact
        effective_model = model or self._config.model
        await self._auto_compact(input_estimate, effective_model, compact_fn)

        # 4. Context gate (ADR-059): block at 95% after compaction.
        gate = ContextGate(get_context_limit(effective_model))
        post_compact_estimate = self._estimate_messages_tokens(effective_model)
        allowed, reason = gate.check(post_compact_estimate)
        if not allowed:
            yield {"type": "context_blocked", "message": reason}
            return

        # 5. Primary query loop with PTL retry.
        fallback_model = self._config.fallback_model
        if self._slog:
            self._slog.model_request(
                model=effective_model, turn=self._turn_count,
            )
        async for event in self._run_with_ptl_retry(
            max_turns, effective_model, fallback_model,
            compact_fn, input_estimate, state,
        ):
            yield event
        if state.budget_exceeded:
            return

        # 6. Fallback retry (once only).
        if state.should_fallback and fallback_model:
            async for event in self._run_fallback(
                max_turns, fallback_model, effective_model, state,
            ):
                yield event
            if state.budget_exceeded:
                return
