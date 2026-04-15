# ADR-071: CI/CD Integration and Headless Mode — Competitive Gaps

**Status:** Proposed
**Date:** 2026-04-15

## Context

AI coding agents are moving beyond interactive developer tools into CI/CD pipelines: automated PR reviews, code generation in GitHub Actions, batch linting, and cost-governed agent runs in headless environments. Every major agent CLI now ships (or is shipping) first-class CI/CD support. D.U.H. has the foundations — `-p` flag, `--output-format json/stream-json`, SDK runner — but lacks the CI-specific machinery that makes adoption in automated pipelines practical.

This ADR surveys how the field handles CI/CD and headless usage, maps D.U.H.'s current state, and identifies the gaps.

## Competitive Landscape

### Claude Code

**Strongest CI story in the field.** The SDK mode (`--output-format stream-json --input-format stream-json`) provides a full bidirectional NDJSON protocol for programmatic consumers. Key CI/CD features:

- **SDK runner**: External processes launch the CLI as a subprocess and communicate via structured NDJSON on stdin/stdout. This is the backbone of the Claude Agent SDK ecosystem.
- **Headless operation**: The `-p` flag runs a single prompt without a REPL. Combined with `--output-format stream-json`, this gives CI pipelines full event-level visibility.
- **GitHub Actions integration**: Official `claude-code-action` runs as a GitHub Action, reviewing PRs, responding to `@claude` mentions in comments, and creating commits directly.
- **PR review automation**: The action posts inline review comments, suggests fixes, and can be triggered on `pull_request` and `issue_comment` events.
- **Permission bypass**: `--dangerously-skip-permissions` enables full-auto mode for CI where no human is present.
- **Max turns**: `--max-turns` caps agentic loops to prevent runaway CI jobs.
- **Model routing**: CI environments can specify `--model` to use cheaper models for automated tasks.
- **Exit codes**: Returns 0 on success, non-zero on error.

**Gaps relative to D.U.H.'s needs**: The action is tightly coupled to Anthropic's API. Not reusable with other providers. No built-in cost ceiling per CI run. No structured artifact collection.

### GitHub Copilot CLI

**Natively integrated with GitHub's ecosystem.** Copilot operates inside GitHub Actions as a first-party citizen:

- **GitHub Actions native**: Copilot agent mode can be invoked within Actions workflows. It has direct access to the repository context, issue data, and PR metadata.
- **PR suggestions**: Posts review comments and code suggestions directly on PRs via the Checks API.
- **Extensions framework**: Third-party extensions can add domain-specific CI capabilities.
- **Auto-fix on CI failure**: Can be configured to attempt fixes when CI checks fail and push corrective commits.

**Gaps**: Locked to GitHub and OpenAI/GitHub models. No provider choice. Closed-source. No cost controls exposed to users.

### Codex CLI

**Designed for scripting from day one.** OpenAI's Codex CLI has the cleanest headless story among open-source agents:

- **Headless mode**: Runs without a terminal UI. Detects non-interactive environments automatically.
- **Pipe-friendly output**: Structured output that can be piped to `jq` or consumed by other tools.
- **CI integration**: Documented patterns for running Codex in GitHub Actions with `--quiet` and `--full-auto` flags.
- **Sandboxed execution**: Network-disabled sandbox by default, which is ideal for CI where you want deterministic, safe execution.
- **Approval modes**: `suggest`, `auto-edit`, `full-auto` — the last maps directly to CI use.
- **Writable working directory**: Configurable sandbox directory, important for CI where the workspace is a checked-out repo.

**Gaps**: OpenAI-only (though supports any OpenAI-compatible endpoint). No built-in PR review mode. No structured artifact output. No cost limits.

### Gemini CLI

**Programmatic API focus.** Google's Gemini CLI emphasizes API-first design:

- **Programmatic invocation**: Can be called from scripts with structured JSON output.
- **Non-interactive mode**: `-p` flag equivalent for single-shot prompts.
- **Directory-aware sessions**: Automatically scopes context to the working directory, useful for CI where each job checks out a specific repo.
- **Theme/output control**: `--non-interactive` suppresses TUI elements for clean pipeline output.

**Gaps**: Google models only. No GitHub Actions action. No PR review integration. Limited exit code semantics.

### OpenCode

**CLI-first, scriptable by nature.** The Go-based OpenCode is built for terminal power users:

- **CLI-first design**: Every operation is a CLI command. No web UI, no electron app.
- **Scriptable**: Commands return structured output suitable for scripting.
- **Provider-agnostic**: Works with any provider (Anthropic, OpenAI, Google, Ollama, etc.), making it viable across different CI environments.
- **Dual-agent architecture**: Build agent (writes code) vs Plan agent (strategizes). CI could invoke just the build agent for deterministic changes.
- **LSP integration**: Language server protocol support means CI gets the same code intelligence as the desktop.

**Gaps**: No GitHub Actions action. No PR review mode. No batch processing. No cost controls. No structured exit codes beyond 0/1.

## Comparative Matrix

| Capability | Claude Code | Copilot CLI | Codex CLI | Gemini CLI | OpenCode | **D.U.H. (current)** |
|---|---|---|---|---|---|---|
| Single-prompt mode | `-p` | N/A (action) | `--quiet` | `-p` equiv | CLI args | **`-p` flag** |
| Structured output | stream-json | Proprietary | Pipe-friendly | JSON | Structured | **json, stream-json** |
| SDK protocol | NDJSON bidir | N/A | N/A | N/A | N/A | **NDJSON bidir** |
| GitHub Action | Official | Native | Community | None | None | **None** |
| PR review mode | Action-based | Native | None | None | None | **None** |
| Batch prompt mode | None | None | None | None | None | **None** |
| Semantic exit codes | 0/1 | N/A | 0/1 | 0/1 | 0/1 | **0/1 only** |
| Cost limit | None | None | None | None | None | **`--max-cost`** |
| Timeout config | None | Action timeout | None | None | None | **None** |
| Artifact collection | None | None | None | None | None | **None** |
| Permission bypass | Flag | Implicit | `full-auto` | Implicit | Auto | **Flag + modes** |
| Max turns | `--max-turns` | N/A | N/A | N/A | N/A | **`--max-turns`** |

## D.U.H. Current State

### What works today

1. **`-p` flag**: Single-prompt headless mode. Runs one prompt, streams output, exits. This is the basic building block for CI.

2. **`--output-format json`**: Collects all events and emits a JSON array on completion. Machine-parseable.

3. **`--output-format stream-json`**: NDJSON streaming, one event per line. Real-time machine consumption.

4. **SDK runner** (`--input-format stream-json --output-format stream-json`): Full bidirectional NDJSON protocol. External SDK consumers can drive D.U.H. as a subprocess.

5. **`--max-cost`**: Budget cap in USD. Stops the agent when the estimated cost exceeds the limit. This is a feature no competitor has.

6. **`--max-turns`**: Caps agentic loop iterations. Prevents runaway jobs.

7. **`--dangerously-skip-permissions`** and **`--approval-mode full-auto`**: Auto-approve all tool calls. Required for headless operation where no human can confirm.

8. **`--model`**: CI can specify cheaper models for automated tasks.

9. **Structured JSON logging** (`--log-json`): Writes structured events to a JSONL file for post-hoc analysis.

10. **Exit codes**: Returns 0 on success, 1 on error.

### Summary

D.U.H. has a solid headless foundation. The `-p` + `--output-format` + `--max-cost` + `--max-turns` combination already covers the "run an agent in a script" use case better than most competitors. The SDK protocol gives parity with Claude Code's programmatic interface. But the CI-specific integrations — the GitHub Action, PR review, batch mode, semantic exit codes, timeouts, artifact tracking — are all missing.

## Identified Gaps

### Gap 1: GitHub Actions Action (`duh-action`)

**Priority: P0**

No competitor except Claude Code and Copilot has an official GitHub Action. This is the single highest-leverage CI integration because GitHub Actions is the dominant CI platform for open-source.

**What it needs:**
- A `duh-action` GitHub Action (composite or Docker-based)
- Inputs: `prompt`, `model`, `provider`, `max-cost`, `max-turns`, `output-format`, `approval-mode`
- Secrets: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. (provider-agnostic)
- Outputs: `result` (text), `exit-code`, `cost`, `artifacts` (JSON list of created/modified files)
- Trigger patterns: `pull_request`, `issue_comment`, `push`, `workflow_dispatch`
- Provider-agnostic: works with any supported provider, unlike Claude Code's Anthropic-only action

**D.U.H. advantage**: The only open-source, provider-agnostic GitHub Action for AI coding agents.

### Gap 2: PR Review Mode (`duh review --pr <number>`)

**Priority: P0**

The killer CI use case. Automatically review pull requests and post comments.

**What it needs:**
- `duh review --pr 123` subcommand (or `duh review --pr-url <url>`)
- Fetches PR diff, file changes, and existing comments via `gh` CLI or GitHub API
- Runs the agent with a review-focused system prompt
- Posts inline comments on specific lines via the GitHub API
- Supports `--severity` filter (e.g., only post comments for bugs, not style)
- Returns exit code 0 (approved), 1 (changes requested), 2 (needs human review)
- Works with any provider — review with Claude, GPT, Gemini, or local models

**Implementation sketch:**
```
duh review --pr 123 \
  --model claude-sonnet-4-20250514 \
  --max-cost 0.50 \
  --severity error,warning
```

### Gap 3: Batch Mode (`duh batch <file>`)

**Priority: P1**

Process multiple prompts from a file or stdin. Useful for bulk operations in CI: "review these 10 files", "generate tests for these modules", "check these configs".

**What it needs:**
- `duh batch prompts.jsonl` — reads JSONL, one prompt per line
- `echo '...' | duh batch -` — reads from stdin
- Each prompt runs as an independent agent session (no shared context)
- Results emitted as JSONL, one result per prompt
- Parallel execution with `--concurrency N` (default: 1)
- Per-prompt cost tracking in output
- Aggregate exit code: 0 if all succeed, 1 if any fail, 2 if any need human review

**JSONL input format:**
```json
{"prompt": "Review src/auth.py for security issues", "model": "claude-sonnet-4-20250514"}
{"prompt": "Generate tests for src/parser.py", "max_turns": 10}
```

**JSONL output format:**
```json
{"index": 0, "status": "success", "exit_code": 0, "cost_usd": 0.012, "result": "..."}
{"index": 1, "status": "error", "exit_code": 1, "cost_usd": 0.003, "error": "..."}
```

### Gap 4: Semantic Exit Codes for CI

**Priority: P1**

Currently D.U.H. returns 0 (success) or 1 (error). CI pipelines need richer semantics.

**Proposed exit codes:**

| Code | Meaning | CI Action |
|------|---------|-----------|
| 0 | Success — task completed | Continue pipeline |
| 1 | Error — task failed (API error, tool failure, etc.) | Fail pipeline |
| 2 | Needs human — agent deferred to human judgment | Flag for review |
| 3 | Budget exceeded — `--max-cost` or `--max-turns` hit | Fail with warning |
| 4 | Timeout — `--timeout` exceeded | Fail with warning |
| 10 | Review approved — PR has no issues (review mode) | Merge gate: pass |
| 11 | Review changes requested — PR has issues (review mode) | Merge gate: fail |
| 12 | Review inconclusive — agent unsure (review mode) | Merge gate: manual |

**Implementation**: The engine already tracks `had_error`. Extend to track `hit_budget`, `hit_timeout`, `needs_human` (via a new `AskUser` tool response), and review outcomes.

### Gap 5: Machine-Readable Tool Results

**Priority: P1**

When `--output-format json` or `stream-json` is used, tool results should include structured metadata, not just raw text output. CI consumers need to know which files were read, which commands were run, what was modified.

**Current**: Tool results are `{"type": "tool_result", "output": "<raw text>", "is_error": false}`

**Proposed**: Enrich tool results with structured fields:
```json
{
  "type": "tool_result",
  "tool_name": "Write",
  "output": "Wrote 42 lines to src/auth.py",
  "is_error": false,
  "structured": {
    "action": "write",
    "path": "src/auth.py",
    "lines_written": 42,
    "bytes": 1847
  }
}
```

This enables downstream CI steps to extract "which files did the agent touch" without parsing natural-language output.

### Gap 6: Timeout Configuration

**Priority: P1**

CI environments have hard time limits. GitHub Actions jobs timeout after 6 hours by default, but teams set tighter limits (10-30 minutes for agent tasks). D.U.H. needs its own timeout to exit gracefully before the CI runner kills it.

**What it needs:**
- `--timeout <seconds>` flag (also `DUH_TIMEOUT` env var)
- Graceful shutdown: when timeout approaches, the engine stops issuing new turns and emits a summary of what was accomplished
- Exit code 4 (timeout)
- The `--max-turns` flag already caps iterations, but a wall-clock timeout is different — a single turn with extended thinking could take minutes

### Gap 7: Cost Limit Enforcement for CI Budgets

**Priority: P2**

D.U.H. already has `--max-cost` (ADR-022), which is ahead of every competitor. But CI needs additional cost controls:

- **Per-run cost reporting in output**: When using `--output-format json`, the final event should include `{"type": "summary", "cost_usd": 0.087, "input_tokens": 12340, "output_tokens": 5670}`.
- **Cost in exit message**: Even in text mode, print cost to stderr on completion: `[cost: $0.087 | 12,340 in / 5,670 out]`.
- **Daily/weekly budget** (future): A config file or env var sets `DUH_DAILY_BUDGET=5.00`. The CLI checks a local ledger and refuses to start if the budget is exhausted. This prevents CI misconfigurations from burning through API credits.
- **Cost alerts**: `--cost-warning-threshold 0.80` emits a warning event when 80% of `--max-cost` is consumed (currently this is the default behavior but not configurable).

### Gap 8: Artifact Collection

**Priority: P2**

After an agent run, CI pipelines need to know what changed. "Which files did the agent create or modify?" Currently, the only way to find out is to run `git diff` after the agent exits.

**What it needs:**
- Track all file operations (Write, Edit, Bash commands that modify files) during the run
- On completion, emit an `artifacts` summary:
  ```json
  {
    "type": "artifacts",
    "created": ["src/auth_test.py", "src/validators.py"],
    "modified": ["src/auth.py", "pyproject.toml"],
    "deleted": []
  }
  ```
- `--artifacts-dir <path>`: Copy all created/modified files to a directory for CI artifact upload
- In GitHub Actions, this integrates with `actions/upload-artifact`

**Implementation**: The `NativeExecutor` already dispatches tool calls. Instrument `Write`, `Edit`, `MultiEdit`, and `Bash` tools to report file mutations back to the engine.

### Gap 9: Non-Interactive Tool Behavior

**Priority: P2**

Several tools behave differently in CI vs interactive mode, and D.U.H. needs to handle this explicitly:

- **AskUser tool**: In headless mode, there is no human to ask. The tool should either: (a) return a "no human available" response and let the agent decide, or (b) trigger exit code 2 (needs human).
- **Confirmation prompts**: `--dangerously-skip-permissions` already handles this, but `--approval-mode full-auto` is the preferred CI path.
- **Browser/GUI tools**: Tools that require a display (e.g., Playwright in headed mode) should detect headless environments and either switch to headless browser mode or skip gracefully.

## Implementation Roadmap

### Wave 1 — CI Foundations (Gaps 4, 6, 7)

These are small changes to the existing runner that make D.U.H. CI-ready:

- Semantic exit codes in `runner.py` (extend the `had_error` logic)
- `--timeout` flag in `parser.py` + wall-clock timer in the engine loop
- Cost summary in JSON output and stderr
- Estimated effort: 2-3 days

### Wave 2 — PR Review (Gaps 2, 5, 8)

The flagship CI feature:

- `duh review` subcommand with GitHub API integration
- Structured tool result metadata
- Artifact collection in the executor
- Estimated effort: 5-7 days

### Wave 3 — GitHub Action (Gap 1)

Packaging and distribution:

- `duh-action` composite action (uses `pip install duh` + runs `duh` with inputs)
- Action metadata (`action.yml`), documentation, marketplace listing
- Example workflows for PR review, code generation, batch operations
- Estimated effort: 3-4 days

### Wave 4 — Batch Mode and Advanced CI (Gaps 3, 9)

Higher-level orchestration:

- `duh batch` subcommand with JSONL input/output
- Parallel execution with concurrency control
- Non-interactive tool behavior policies
- Daily/weekly budget enforcement
- Estimated effort: 4-5 days

## Consequences

1. **D.U.H. becomes the only provider-agnostic CI agent**. Claude Code's action only works with Anthropic. Copilot only works with GitHub. D.U.H. works with any provider in any CI system.

2. **`--max-cost` is a unique competitive advantage**. No other agent CLI has budget enforcement. For CI, this is critical — a misconfigured workflow should not burn $500 in API credits overnight.

3. **Semantic exit codes enable merge gates**. Teams can use `duh review` as a required check: exit 10 = pass, exit 11 = block merge, exit 12 = request human review.

4. **Batch mode enables bulk operations**. Generate tests for 20 modules, review 15 PRs, audit 30 config files — all in one CI job.

5. **Artifact collection closes the feedback loop**. CI can upload agent-created files, diff them, and include them in PR comments without post-hoc git gymnastics.

## References

- ADR-008: CLI Design (existing `-p`, `--output-format`, exit codes)
- ADR-021: NDJSON SDK Protocol (stream-json bidirectional protocol)
- ADR-022: Token Counting, Cost Control & Auto-Compaction (`--max-cost`)
- ADR-038: Tiered Approval (approval modes for CI)
- ADR-062: Output Styles (output formatting)
- ADR-065: Competitive Positioning (field survey)
