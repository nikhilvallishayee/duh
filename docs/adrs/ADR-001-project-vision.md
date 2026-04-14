# ADR-001: Project Vision

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-07

## Context

AI coding agents are becoming essential developer tools, but every harness is either:
- Locked to one provider (e.g. Anthropic-only harnesses)
- Missing production safety (Aider → no permissions)
- Too complex to understand (production harnesses grow to hundreds of thousands of lines)
- Not extensible via standards (Aider → no MCP)

## Decision

Build D.U.H. — **Duh is a Universal Harness** — the first production-grade, open-source, provider-agnostic AI coding harness with a clean kernel under 5K LOC.

## Principles

1. **Core loop must be basically good** — ship what works, improve iteratively
2. **Ports and adapters** — core never imports provider SDKs
3. **Remove duplication, improve names** — in small cycles
4. **Evolutionary design** — refactor when the 3rd use case reveals the pattern
5. **Unix composability** — stdout for data, stderr for humans, --json for machines
6. **Human-first CLI** — errors tell what to do, 30-second time-to-value
7. **Extensibility through plugins** — community adds without modifying core
8. **Safety as architecture** — defense in depth, schema filtering
9. **Context engineering** — first-class concern, not afterthought
10. **Properties not rules** — composable, predictable, idiomatic, domain-based

## Architecture

```
duh/
  kernel/     # <5K LOC — the agentic loop, zero external deps
  ports/      # abstract interfaces for providers, tools, safety
  adapters/   # concrete implementations
  tools/      # pluggable tool set
  ui/         # pluggable TUI
  cli/        # CLI entry point
```

## Consequences

- Every feature starts with a test, then a spec, then code
- Every commit builds on the previous — clean, incremental, reviewable
- Reference implementations studied: leading TS, Python, Go, and Rust harnesses
- No single codebase is copied — universal patterns are extracted
