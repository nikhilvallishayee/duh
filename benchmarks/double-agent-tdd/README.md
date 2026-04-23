# Double-Agent TDD Benchmark

A single-task cross-CLI benchmark. Six agents attempt the same coding
task (add a double-agent TDD flow to D.U.H.) in six separate git
worktrees, and three LLM judges score each result on a shared rubric.

## Agent roster

| ID                  | CLI        | Model                     |
|---------------------|------------|---------------------------|
| `claude-code-opus`  | Claude Code| `claude-opus-4-7`         |
| `duh-opus`          | D.U.H.     | `claude-opus-4-7`         |
| `codex-gpt54`       | Codex CLI  | `gpt-5.4`                 |
| `duh-gpt54`         | D.U.H.     | `gpt-5.4`                 |
| `gemini-cli-3.1`    | Gemini CLI | `gemini-3.1-pro-preview`  |
| `duh-gemini-3.1`    | D.U.H.     | `gemini-3.1-pro-preview`  |

Every CLI is invoked in API-key mode (no OAuth) so per-run token usage
and cost are directly comparable.

## Judge roster

Three LLMs judge every run — each provider's own output is judged by
every provider including itself (cross-judge so biases cancel):

| Judge ID   | Model via D.U.H.           |
|------------|----------------------------|
| `j-opus`   | `claude-opus-4-7`          |
| `j-gpt54`  | `gpt-5.4`                  |
| `j-g31`    | `gemini-3.1-pro-preview`   |

6 targets × 3 judges = **18 judgments**.

## Layout

```
TASK.md          — the prompt (given verbatim to every agent)
JUDGE.md         — rubric (given to every judge)
run.sh           — one run: run.sh <agent-id>
run_all.sh       — all six runs, sequentially
judge.sh         — one judgment: judge.sh <agent-id> <judge-id>
judge_all.sh     — all 18 judgments
aggregate.py     — produce scoreboard.md from score JSONs
worktrees/       — one per agent (created by run.sh)
results/
  <agent-id>/
    diff.patch       — `git diff` vs the baseline commit
    files.txt        — list of files changed
    session.log      — stdout+stderr of the CLI run
    meta.json        — agent, cli, model, start/end, elapsed, exit code
  judgments/
    <judge-id>/
      <agent-id>.json   — one score JSON per judge × target
  scoreboard.md    — aggregated final report
```

## Baseline

Every worktree starts at D.U.H. commit `645d91ad10ad83b5778bcd14f2c53b8e3366497c`
(v0.8.0 release-prep commit). Each agent sees identical source.

## Reproducing

```bash
cd benchmarks/double-agent-tdd
./run_all.sh         # ~1.5–3 hours
./judge_all.sh       # ~1–2 hours
python3 aggregate.py # writes results/scoreboard.md
```

See `../../adrs/ADR-025-double-agent-tdd-benchmark.md` for the design
rationale and interpretation guidance.
