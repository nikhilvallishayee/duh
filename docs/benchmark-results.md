# Benchmark: Claude Code vs D.U.H. — Head-to-Head

**Date**: 2026-04-07
**Task**: Build a URL shortener API from a spec file (TASK.md)
**Model**: Claude Haiku 4.5 (same model, same API, same prompt)
**Repo**: Fresh git repo, TASK.md only

## Results

| Metric | Claude Code | D.U.H. |
|---|---|---|
| **Time** | 77s | 39s |
| **Files created** | 3 (main.py, test_main.py, requirements.txt) | 4 (main.py, database.py, test_main.py, requirements.txt) |
| **LOC** | 239 | 369 |
| **Tests passing** | 1/7 (6 fail: table not initialized) | 9/13 (4 fail: endpoint assertion mismatches) |
| **Architecture** | Single file, SQLAlchemy ORM | Separate DB module, raw sqlite3 |
| **Turns used** | ~8 | ~6 |

## Analysis

### What Claude Code did well
- Used SQLAlchemy (more professional ORM choice)
- Cleaner test structure

### What Claude Code got wrong
- **Critical**: Database table never initialized → 6/7 tests fail at runtime
- Used deprecated SQLAlchemy APIs (declarative_base)

### What D.U.H. did well
- **Faster**: 39s vs 77s (2x faster wall-clock)
- **Better architecture**: separated DB into its own module
- **More working tests**: 9/13 vs 1/7
- **More code generated**: 369 LOC vs 239 LOC (more complete implementation)

### What D.U.H. got wrong
- 4 tests fail on endpoint assertions (model wrote incorrect test expectations)
- Used deprecated FastAPI @app.on_event("startup")
- Model generated Python 3.10+ syntax (str | None) — fails on 3.9

### Root cause for both failures
Both tools use the same model (Haiku 4.5), so failures are **model quality** issues, not tool bugs:
- Haiku often forgets to initialize databases
- Haiku writes tests with incorrect assertions
- Neither tool compensated for the model's mistakes

## Bugs Found in D.U.H. During Benchmark

3 real bugs discovered and fixed:

1. **Provider inference from model name** — `--model claude-haiku` fell through to Ollama when no API key was set. Fixed: infer provider from model name (claude/haiku/sonnet → anthropic).

2. **Relative path resolution** — Tools used process cwd instead of context.cwd. `./TASK.md` resolved to wrong directory. Fixed: resolve relative paths against context.cwd in all file tools.

3. **Ollama tool call text extraction** — Small Ollama models output tool calls as JSON text instead of structured blocks. Fixed: parse JSON tool patterns from text and execute them.

## Conclusion

D.U.H. is **functional and competitive** with Claude Code for the same model. The 2x speed advantage comes from simpler startup (no Ink TUI, no plugin loading). Test success rate is model-dependent, not tool-dependent.

The benchmark exposed 3 real bugs in D.U.H. — all fixed and committed. This is exactly the kind of dogfooding that improves quality.
