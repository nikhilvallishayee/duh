# Benchmarks

D.U.H. ships its benchmark artifacts with the repository. This page documents the `double-agent-tdd` benchmark — a six-agent × three-judge comparison of D.U.H. against first-party coding CLIs on a single non-trivial feature task. For design rationale see [ADR-025](../adrs/ADR-025-double-agent-tdd-benchmark.md).

## Design

The benchmark holds the **model constant** between runs. For each of three frontier models (`claude-opus-4-7`, `gpt-5.4`, `gemini-3.1-pro-preview`), the same prompt is executed twice — once through the model vendor's own CLI, once through D.U.H. — producing six runs. Three LLM judges (one per model family) score every run on a shared 7-dimension rubric. This gives 18 judgments per benchmark pass.

Every run starts from a pinned baseline commit (`645d91ad10ad83b5778bcd14f2c53b8e3366497c`, D.U.H. v0.8.0 release-prep) in a fresh git worktree. Every CLI is forced into API-key mode — no OAuth — so token cost and elapsed time are directly comparable. Neither the working tree nor the CLI has network access to any other D.U.H. checkout.

## Task

Agents are asked to add a **double-agent TDD flow** to D.U.H. itself (Driver writes tests, Navigator reviews; six phases: spec → RED → GREEN → refactor-proposal → refactor-application → validation). The task is deliberately chosen to exercise every deliverable category:

1. An ADR must exist before implementation.
2. Real wiring (subcommand, slash command, or public API).
3. Unit tests covering each phase transition, RED-vs-GREEN distinction, refactor application, end-to-end stub flow.
4. README + wiki + help-text updates.

The exact prompt given verbatim to every agent is in [`TASK.md`](../../benchmarks/double-agent-tdd/TASK.md).

## Rubric

Every judge scores every run on the same 7 dimensions, each 0–5, summed to /35:

| # | Dimension | What 5 looks like |
|---|---|---|
| 1 | ADR quality | ADR exists, predates the implementation, names the problem, presents real alternatives, explains the Driver/Navigator contract. |
| 2 | Implementation completeness | Real wiring — a new subcommand / slash command / public API. Not just a skeleton. |
| 3 | Use of existing abstractions | Reuses Engine / PlanMode / agents.py / Swarm where they fit. Does not reinvent infrastructure. |
| 4 | Test coverage of the six-phase contract | Tests assert each phase transition, RED-distinct-from-GREEN, refactor application, end-to-end happy path. |
| 5 | Documentation updates | README section with worked example, wiki page/section, help text coherent with the implementation. |
| 6 | Code quality | Clean names, small functions, no dead code, consistent with existing style. |
| 7 | Protocol adherence | No git commits, no pushes, working tree contains the changes. |

Judges return a strict JSON object (target id, per-dimension scores, total, one-line summary, up to three strengths and weaknesses). The full rubric prompt is in [`JUDGE.md`](../../benchmarks/double-agent-tdd/JUDGE.md).

## Results

Current scoreboard (N=1 per agent, mean across 3 judges):

| Rank | Agent | j-opus | j-gpt54 | j-g31 | Mean /35 | Mean /5 | Elapsed |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1= | `claude-code-opus` | 35 | 30 | 35 | 33.3 | 4.76 | 742s |
| 1= | `codex-gpt54` | 35 | 30 | 35 | 33.3 | 4.76 | 510s |
| 3 | `duh-opus` | 35 | 29 | 35 | 33.0 | 4.71 | 915s |
| 4 | `duh-gpt54` | 33 | 30 | 35 | 32.7 | 4.67 | 230s |
| 5 | `duh-gemini-3.1` | 25 | 23 | 33 | 27.0 | 3.86 | 305s |
| 6 | `gemini-cli-3.1` | 25 | 22 | 28 | 25.0 | 3.57 | 358s |

### Per-dimension (mean across 3 judges)

| Agent | ADR | Impl | Abstractions | Tests | Docs | Code | Protocol |
|---|---:|---:|---:|---:|---:|---:|---:|
| `claude-code-opus` | 5.0 | 4.7 | 4.7 | 4.7 | 5.0 | 4.3 | 5.0 |
| `duh-opus`         | 5.0 | 4.7 | 4.7 | 4.7 | 4.7 | 4.3 | 5.0 |
| `codex-gpt54`      | 5.0 | 4.7 | 4.7 | 4.7 | 5.0 | 4.3 | 5.0 |
| `duh-gpt54`        | 5.0 | 4.7 | 4.3 | 4.7 | 5.0 | 4.0 | 5.0 |
| `gemini-cli-3.1`   | 4.0 | 3.3 | 4.0 | 1.3 | 4.3 | 3.0 | 5.0 |
| `duh-gemini-3.1`   | 4.3 | 3.7 | 3.7 | 3.0 | 4.3 | 3.0 | 5.0 |

### Same-model deltas (D.U.H. vs first-party)

| Model | First-party | D.U.H. | Δ |
|---|---:|---:|---:|
| `claude-opus-4-7` | 33.3 | 33.0 | −0.3 |
| `gpt-5.4` | 33.3 | 32.7 | −0.6 |
| `gemini-3.1-pro-preview` | 25.0 | 27.0 | **+2.0** |

Per ADR-025, differences under ~3 points on /35 are within judge noise. Two of the three same-model deltas fall inside that band; the Gemini delta does not.

## Judge disagreement

Per-target spread (max − min across the three judges):

| Agent | Spread | Notes |
|---|---:|---|
| `claude-code-opus` | 5 | j-gpt54 deducts from code quality + implementation details |
| `duh-opus` | 6 | j-gpt54 cites contract gaps, relies on `engine._config` seam |
| `codex-gpt54` | 5 | j-gpt54 deducts polish and Navigator-edit-permission point |
| `duh-gpt54` | 5 | j-opus notes lack of Engine/PlanMode/Swarm reuse |
| `gemini-cli-3.1` | 6 | Uniformly below ceiling across judges |
| `duh-gemini-3.1` | **10** | j-g31 scores 33; j-opus scores 25 |

The 10-point spread on `duh-gemini-3.1` is the most notable outlier — j-g31 reads its submission as complete and well-documented; j-opus and j-gpt54 call the RED detection brittle (substring check on `"pass"`/`"fail"` rather than real test exit codes) and the tests shallow (mocks on `run_agent` rather than end-to-end against a stub). ADR-025 commits to an N=3 re-run if any target shows >1σ judge disagreement; this target qualifies and is the first candidate.

The general pattern (visible in raw scores): **j-gpt54 is the strictest judge**; **j-g31 is the most lenient**; **j-opus is near the middle**. This is consistent with the cross-judge design's assumption that averaging cancels most of the bias.

## Caveats

- **N=1 per agent.** Single-run variance is real. Mean-across-three-judges reduces scoring noise but not run-to-run variance.
- **Judge self-preference.** Each judge scores a submission from its own model family. The cross-judge average is the mitigation; it is not a cure. See per-judge columns.
- **Single task.** One task surfaces one kind of signal. A competent agent on a single-session, well-specified feature says nothing about long-context, multi-session, or multi-agent behavior.
- **Prompt-caching left on defaults.** Anthropic `cache_control`, OpenAI automatic cache (≥1024 tokens), and Gemini `CachedContent` are left at each CLI's default setting so timings reflect realistic operation, not a synthetic no-cache condition.
- **Cost is elapsed time + default caching.** Per-run USD is captured where the CLI emits it, but absolute USD matters less than the ratio between a CLI and its D.U.H. counterpart on the same model.

## Reproduce

```bash
cd benchmarks/double-agent-tdd

# 6 runs, ~1.5–3 hours total
./run_all.sh

# 18 judgments, ~1–2 hours
./judge_all.sh

# aggregate to scoreboard.md
python3 aggregate.py

# single agent
./run.sh duh-opus

# single judgment
./judge.sh duh-opus j-opus
```

Every artifact persists under `results/`:

```
results/
  <agent-id>/
    diff.patch      # git diff vs baseline
    files.txt       # list of changed files
    session.log     # stdout+stderr of the CLI run
    meta.json       # agent, cli, model, elapsed, exit code, sizes
  judgments/
    <judge-id>/
      <agent-id>.json   # one score JSON per judge × target
  scoreboard.md
```

## Open-model extension (in progress)

Three additional D.U.H. runs are underway on models that have **no first-party coding CLI**: `openai/gpt-oss-120b`, `qwen3-32b`, and `llama-4-scout`, all served via Groq. These runs will be judged by the same three-judge panel and added to the scoreboard when they complete. This extension is what makes a universal harness uniquely valuable for benchmarking — first-party CLIs can only bench their own provider's models.

## Related benchmarks

- [`baseline-v0`](../../benchmarks/baseline-v0.md) — atomic-question benchmark (precursor, Claude Code only).
- `benchmark-2-spec.md` (in `writeup/`) — draft spec for a harder multi-file, multi-agent benchmark (distributed rate limiter with adversarial tests).
- `benchmark-3-spec.md` (in `writeup/`) — draft spec for a documentation-generation benchmark over a complex codebase.

## See also

- [ADR-025 — Double-Agent TDD Cross-CLI Benchmark](../adrs/ADR-025-double-agent-tdd-benchmark.md) — methodology and rationale.
- [ADR-023 — Universal harness architecture](../adrs/ADR-023-universal-harness-architecture.md).
- [ADR-024 — Universal harness design principles](../adrs/ADR-024-universal-harness-design-principles.md).
