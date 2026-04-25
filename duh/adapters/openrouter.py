"""Native OpenRouter adapter — uses the OpenAI Python SDK against
OpenRouter's OpenAI-compatible endpoint.

OpenRouter (https://openrouter.ai) routes to ~250 open-weights and
hosted models through a single OpenAI-shaped API. Their endpoint
implements the OpenAI Chat Completions spec verbatim, so the same
``openai.AsyncOpenAI`` client D.U.H. already uses talks to them
natively — streaming SSE, tool calls, JSON-mode, all unchanged.

Why a separate provider class instead of letting users pass
``base_url`` to ``OpenAIProvider`` directly:

1. **Model-prefix strip.** D.U.H. uses ``openrouter/<vendor>/<model>``
   as the canonical name (so the registry can route by prefix),
   but OpenRouter's API expects ``<vendor>/<model>`` in the request
   body. The strip happens in one place.

2. **Attribution headers.** OpenRouter recommends sending
   ``HTTP-Referer`` and ``X-Title`` headers so usage shows up as
   D.U.H. in their leaderboard rather than anonymous traffic.

3. **Custom routing knobs.** OpenRouter accepts per-request hints
   like ``provider: {order: [...]}`` to pin a specific upstream.
   The adapter exposes that via the standard ``extra_body``
   passthrough.

Usage::

    from duh.adapters.openrouter import OpenRouterProvider
    provider = OpenRouterProvider(api_key="sk-or-…",
                                   model="deepseek/deepseek-v4-pro")
    deps = Deps(call_model=provider.stream)
"""

from __future__ import annotations

import os
from typing import Any

from duh.adapters.openai import OpenAIProvider


_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_DUH_REFERER = "https://github.com/nikhilvallishayee/duh"
_DUH_TITLE = "D.U.H."


class OpenRouterProvider(OpenAIProvider):
    """OpenAI-shaped client pointed at OpenRouter.

    Inherits the entire OpenAI streaming / tool-call / message-shape
    logic from :class:`OpenAIProvider`. The only OpenRouter-specific
    bits live in ``__init__`` (base_url + attribution headers) and
    in :meth:`stream` (model-name normalisation).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "",
        timeout: float = 600.0,
        max_retries: int = 2,
        base_url: str | None = None,
        referer: str | None = None,
        title: str | None = None,
    ) -> None:
        import openai

        resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        # Strip "openrouter/" prefix from default model — OpenRouter
        # expects "<vendor>/<model>" in the request body.
        self._default_model = _strip_or_prefix(model)
        self._client = openai.AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or _OPENROUTER_BASE,
            timeout=timeout,
            max_retries=max_retries,
            default_headers={
                "HTTP-Referer": referer or _DUH_REFERER,
                "X-Title": title or _DUH_TITLE,
            },
        )

    async def stream(self, **kwargs: Any):
        # Strip "openrouter/" from any model passed at call time.
        if "model" in kwargs and kwargs["model"]:
            kwargs["model"] = _strip_or_prefix(kwargs["model"])
        async for event in super().stream(**kwargs):
            yield event


def _strip_or_prefix(model: str) -> str:
    """Remove a leading ``openrouter/`` segment from a model name."""
    if model and model.startswith("openrouter/"):
        return model[len("openrouter/"):]
    return model
