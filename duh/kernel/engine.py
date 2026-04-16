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
from typing import TYPE_CHECKING, Any, AsyncGenerator

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

    async def run(
        self,
        prompt: str | list[Any],
        *,
        max_turns: int | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Submit a user message and stream the response.

        Yields the same events as kernel.query(), plus:
        - {"type": "session", "session_id": "...", "turn": N}
        """
        # Add user message
        user_msg = Message(role="user", content=prompt if isinstance(prompt, str) else prompt)
        self._messages.append(user_msg)
        self._turn_count += 1

        # Estimate input tokens (all messages + system prompt sent to model)
        # Use model-calibrated chars/token ratio for better accuracy.
        # PERF-1: Use incremental token cache — only compute tokens for the
        # new user message; prior messages are already cached.
        effective_model_for_count = model or self._config.model
        sys_text = (
            self._config.system_prompt
            if isinstance(self._config.system_prompt, str)
            else " ".join(self._config.system_prompt)
        )
        # Ensure cache is initialized / matches current model
        if self._cache_model != effective_model_for_count:
            self._rebuild_token_cache(effective_model_for_count)
        else:
            # Track the newly appended user message incrementally
            self._track_new_message(user_msg, effective_model_for_count)
        input_estimate = (
            self._cached_token_total
            + count_tokens_for_model(sys_text, effective_model_for_count)
        )
        self._total_input_tokens += input_estimate
        # Track per-turn: start with estimated input; output updated below after response
        _turn_input_tokens = input_estimate
        _turn_output_tokens = 0

        yield {
            "type": "session",
            "session_id": self._session_id,
            "turn": self._turn_count,
        }

        if self._slog and self._turn_count == 1:
            self._slog.session_start(model=self._config.model)

        # Emit SETUP hook once per session (first run only)
        if self._deps.hook_registry and not self._setup_emitted:
            self._setup_emitted = True
            await execute_hooks(
                self._deps.hook_registry,
                HookEvent.SETUP,
                {"session_id": self._session_id, "model": self._config.model},
            )

        # Emit TASK_CREATED hook (fires on every run call)
        if self._deps.hook_registry:
            await execute_hooks(
                self._deps.hook_registry,
                HookEvent.TASK_CREATED,
                {"session_id": self._session_id, "turn": self._turn_count},
            )

        # --- Auto-compact if approaching context limit ---
        # Use the injected compact function if provided (backward compat),
        # otherwise fall back to AdaptiveCompactor (ADR-056).
        compact_fn = self._deps.compact
        if compact_fn is None:
            compact_fn = self._get_adaptive_compactor().compact

        effective_model = model or self._config.model
        context_limit = get_context_limit(effective_model)
        threshold = int(context_limit * 0.80)
        if input_estimate > threshold:
            logger.info(
                "Auto-compacting: ~%d tokens exceeds 80%% threshold (%d) "
                "for %s (limit %d)",
                input_estimate, threshold, effective_model, context_limit,
            )
            # Emit PRE_COMPACT hook
            if self._deps.hook_registry:
                await execute_hooks(
                    self._deps.hook_registry,
                    HookEvent.PRE_COMPACT,
                    {"message_count": len(self._messages), "token_estimate": input_estimate},
                )

            count_before = len(self._messages)
            tokens_before = input_estimate
            self._messages = await compact_fn(
                self._messages, token_limit=threshold,
            )
            # PERF-1: Invalidate token cache — compaction replaced the message list.
            self._invalidate_token_cache()
            # ADR-061 Phase 3: suppress false cache-break after compaction
            self._cache_tracker.notify_compaction()

            # ADR-058: Post-compact file state rebuild
            file_tracker = getattr(self._deps, "file_tracker", None)
            if file_tracker is not None:
                self._messages = await rebuild_post_compact_context(
                    self._messages, file_tracker,
                )
                # Invalidate again — rebuild may have replaced the list.
                self._invalidate_token_cache()

            # ADR-058 Phase 4: Restore plan and skill context
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

            # ADR-058: Record compact analytics — _estimate_messages_tokens
            # will rebuild the cache from the compacted message list.
            tokens_after = self._estimate_messages_tokens(effective_model)
            self._compact_stats.record(
                "auto", tokens_freed=max(0, tokens_before - tokens_after),
            )

            # Emit POST_COMPACT hook
            if self._deps.hook_registry:
                await execute_hooks(
                    self._deps.hook_registry,
                    HookEvent.POST_COMPACT,
                    {
                        "message_count_before": count_before,
                        "message_count_after": len(self._messages),
                    },
                )

        # --- Context gate: block at 95% AFTER auto-compact (ADR-059) ---
        # Placed after compaction so compaction has a chance to free context first.
        # Only blocks if context is still over 95% after all compaction attempts.
        gate_limit = get_context_limit(model or self._config.model)
        gate = ContextGate(gate_limit)
        # PERF-1: Use cached total instead of full-scan sum.
        post_compact_estimate = self._estimate_messages_tokens(
            model or self._config.model,
        )
        allowed, reason = gate.check(post_compact_estimate)
        if not allowed:
            yield {"type": "context_blocked", "message": reason}
            return

        # Run the query loop
        effective_model = model or self._config.model
        fallback_model = self._config.fallback_model
        should_fallback = False

        if self._slog:
            self._slog.model_request(model=effective_model, turn=self._turn_count)

        # --- Query with PTL retry ---
        # ADR-057: self._messages now has correct user/assistant alternation
        # (including tool_result user messages) so validate_alternation is
        # no longer needed on the hot path. A fresh copy is taken each
        # iteration so PTL-retry after compaction sees the updated list.
        ptl_retries = 0
        while True:
            ptl_detected = False

            async for event in query(
                messages=list(self._messages),
                system_prompt=self._config.system_prompt,
                deps=self._deps,
                tools=self._config.tools,
                max_turns=max_turns or self._config.max_turns,
                model=effective_model,
                thinking=self._config.thinking,
                tool_choice=self._config.tool_choice,
            ):
                event_type = event.get("type", "")

                # Track assistant messages in history and count output tokens.
                # Prefer real usage from provider metadata when available;
                # fall back to model-calibrated heuristic otherwise.
                if event_type == "assistant":
                    msg = event.get("message")
                    if isinstance(msg, Message):
                        self._messages.append(msg)
                        # PERF-1: Track new message in incremental token cache.
                        self._track_new_message(msg, effective_model)
                        usage = msg.metadata.get("usage", {}) if msg.metadata else {}
                        real_input = usage.get("input_tokens", 0)
                        real_output = usage.get("output_tokens", 0)
                        if real_output > 0:
                            # Real usage data from provider — use it and correct
                            # the input estimate if provider also reported input tokens.
                            out_tokens = real_output
                            if real_input > 0:
                                # Replace the heuristic input estimate with real data.
                                # Adjust cumulative total: remove estimate, add real.
                                delta = real_input - _turn_input_tokens
                                self._total_input_tokens += delta
                                _turn_input_tokens = real_input
                        else:
                            # No real usage — use calibrated heuristic
                            out_tokens = count_tokens_for_model(
                                msg.text, effective_model
                            )
                        self._total_output_tokens += out_tokens
                        _turn_output_tokens += out_tokens
                        # ADR-061 Phase 3: track cache hit rates
                        self._cache_tracker.record_usage(usage)
                    if self._slog:
                        self._slog.model_response(model=effective_model, turn=self._turn_count)

                # Capture tool_result user messages into canonical history (ADR-057)
                if event_type == "tool_result_message":
                    msg = event.get("message")
                    if isinstance(msg, Message):
                        self._messages.append(msg)
                        # PERF-1: Track tool_result message in incremental token cache.
                        self._track_new_message(msg, effective_model)
                    continue  # internal event — don't yield to caller

                # Structured logging for tool & error events
                if self._slog:
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

                # Detect PTL errors for retry
                if event_type == "error":
                    error_text = event.get("error", "")
                    if (_is_ptl_error(error_text)
                            and ptl_retries < MAX_PTL_RETRIES
                            and compact_fn is not None):
                        ptl_detected = True
                        continue  # don't yield PTL error, we'll retry

                # Detect fallback-eligible errors
                if fallback_model and event_type == "error":
                    error_text = event.get("error", "")
                    if _is_fallback_error(error_text):
                        should_fallback = True
                        # Don't yield this error — we'll retry with fallback
                        continue

                # Don't yield done if we're about to PTL-retry
                if event_type == "done" and ptl_detected:  # pragma: no cover - defensive; query() returns after error
                    continue

                yield event

                # Emit TASK_COMPLETED hook when the query loop finishes
                if event_type == "done" and self._deps.hook_registry:
                    await execute_hooks(
                        self._deps.hook_registry,
                        HookEvent.TASK_COMPLETED,
                        {
                            "session_id": self._session_id,
                            "turn": self._turn_count,
                            "stop_reason": event.get("stop_reason", "end_turn"),
                        },
                    )

                # Record per-turn token snapshot when the turn completes
                if event_type == "done":
                    self._turn_token_history.append((_turn_input_tokens, _turn_output_tokens))

                # --- Budget enforcement after each turn ---
                if event_type == "done":
                    budget_events = self._check_budget(effective_model)
                    for be in budget_events:
                        yield be
                    if any(be["type"] == "budget_exceeded" for be in budget_events):
                        return

                # Auto-save session after each turn completes
                if event_type == "done" and self._session_store:
                    try:
                        await self._session_store.save(
                            self._session_id, self._messages,
                        )
                    except Exception:
                        logger.warning("Session auto-save failed; conversation history may be lost", exc_info=True)

                # ADR-069 P1: Auto-memory extraction after each turn
                if event_type == "done" and self._config.auto_memory:
                    extracted = await self._run_auto_memory(effective_model)
                    if extracted:
                        yield {
                            "type": "auto_memory",
                            "facts": extracted,
                        }

            if ptl_detected:
                ptl_retries += 1
                logger.info(
                    "Prompt too long (retry %d/%d), compacting...",
                    ptl_retries, MAX_PTL_RETRIES,
                )
                context_limit = get_context_limit(effective_model)
                # Progressive compaction targets per ADR-031: 70% → 50% → 30%.
                target_ratio = _PTL_COMPACTION_TARGETS[min(ptl_retries - 1, len(_PTL_COMPACTION_TARGETS) - 1)]
                target = int(context_limit * target_ratio)

                # Emit PRE_COMPACT hook
                if self._deps.hook_registry:
                    await execute_hooks(
                        self._deps.hook_registry,
                        HookEvent.PRE_COMPACT,
                        {"message_count": len(self._messages), "token_estimate": input_estimate},
                    )

                count_before = len(self._messages)
                tokens_before_ptl = self._estimate_messages_tokens(effective_model)
                self._messages = await compact_fn(
                    self._messages, token_limit=target,
                )
                # PERF-1: Invalidate token cache — compaction replaced the message list.
                self._invalidate_token_cache()
                # ADR-061 Phase 3: suppress false cache-break after PTL compaction
                self._cache_tracker.notify_compaction()

                # ADR-058: Post-compact file state rebuild (PTL path)
                file_tracker = getattr(self._deps, "file_tracker", None)
                if file_tracker is not None:
                    self._messages = await rebuild_post_compact_context(
                        self._messages, file_tracker,
                    )
                    # Invalidate again — rebuild may have replaced the list.
                    self._invalidate_token_cache()

                # ADR-058 Phase 4: Restore plan and skill context (PTL path)
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

                # ADR-058: Record compact analytics (PTL path) — will rebuild cache.
                tokens_after_ptl = self._estimate_messages_tokens(effective_model)
                self._compact_stats.record(
                    "ptl_retry", tokens_freed=max(0, tokens_before_ptl - tokens_after_ptl),
                )

                # Emit POST_COMPACT hook
                if self._deps.hook_registry:
                    await execute_hooks(
                        self._deps.hook_registry,
                        HookEvent.POST_COMPACT,
                        {
                            "message_count_before": count_before,
                            "message_count_after": len(self._messages),
                        },
                    )

                continue  # retry the query

            break  # Query completed normally

        # --- Fallback retry (once only) ---
        if should_fallback:
            logger.info(
                "Primary model overloaded, switching to fallback: %s",
                fallback_model,
            )
            async for event in query(
                messages=self._messages,
                system_prompt=self._config.system_prompt,
                deps=self._deps,
                tools=self._config.tools,
                max_turns=max_turns or self._config.max_turns,
                model=fallback_model,
                thinking=self._config.thinking,
                tool_choice=self._config.tool_choice,
            ):
                event_type = event.get("type", "")

                if event_type == "assistant":
                    msg = event.get("message")
                    if isinstance(msg, Message):
                        self._messages.append(msg)
                        # PERF-1: Track in incremental cache (fallback model).
                        self._track_new_message(msg, fallback_model or effective_model)
                        usage = msg.metadata.get("usage", {}) if msg.metadata else {}
                        real_output = usage.get("output_tokens", 0)
                        out_tokens = real_output if real_output > 0 else count_tokens_for_model(
                            msg.text, fallback_model or effective_model
                        )
                        self._total_output_tokens += out_tokens
                        _turn_output_tokens += out_tokens

                # ADR-057: Capture tool_result user messages in fallback path too
                if event_type == "tool_result_message":
                    msg = event.get("message")
                    if isinstance(msg, Message):
                        self._messages.append(msg)
                        # PERF-1: Track in incremental cache (fallback model).
                        self._track_new_message(msg, fallback_model or effective_model)
                    continue  # internal event — don't yield to caller

                yield event

                # Record per-turn token snapshot for fallback turn
                if event_type == "done":
                    self._turn_token_history.append((_turn_input_tokens, _turn_output_tokens))

                # --- Budget enforcement in fallback loop ---
                if event_type == "done":
                    budget_events = self._check_budget(fallback_model)
                    for be in budget_events:
                        yield be
                    if any(be["type"] == "budget_exceeded" for be in budget_events):
                        return

                if event_type == "done" and self._session_store:
                    try:
                        await self._session_store.save(
                            self._session_id, self._messages,
                        )
                    except Exception:
                        logger.debug("Session auto-save failed", exc_info=True)
