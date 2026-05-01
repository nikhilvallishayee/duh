"""Real-OpenAI :data:`WorkerRunner` — drops in for the stub router.

Replaces the deterministic stub runners with calls to a real OpenAI
model via D.U.H.'s native adapter (:class:`duh.adapters.openai.OpenAIProvider`).
The architecture is otherwise identical:

    coordinator's REPL            (real RLMRepl subprocess)
                ↓ Spawn(expose=[handles…], bind_as=...)
    InProcessExecutor + Task       (real lifecycle, real registry)
                ↓
    THIS runner                    (OpenAI streaming text completion)
                ↓
    response text bound back as a new handle in the coordinator's REPL

Usage from ``main.py``::

    if args.use_openai:
        from examples.duhwave.agile_team.openai_runner import build_openai_router
        runner = build_openai_router(active_role, model=args.openai_model)
    else:
        runner = build_runner_router(active_role)

The function signature matches :func:`runners.build_runner_router` so
the dispatch slot stays the same.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from duh.duhwave.coordinator.runner_protocol import WorkerRunner
from duh.duhwave.coordinator.view import RLMHandleView
from duh.duhwave.task.registry import Task

from examples.duhwave.agile_team.roles import BUILTIN_AGILE_ROLES


# ---------------------------------------------------------------------------
# Per-stage user-prompt templates
# ---------------------------------------------------------------------------
#
# Each role's runner reads the exposed handles via :class:`RLMHandleView`,
# concatenates them under labelled headers, and asks the model to produce
# the role's deliverable. The role's own system prompt comes from
# :data:`BUILTIN_AGILE_ROLES`.

_STAGE_INSTRUCTIONS: dict[str, str] = {
    "pm": (
        "You are the Product Manager. Read the user request below and the "
        "codebase context. Produce a refined spec with explicit acceptance "
        "criteria as a markdown bullet list. End with a one-line summary. "
        "Length: ~10 lines. Markdown only — no code blocks."
    ),
    "architect": (
        "You are the Architect. Read the refined spec, the codebase, and "
        "produce an ADR-shaped design document. Sections: Status, Context, "
        "Decision (with API + data model), Tradeoffs, Deferred. Length: "
        "~30 lines. Markdown only — no executable code beyond a small API "
        "sketch in a fenced block."
    ),
    "engineer": (
        "You are the Engineer. Read the refined spec and the ADR. Produce a "
        "single Python file implementing the design. Stdlib only. Include "
        "type hints and one short module docstring. Output: pure Python — "
        "no markdown, no fences, no commentary."
    ),
    "tester": (
        "You are the Tester. Read the refined spec and the implementation. "
        "Produce a single pytest file with at least three tests covering: "
        "happy path, refill / continuity, and error handling. The "
        "implementation lives in a sibling file imported as "
        "``from implementation import …``. Output: pure Python — no "
        "markdown, no fences, no commentary."
    ),
    "reviewer": (
        "You are the Reviewer. Read the ADR, the implementation, and the "
        "test suite. Produce a markdown review with sections Summary, "
        "Concerns, Verdict (one of APPROVE / APPROVE WITH NITS / REQUEST "
        "CHANGES). Length: ~10 lines. Markdown only."
    ),
}


# ---------------------------------------------------------------------------
# Cost / metrics ledger — populated as each role runs
# ---------------------------------------------------------------------------


@dataclass
class _StageMetric:
    role: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    duration_s: float = 0.0
    bytes_out: int = 0


@dataclass
class BenchmarkLedger:
    """Aggregates per-stage metrics across the run.

    Populated by the OpenAI runner; printed at the end of ``main.py``.
    """

    model: str
    stages: list[_StageMetric] = field(default_factory=list)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(s.prompt_tokens for s in self.stages)

    @property
    def total_completion_tokens(self) -> int:
        return sum(s.completion_tokens for s in self.stages)

    @property
    def total_cached_tokens(self) -> int:
        return sum(s.cached_tokens for s in self.stages)

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.stages)

    def estimated_cost_usd(self) -> float:
        """Rough USD cost estimate using current published rates.

        Rates per 1M tokens (April 2026 list prices, OpenAI):
          gpt-5-nano:    $0.05 in / $0.40 out
          gpt-5-mini:    $0.25 in / $2.00 out
          gpt-4o-mini:   $0.15 in / $0.60 out
          gpt-4o:        $2.50 in / $10.00 out
          gpt-5.4:       $5.00 in / $15.00 out

        Fallback: gpt-4o rates.
        """
        rates = {
            "gpt-5-nano":    (0.05, 0.40),
            "gpt-5-mini":    (0.25, 2.00),
            "gpt-4o-mini":   (0.15, 0.60),
            "gpt-4o":        (2.50, 10.00),
            "gpt-5.4":       (5.00, 15.00),
        }
        in_per_1m, out_per_1m = rates.get(self.model, (2.50, 10.00))
        # Cached tokens billed at typical 50% discount (OpenAI prompt-caching).
        cached_in = self.total_cached_tokens * (in_per_1m * 0.5) / 1_000_000
        billable_in = (self.total_prompt_tokens - self.total_cached_tokens)
        billable_in = max(0, billable_in) * in_per_1m / 1_000_000
        out = self.total_completion_tokens * out_per_1m / 1_000_000
        return cached_in + billable_in + out


# ---------------------------------------------------------------------------
# Runner factory
# ---------------------------------------------------------------------------


def build_openai_router(
    active_role: dict[str, str],
    *,
    model: str = "gpt-4o-mini",
    ledger: BenchmarkLedger | None = None,
    max_output_tokens: int = 1500,
) -> WorkerRunner:
    """Build a :data:`WorkerRunner` that calls a real OpenAI model.

    Mirrors the contract of :func:`runners.build_runner_router`: the host
    sets the next stage's role into ``active_role["name"]`` immediately
    before calling :meth:`Spawn.call`; the router dispatches to the
    role-appropriate prompt template.

    The ``ledger`` arg, when provided, accumulates per-stage metrics
    that ``main.py`` prints at the end of the run.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY not set; cannot use --use-openai. "
            "Set it via `export OPENAI_API_KEY=sk-...` or "
            "`duh /connect openai` and re-run."
        )

    # Lazy import — keeps stub-runner path importable without openai.
    from duh.adapters.openai import OpenAIProvider

    provider = OpenAIProvider(model=model)

    async def router(task: Task, view: RLMHandleView) -> str:
        role = active_role.get("name", "")
        if role not in _STAGE_INSTRUCTIONS:
            raise ValueError(f"no OpenAI runner registered for role {role!r}")
        # Re-stamp the task metadata so the per-role invariant holds.
        task.metadata["role"] = role

        # 1. Read every exposed handle into the user prompt.
        exposed_blocks: list[str] = []
        for handle_name in view.list_exposed():
            try:
                content = await view.peek(handle_name, start=0, end=8000)
            except Exception:
                content = "<peek failed>"
            exposed_blocks.append(f"## handle: {handle_name}\n\n{content}\n")

        user_prompt = (
            _STAGE_INSTRUCTIONS[role]
            + "\n\n"
            + "\n".join(exposed_blocks)
        )

        role_def = BUILTIN_AGILE_ROLES.get(role)
        if role_def is None:
            raise ValueError(f"role spec missing: {role!r}")

        messages = [{"role": "user", "content": user_prompt}]

        # 2. Stream the response; accumulate text + token usage.
        t0 = time.monotonic()
        text_chunks: list[str] = []
        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        async for event in provider.stream(
            messages=messages,
            system_prompt=role_def.system_prompt,
            model=model,
            max_tokens=max_output_tokens,
        ):
            et = event.get("type")
            if et == "text_delta":
                text_chunks.append(event.get("text", ""))
            elif et in ("usage", "usage_delta"):
                prompt_tokens = event.get("input_tokens", prompt_tokens)
                completion_tokens = event.get("output_tokens", completion_tokens)
                cached_tokens = event.get(
                    "cached_tokens",
                    event.get("cached_input_tokens", cached_tokens),
                )
            elif et == "error":
                raise RuntimeError(f"openai stream error: {event.get('error')}")
            elif et == "assistant":
                # Final assistant message — extract text blocks. D.U.H.'s
                # OpenAI adapter emits this once at end-of-turn; if no
                # text_delta events fired (non-streaming path or auth
                # failure that returned an error message as content), we
                # still want the body.
                msg = event.get("message")
                if msg is not None and not text_chunks:
                    blocks = getattr(msg, "content", []) or []
                    for blk in blocks:
                        if isinstance(blk, dict) and blk.get("type") == "text":
                            text_chunks.append(blk.get("text", ""))
        text = "".join(text_chunks)
        # Surface auth / API errors loudly instead of producing empty docs.
        if not text.strip():
            raise RuntimeError(
                f"openai returned empty completion for role={role!r}; "
                f"check OPENAI_API_KEY validity and model availability."
            )
        if "Incorrect API key" in text or text.startswith("Error code:"):
            raise RuntimeError(f"openai api error: {text[:200]}")
        # Roles that asked for "no fences" sometimes get them anyway
        # (gpt-4o is a known offender here, vs. gpt-4o-mini which obeys).
        # Strip a single enveloping ``` … ``` block if present.
        if role in ("engineer", "tester"):
            text = _strip_outer_code_fence(text)
        elapsed = time.monotonic() - t0

        if ledger is not None:
            ledger.stages.append(
                _StageMetric(
                    role=role,
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cached_tokens=cached_tokens,
                    duration_s=elapsed,
                    bytes_out=len(text.encode("utf-8")),
                )
            )

        return text

    return router


def _strip_outer_code_fence(text: str) -> str:
    """If ``text`` is wrapped in a single ```…``` block, return the inside.

    Robust to leading language hint (```python, ```py) and trailing
    whitespace. Leaves multi-block prose alone.
    """
    s = text.strip()
    if not s.startswith("```"):
        return text
    # Find the end of the first line — that's the opening fence.
    nl = s.find("\n")
    if nl == -1:
        return text
    closing = s.rfind("```")
    if closing <= nl:
        return text
    inner = s[nl + 1 : closing]
    # Only accept the strip if there's no other ``` inside (avoid eating
    # multi-block content).
    if "```" in inner:
        return text
    return inner.rstrip() + "\n"


__all__ = ["build_openai_router", "BenchmarkLedger"]
