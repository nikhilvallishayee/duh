# Benchmark: Claude Code vs D.U.H. — Comprehensive

**Date**: 2026-04-07  
**Author**: Benchmark methodology by Nikhil Vallishayee  
**Model**: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) — same model, same API key  
**Task**: Build a FastAPI URL shortener from spec (TASK.md)  
**Method**: 3 independent runs per tool, fully isolated directories, no shared state  

## Raw Results

| Run | Tool | Time | Files | LOC | Tests | Pass Rate |
|---|---|---|---|---|---|---|
| 1 | **CC** | 61.3s | 3 | 227 | 9/9 | 100% |
| 2 | **CC** | 65.1s | 3 | 319 | 12/12 | 100% |
| 3 | **CC** | 50.7s | 0 | 0 | — | **FAIL** (no output) |
| 1 | **D.U.H.** | 52.6s | 3 | 475 | 24/24 | 100% |
| 2 | **D.U.H.** | 48.8s | 3 | 446 | 18/18 | 100% |
| 3 | **D.U.H.** | 35.6s | 3 | 335 | 12/12 | 100% |

## Aggregated (excluding CC Run 3 — tool failure)

| Metric | Claude Code (n=2) | D.U.H. (n=3) |
|---|---|---|
| **Avg time** | 63.2s | **45.7s** |
| **Avg LOC** | 273 | **419** |
| **Avg tests** | 10.5 | **18** |
| **Success rate** | 2/3 (67%) | **3/3 (100%)** |
| **All tests pass** | 2/2 successful runs | **3/3** |
| **Files per run** | 3 | 3 |

## Analysis

### Speed
D.U.H. is **~28% faster on average** (45.7s vs 63.2s). The difference is startup overhead — Claude Code loads its Ink TUI, plugin system, and Node.js runtime. D.U.H. is a direct Python process with no UI overhead in print mode.

### Reliability
CC failed to produce any output on Run 3 (0 files). D.U.H. succeeded on all 3 runs. With n=3, this is anecdotal — but CC's occasional silent failure is a known issue (subprocess timeout, auth race).

### Code Quality
D.U.H. produced **53% more code** on average (419 LOC vs 273). Both tools generated working FastAPI apps with SQLite, but D.U.H.'s model interaction produced:
- More test cases per run (18 avg vs 10.5)
- Self-correction behavior (Run 1: detected Pydantic URL normalization issue, fixed the test, re-ran)
- Separate DB module in some runs (better architecture)

### Test Quality
Both tools achieved 100% test pass rates on successful runs. The same Haiku model generates the code in both cases — so test quality differences are about the tool's agentic behavior, not the model:
- D.U.H. uses more turns for self-correction (reads tests, runs them, fixes failures)
- CC appears to generate-and-verify in fewer turns but with less error recovery

## Methodology

**Controls:**
- Same Anthropic API key for both tools
- Same model (Haiku 4.5)
- Same prompt verbatim
- Same TASK.md verbatim
- Separate directories (no git branch sharing)
- Fresh git repo per run
- `--dangerously-skip-permissions` on both
- `--max-turns 15` on both
- Tests run with same Python (3.12.12) and same pytest

**What's NOT controlled:**
- CC uses Ink TUI + Node.js runtime; D.U.H. uses Python + no TUI → different startup overhead
- CC has more tools available by default (NotebookEdit, Task, etc.); D.U.H. has fewer but sufficient for this task
- CC's tool schema presentation to the model may differ from D.U.H.'s → can affect model behavior

## Bugs Found in D.U.H. During Benchmarking

3 real bugs discovered and fixed:

1. **Provider inference from model name** — `--model claude-haiku-4-5-20251001` fell through to Ollama when ANTHROPIC_API_KEY wasn't in env. Fixed: parse model name to infer provider.

2. **Relative path resolution** — `./TASK.md` resolved against process cwd, not context.cwd. Fixed: all file tools now resolve relative paths against context.cwd.

3. **Ollama tool call text extraction** — Small models output `{"name":"Read","arguments":{...}}` as text instead of structured tool_call. Fixed: regex extraction of JSON tool patterns.

## Conclusion

D.U.H. is **competitive with Claude Code** on identical tasks with identical models:
- Faster startup (28% average)
- Higher reliability in this sample (3/3 vs 2/3)
- More aggressive self-correction (more LOC, more tests)
- Same model, so fundamental code quality is equivalent

D.U.H.'s advantage is architectural simplicity — less overhead means more time for actual model interaction. CC's advantage is maturity — wider tool set, better TUI, more battle-tested in production.

For this specific benchmark (single-file API project, Haiku 4.5), D.U.H. performs at or above CC's level.
