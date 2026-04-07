# D.U.H. — D.U.H. is a Universal Harness

> Because connecting AI to your codebase should be obvious.

The first production-grade, open-source, provider-agnostic AI coding harness built on first principles.

## What is D.U.H.?

D.U.H. is the scaffolding between you and any AI model. It handles the agentic loop (prompt → model → tool → result → iterate), tool execution, safety, sessions, and terminal UI — so you can use Claude, GPT, Gemini, Ollama, or any model through one clean interface.

**Not another AI wrapper.** D.U.H. is a harness — the hands, eyes, memory, and safety boundaries that make a model useful as a coding agent.

## Quick Start

```bash
pip install duh-cli
export ANTHROPIC_API_KEY=sk-ant-...
duh -p "fix the bug"
```

Or with a local model (no API key needed):

```bash
# Start Ollama
ollama serve
ollama pull qwen2.5-coder:7b

# Use D.U.H. with local model
duh -p "what files are here?" --provider ollama
```

## Verified Working

```
$ duh -p "Say DUH"
DUH! 🤦

$ duh -p "Read pyproject.toml project name" --dangerously-skip-permissions
  > Read(file_path='pyproject.toml')
The project name is duh-cli.

$ duh -p "What is 7*8?" --tool-choice none
56

$ duh -p "List all ADRs" --model claude-opus-4-6 --dangerously-skip-permissions
  > Glob(pattern='docs/adrs/**/*')
  > Read(file_path='docs/adrs/ADR-001-project-vision.md')
  > Read(file_path='docs/adrs/ADR-002-kernel-design.md')
  ... (11 tools called, all ADRs read and summarized)

$ duh -p "2+2?" --provider ollama --model qwen2.5-coder:1.5b
4

$ duh doctor
  [  ok] Python version: 3.12.12 (>= 3.12)
  [  ok] ANTHROPIC_API_KEY: set
  [  ok] Config directory: ~/.config/duh
  [  ok] anthropic SDK: installed
  [  ok] Tools available: Read, Write, Edit, Bash, Glob, Grep
```

## Architecture

```
duh/
  kernel/           # 5 files, <1K LOC — the agentic loop
    loop.py         # async generator: prompt → model → tool → result
    engine.py       # session lifecycle wrapper
    tool.py         # Tool protocol (4 required fields)
    messages.py     # Message data model
    deps.py         # Injectable dependencies — every external call is a seam

  ports/            # Abstract interfaces (what the kernel expects)
    provider.py     # ModelProvider — any LLM, uniform events
    executor.py     # ToolExecutor — run tools by name
    approver.py     # ApprovalGate — check before execution
    store.py        # SessionStore — persist conversations
    context.py      # ContextManager — manage context window

  adapters/         # Wrappers WE write (provider SDKs → our format)
    anthropic.py    # Anthropic SDK → D.U.H. events
    ollama.py       # Ollama HTTP API → D.U.H. events
    approvers.py    # Auto / Interactive / Rule-based approval
    native_executor.py  # Run Python Tool objects
    mcp_executor.py # MCP server tool transport
    file_store.py   # JSONL session persistence
    simple_compactor.py # Context window management

  tools/            # 6 core tools
    read.py, write.py, edit.py, bash.py, glob_tool.py, grep.py

  hooks.py          # Data-driven hook system (6 events, 2 types)
  cli/main.py       # CLI entry point
```

### Ports and Adapters

Providers each have their own SDK, streaming format, and tool calling convention. **None provide a uniform interface.** The port defines what D.U.H. expects; the adapter translates.

```
Provider SDK (their code, their format)
    ↓
Adapter (our wrapper — translates to our format)
    ↓
Port (our interface contract)
    ↓
Kernel (consumes uniform events, provider-agnostic)
```

## Providers

| Provider | Status | Notes |
|----------|--------|-------|
| **Anthropic** | Working | Claude Sonnet, Opus, Haiku. Native tool_choice. |
| **Ollama** | Working | Any local model. tool_choice emulated via prompt. |
| **OpenAI** | Planned | GPT-4o, o1, etc. |
| **Google Gemini** | Planned | Via Vertex or direct API. |
| **litellm** | Planned | 100+ models via one adapter. |

Auto-detection: if `ANTHROPIC_API_KEY` is set, uses Anthropic. Otherwise checks for local Ollama.

## CLI Flags

```
duh -p "prompt"                      # print mode (non-interactive)
duh -p "prompt" --model opus         # specify model
duh -p "prompt" --provider ollama    # force provider
duh -p "prompt" --tool-choice none   # no tool use (text only)
duh -p "prompt" --tool-choice any    # force tool use
duh -p "prompt" --dangerously-skip-permissions  # auto-approve tools
duh -p "prompt" --output-format json # JSON output
duh -p "prompt" --max-turns 5        # limit agentic turns
duh -p "prompt" --debug              # full event tracing
duh -p "prompt" --system-prompt "Be a pirate"
duh doctor                           # diagnostics
duh --version                        # version
```

## Design Principles

1. **The core loop must be basically good** — ship what works, iterate
2. **Ports and adapters** — core never imports provider SDKs
3. **Remove duplication, improve names** — in small cycles
4. **Evolutionary design** — YAGNI, refactor when the 3rd case reveals the pattern
5. **Unix composability** — stdout for data, stderr for humans, `--json` for machines
6. **Human-first CLI** — errors tell what to do, 30s time-to-value
7. **Extensibility through plugins** — community adds without modifying core
8. **Safety as architecture** — defense in depth (schema filtering → approval → tool validation)
9. **Context engineering** — first-class concern
10. **Properties not rules** — composable, predictable, idiomatic, domain-based

## Design Decisions (ADRs)

| ADR | Decision |
|-----|----------|
| [001](docs/adrs/ADR-001-project-vision.md) | Project vision — 10 principles |
| [002](docs/adrs/ADR-002-kernel-design.md) | Kernel — 5 files, <1K LOC, zero deps |
| [003](docs/adrs/ADR-003-ports-and-adapters.md) | Ports and adapters — providers don't give uniform interfaces |
| [004](docs/adrs/ADR-004-tool-protocol.md) | Tool protocol — 4 fields vs 30 methods |
| [005](docs/adrs/ADR-005-safety-architecture.md) | Safety — 3 layers: schema, approval, tool validation |
| [006](docs/adrs/ADR-006-context-engineering.md) | Context — token estimation, compaction |
| [007](docs/adrs/ADR-007-session-persistence.md) | Sessions — JSONL, resume, atomic writes |
| [008](docs/adrs/ADR-008-cli-design.md) | CLI — flags, errors, debug, auto-detect |
| [009](docs/adrs/ADR-009-provider-adapters.md) | Providers — Anthropic + Ollama adapters |
| [010](docs/adrs/ADR-010-mcp-integration.md) | MCP — tool transport protocol |
| [013](docs/adrs/ADR-013-hook-system.md) | Hooks — data-driven dispatch, 6 events |

## Comparison

| | Claude Code | OpenCode | Aider | Goose | **D.U.H.** |
|---|---|---|---|---|---|
| Language | TypeScript | Go | Python | Rust | **Python** |
| Multi-provider | No | Yes | Yes | Yes | **Yes** |
| MCP support | Full | Config | None | Native | **Adapter** |
| Clean kernel | Large monolith | Lean | Focused | Multi-crate | **<1K LOC** |
| Safety layers | 3 | 1 | 0 | 1 | **3** |
| tool_choice | Yes | No | No | No | **Yes (uniform)** |
| Open source | No | Yes | Yes | Yes | **Yes** |

## Tests

```
576 tests in 2.70s — 0 failures

Kernel:     70 tests (loop, engine, messages, tool, deps)
Exhaustive: 83 tests (every branch in loop + messages)
Ports:      15 tests (protocol satisfaction)
Adapters:   120 tests (anthropic, ollama, approvers, executor, store, compactor)
Tools:      81 tests (all 6 tools, protocol conformance)
Hooks:      34 tests (registry, command, function, timeout)
MCP:        19 tests (connection, discovery, execution)
CLI:        30 tests (parsing, doctor, print mode)
Integration: 4 tests (subprocess e2e)
Compactor:  39 tests (100% coverage)
File store: 26 tests (100% coverage)
```

## Contributing

Every change follows the cycle:
1. Write the test (what)
2. Watch it fail (red)
3. Write the code (green)
4. Refactor (clean)
5. Verify coverage
6. Clean commit

## License

Apache 2.0
