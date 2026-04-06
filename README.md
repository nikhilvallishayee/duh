# D.U.H. — D.U.H. is a Universal Harness

> Because connecting AI to your codebase should be obvious.

The first production-grade, open-source, provider-agnostic AI coding harness built on first principles.

## What is D.U.H.?

D.U.H. is the scaffolding between you and any AI model. It handles the agentic loop (prompt → model → tool → result → iterate), tool execution, safety, sessions, and terminal UI — so you can use Claude, GPT, Gemini, Ollama, or any model through one clean interface.

**Not another AI wrapper.** D.U.H. is a harness — the hands, eyes, memory, and safety boundaries that make a model useful as a coding agent.

## Principles

Built on Kent Beck's 4 Rules of Simple Design, Unix philosophy, Clean Architecture, and hard-won lessons from analyzing Claude Code (513K LOC TS), OpenCode (Go), Aider (Python), and Goose (Rust).

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

## Architecture

```
duh/
  kernel/           # 5 files, <1K LOC — the agentic loop
    loop.py         # async generator: prompt → model → tool → result
    engine.py       # session lifecycle wrapper
    tool.py         # Tool protocol (4 required fields, that's it)
    messages.py     # Message data model (TextBlock, ToolUseBlock, etc.)
    deps.py         # Injectable dependencies — every external call is a seam

  ports/            # Abstract interfaces (what the kernel expects)
    provider.py     # ModelProvider — any LLM, any SDK, uniform events
    executor.py     # ToolExecutor — run tools by name
    approver.py     # ApprovalGate — check before execution
    store.py        # SessionStore — persist conversations
    context.py      # ContextManager — manage the context window

  adapters/         # Concrete wrappers WE write (not provider code)
    anthropic.py    # Anthropic SDK → D.U.H. events
    approvers.py    # Auto / Interactive / Rule-based approval
    native_executor.py  # Run Python Tool objects
```

### Why "Ports and Adapters"?

Providers (Anthropic, OpenAI, Ollama) each have their own SDK, streaming format, and tool calling convention. **None provide a uniform interface.** The port defines what D.U.H. expects; the adapter is the wrapper we write to translate.

```
Provider SDK (their code, their format)
    ↓
Adapter (our wrapper — translates to our format)
    ↓
Port (our interface contract)
    ↓
Kernel (consumes uniform events, provider-agnostic)
```

## Quick Start

```bash
pip install duh-cli
export ANTHROPIC_API_KEY=sk-ant-...
duh -p "fix the bug"
```

## Status: Building in Public

Built incrementally, commit by commit, test-first. Each commit adds one capability with full test coverage and clean code that reveals its intention.

| Component | Status | Tests |
|-----------|--------|-------|
| Kernel (loop, engine, messages, tool, deps) | Done | 100% coverage |
| Ports (provider, executor, approver, store, context) | Done | 100% coverage |
| Anthropic adapter | Done | 29 tests |
| Approvers (auto, interactive, rule-based) | Done | — |
| Native executor | Done | — |
| File store | Planned | — |
| Core tools (Read, Write, Edit, Bash, Glob, Grep) | Planned | — |
| CLI entry point (`duh` command) | Planned | — |
| TUI (terminal UI) | Planned | — |
| OpenAI adapter | Planned | — |
| MCP tool transport | Planned | — |

## Design Decisions

All architectural decisions are documented as ADRs in [`docs/adrs/`](docs/adrs/):

| ADR | Decision |
|-----|----------|
| [ADR-001](docs/adrs/ADR-001-project-vision.md) | Project vision — 10 principles, why D.U.H. exists |
| [ADR-002](docs/adrs/ADR-002-kernel-design.md) | Kernel design — 5 files, <1K LOC, zero external deps |
| [ADR-003](docs/adrs/ADR-003-ports-and-adapters.md) | Ports and adapters — providers don't give us uniform interfaces, we write wrappers |
| [ADR-004](docs/adrs/ADR-004-tool-protocol.md) | Tool protocol — 4 required fields vs Claude Code's 30 methods |
| [ADR-005](docs/adrs/ADR-005-safety-architecture.md) | Safety architecture — 3 layers: schema filtering, approval gate, tool validation |

## Why D.U.H.?

| | Claude Code | OpenCode | Aider | Goose | **D.U.H.** |
|---|---|---|---|---|---|
| Language | TypeScript | Go | Python | Rust | **Python** |
| Multi-provider | No | Yes | Yes (litellm) | Yes | **Yes** |
| MCP support | Full | Config | None | Native | **Planned** |
| Kernel size | 513K LOC | ~25K | ~30K | ~50K | **<1K LOC** |
| Safety layers | 3 | 1 | 0 | 1 | **3** |
| Open source | No | Yes | Yes | Yes | **Yes** |

No existing harness combines: Python + multi-provider + MCP + production safety + clean architecture + <1K LOC kernel.

## Contributing

Every change follows the cycle:
1. Write the test (what)
2. Watch it fail (red)
3. Write the code (green)
4. Refactor (clean)
5. Verify 100% coverage
6. Clean commit with clear message

## License

Apache 2.0
