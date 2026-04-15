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
from duh.kernel.confirmation import ConfirmationMinter
from duh.kernel.deps import Deps
from duh.kernel.loop import query
from duh.kernel.messages import Message, UserMessage
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

        # SESSION_START: refuse sessions where all three trifecta capabilities
        # are simultaneously present without explicit acknowledgement.
        _caps = compute_session_capabilities(self._config.tools)
        check_trifecta(_caps, acknowledged=self._config.trifecta_acknowledged)

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
        effective_model_for_count = model or self._config.model
        prompt_text = prompt if isinstance(prompt, str) else str(prompt)
        sys_text = (
            self._config.system_prompt
            if isinstance(self._config.system_prompt, str)
            else " ".join(self._config.system_prompt)
        )
        input_estimate = (
            count_tokens_for_model(prompt_text, effective_model_for_count)
            + count_tokens_for_model(sys_text, effective_model_for_count)
        )
        # Include prior message context sent with this turn
        for m in self._messages[:-1]:
            input_estimate += count_tokens_for_model(
                m.text if isinstance(m, Message) else str(m),
                effective_model_for_count,
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
            self._messages = await compact_fn(
                self._messages, token_limit=threshold,
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

        # Run the query loop
        effective_model = model or self._config.model
        fallback_model = self._config.fallback_model
        should_fallback = False

        if self._slog:
            self._slog.model_request(model=effective_model, turn=self._turn_count)

        # --- Validate message alternation (API requires strict user/assistant) ---
        from duh.kernel.messages import validate_alternation
        self._messages = validate_alternation(self._messages)

        # --- Query with PTL retry ---
        ptl_retries = 0
        while True:
            ptl_detected = False

            async for event in query(
                messages=self._messages,
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
                    if self._slog:
                        self._slog.model_response(model=effective_model, turn=self._turn_count)

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
                self._messages = await compact_fn(
                    self._messages, token_limit=target,
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
                        usage = msg.metadata.get("usage", {}) if msg.metadata else {}
                        real_output = usage.get("output_tokens", 0)
                        out_tokens = real_output if real_output > 0 else count_tokens_for_model(
                            msg.text, fallback_model or effective_model
                        )
                        self._total_output_tokens += out_tokens
                        _turn_output_tokens += out_tokens

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
