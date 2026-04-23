# Double-Agent TDD Benchmark — Final Scoreboard (9 agents, 3 judges)

| Agent | j-opus | j-gpt54 | j-g31 | Mean /35 | Elapsed | Diff | Tests P/F | Ruff |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `claude-code-opus` | 35 | 30 | 35 | **33.3** | 742s | 85K | 25/0 | 0 |
| `codex-gpt54` | 35 | 30 | 35 | **33.3** | 510s | 43K | 32/2 | 0 |
| `duh-opus` | 35 | 29 | 35 | **33.0** | 915s | 86K | 19/0 | 0 |
| `duh-gpt54` | 33 | 30 | 35 | **32.7** | 230s | 35K | 70/0 | 0 |
| `duh-gemini-3.1` | 25 | 23 | 33 | **27.0** | 305s | 21K | 3/0 | 0 |
| `gemini-cli-3.1` | 25 | 22 | 28 | **25.0** | 358s | 16K | 2/0 | 0 |
| `duh-llama4-scout` | 8 | 9 | 12 | **9.7** | 54s | 21K | 0/0 | 0 |
| `duh-gpt-oss-120b` | – | – | – | **FAILED** | 32s | – | – | – |
| `duh-qwen3-32b` | – | – | – | **FAILED** | 5s | – | – | – |

## Per-dimension means (3-judge avg)

| Agent | adr quality | implementati | use of abstr | test coverag | documentatio | code quality | protocol adh |
|---|---:|---:|---:|---:|---:|---:|---:|
| `claude-code-opus` | 5.0 | 4.7 | 4.7 | 4.7 | 5.0 | 4.3 | 5.0 |
| `codex-gpt54` | 5.0 | 4.7 | 4.7 | 4.7 | 5.0 | 4.3 | 5.0 |
| `duh-opus` | 5.0 | 4.7 | 4.7 | 4.7 | 4.7 | 4.3 | 5.0 |
| `duh-gpt54` | 5.0 | 4.7 | 4.3 | 4.7 | 5.0 | 4.0 | 5.0 |
| `duh-gemini-3.1` | 4.3 | 3.7 | 3.7 | 3.0 | 4.3 | 3.0 | 5.0 |
| `gemini-cli-3.1` | 4.0 | 3.3 | 4.0 | 1.3 | 4.3 | 3.0 | 5.0 |
| `duh-llama4-scout` | 2.0 | 0.3 | 1.0 | 0.0 | 1.0 | 1.0 | 4.3 |
| `duh-gpt-oss-120b` | – | – | – | – | – | – | – |
| `duh-qwen3-32b` | – | – | – | – | – | – | – |

## Code quality detail

| Agent | +/-  lines | Files | New tests tried |
|---|---:|---:|---|
| `claude-code-opus` | +2049/-1 | 10 | tests/unit/test_double_agent_tdd.py |
| `codex-gpt54` | +1154/-0 | 12 | tests/integration/test_double_agent_tdd_flow.py tests/unit/test_cli.py tests/unit/test_double_agent_tdd.py |
| `duh-opus` | +2009/-1 | 12 | tests/unit/test_tdd_mode.py |
| `duh-gpt54` | +804/-4 | 11 | tests/unit/test_cli.py tests/unit/test_slash_dispatch.py tests/unit/test_tdd.py |
| `duh-gemini-3.1` | +402/-0 | 7 | tests/unit/test_tdd_flow.py |
| `gemini-cli-3.1` | +330/-0 | 7 | tests/unit/test_tdd.py |
| `duh-llama4-scout` | +123/-215 | 5 | tests/test_double_agent_tdd.py |
| `duh-gpt-oss-120b` | +–/-– | – | — |
| `duh-qwen3-32b` | +–/-– | – | — |