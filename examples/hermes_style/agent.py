"""Build your own Hermes-style coding agent on D.U.H.

A complete agent loop in one file, demonstrating the four Hermes
opinions ported onto D.U.H.'s primitives:

1. Multi-mode native adapters — D.U.H. already provides per-provider
   adapters; we just pick one.
2. Tool-arg repair middleware (ADR-028) — applied automatically via
   the OpenAI-shape adapter.
3. Parallel-safe tool dispatch — read-only tools run concurrently
   under a bounded thread pool.
4. Shared turn budget across parent + sub-agents.

Usage::

    python agent.py --model claude-opus-4-7   -p "Audit auth.py."
    python agent.py --model deepseek-chat     -p "Same question."
    python agent.py --model qwen/qwen3-max    -p "Same question."

Reads the cookbook at ``docs/cookbook/build-your-own-agent.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.tokens import count_tokens, get_context_limit
from duh.providers.registry import build_model_backend, infer_provider_from_model
from duh.tools.registry import get_all_tools


# ---------------------------------------------------------------------------
# 1. Hermes-style turn budget
# ---------------------------------------------------------------------------

@dataclass
class IterationBudget:
    """Shared turn budget — parent and any children draw from the same pool."""

    remaining: int

    def take(self, n: int = 1) -> bool:
        if self.remaining < n:
            return False
        self.remaining -= n
        return True


# ---------------------------------------------------------------------------
# 2. Hermes-style parallel-safe tool dispatch
# ---------------------------------------------------------------------------

# Read-only tools that are safe to run concurrently. Mutating tools
# (Write/Edit/Bash) must run sequentially to preserve ordering.
PARALLEL_SAFE_TOOLS: frozenset[str] = frozenset({
    "Read", "Glob", "Grep", "WebFetch", "WebSearch",
})

# Hard cap on concurrency — even if 50 reads arrive at once, dispatch
# in batches of 8. Matches Hermes' ``_MAX_TOOL_WORKERS`` default.
MAX_TOOL_WORKERS: int = 8


async def parallel_dispatch(run_tool, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hermes-style dispatch: concurrent reads, sequential writes."""
    results: list[dict[str, Any]] = []
    sem = asyncio.Semaphore(MAX_TOOL_WORKERS)
    safe_batch: list[dict[str, Any]] = []

    async def _bounded(call):
        async with sem:
            return await run_tool(call)

    async def _flush_safe():
        nonlocal safe_batch
        if not safe_batch:
            return
        batch_results = await asyncio.gather(*(_bounded(c) for c in safe_batch))
        results.extend(batch_results)
        safe_batch = []

    for call in tool_calls:
        if call.get("name") in PARALLEL_SAFE_TOOLS:
            safe_batch.append(call)
            continue
        # Mutating tool — flush the safe batch first to preserve ordering.
        await _flush_safe()
        results.append(await run_tool(call))
    await _flush_safe()
    return results


# ---------------------------------------------------------------------------
# 3. Hermes-style threshold-based context compression
# ---------------------------------------------------------------------------

async def maybe_compact(
    messages: list[Any],
    model: str,
    deps: Deps,
    *,
    trigger_ratio: float = 0.50,
    target_ratio: float = 0.20,
    protect_head: int = 3,
    protect_tail: int = 20,
) -> list[Any]:
    """Compress when context > trigger_ratio of limit; target ratio after.

    Mirrors Hermes' ``context_compressor`` defaults: trigger at 50%,
    target 20%, never touch the first 3 (system + initial user turn)
    or the last 20 (active working set).
    """
    if len(messages) <= protect_head + protect_tail:
        return messages
    used = count_tokens(messages, model)
    limit = get_context_limit(model)
    if not limit or used < trigger_ratio * limit:
        return messages

    compactor = getattr(deps, "compact", None)
    if compactor is None:
        return messages
    target_tokens = int(target_ratio * limit)
    return await compactor(messages, token_limit=target_tokens)


# ---------------------------------------------------------------------------
# 4. The agent loop
# ---------------------------------------------------------------------------

async def run_hermes_style_agent(
    *,
    model: str,
    prompt: str,
    max_turns: int = 50,
) -> int:
    """End-to-end agent loop: Hermes patterns layered on D.U.H. primitives."""

    # 4.1 Pick the native adapter for this model.
    provider = infer_provider_from_model(model) or "openai"
    backend = build_model_backend(provider, model)
    if not backend.ok:
        sys.stderr.write(f"backend error: {backend.error}\n")
        return 2

    # 4.2 Wire up the engine. Tools come from D.U.H.'s standard registry.
    tools = get_all_tools()
    deps = Deps(
        call_model=backend.call_model,
        # Note: production wiring (approvers, hooks, audit) is more
        # involved — see duh/cli/session_builder.py. This minimal
        # example keeps the focus on the Hermes-pattern layer.
    )

    cfg = EngineConfig(model=model, max_turns=max_turns)
    engine = Engine(deps=deps, config=cfg)

    # 4.3 Shared budget — a real implementation would thread this
    # into any sub-agents spawned during the run.
    budget = IterationBudget(remaining=max_turns)

    # 4.4 Stream events. Tool-call arguments are already repaired via
    # ADR-028's middleware in the OpenAI-shape adapter.
    print(f"--- agent: {model} ---")
    print(f"--- prompt: {prompt}\n")

    async for event in engine.run(prompt):
        et = event.get("type", "")
        if et == "text_delta":
            sys.stdout.write(event.get("text", ""))
            sys.stdout.flush()
        elif et == "tool_use":
            print(f"\n  > {event.get('name')}({_short(event.get('input', {}))})")
            if not budget.take():
                print("\n[budget exhausted — stopping]")
                return 0
        elif et == "tool_result":
            output = event.get("output", "")
            if isinstance(output, str) and len(output) > 200:
                output = output[:200] + "…"
            print(f"  < {output}")
        elif et == "error":
            print(f"\n[error] {event.get('error', 'unknown')}")
            return 1
        elif et == "done":
            print(f"\n--- done in {event.get('turns', '?')} turns")
            break

    return 0


def _short(payload: dict[str, Any]) -> str:
    """Compact one-line summary of a tool input dict for the run log."""
    parts = []
    for k, v in list(payload.items())[:2]:
        if isinstance(v, str) and len(v) > 50:
            v = v[:50] + "…"
        parts.append(f"{k}={v!r}")
    return ", ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hermes-style coding agent built on D.U.H.",
    )
    parser.add_argument("--model", required=True,
                        help="Any model D.U.H. supports — e.g. claude-opus-4-7, "
                             "gpt-5.4, deepseek-chat, qwen/qwen3-max, "
                             "mistral/mistral-large-2512.")
    parser.add_argument("-p", "--prompt", required=True,
                        help="Initial prompt for the agent.")
    parser.add_argument("--max-turns", type=int, default=50,
                        help="Hermes-style turn budget (default 50).")
    args = parser.parse_args()

    return asyncio.run(run_hermes_style_agent(
        model=args.model,
        prompt=args.prompt,
        max_turns=args.max_turns,
    ))


if __name__ == "__main__":
    sys.exit(main())
