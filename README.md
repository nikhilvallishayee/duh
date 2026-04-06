# D.U.H. — Duh is a Universal Harness

> Because connecting AI to your codebase should be obvious.

The first production-grade, open-source, provider-agnostic AI coding harness.

## Status: Building in Public

Architecture: [ADR-024](docs/adrs/ADR-024-design-principles.md)

## Quick Start

```bash
pip install duh-cli
duh -p "fix the bug"
```

## Principles

1. The core loop must be basically good
2. Ports and adapters, not provider lock-in
3. Remove duplication and improve names in small cycles
4. Evolutionary design, not upfront architecture
5. Unix composability
6. Human-first CLI design
7. Extensibility through plugins, not modification
8. Safety as architecture, not afterthought
9. Context engineering as first-class concern
10. Properties, not rules (CUPID)

## Architecture

```
duh/
  kernel/           # <5K LOC, zero external deps
    loop.py         # prompt → model → tool → result
    engine.py       # session lifecycle
    tool.py         # tool protocol
    messages.py     # message data model
    deps.py         # injectable dependencies
  ports/            # abstract interfaces
    provider.py     # ModelProvider protocol
    executor.py     # ToolExecutor protocol  
    approver.py     # ApprovalGate protocol
    store.py        # SessionStore protocol
    context.py      # ContextManager protocol
  adapters/         # concrete implementations
  tools/            # pluggable tool set
  ui/               # pluggable TUI
  cli/              # CLI entry point
```

## License

Apache 2.0

