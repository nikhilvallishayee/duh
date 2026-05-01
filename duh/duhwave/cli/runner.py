"""Daemon-side worker runner — single-turn OpenAI text completion.

The duhwave host attaches a runner via :class:`Dispatcher` so triggers
auto-spawn agents end-to-end. This module ships one runner — single-
turn streaming text — that is enough to demonstrate the trigger →
spawn → reply → outbox arc against a real provider.

Real production deployments will swap this for a runner that drives
``duh.kernel.engine.Engine`` (full agent loop with tool use). The
contract :data:`HostRunner` stays the same.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Awaitable, Callable


# ---- contract ----------------------------------------------------------


#: A host runner takes ``(prompt, system_prompt, model)`` and returns the
#: model's text completion. The dispatcher feeds it the trigger payload as
#: a JSON string (``prompt``) and the agent's role-specific
#: ``system_prompt`` parsed from the swarm topology.
HostRunner = Callable[[str, str, str], Awaitable[str]]


# ---- OpenAI text runner ----------------------------------------------


async def openai_text_runner(prompt: str, system_prompt: str, model: str) -> str:
    """Single-turn streaming text completion via D.U.H.'s OpenAI adapter.

    Raises ``RuntimeError`` if ``OPENAI_API_KEY`` is unset or the API
    returns an error event. The dispatcher catches the runtime error and
    transitions the spawned Task to FAILED with the error message so it
    surfaces in ``duh wave logs``.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not set; daemon cannot drive OpenAI runner")
    from duh.adapters.openai import OpenAIProvider

    provider = OpenAIProvider(model=model)
    chunks: list[str] = []
    usage: dict[str, int] = {"in": 0, "out": 0, "cached": 0}
    async for ev in provider.stream(
        messages=[{"role": "user", "content": prompt}],
        system_prompt=system_prompt,
        model=model,
        max_tokens=600,
    ):
        et = ev.get("type")
        if et == "text_delta":
            chunks.append(ev.get("text", ""))
        elif et in ("usage", "usage_delta"):
            usage["in"] = ev.get("input_tokens", usage["in"])
            usage["out"] = ev.get("output_tokens", usage["out"])
            usage["cached"] = ev.get("cached_tokens", usage["cached"])
        elif et == "error":
            raise RuntimeError(f"openai stream error: {ev.get('error')}")
    text = "".join(chunks).strip()
    if not text:
        raise RuntimeError("openai returned empty completion")
    return json.dumps({"text": text, "usage": usage, "ts": time.time()})


# ---- "no runner" sentinel --------------------------------------------


async def disabled_runner(_prompt: str, _system_prompt: str, _model: str) -> str:
    """Default when no real runner is configured.

    Conforms to :data:`HostRunner` so it can drop into any dispatcher
    slot. The dispatcher uses this when ``--no-runner`` is set or when
    ``OPENAI_API_KEY`` is missing at host start; the trigger arc still
    runs so the rest of the daemon's bookkeeping is observable.
    """
    return json.dumps(
        {
            "text": (
                "(daemon has no runner attached; trigger payload was "
                "received but no agent was invoked)"
            ),
            "usage": {"in": 0, "out": 0, "cached": 0},
            "ts": time.time(),
            "_disabled": True,
        }
    )


__all__ = ["HostRunner", "openai_text_runner", "disabled_runner"]
