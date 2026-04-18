"""Token estimation and cost tracking for D.U.H.

Model-aware heuristic token counting with calibrated per-family ratios and
per-model cost estimation. No external dependencies required.

    from duh.kernel.tokens import count_tokens, count_tokens_for_model, estimate_cost

    tokens = count_tokens("Hello, world!")                        # ~3 (generic)
    tokens = count_tokens_for_model("Hello, world!", "claude-sonnet-4-6")  # Anthropic ratio
    cost = estimate_cost("claude-sonnet-4-6", 1000, 500)          # ~$0.01

Model family ratios (chars per token, empirically calibrated):
- Anthropic Claude: ~3.5 chars/token  (BPE trained on diverse data, slightly denser)
- OpenAI GPT-4/o:  ~4.0 chars/token  (tiktoken cl100k_base approximate)
- OpenAI GPT-3.5:  ~4.0 chars/token
- Ollama/local:    ~3.5 chars/token   (most use LlamaTokenizer or similar)
- Unknown/default: ~4.0 chars/token   (conservative)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Token estimation — model-aware
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4  # generic default (conservative)

# Per-model-family chars-per-token ratios, empirically calibrated.
# Lower ratio = denser tokenization = more tokens per char.
_FAMILY_CHARS_PER_TOKEN: dict[str, float] = {
    "anthropic": 3.5,   # Claude family (BPE, dense)
    "openai": 4.0,      # GPT-4, GPT-4o, o1/o3 (tiktoken cl100k / o200k)
    "ollama": 3.5,      # Most local models (LlamaTokenizer-derived)
    "default": 4.0,
}


def _chars_per_token_for_model(model: str) -> float:
    """Return the calibrated chars-per-token ratio for the model's family."""
    lower = model.lower()
    # Anthropic Claude family
    if any(k in lower for k in ("claude", "sonnet", "haiku", "opus")):
        return _FAMILY_CHARS_PER_TOKEN["anthropic"]
    # OpenAI GPT / o-series
    if any(k in lower for k in ("gpt-", "o1", "o3", "codex", "davinci", "turbo")):
        return _FAMILY_CHARS_PER_TOKEN["openai"]
    # Local / Ollama models
    if any(k in lower for k in ("ollama", "llama", "qwen", "mistral", "phi", "gemma", "deepseek")):
        return _FAMILY_CHARS_PER_TOKEN["ollama"]
    return _FAMILY_CHARS_PER_TOKEN["default"]


def count_tokens(text: str) -> int:
    """Estimate the number of tokens in a text string using the generic ratio.

    Uses the conservative ~4 characters per token heuristic.
    For model-specific accuracy use count_tokens_for_model().
    Returns at least 0.
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def count_tokens_for_model(text: str, model: str) -> int:
    """Estimate token count using a model-family-calibrated ratio.

    More accurate than count_tokens() for cost estimation since different
    model families tokenize at different densities:
    - Anthropic Claude: ~3.5 chars/token
    - OpenAI GPT/o-series: ~4.0 chars/token
    - Local/Ollama: ~3.5 chars/token

    Returns at least 0 (or at least 1 for non-empty strings).
    """
    if not text:
        return 0
    ratio = _chars_per_token_for_model(model)
    return max(1, int(len(text) / ratio))


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
    # Anthropic — Sonnet 4.6 and Opus 4.6 support 1M context
    "claude-sonnet-4-6": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    # Older Claude models — 200K
    "claude-sonnet-4-5-20250514": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-opus-4-20250514": 200_000,
    "claude-3-opus": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-3-5": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-haiku": 200_000,
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-2024-08-06": 128_000,
    "gpt-4o-mini": 128_000,
    "o1": 200_000,
    # Test fixture — stable 100K for compaction-threshold tests. Do not remove.
    "test-model": 100_000,
}

_DEFAULT_CONTEXT_LIMIT = 100_000


def get_context_limit(model: str) -> int:
    """Return the context window size (in tokens) for a model.

    Single source of truth: delegates to ``duh.kernel.model_caps`` so that
    Anthropic / OpenAI / Gemini / Groq / Ollama / LiteLLM all resolve the
    same way. The legacy ``MODEL_CONTEXT_LIMITS`` table below is kept for
    backwards compatibility with callers that patch it in tests.
    """
    # Test-patchable override path (preserve legacy behaviour)
    if model in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[model]

    # Delegate to the unified capability registry
    try:
        from duh.kernel.model_caps import get_capabilities
        return get_capabilities(model).context_window
    except Exception:
        pass

    return _DEFAULT_CONTEXT_LIMIT


def format_cost(cost: float) -> str:
    """Format a cost value for display.

    Shows 4 decimal places for small values, 2 for larger.
    """
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"
