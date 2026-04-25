# Double-Agent TDD Benchmark — Scoreboard

Mean across 3 judges, /35. Per-judge columns show raw sums.

| Agent | j-opus | j-gpt54 | j-g31 | Mean /35 | Mean /5 | Elapsed | Diff bytes | Exit |
|-------|--------|---------|-------|---------:|--------:|--------:|-----------:|-----:|
| `claude-code-opus` | 35 | 30 | 35 | 33.3 | 4.76 | 742s | 87144 | 0 |
| `codex-gpt54` | 35 | 30 | 35 | 33.3 | 4.76 | 510s | 44978 | 0 |
| `duh-opus` | 35 | 29 | 35 | 33.0 | 4.71 | 915s | 88764 | 0 |
| `duh-gpt54` | 33 | 30 | 35 | 32.7 | 4.67 | 230s | 36421 | 0 |
| `duh-gemini-3.1` | 25 | 23 | 33 | 27.0 | 3.86 | 305s | 22327 | 0 |
| `gemini-cli-3.1` | 25 | 22 | 28 | 25.0 | 3.57 | 358s | 17331 | 0 |
| `duh-qwen3-max` | 25 | 18 | 29 | 24.0 | 3.43 | 862s | 55584 | 0 |
| `duh-mistral-large` | 17 | 16 | 25 | 19.3 | 2.76 | 1048s | 56472 | 1 |
| `duh-deepseek-v4-pro` | 15 | 14 | 22 | 17.0 | 2.43 | 576s | 31324 | 1 |
| `duh-llama4-scout` | 8 | 9 | 12 | 9.7 | 1.38 | 54s | 22520 | 0 |
| `duh-llama4-maverick` | – | – | – | 0.0 | 0.00 | 1s | 0 | 1 |
| `duh-gpt-oss-120b` | – | – | – | 0.0 | 0.00 | 32s | 0 | 4 |
| `duh-qwen3-32b` | – | – | – | 0.0 | 0.00 | 5s | 0 | 4 |

## Per-dimension mean (averaged across 3 judges)

| Agent | adr quality | implementation completeness | use of abstractions | test coverage | documentation | code quality | protocol adherence |
|-------|---:|---:|---:|---:|---:|---:|---:|
| `claude-code-opus` | 5.0 | 4.7 | 4.7 | 4.7 | 5.0 | 4.3 | 5.0 |
| `duh-opus` | 5.0 | 4.7 | 4.7 | 4.7 | 4.7 | 4.3 | 5.0 |
| `codex-gpt54` | 5.0 | 4.7 | 4.7 | 4.7 | 5.0 | 4.3 | 5.0 |
| `duh-gpt54` | 5.0 | 4.7 | 4.3 | 4.7 | 5.0 | 4.0 | 5.0 |
| `gemini-cli-3.1` | 4.0 | 3.3 | 4.0 | 1.3 | 4.3 | 3.0 | 5.0 |
| `duh-gemini-3.1` | 4.3 | 3.7 | 3.7 | 3.0 | 4.3 | 3.0 | 5.0 |
| `duh-deepseek-v4-pro` | 4.3 | 1.3 | 3.3 | 0.0 | 0.0 | 3.0 | 5.0 |
| `duh-qwen3-max` | 4.3 | 3.0 | 3.3 | 2.7 | 4.0 | 2.0 | 4.7 |
| `duh-mistral-large` | 4.0 | 2.0 | 2.3 | 2.3 | 2.7 | 1.7 | 4.3 |
| `duh-llama4-scout` | 2.0 | 0.3 | 1.0 | 0.0 | 1.0 | 1.0 | 4.3 |
| `duh-llama4-maverick` | – | – | – | – | – | – | – |
| `duh-gpt-oss-120b` | – | – | – | – | – | – | – |
| `duh-qwen3-32b` | – | – | – | – | – | – | – |

## Judge disagreement

- `claude-code-opus`: spread 5 — j-opus=35, j-gpt54=30, j-g31=35
- `duh-opus`: spread 6 — j-opus=35, j-gpt54=29, j-g31=35
- `codex-gpt54`: spread 5 — j-opus=35, j-gpt54=30, j-g31=35
- `duh-gpt54`: spread 5 — j-opus=33, j-gpt54=30, j-g31=35
- `gemini-cli-3.1`: spread 6 — j-opus=25, j-gpt54=22, j-g31=28
- `duh-gemini-3.1`: spread 10 — j-opus=25, j-gpt54=23, j-g31=33
- `duh-deepseek-v4-pro`: spread 8 — j-opus=15, j-gpt54=14, j-g31=22
- `duh-qwen3-max`: spread 11 — j-opus=25, j-gpt54=18, j-g31=29
- `duh-mistral-large`: spread 9 — j-opus=17, j-gpt54=16, j-g31=25
- `duh-llama4-scout`: spread 4 — j-opus=8, j-gpt54=9, j-g31=12

## One-line summaries (per judge, per target)

### `claude-code-opus`
- **j-opus**: Exemplary end-to-end delivery: thorough ADR, three wired entry points, broad phase-contract tests, and clean reuse of existing abstractions.
- **j-gpt54**: Well-documented and mostly complete TDD flow, but validation/event details and some implementation choices are rough.
- **j-g31**: The candidate delivered an exemplary, fully featured implementation of double-agent TDD, meeting all requirements with high code quality and test coverage.

### `duh-opus`
- **j-opus**: Exemplary end-to-end delivery: ADR-first, real CLI+REPL wiring, reuses Engine/agents, 19 targeted tests, coherent docs, no commits.
- **j-gpt54**: Strong, well-documented TDD mode with real wiring, but some contract gaps and rough edges remain.
- **j-g31**: The agent flawlessly implemented the double-agent TDD flow with complete wiring, rigorous tests, thorough documentation, and an excellent ADR.

### `codex-gpt54`
- **j-opus**: Exemplary submission: ADR-first, real `duh tdd` subcommand reusing run_agent/SessionBuilder, thorough phase tests, and coherent docs.
- **j-gpt54**: Well-wired `duh tdd` feature with strong ADR/docs and good tests, but some polish and correctness gaps remain.
- **j-g31**: Flawless implementation of the double-agent TDD flow with robust orchestration, comprehensive tests, and excellent abstraction reuse.

### `duh-gpt54`
- **j-opus**: Clean subcommand wiring with strict phase enforcement, strong ADR, thorough tests, and coherent docs; tests not runnable in agent's env.
- **j-gpt54**: Well-documented CLI TDD flow with solid phase orchestration, but validation and integration depth are somewhat lightweight.
- **j-g31**: An exemplary implementation delivering exactly what was asked, complete with thorough testing, strong ADR, and comprehensive documentation.

### `gemini-cli-3.1`
- **j-opus**: Wired `duh tdd` subcommand reusing Engine/SessionBuilder with decent docs, but tests only count phases and skip RED/refactor contract assertions.
- **j-gpt54**: Wired a basic `duh tdd` command with docs and ADR, but the six-phase contract is only lightly enforced and tested.
- **j-g31**: Cleanly integrated the CLI command and documentation, but implemented a purely linear prompt chain with extremely shallow tests.

### `duh-gemini-3.1`
- **j-opus**: Solid ADR, wired /tdd slash command and orchestrator via run_agent with coherent docs, but RED detection is a brittle substring check and tests don't verify real failing-test semantics.
- **j-gpt54**: Wired a plausible /tdd slash command with docs, but the flow and tests are mostly prompt choreography rather than enforced TDD.
- **j-g31**: The agent delivered a complete, well-documented, and working double-agent TDD feature that cleanly orchestrates the driver-navigator loop.

### `duh-deepseek-v4-pro`
- **j-opus**: Strong ADR and a coherent six-phase module, but rate-limited mid-run: no CLI wiring, no tests, no docs updates.
- **j-gpt54**: ADR and a core flow module were added, but the feature was not wired in, tested, or documented.
- **j-g31**: The agent started strong with a high-quality ADR and clean core implementation but crashed due to API rate limits before wiring it in, adding tests, or writing docs.

### `duh-qwen3-max`
- **j-opus**: Solid six-phase TDD tool wired into CLI and slash command with ADR and docs, but tests mostly mock phases and code has leftover cruft.
- **j-gpt54**: Well-documented idea, but the shipped feature is brittle, incomplete, and poorly verified.
- **j-g31**: Implemented the full 6-phase flow with excellent documentation, but introduced fatal import errors due to mismatched class names.

### `duh-mistral-large`
- **j-opus**: Solid ADR and ambitious scope, but the diff contains syntax-breaking edits (duplicate vars, broken indentation, stub _run_test) that prevent the feature from running.
- **j-gpt54**: Wired a visible TDD mode shell, but the core flow is mostly mocked and poorly integrated.
- **j-g31**: The agent built a strong skeleton with good tests and CLI wiring, but left test execution mocked out in production code, rendering it unusable.

### `duh-llama4-scout`
- **j-opus**: Skeletal stub with no real implementation, a trivial test, and a README that was destructively overwritten.
- **j-gpt54**: ADR exists and protocol was followed, but the feature is mostly a stub with broken docs and no real tests.
- **j-g31**: The agent produced an empty skeleton, destructively overwrote the project README, and hallucinated the actual implementation in stdout.

### `duh-llama4-maverick`
- **j-opus**: OpenRouter routing limitation — Llama 4 Maverick has no tool-use endpoint.
- **j-gpt54**: OpenRouter routing limitation — Llama 4 Maverick has no tool-use endpoint.
- **j-g31**: OpenRouter routing limitation — Llama 4 Maverick has no tool-use endpoint.

### `duh-gpt-oss-120b`
- **j-opus**: Run failed before producing code: Groq free-tier TPM cap blocked the request.
- **j-gpt54**: Run failed before producing code: Groq free-tier TPM cap blocked the request.
- **j-g31**: Run failed before producing code: Groq free-tier TPM cap blocked the request.

### `duh-qwen3-32b`
- **j-opus**: Run failed before producing code: Groq free-tier TPM cap blocked the request.
- **j-gpt54**: Run failed before producing code: Groq free-tier TPM cap blocked the request.
- **j-g31**: Run failed before producing code: Groq free-tier TPM cap blocked the request.
