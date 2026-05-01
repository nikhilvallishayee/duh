#!/usr/bin/env python3
"""01 — Multi-mode native adapters (Hermes pattern → ADR-027 realisation).

Hermes Agent ships per-API-mode adapters (``chat_completions``,
``codex_responses``, ``anthropic_messages``) selected per-provider.
duhwave's ADR-027 takes the same opinion further: every supported
provider gets its own native adapter, registered in
:data:`duh.providers.registry.PROVIDER_TIER_MODELS`, with a coherent
``small`` / ``medium`` / ``large`` tier map per provider.

This script is read-only — no API calls. It walks the registry and
prints the tier map for every native provider, then demonstrates
:func:`infer_provider_from_model` resolving a namespaced model id
(``deepseek/deepseek-v4-pro``) back to the registered provider.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_hermes/01_multimode_adapters.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the demo runnable from anywhere.
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.providers.registry import (  # noqa: E402
    PROVIDER_TIER_MODELS,
    infer_provider_from_model,
)


# ---- pretty output -------------------------------------------------------


def section(title: str) -> None:
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def step(msg: str) -> None:
    print(f"  → {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


# ---- the demo ------------------------------------------------------------


def main() -> int:
    section("01 — Multi-mode native adapters (Hermes → ADR-027)")
    print()
    print("  Hermes selects one of three API modes per provider.")
    print("  duhwave goes further: every provider has a native adapter")
    print("  with a coherent small/medium/large tier map. No proxies,")
    print("  no LiteLLM, no OpenRouter.")

    section("Registered native providers (PROVIDER_TIER_MODELS)")
    providers = list(PROVIDER_TIER_MODELS.keys())
    print(f"  count: {len(providers)}")
    print(f"  list:  {', '.join(providers)}")

    section("Per-provider tier maps")
    print()
    print(f"  {'provider':<12} {'small':<46} {'medium':<46} {'large':<46}")
    print(f"  {'-' * 12} {'-' * 46} {'-' * 46} {'-' * 46}")
    for provider, tiers in PROVIDER_TIER_MODELS.items():
        small = tiers.get("small", "—")
        medium = tiers.get("medium", "—")
        large = tiers.get("large", "—")
        print(f"  {provider:<12} {small:<46} {medium:<46} {large:<46}")

    section("infer_provider_from_model — namespaced model id resolution")
    cases: list[tuple[str, str | None]] = [
        ("deepseek/deepseek-v4-pro", "deepseek"),
        ("claude-opus-4-7",          "anthropic"),
        ("gemini-2.5-pro",           "gemini"),
        ("mistral/mistral-large-2512", "mistral"),
        ("qwen/qwen3-max",           "qwen"),
        ("together/meta-llama/Llama-4-Scout-17B-16E-Instruct", "together"),
        ("gpt-4o",                   "openai"),
    ]
    print()
    print(f"  {'model id':<60} {'expected':<12} {'inferred':<12}")
    print(f"  {'-' * 60} {'-' * 12} {'-' * 12}")
    failures: list[str] = []
    for model, expected in cases:
        actual = infer_provider_from_model(model)
        marker = "✓" if actual == expected else "✗"
        print(f"  {model:<60} {expected!s:<12} {str(actual):<12} {marker}")
        if actual != expected:
            failures.append(f"{model}: expected {expected!r}, got {actual!r}")

    section("Summary")
    if failures:
        for f in failures:
            fail(f)
        return 1
    ok(f"multi-mode adapters: {len(providers)} native providers ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
