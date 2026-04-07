"""Token estimation and cost tracking for D.U.H.

Simple heuristic-based token counting (~4 chars per token for English)
and per-model cost estimation. No external dependencies.

    from duh.kernel.tokens import count_tokens, estimate_cost

    tokens = count_tokens("Hello, world!")           # ~3
    cost = estimate_cost("claude-sonnet-4-6", 1000, 500)  # ~$0.01
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4


def count_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string.

    Uses the standard heuristic of ~4 characters per token for English.
    Returns at least 0.
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

# Pricing per 1M tokens: (input_cost, output_cost) in USD
# Updated as of early 2025 public pricing.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5-20250514": (3.0, 15.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-opus-4-20250514": (15.0, 75.0),
    "claude-3-opus": (15.0, 75.0),
    "claude-haiku-3-5": (0.25, 1.25),
    "claude-3-5-haiku-20241022": (0.25, 1.25),
    "claude-3-haiku": (0.25, 1.25),
    # OpenAI
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-2024-08-06": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
    # Ollama / local — free
    "ollama": (0.0, 0.0),
}


def _resolve_pricing(model: str) -> tuple[float, float]:
    """Resolve pricing for a model, falling back to pattern matching."""
    # Exact match
    if model in _MODEL_PRICING:
        return _MODEL_PRICING[model]

    # Pattern matching for common prefixes
    lower = model.lower()
    if "opus" in lower:
        return (15.0, 75.0)
    if "haiku" in lower:
        return (0.25, 1.25)
    if "sonnet" in lower:
        return (3.0, 15.0)
    if "gpt-4o-mini" in lower:
        return (0.15, 0.60)
    if "gpt-4o" in lower:
        return (2.50, 10.0)
    if "gpt-4" in lower:
        return (2.50, 10.0)
    # Local models (ollama, llama, qwen, etc.)
    for prefix in ("ollama", "llama", "qwen", "mistral", "phi", "gemma"):
        if prefix in lower:
            return (0.0, 0.0)

    # Unknown model — use Sonnet pricing as a reasonable default
    return (3.0, 15.0)


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Estimate the cost in USD for a given number of tokens.

    Args:
        model: Model identifier string.
        input_tokens: Number of input (prompt) tokens.
        output_tokens: Number of output (completion) tokens.

    Returns:
        Estimated cost in USD.
    """
    input_price, output_price = _resolve_pricing(model)
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


# ---------------------------------------------------------------------------
# Model context limits (max tokens the model can accept)
# ---------------------------------------------------------------------------

MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # Anthropic
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5-20250514": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-opus-4-20250514": 200_000,
    "claude-3-opus": 200_000,
    "claude-haiku-3-5": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-haiku": 200_000,
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-2024-08-06": 128_000,
    "gpt-4o-mini": 128_000,
    "o1": 200_000,
}

_DEFAULT_CONTEXT_LIMIT = 100_000


def get_context_limit(model: str) -> int:
    """Return the context window size (in tokens) for a model.

    Falls back to a safe default (100K) for unknown models.
    """
    if model in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[model]

    # Pattern matching for common families
    lower = model.lower()
    if any(k in lower for k in ("claude", "sonnet", "opus", "haiku")):
        return 200_000
    if "gpt-4o" in lower:
        return 128_000
    if "o1" in lower:
        return 200_000

    return _DEFAULT_CONTEXT_LIMIT


def format_cost(cost: float) -> str:
    """Format a cost value for display.

    Shows 4 decimal places for small values, 2 for larger.
    """
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"
