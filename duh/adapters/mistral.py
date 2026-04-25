"""Native Mistral adapter.

Mistral's API at ``https://api.mistral.ai`` is OpenAI-compatible at
the wire level for chat completions, so the same ``openai.AsyncOpenAI``
client D.U.H. uses for OpenAI talks to it natively — including their
parallel function-calling protocol, which is the whole reason for
preferring native over an OpenAI-shape proxy:

- **Native parallel tool calls.** Mistral supports calling multiple
  tools in one assistant turn via the standard OpenAI ``tool_calls``
  array. No ``[TOOL_CALLS]`` text-block parsing needed when going
  through the official endpoint.
- **Native usage reporting.** Including any KV-cache hit data the
  upstream chooses to expose.

Auth: ``MISTRAL_API_KEY`` env var, or pass ``api_key=`` explicitly.

Models: ``mistral-large-2512``, ``mistral-medium-2505``,
``mistral-small-2603``, ``codestral-2511``, etc. The ``mistral/``
prefix is stripped before the request.
"""

from __future__ import annotations

import os
from typing import Any

from duh.adapters.openai import OpenAIProvider


_MISTRAL_BASE = "https://api.mistral.ai/v1"


class MistralProvider(OpenAIProvider):
    """OpenAI-shaped client pointed at Mistral's native API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "mistral-medium-2505",
        timeout: float = 600.0,
        max_retries: int = 2,
        base_url: str | None = None,
        tool_format: str | None = None,
    ) -> None:
        import openai

        resolved_key = api_key or os.environ.get("MISTRAL_API_KEY", "")
        self._default_model = _strip_prefix(model)
        # Mistral natively speaks OpenAI tool_calls — passthrough by default.
        # Override with ``tool_format="mistral"`` if hitting a hosted
        # variant that emits the [TOOL_CALLS] text protocol.
        self._tool_format = tool_format
        self._client = openai.AsyncOpenAI(
            api_key=resolved_key,
            base_url=base_url or _MISTRAL_BASE,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def stream(self, **kwargs: Any):
        if "model" in kwargs and kwargs["model"]:
            kwargs["model"] = _strip_prefix(kwargs["model"])
        async for event in super().stream(**kwargs):
            yield event


def _strip_prefix(model: str) -> str:
    if model and model.startswith("mistral/"):
        return model[len("mistral/"):]
    return model
