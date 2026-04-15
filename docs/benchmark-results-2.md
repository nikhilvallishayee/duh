# Benchmark: D.U.H. vs Claude Code — CLI Todo App (April 15, 2026)

**Date**: 2026-04-15
**Task**: Build a complete CLI todo app with persistence, argparse, tests (12+), type hints, error handling
**Method**: 3 independent runs per tool, fully isolated directories, same prompt
**Prompt**: "Read TASK.md and implement everything exactly as specified. Do not ask questions — just write the code, create all files, and run pytest. Start now."

## Raw Results

| Run | Tool | Model | Time | LOC | Tests | Pass Rate | Notes |
|---|---|---|---|---|---|---|---|
| 1 | **D.U.H.** | Haiku 4.5 | 65.8s | 716 | 40/40 | 100% | |
| 2 | **D.U.H.** | Haiku 4.5 | 56.2s | 619 | 32/32 | 100% | |
| 3 | **D.U.H.** | Haiku 4.5 | 66.9s | 559 | 27/27 | 100% | |
| 1 | **CC** | Haiku 4.5 | 65.6s | 488 | 27/27 | 100% | |
| 2 | **CC** | Haiku 4.5 | 31.5s | 0 | — | **FAIL** | Asked clarifying question instead of implementing |
| 2b | **CC** | Haiku 4.5 | 67.8s | 541 | 34/34 | 100% | Retry with directive prompt |
| 3 | **CC** | Haiku 4.5 | 49.3s | 593 | 31/31 | 100% | |
| 1 | **D.U.H.** | Sonnet 4.6 | 104.2s | 649 | 27/27 | 100% | |
| 1 | **D.U.H.** | GPT-4o | 146.4s | 168 | 12/12 | 100% | OpenAI provider |

## Aggregated (D.U.H. Haiku vs Claude Code Haiku, 3 runs each)

| Metric | D.U.H. (n=3) | Claude Code (n=3) |
|---|---|---|
| **Avg time** | **63.0s** | 60.9s* |
| **Avg LOC** | **631** | 541 |
| **Avg tests** | **33.0** | 30.7 |
| **Success rate** | **3/3 (100%)** | 2/3 (67%) |
| **All tests pass** | 3/3 | 2/2 successful runs |

*CC average excludes Run 2 (failed to produce output).

## Analysis

### Reliability
D.U.H. completed successfully on **all 3 runs** (100%). Claude Code failed on Run 2, producing no files — it asked a clarifying question instead of implementing, despite running in `-p` (print/non-interactive) mode. This is consistent with the previous benchmark where CC had a 67% success rate.

### Test Generation
D.U.H. generated **8% more tests on average** (33 vs 30.7) and hit a peak of **40 tests** on Run 1. Both tools exceeded the 12-test minimum, but D.U.H. was more thorough.

### Code Volume
D.U.H. produced **17% more code** (631 vs 541 LOC). More LOC isn't always better, but in this case it corresponded to more comprehensive implementations (more error handling, more edge cases, more tests).

### Speed
Comparable on Haiku (~63s vs ~61s). D.U.H. has slightly less startup overhead (Python vs Node.js + Ink TUI), but the difference is within noise.

### Multi-Provider
D.U.H. successfully ran the same task on GPT-4o (146s, 12 tests) and Sonnet (104s, 27 tests), demonstrating true provider-agnostic operation. Claude Code is Anthropic-only.

## Environment

- Machine: MacBook (Apple Silicon)
- D.U.H. version: 0.4.2
- Claude Code version: 2.1.109
- Anthropic API key: same key for both tools
- OpenAI API key: separate key for GPT-4o run
- Each run in a fresh isolated directory with only TASK.md
