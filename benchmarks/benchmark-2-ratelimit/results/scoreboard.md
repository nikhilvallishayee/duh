# Benchmark 2 — Rate Limiter — Scoreboard

Mean across 3 judges, /50. Adv = hidden adversarial pass rate.

| Agent | j-opus | j-gpt54 | j-g31 | Mean /50 | Adv | Elapsed | Diff |
|---|---:|---:|---:|---:|---:|---:|---:|
| `claude-code-opus` | 50 | 48 | 50 | **49.3** | 100% | 606s | 85K |
| `duh-opus` | 50 | 47 | 50 | **49.0** | 100% | 538s | 87K |
| `codex-gpt54` | 49 | 45 | 50 | **48.0** | 100% | 431s | 49K |
| `duh-gpt54` | 47 | 47 | 50 | **48.0** | 100% | 333s | 40K |
| `gemini-cli-3.1` | 44 | 43 | 50 | **45.7** | 100% | 342s | 40K |
| `duh-gemini-3.1` | 45 | 42 | 50 | **45.7** | 100% | 515s | 38K |

## Per-dimension means (3-judge average)

| Agent | adr quality | implementation | correctness ha | adversarial co | concurrency di | design doc | api ergonomics | test coverage | code quality | protocol adher |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `claude-code-opus` | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 4.7 | 5.0 | 4.7 | 5.0 |
| `duh-opus` | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 4.7 | 4.7 | 4.7 | 5.0 |
| `codex-gpt54` | 4.7 | 5.0 | 5.0 | 5.0 | 5.0 | 4.7 | 4.7 | 4.3 | 4.7 | 5.0 |
| `duh-gpt54` | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 5.0 | 4.3 | 4.3 | 4.3 | 5.0 |
| `gemini-cli-3.1` | 4.0 | 5.0 | 4.3 | 5.0 | 5.0 | 4.3 | 4.3 | 4.3 | 4.3 | 5.0 |
| `duh-gemini-3.1` | 4.3 | 5.0 | 4.3 | 5.0 | 4.7 | 4.7 | 4.3 | 4.0 | 4.3 | 5.0 |

## Judge one-liners

### `claude-code-opus`  (mean 49.3/50)
- **j-opus**: Exemplary submission: thorough ADR, atomic Lua-based Redis backend, complete API surface, and full adversarial + property test pass.
- **j-gpt54**: Complete, well-tested implementation with strong concurrency semantics and perfect hidden-suite performance.
- **j-g31**: The agent produced a complete, correct, and robust distributed rate limiter with exceptional documentation, concurrency handling, and tests.

### `duh-opus`  (mean 49.0/50)
- **j-opus**: Exemplary implementation: ADR-first, atomic Lua, both algorithms+backends, full adversarial pass, strong property/concurrency tests.
- **j-gpt54**: Complete, well-documented implementation with strong concurrency safety and a perfect hidden-suite result.
- **j-g31**: An exemplary, production-ready rate limiter implementation hitting all requirements with complete documentation and robust concurrency controls.

### `codex-gpt54`  (mean 48.0/50)
- **j-opus**: Exemplary submission: complete, atomic Lua-backed Redis impl, thorough ADR/design, and full adversarial pass rate.
- **j-gpt54**: Complete, atomic Lua-based rate limiter with strong tests and docs, minus some polish on API/spec details.
- **j-g31**: An exceptional and completely flawless implementation featuring solid concurrency mechanisms, thorough documentation, and clean architecture.

### `duh-gpt54`  (mean 48.0/50)
- **j-opus**: Complete, well-documented limiter with atomic Lua, solid concurrency tests, and a perfect adversarial pass rate.
- **j-gpt54**: Complete, well-documented implementation with strong concurrency handling and a perfect hidden-suite pass rate.
- **j-g31**: An exceptional, fully featured implementation with flawless concurrency handling, robust tests, and highly thoughtful design documentation.

### `gemini-cli-3.1`  (mean 45.7/50)
- **j-opus**: Solid, complete implementation with atomic Lua scripts, property tests, and full adversarial suite pass; ADR and design doc are competent if not exhaustive.
- **j-gpt54**: Complete, atomic implementation with full hidden-suite pass rate, but ADR/design depth and standards polish are only moderate.
- **j-g31**: An exceptional, production-ready submission that flawlessly implements all requirements with robust concurrency and excellent documentation.

### `duh-gemini-3.1`  (mean 45.7/50)
- **j-opus**: Complete, well-documented implementation with sliding-window-log, atomic Lua scripts, and perfect adversarial pass rate.
- **j-gpt54**: Complete and concurrency-safe implementation with perfect hidden-suite results, but docs/tests/API details are a bit rough.
- **j-g31**: An exemplary, flawless implementation delivering perfect adversarial test passes, rigorous concurrency handling, and highly detailed documentation.
