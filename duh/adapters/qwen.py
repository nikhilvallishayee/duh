"""Native Qwen adapter (Alibaba DashScope).

Alibaba's DashScope serves Qwen models with two API shapes:

- **OpenAI-compatible** at ``https://dashscope-intl.aliyuncs.com/compatible-mode/v1``
- **Native DashScope** at ``https://dashscope-intl.aliyuncs.com/api/v1``

This adapter uses the OpenAI-compatible endpoint because it gives
us streaming + tool calls + ``usage`` reporting in the same shape
the rest of D.U.H. already understands. The native DashScope shape
adds nothing on top for our use case — the OpenAI-compat path
forwards Qwen's full feature set.

What's worth knowing about Qwen:

- **Native parallel function calling** via the standard ``tools`` /
  ``tool_calls`` parameters.
- **Context Cache** with explicit Anthropic-style ``cache_control``
  blocks. Minimum cacheable prefix is 1024 tokens. Cached tokens are
  surfaced via the standard ``prompt_tokens_details.cached_tokens``
  field, which D.U.H.'s ``_normalise_usage`` already reads.
- ``thinking`` parameter for reasoning models like
  ``qwen3-max-thinking``.

Auth: ``DASHSCOPE_API_KEY`` (canonical) or ``ALIBABA_API_KEY`` (alias).

Models: ``qwen3-max-thinking``, ``qwen3-coder-plus``,
``qwen3.5-397b-a17b``, ``qwen3.5-122b-a10b``, etc.
"""

from __future__ import annotations

import os
from typing import Any

from duh.adapters.openai import OpenAIProvider


_DASHSCOPE_BASE = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


class QwenProvider(OpenAIProvider):
    """OpenAI-shaped client pointed at DashScope's compat endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "qwen3-max",
        timeout: float = 600.0,
        max_retries: int = 2,
        base_url: str | None = None,
        tool_format: str | None = None,
    ) -> None:
        import openai

        resolved_key = (
            api_key
            or os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("ALIBABA_API_KEY", "")
        )
        self._default_model = _strip_prefix(model)
        # Qwen via DashScope native function-calling = OpenAI-shape.
        # Passthrough by default.
        self._tool_format = tool_format
        self._client = openai.AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or _DASHSCOPE_BASE,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def stream(self, **kwargs: Any):
        if "model" in kwargs and kwargs["model"]:
            kwargs["model"] = _strip_prefix(kwargs["model"])
        async for event in super().stream(**kwargs):
            yield event


def _strip_prefix(model: str) -> str:
    if model and model.startswith("qwen/"):
        return model[len("qwen/"):]
    return model
