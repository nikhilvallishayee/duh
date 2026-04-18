"""Model capability detection for D.U.H.

Maps model names to their capabilities (context window, tool support,
thinking support, etc.) so that the harness can adapt behaviour when the
user switches models mid-session via /model.

    from duh.kernel.model_caps import get_capabilities

    caps = get_capabilities("claude-opus-4-6")
    assert caps.context_window == 1_000_000
    assert caps.supports_thinking is True
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelCapabilities:
    """Immutable capability descriptor for a model."""

    supports_tools: bool = True
    supports_thinking: bool = False
    supports_vision: bool = False
    supports_cache_control: bool = False
    max_output_tokens: int = 8192
    context_window: int = 200_000


# ── Capability definitions by model family ──────────────────────────

_CLAUDE_OPUS_4_6 = ModelCapabilities(
    supports_tools=True,
    supports_thinking=True,
    supports_vision=True,
    supports_cache_control=True,
    max_output_tokens=16384,
    context_window=1_000_000,
)

_CLAUDE_SONNET_4_6 = ModelCapabilities(
    supports_tools=True,
    supports_thinking=True,
    supports_vision=True,
    supports_cache_control=True,
    max_output_tokens=16384,
    context_window=1_000_000,
)

_CLAUDE_OPUS_4 = ModelCapabilities(
    supports_tools=True,
    supports_thinking=True,
    supports_vision=True,
    supports_cache_control=True,
    max_output_tokens=16384,
    context_window=200_000,
)

_CLAUDE_SONNET_4 = ModelCapabilities(
    supports_tools=True,
    supports_thinking=True,
    supports_vision=True,
    supports_cache_control=True,
    max_output_tokens=16384,
    context_window=200_000,
)

_CLAUDE_HAIKU_4 = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=True,
    supports_cache_control=True,
    max_output_tokens=8192,
    context_window=200_000,
)

_CLAUDE_HAIKU_3 = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=True,
    supports_cache_control=True,
    max_output_tokens=4096,
    context_window=200_000,
)

_GPT_4O = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=True,
    supports_cache_control=False,
    max_output_tokens=16384,
    context_window=128_000,
)

_GPT_4O_MINI = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=True,
    supports_cache_control=False,
    max_output_tokens=16384,
    context_window=128_000,
)

_GEMINI_25_PRO = ModelCapabilities(
    supports_tools=True,
    supports_thinking=True,
    supports_vision=True,
    supports_cache_control=True,
    max_output_tokens=65_536,
    context_window=2_000_000,
)

_GEMINI_25_FLASH = ModelCapabilities(
    supports_tools=True,
    supports_thinking=True,
    supports_vision=True,
    supports_cache_control=True,
    max_output_tokens=65_536,
    context_window=1_048_576,
)

_GEMINI_20_FLASH = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=True,
    supports_cache_control=False,
    max_output_tokens=8192,
    context_window=1_048_576,
)

_GEMINI_15_PRO = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=True,
    supports_cache_control=False,
    max_output_tokens=8192,
    context_window=2_000_000,
)

_GEMINI_15_FLASH = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=True,
    supports_cache_control=False,
    max_output_tokens=8192,
    context_window=1_048_576,
)

# Groq — all models served with 128K context
_GROQ_LLAMA_70B = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=False,
    supports_cache_control=False,
    max_output_tokens=32_768,
    context_window=128_000,
)

_GROQ_LLAMA_8B = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=False,
    supports_cache_control=False,
    max_output_tokens=8192,
    context_window=128_000,
)

# Qwen 2.5 family (Ollama local) — 128K native
_QWEN_25 = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=False,
    supports_cache_control=False,
    max_output_tokens=8192,
    context_window=128_000,
)

# Qwen 2.5 Coder 1.5B — smaller context (32K native)
_QWEN_25_SMALL = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=False,
    supports_cache_control=False,
    max_output_tokens=4096,
    context_window=32_000,
)

# DeepSeek Coder V2 Lite — 128K native
_DEEPSEEK_CODER_V2 = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=False,
    supports_cache_control=False,
    max_output_tokens=8192,
    context_window=128_000,
)

# Llama 3.2 (local via Ollama) — 128K
_LLAMA_32 = ModelCapabilities(
    supports_tools=True,
    supports_thinking=False,
    supports_vision=False,
    supports_cache_control=False,
    max_output_tokens=8192,
    context_window=128_000,
)

_OLLAMA_DEFAULT = ModelCapabilities(
    supports_tools=False,
    supports_thinking=False,
    supports_vision=False,
    supports_cache_control=False,
    max_output_tokens=4096,
    context_window=32_000,
)

_DEFAULT = ModelCapabilities()


# ── Prefix-match table (checked in order, first match wins) ────────

_PREFIX_TABLE: list[tuple[str, ModelCapabilities]] = [
    # Claude 4.6 (1M context)
    ("claude-opus-4-6", _CLAUDE_OPUS_4_6),
    ("claude-sonnet-4-6", _CLAUDE_SONNET_4_6),
    # Claude 4 / 4.5 (200K context)
    ("claude-opus-4", _CLAUDE_OPUS_4),
    ("claude-sonnet-4", _CLAUDE_SONNET_4),
    # Claude Haiku (longer prefixes first so 3-5 beats 3)
    ("claude-haiku-4", _CLAUDE_HAIKU_4),
    ("claude-haiku-3-5", _CLAUDE_HAIKU_4),
    ("claude-haiku-3", _CLAUDE_HAIKU_3),
    ("claude-3-5-haiku", _CLAUDE_HAIKU_4),
    ("claude-3-haiku", _CLAUDE_HAIKU_3),
    # Claude 3.5 Sonnet (legacy names)
    ("claude-3-5-sonnet", _CLAUDE_SONNET_4),
    ("claude-3-opus", _CLAUDE_OPUS_4),
    # OpenAI
    ("gpt-4o-mini", _GPT_4O_MINI),
    ("gpt-4o", _GPT_4O),
    # Gemini — longer prefixes first so 2.5-pro beats 2.5 and gemini
    ("gemini/gemini-2.5-pro", _GEMINI_25_PRO),
    ("gemini/gemini-2.5-flash", _GEMINI_25_FLASH),
    ("gemini/gemini-2.0-flash", _GEMINI_20_FLASH),
    ("gemini/gemini-1.5-pro", _GEMINI_15_PRO),
    ("gemini/gemini-1.5-flash", _GEMINI_15_FLASH),
    ("gemini-2.5-pro", _GEMINI_25_PRO),
    ("gemini-2.5-flash", _GEMINI_25_FLASH),
    ("gemini-2.0-flash", _GEMINI_20_FLASH),
    ("gemini-1.5-pro", _GEMINI_15_PRO),
    ("gemini-1.5-flash", _GEMINI_15_FLASH),
    ("gemini", _GEMINI_20_FLASH),  # generic fallback (1M, reasonable default)
    # Groq (via LiteLLM) — 128K context across all models
    ("groq/llama-3.3-70b", _GROQ_LLAMA_70B),
    ("groq/llama-3.1-70b", _GROQ_LLAMA_70B),
    ("groq/llama-3.1-8b", _GROQ_LLAMA_8B),
    ("groq/", _GROQ_LLAMA_70B),  # sensible default for unknown Groq models
    # Ollama-local — longer/specific prefixes before generic substrings
    ("qwen2.5-coder:1.5b", _QWEN_25_SMALL),
    ("qwen2.5-coder", _QWEN_25),
    ("qwen2.5:1.5b", _QWEN_25_SMALL),
    ("qwen2.5", _QWEN_25),
    ("deepseek-coder-v2", _DEEPSEEK_CODER_V2),
    ("llama3.2", _LLAMA_32),
    ("llama3.3", _LLAMA_32),
]

# ── Substring fallbacks (for Ollama / local models) ─────────────────

_SUBSTRING_TABLE: list[tuple[str, ModelCapabilities]] = [
    ("ollama", _OLLAMA_DEFAULT),
    ("llama", _OLLAMA_DEFAULT),
    ("qwen", _OLLAMA_DEFAULT),
    ("mistral", _OLLAMA_DEFAULT),
    ("phi", _OLLAMA_DEFAULT),
    ("gemma", _OLLAMA_DEFAULT),
    ("deepseek", _OLLAMA_DEFAULT),
]


def get_capabilities(model: str) -> ModelCapabilities:
    """Return capabilities for a model by name prefix matching.

    Tries exact prefix matching first (most specific), then falls back
    to substring matching for local/Ollama models, and finally returns
    a conservative default.
    """
    lower = model.lower()

    # Prefix match (order matters — longer prefixes checked first)
    for prefix, caps in _PREFIX_TABLE:
        if lower.startswith(prefix):
            return caps

    # Substring fallback for local models
    for substring, caps in _SUBSTRING_TABLE:
        if substring in lower:
            return caps

    return _DEFAULT


def model_context_block(model: str) -> str:
    """Build a ``<model-context>`` block for embedding in the system prompt.

    The block is a small structured text section that tells the model its own
    identity, context window size, and key capabilities.  It is rebuilt every
    time the user switches models via ``/model`` so the system prompt stays
    accurate.
    """
    caps = get_capabilities(model)
    lines = [
        "<model-context>",
        f"model: {model}",
        f"context_window: {caps.context_window:,}",
        f"max_output_tokens: {caps.max_output_tokens:,}",
        f"supports_tools: {str(caps.supports_tools).lower()}",
        f"supports_thinking: {str(caps.supports_thinking).lower()}",
        f"supports_vision: {str(caps.supports_vision).lower()}",
        "</model-context>",
    ]
    return "\n".join(lines)


def rebuild_system_prompt(
    system_prompt: str | list[str],
    old_model: str,
    new_model: str,
) -> str:
    """Return *system_prompt* with the ``<model-context>`` block updated.

    If the prompt already contains a ``<model-context>`` block for
    *old_model* it is replaced.  If no block is present one is appended.
    """
    import re

    sp = "\n\n".join(system_prompt) if isinstance(system_prompt, list) else system_prompt

    old_block = model_context_block(old_model)
    new_block = model_context_block(new_model)

    if old_block in sp:
        return sp.replace(old_block, new_block)

    if "<model-context>" in sp:
        return re.sub(
            r"<model-context>.*?</model-context>",
            new_block,
            sp,
            flags=re.DOTALL,
        )

    # No existing block — append
    return sp + "\n\n" + new_block
