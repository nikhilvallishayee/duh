"""Native Together AI adapter.

Together (https://api.together.xyz) hosts an OpenAI-compatible chat
completions endpoint over a wide catalogue of open-weights models —
Llama 3.x and 4.x, DeepSeek, Mixtral, Qwen, Gemma, GLM, and a long
tail of fine-tunes. Their wire format is OpenAI Chat Completions,
plus a few Together-specific knobs (``dollars`` field in usage,
``logprobs``, etc.).

What this adapter is for:

- **Llama models**, which have no first-party API — Together is
  the closest thing to an upstream. Inference servers wrap the
  model's tool template (Llama-3 uses ``<|python_tag|>``-style or
  Hermes-style depending on the fine-tune); Together's tool parser
  normalises most popular fine-tunes back to OpenAI ``tool_calls``.
- **A managed alternative for DeepSeek / Qwen / Mistral** when the
  user prefers Together's pricing or reliability over the
  first-party APIs.
- **Together's prompt caching**, surfaced in the response usage when
  available. Some Together-hosted models cache stable prefixes
  automatically; D.U.H.'s ``_normalise_usage`` reads the
  ``cached_tokens`` field that Together emits in OpenAI-shape.

Auth: ``TOGETHER_API_KEY`` env var, or pass ``api_key=`` explicitly.

Models: ``meta-llama/Llama-3.3-70B-Instruct-Turbo``,
``meta-llama/Llama-4-Scout-17B-16E-Instruct``,
``deepseek-ai/DeepSeek-V3``, ``Qwen/Qwen2.5-72B-Instruct-Turbo``, etc.
"""

from __future__ import annotations

import os
from typing import Any

from duh.adapters.openai import OpenAIProvider


_TOGETHER_BASE = "https://api.together.xyz/v1"


class TogetherProvider(OpenAIProvider):
    """OpenAI-shaped client pointed at Together's native API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "meta-llama/Llama-4-Scout-17B-16E-Instruct",
        timeout: float = 600.0,
        max_retries: int = 2,
        base_url: str | None = None,
        tool_format: str | None = None,
    ) -> None:
        import openai

        resolved_key = api_key or os.environ.get("TOGETHER_API_KEY", "")
        self._default_model = _strip_prefix(model)
        # Together's tool parser handles most modern fine-tunes back
        # to OpenAI shape. Passthrough by default; users on legacy
        # fine-tunes can pin ``tool_format="hermes"`` or similar.
        self._tool_format = tool_format
        self._client = openai.AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or _TOGETHER_BASE,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def stream(self, **kwargs: Any):
        if "model" in kwargs and kwargs["model"]:
            kwargs["model"] = _strip_prefix(kwargs["model"])
        async for event in super().stream(**kwargs):
            yield event


def _strip_prefix(model: str) -> str:
    if model and model.startswith("together/"):
        return model[len("together/"):]
    return model
