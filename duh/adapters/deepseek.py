"""Native DeepSeek adapter.

DeepSeek's API at ``https://api.deepseek.com`` is OpenAI-compatible at
the wire level, so the same ``openai.AsyncOpenAI`` client D.U.H. uses
talks to it natively — streaming SSE, tool calls, JSON mode, all
unchanged.

Two reasons to use this instead of OpenRouter for DeepSeek:

1. **Native automatic prompt caching.** DeepSeek's API returns
   ``prompt_cache_hit_tokens`` and ``prompt_cache_miss_tokens`` in
   ``usage`` — D.U.H.'s ``_normalise_usage`` reads both shapes.
   Going via OpenRouter, the cache savings are still applied at the
   provider level but the response shape can vary.
2. **Cost.** Direct DeepSeek pricing is ~30% cheaper than the same
   model through OpenRouter at most provider tiers.

Auth: ``DEEPSEEK_API_KEY`` env var, or pass ``api_key=`` explicitly.

Models: ``deepseek-chat``, ``deepseek-coder``, ``deepseek-reasoner``,
``deepseek-v4-pro``, etc. The same model name is forwarded; no prefix
strip needed.
"""

from __future__ import annotations

import os
from typing import Any

from duh.adapters.openai import OpenAIProvider


_DEEPSEEK_BASE = "https://api.deepseek.com/v1"


class DeepSeekProvider(OpenAIProvider):
    """OpenAI-shaped client pointed at DeepSeek's native API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-chat",
        timeout: float = 600.0,
        max_retries: int = 2,
        base_url: str | None = None,
        tool_format: str | None = None,
    ) -> None:
        import openai

        resolved_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self._default_model = _strip_ds_prefix(model)
        self._tool_format = tool_format
        self._client = openai.AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or _DEEPSEEK_BASE,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def stream(self, **kwargs: Any):
        if "model" in kwargs and kwargs["model"]:
            kwargs["model"] = _strip_ds_prefix(kwargs["model"])
        async for event in super().stream(**kwargs):
            yield event


def _strip_ds_prefix(model: str) -> str:
    """Strip a leading ``deepseek/`` segment if present.

    Lets users pass either the canonical D.U.H. ``deepseek/<model>``
    form or the bare DeepSeek API ``<model>`` form.
    """
    if model and model.startswith("deepseek/"):
        return model[len("deepseek/"):]
    return model
