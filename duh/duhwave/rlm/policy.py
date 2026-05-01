"""Policy router — when does the RLM engine activate? ADR-028 §"When the engine activates"."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ContextMode(str, Enum):
    AUTO = "auto"
    RLM = "rlm"
    COMPACT = "compact"


@dataclass(slots=True)
class _Decision:
    mode: ContextMode
    reason: str


# Default activation threshold: input >= 25% of context window triggers RLM.
_DEFAULT_ACTIVATE_RATIO = 0.25


def choose_context_mode(
    *,
    explicit: ContextMode = ContextMode.AUTO,
    bulk_input_tokens: int,
    context_window: int,
    model_supports_tool_calls: bool,
    activate_ratio: float = _DEFAULT_ACTIVATE_RATIO,
) -> ContextMode:
    """Decide which context engine handles this turn.

    Inputs:
        explicit: user override from --context-mode flag.
        bulk_input_tokens: estimated tokens of large attached corpora
            (file contents, fetched URLs, large prior tool results).
        context_window: model's context-window in tokens.
        model_supports_tool_calls: false → no RLM (RLM tools are tool calls).

    Returns:
        ContextMode.RLM or ContextMode.COMPACT.
        ``AUTO`` is resolved here; callers always see a concrete mode.
    """
    if explicit == ContextMode.RLM:
        return ContextMode.RLM
    if explicit == ContextMode.COMPACT:
        return ContextMode.COMPACT
    # AUTO branch.
    if not model_supports_tool_calls:
        return ContextMode.COMPACT
    if context_window <= 0:
        return ContextMode.COMPACT
    if bulk_input_tokens / context_window >= activate_ratio:
        return ContextMode.RLM
    return ContextMode.COMPACT


def explain(
    *,
    explicit: ContextMode,
    bulk_input_tokens: int,
    context_window: int,
    model_supports_tool_calls: bool,
) -> _Decision:
    """Same logic as :func:`choose_context_mode` but returns a reason string.

    Useful for ``duh wave inspect`` and debug logs.
    """
    mode = choose_context_mode(
        explicit=explicit,
        bulk_input_tokens=bulk_input_tokens,
        context_window=context_window,
        model_supports_tool_calls=model_supports_tool_calls,
    )
    if explicit == ContextMode.RLM:
        return _Decision(mode, "explicit --context-mode rlm")
    if explicit == ContextMode.COMPACT:
        return _Decision(mode, "explicit --context-mode compact")
    if not model_supports_tool_calls:
        return _Decision(mode, "model lacks tool-calling; RLM unavailable")
    ratio = bulk_input_tokens / context_window if context_window else 0.0
    if mode == ContextMode.RLM:
        return _Decision(mode, f"bulk input {ratio:.0%} of window ≥ 25% threshold")
    return _Decision(mode, f"bulk input {ratio:.0%} of window < 25% threshold")
