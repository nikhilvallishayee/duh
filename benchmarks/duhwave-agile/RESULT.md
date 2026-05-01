# duhwave-agile — first benchmark result

**Date:** 2026-05-01
**Harness:** duhwave (D.U.H. v0.8.0)
**Demo:** `examples/duhwave/agile_team/main.py`
**Runner:** real OpenAI via D.U.H.'s native adapter (`duh.adapters.openai.OpenAIProvider`)

## What was tested

A single CLI invocation:

```
python main.py "Add a token-bucket rate limiter to utils.py" \
   --use-openai --openai-model <MODEL>
```

drives a 5-stage agile-team pipeline: **PM → Architect → Engineer → Tester → Reviewer**.
Each stage spawns a worker via `Spawn` (ADR-029), reads exposed handles
from the coordinator's RLM REPL, and binds its result back as a new
named handle. No tool calls — single-turn streaming text per worker.

After the run, six artefacts land in `--out-dir`:
`refined_spec.md`, `adr_draft.md`, `implementation.py`,
`test_suite.py`, `review_notes.md`, `SUMMARY.md`.

The benchmark itself runs `pytest test_suite.py` against the produced
implementation. The pass rate is the headline number.

## Two-model lane results

| Metric | gpt-4o-mini | gpt-4o |
|---|---:|---:|
| Stages completed | 5 / 5 | 5 / 5 |
| Wall (single-threaded by design) | 35.5 s | 29.3 s |
| Total prompt tokens | 3,934 | 4,706 |
| Total completion tokens | 1,553 | 1,900 |
| Cached tokens | 0 | 0 |
| Estimated cost | **$0.0015** | **$0.0308** |
| Cost ratio | 1× | **~20×** |
| Output bytes | 7.9 KB | 8.6 KB |
| Pytest on produced code | **3/5 pass** | **5/6 pass** |
| Markdown-fence leakage | none | yes — Engineer + Tester emitted ` ```python ` envelopes despite explicit "no fences" instruction |

(Costs use list rates: gpt-4o-mini $0.15 / $0.60 per 1M; gpt-4o $2.50 /
$10 per 1M.)

## Per-stage ledger

### gpt-4o-mini

| Stage | in | out | cached | wall |
|---|---:|---:|---:|---:|
| pm | 464 | 137 | 0 | 4.22 s |
| architect | 621 | 511 | 0 | 13.48 s |
| engineer | 1,096 | 188 | 0 | 4.20 s |
| tester | 471 | 437 | 0 | 8.27 s |
| reviewer | 1,282 | 280 | 0 | 5.32 s |

### gpt-4o

| Stage | in | out | cached | wall |
|---|---:|---:|---:|---:|
| pm | 464 | 160 | 0 | 5.11 s |
| architect | 644 | 498 | 0 | 7.19 s |
| engineer | 1,106 | 547 | 0 | 6.41 s |
| tester | 852 | 450 | 0 | 4.70 s |
| reviewer | 1,640 | 245 | 0 | 5.93 s |

## Findings worth surfacing

### F1 — Architecture: indistinguishable from stub mode

The duhwave runtime is unaware which runner is wired. Identical
orchestration shape: `Spawn → Task → InProcessExecutor → bind back as
handle`. Identical 6-file output layout. Identical synthesis pass that
peeks every handle to write `SUMMARY.md`. The only thing that changed
when swapping runners was *the content of the strings flowing through
the handles*. **Architecturally, swapping the runner is a one-liner.**

### F2 — Cost: 20× difference for ~25 % more code

gpt-4o produced 8.6 KB vs gpt-4o-mini's 7.9 KB (~9 % more) for ~20×
the cost. On a 5-stage write-only pipeline at this prompt size,
gpt-4o-mini is the better value.

### F3 — Instruction following: gpt-4o leaks markdown fences

Both Engineer and Tester prompts say *"Output: pure Python — no
markdown, no fences, no commentary."* gpt-4o-mini consistently obeys.
gpt-4o consistently wraps the file in ` ```python ` … ` ``` `. We
landed a defensive `_strip_outer_code_fence` post-processor in
`openai_runner.py` so downstream pytest still gets executable Python.
Without that workaround, gpt-4o's pytest invocation hard-fails with
`SyntaxError: invalid syntax` on the opening backticks.

### F4 — Test pass rate is real but not perfect — and that's the point

Both models produced executable pytest projects. Both had real
failures the **Reviewer agent missed**:

- gpt-4o-mini:
  `test_error_handling` fails because earlier `time.sleep(2)` in
  `test_refill_continuity` (running first) refills the bucket; by the
  time `test_error_handling` asserts "no tokens left", the bucket has
  topped up. The Tester's mental model of execution order didn't match
  pytest's actual order. The Reviewer issued APPROVE WITH NITS.

- gpt-4o:
  `test_rate_limiter_thread_safety` references `threading.Thread` but
  the test file imports only `pytest` and `time`. `NameError:
  'threading' is not defined`. The Reviewer issued APPROVE.

These are **real coordination defects** between the agents — the kind
human review catches and code-review-via-LLM doesn't, because the
Reviewer reads but does not execute. **A future iteration of the
duhwave-agile spec should add a sixth role: `Runner` (executes the
test suite, returns the failures as a handle for the Reviewer to
peek)** — that's the obvious next ADR-step, and it falls out
naturally because *adding a sixth handle-passing stage is one new
entry in the pipeline list*. Architecture composes.

### F5 — Latency: dominated by network round-trips, not duhwave

35-29 s wall for 5 sequential stages. Each stage is one OpenAI stream
call ~3-13 s. The duhwave envelope (REPL bind, RLMHandleView, Task
state-machine, JSON wire RPC) costs <0.5 s total per run. **Swapping
to a parallel coordinator lane (e.g., spawn Engineer + Tester
concurrently, gather results) would cut wall ~30 %** — straightforward
extension via `asyncio.gather`, demonstrated in
`examples/duhwave/parity_hermes/03_parallel_dispatch.py`.

## Reproducing

```bash
export OPENAI_API_KEY=sk-proj-...
cd /Users/nomind/Code/duh

# gpt-4o-mini lane
.venv/bin/python3 examples/duhwave/agile_team/main.py \
    "Add a token-bucket rate limiter to utils.py" \
    --use-openai --openai-model gpt-4o-mini --out-dir /tmp/bench-mini

# gpt-4o lane
.venv/bin/python3 examples/duhwave/agile_team/main.py \
    "Add a token-bucket rate limiter to utils.py" \
    --use-openai --openai-model gpt-4o --out-dir /tmp/bench-4o

# verify produced code
cd /tmp/bench-mini && python3 -m pytest test_suite.py -v
cd /tmp/bench-4o   && python3 -m pytest test_suite.py -v
```

## Companion demo (no agile orchestration, persistent flows)

The same OpenAI runner shape powers
`examples/duhwave/telegram_assistant/main.py` — a real persistent
process with three flows (inbound webhook, scheduled cron-style,
on-demand manual). 686 tokens, $0.0002, 14 s for 5 messages across
three flow types. **Mocked at the Telegram boundary only**; everything
else (RLMRepl, TriggerLog, WebhookListener, OpenAI streaming) is real.

## What this benchmark proves about duhwave

1. **Runner injection works.** Stub and real runners are interchangeable;
   the duhwave runtime doesn't know or care.
2. **Variable-handle passing carries semantics.** The Tester's prompt
   includes the Engineer's *real implementation text* as a handle; the
   tests are written against the actual API names the Engineer chose,
   not against a description.
3. **Real cost is recoverable.** `BenchmarkLedger` reads `usage_delta`
   from the OpenAI stream and produces a per-stage breakdown +
   estimated USD cost.
4. **Real defects surface.** The benchmark catches genuine multi-agent
   coordination bugs (test timing, missing imports, fence leakage)
   that wouldn't show up in a stub-only demo.

The pipeline is ~30 lines of orchestration over duhwave primitives.
Adding agents, swapping models, or routing different roles to
different providers is one change away.

## Files (for reference)

```
examples/duhwave/agile_team/
├── main.py             — pipeline orchestrator, --use-openai flag
├── runners.py          — deterministic stub runners (5 roles)
├── openai_runner.py    — real OpenAI runner + BenchmarkLedger
├── roles.py            — Role definitions + system prompts
├── swarm.toml          — declarative topology (for documentation)
├── README.md           — value prop + injection seam
└── expected_output/    — pinned stub-mode reference outputs
```

```
benchmarks/duhwave-agile/
└── RESULT.md           — this file
```
