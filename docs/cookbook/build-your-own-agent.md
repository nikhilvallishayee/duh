# Build your own coding agent on D.U.H. — a Hermes-style walkthrough

This cookbook shows how to build a complete coding agent on top of
D.U.H. using the architectural patterns that make
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
worth studying — multi-mode adapters, tool-arg repair, parallel-safe
tool dispatch, threshold-based context compression, sub-agent
delegation.

We choose Hermes Agent as the teacher because:

- **It is genuinely an agent harness.** Several other "agent"
  projects in the open-source ecosystem turn out to be channel
  brokers, plugin shells, or marketing pages. Hermes is the working
  thing, 5,879 commits and 11 releases of working thing.
- **The patterns are clean.** Hermes' design choices (per-API-mode
  adapters, `_PARALLEL_SAFE_TOOLS` allowlist, `_repair_tool_call_arguments`,
  `context_compressor`) each isolate one cross-cutting concern in
  one place. They are easy to lift.
- **The patterns are model-agnostic.** D.U.H. already has native
  adapters for every major provider; Hermes' patterns slot on top
  cleanly without re-doing the wire layer.

By the end of this walkthrough you will have a runnable agent at
`examples/hermes_style/agent.py` that:

1. Drives any model D.U.H. supports (Claude, GPT-5.x, Gemini,
   DeepSeek, Mistral, Qwen, Llama-via-Together, local Ollama).
2. Tolerates malformed tool-call JSON from local fine-tunes.
3. Dispatches read-only tools in parallel.
4. Compresses context before it overflows.
5. Spawns sub-agents under a shared turn budget.

D.U.H. provides every primitive; this cookbook is glue plus a
500-line example file.

---

## 0. What you actually need

```bash
pip install duh-cli
```

You also need an API key for whichever model you want to drive:
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`, `DASHSCOPE_API_KEY`,
`TOGETHER_API_KEY`, or a running Ollama. D.U.H.'s native adapter
for the matching provider handles streaming, caching, and tool
calls in the provider's own shape.

---

## 1. Mental model — what an agent loop actually is

An agent loop is four things:

1. **A model adapter** that streams tokens out of an LLM.
2. **A tool registry** that maps tool names to callables.
3. **A dispatcher** that takes the model's tool calls, runs the
   tools, and feeds results back as the next turn's input.
4. **A context manager** that decides what fits and what doesn't.

D.U.H. ships all four:

- Adapters: `duh.adapters.{anthropic,openai,gemini,deepseek,mistral,qwen,together,ollama}`
- Registry: `duh.tools.registry.get_all_tools`
- Dispatcher: `duh.kernel.engine.Engine` + `duh.kernel.loop`
- Context: `duh.kernel.context_gate` + `duh.kernel.compaction`

A Hermes-style agent is just these primitives with a few opinions
layered on top.

---

## 2. Walkthrough — the four Hermes opinions, ported

### 2.1 Multi-mode adapters (already done in D.U.H.)

Hermes Agent has three modes — `chat_completions`, `codex_responses`,
`anthropic_messages` — selected per-provider. The adapter for each
translates D.U.H.-shape messages into the provider's wire format.

D.U.H. already does this, one adapter per provider, registered in
`duh/providers/registry.py`:

```python
from duh.providers.registry import build_model_backend

backend = build_model_backend("anthropic", "claude-opus-4-7")
# backend.call_model is now an async generator yielding D.U.H. events.
```

You can drop this into your own loop the way Hermes wires
`AIAgent.run_conversation()` to its API-mode adapter.

### 2.2 Tool-arg repair middleware (ADR-028)

Hermes' `_repair_tool_call_arguments` is the single most-effective
quality lift for local / fine-tuned models. They emit JSON that's
*almost* valid — trailing commas, Python `True`/`False`/`None`,
smart quotes, prose wrappers around the body — and strict
`json.loads()` rejects all of it.

D.U.H. ships the same repair pipeline:

```python
from duh.adapters.tool_repair import repair_tool_arguments

# Trailing comma
repair_tool_arguments('{"path": "main.py",}')         # → {"path": "main.py"}

# Python literals
repair_tool_arguments('{"recursive": True}')          # → {"recursive": True}

# Prose wrapper
repair_tool_arguments('Sure: {"path": "x.py"}.')      # → {"path": "x.py"}

# Combined breakage
repair_tool_arguments(
  'Here you go:\n{\u201cpath\u201d: \u201cmain.py\u201d, \u201cverbose\u201d: True,}'
)
# → {"path": "main.py", "verbose": True}
```

It is wired into the OpenAI-shape adapter automatically — every
tool call from every provider runs through the repair pipeline
before reaching the dispatcher. Strict JSON fast-paths first; only
broken inputs hit the repair codepath.

### 2.3 Parallel-safe tool dispatch (port from Hermes)

Hermes' `_PARALLEL_SAFE_TOOLS` allowlist + `_MAX_TOOL_WORKERS=8`
is a 50-line policy that materially speeds up read-heavy turns.
Read-only tools (`Read`, `Glob`, `Grep`) run concurrently; mutating
tools (`Write`, `Edit`, `Bash`) run sequentially.

D.U.H.'s engine currently dispatches sequentially. The cookbook
example wraps the engine's dispatcher with this Hermes-style policy:

```python
import asyncio
from duh.kernel.engine import Engine

PARALLEL_SAFE = {"Read", "Glob", "Grep", "WebFetch", "WebSearch"}

async def parallel_dispatch(engine: Engine, tool_calls: list[dict]) -> list[dict]:
    """Run safe tools concurrently; everything else sequentially."""
    results: list[dict] = []
    safe_batch: list[dict] = []
    for call in tool_calls:
        if call["name"] in PARALLEL_SAFE:
            safe_batch.append(call)
            continue
        # Flush any pending safe-batch first to preserve ordering.
        if safe_batch:
            results.extend(await asyncio.gather(
                *(engine.run_tool(c) for c in safe_batch)
            ))
            safe_batch = []
        # Mutating tool — run sequentially.
        results.append(await engine.run_tool(call))
    if safe_batch:
        results.extend(await asyncio.gather(
            *(engine.run_tool(c) for c in safe_batch)
        ))
    return results
```

The `examples/hermes_style/agent.py` companion file uses this
pattern as a custom dispatcher passed to `Engine` via the
`Deps.run_tool` port. ~40 LOC; measurable wall-clock win on
multi-Read turns.

### 2.4 Threshold-based context compression

Hermes' `context_compressor` triggers at 50% of the context window,
targets 20%, protects the first 3 messages (system / first user
turn) and the last 20 (current working set). D.U.H. has a
compaction module already; the cookbook wraps it with the same
threshold policy:

```python
from duh.kernel.compaction import compact_messages
from duh.kernel.tokens import count_tokens, get_context_limit

async def maybe_compact(messages: list, model: str, deps) -> list:
    """Hermes-style: compress at 50% full, target 20% full,
    protect first 3 + last 20 messages.
    """
    used = count_tokens(messages, model)
    limit = get_context_limit(model)
    if used < 0.50 * limit:
        return messages  # plenty of headroom
    target = int(0.20 * limit)
    return await compact_messages(
        messages,
        target_tokens=target,
        protect_head=3,
        protect_tail=20,
        compactor=deps.compact,
    )
```

Call this once per turn before sending the message list to the
backend. The example agent inserts it as a hook on D.U.H.'s
`PreModelCall` event.

### 2.5 Sub-agent delegation under a shared budget

Hermes' `_active_children` + shared `IterationBudget` pattern means
a parent agent and any sub-agents it spawns share a turn budget —
sub-agents can't blow it out by recursively spawning more. D.U.H.'s
`run_agent` already supports a `max_turns` argument; the cookbook
threads a budget object through:

```python
from duh.agents import run_agent
from dataclasses import dataclass

@dataclass
class IterationBudget:
    """Shared turn budget — parent and children draw from the same pool."""
    remaining: int

    def take(self, n: int = 1) -> bool:
        if self.remaining < n:
            return False
        self.remaining -= n
        return True

# Parent tracks its own turns; sub-agents get the same object.
budget = IterationBudget(remaining=50)

async def delegate(prompt: str, agent_type: str = "researcher"):
    if not budget.take(5):  # reserve a chunk for the child
        raise RuntimeError("turn budget exhausted")
    return await run_agent(
        prompt=prompt,
        agent_type=agent_type,
        max_turns=min(budget.remaining, 10),  # cap each child run
    )
```

---

## 3. The runnable example

`examples/hermes_style/agent.py` ties all the above together into
a single ~500 LOC file:

```bash
# Drive the example agent against any model D.U.H. supports
cd examples/hermes_style
python agent.py --model claude-opus-4-7  -p "Audit auth.py for bugs."
python agent.py --model deepseek-chat    -p "Same question."
python agent.py --model qwen3-max        -p "Same question."
```

Every step in the agent loop is labelled and uncommented — open
the file alongside this doc. The Hermes patterns are clearly
boundaried; you can take any one of them in isolation and apply
it to your own agent without taking the rest.

---

## 4. What this is *not*

- **Not a fork of D.U.H.** — `examples/hermes_style/agent.py`
  is ~500 LOC of glue calling D.U.H. as a library. Same pattern
  for any custom agent: write your own Python, import the
  primitives, run the loop.
- **Not a re-implementation of Hermes.** Their model fine-tunes,
  their `agentskills.io` registry, their JSONL trajectory
  protocol — those are separate concerns. This cookbook borrows
  the *control-flow* patterns, not the surface API.
- **Not the only way.** OpenCode-style (`SessionPrompt.prompt`),
  Codex-style (`ToolOrchestrator` / `ThreadManager`), and
  D.U.H.-default-style each have valid takes. Pick whichever fits
  your application.

---

## 5. What's worth building next on top of this

Once you have a Hermes-style agent running, the obvious extensions:

- **Trajectory export** — Hermes ships a JSONL replay format;
  D.U.H. has session logs but no canonical replay schema. ~150 LOC.
- **Skill auto-creation** — Hermes-agent creates new skills after
  complex tasks. D.U.H. has skills via the `agentskills.io` Open
  Skill format but no auto-creation path. ~300 LOC.
- **Auth-profile cooldown pool** — multiple keys per provider with
  rotation on rate-limit. Borrowable from OpenClaw. ~200 LOC.
- **Hermes-XML adapter for fine-tuned models** — drive
  `Hermes-2-Pro-Mistral-7B`, `Hermes-4.3-36B` etc. via their
  trained-on prompt format. ~200 LOC, slots into ADR-026's
  format-adapter layer.

If you ship any of these, send a PR. The patterns generalise.

---

## See also

- [ADR-026](../../benchmarks/double-agent-tdd/) — tool-format adapters for non-OpenAI-shape models
- [ADR-027](../../benchmarks/double-agent-tdd/) — native adapters per provider
- [ADR-028](../../benchmarks/double-agent-tdd/) — tool-call argument repair (this file's `repair_tool_arguments`)
- [Hermes Agent docs](https://hermes-agent.nousresearch.com/docs/) — the project this is teaching from
- [`research/openclaw-hermes-architecture.md`](../../research/) — the architectural deep-dive that informed this cookbook
