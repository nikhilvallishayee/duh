# Claude Code vs D.U.H. — Detailed Comparison

**Last updated:** 2026-04-14  
**Benchmark data source:** [`docs/benchmark-results.md`](../../../docs/benchmark-results.md)  
**Architecture data source:** [`docs/architecture-comparison.md`](../../../docs/architecture-comparison.md)

---

## TL;DR

| | Claude Code | D.U.H. |
|---|---|---|
| **Provider lock-in** | Anthropic only | 5 providers (Claude, OpenAI, ChatGPT/Codex OAuth, Ollama, Stub) |
| **Cost** | Subscription or API key | Free — bring your own key or use Ollama locally |
| **Open source** | No | Yes (Apache 2.0) |
| **Security depth** | Pattern-based sandboxing | 3-layer: 13 scanners + taint propagation + OS sandbox |
| **Architecture** | Monolith + React (Ink) | Hexagonal (ports & adapters) |
| **Benchmark speed** | 63.2s avg (n=2 successful) | 45.7s avg (n=3, all successful) |
| **Benchmark reliability** | 2/3 runs succeeded | 3/3 runs succeeded |

---

## Performance Benchmark

Both tools were given the identical task — build a FastAPI URL shortener from spec — using the
identical model (Claude Haiku 4.5) and the same API key. 3 independent runs each.

| Metric | Claude Code | D.U.H. |
|---|---|---|
| Average time | 63.2s (n=2 successful) | **45.7s** (n=3) |
| Success rate | 2/3 (67%) | **3/3 (100%)** |
| Average LOC produced | 273 | **419** |
| Average tests generated | 10.5 | **18** |
| All tests pass on success | Yes (2/2) | Yes (3/3) |

**D.U.H. is ~28% faster** because it has no Ink TUI, Node.js runtime, or plugin system overhead.
Claude Code's Run 3 produced zero output (silent subprocess failure — a known issue). D.U.H.
completed all 3 runs cleanly and self-corrected on Run 1 (detected a Pydantic URL normalization
issue, fixed the test, re-ran).

Full methodology in [`docs/benchmark-results.md`](../../../docs/benchmark-results.md).

---

## Feature Comparison

### Core Loop

| Feature | Claude Code | D.U.H. |
|---|---|---|
| Multi-turn agentic loop | Y | Y |
| Streaming text output | Y | Y |
| Thinking/reasoning blocks | Y | Y |
| Max turns enforcement | Y | Y |
| Error recovery in loop | Y | Y |
| Print mode (`-p`) | Y | Y |
| Interactive REPL | Y (Ink/React TUI) | Y (Rich TUI) |
| Session resume (`--continue`) | Y | Y |
| Context compaction (truncation) | Y | Y |
| Context compaction (LLM summary) | Y | Y |
| Ghost snapshots (`/snapshot`) | Y | Y |
| Undo (`/undo`) | Y | Y |
| Plan mode (`/plan`) | Y | Y |

### Tools

| Tool | Claude Code | D.U.H. |
|---|---|---|
| Read / Write / Edit / MultiEdit | Y | Y |
| Bash | Y | Y |
| Glob / Grep | Y | Y |
| WebSearch | Y | Y (Serper + Tavily) |
| WebFetch | Y | Y (taint-tagged) |
| Task (subagent spawn) | Y | Y (4 types + model select) |
| Skill | Y | Y |
| ToolSearch (deferred tools) | Y | Y |
| NotebookEdit | Y | Y |
| LSP integration | Y | Y |
| Docker | N | Y |
| Database | N | Y |
| HTTP | N | Y |
| GitHub (PR/issues) | Y | Y |
| TestImpact | N | Y |
| TodoWrite | Y | Y |
| AskUserQuestion | Y | Y |
| EnterWorktree / ExitWorktree | Y | Y |
| MemoryStore / MemoryRecall | Y | Y |
| **Total built-in tools** | 25+ | **26** |

### Providers

| Provider | Claude Code | D.U.H. |
|---|---|---|
| Anthropic (Claude) | Y | Y |
| OpenAI (API key) | N | Y |
| OpenAI ChatGPT / Codex (OAuth) | N | Y (PKCE, ADR-051/052) |
| Ollama (local, free) | N | Y |
| Stub (deterministic tests) | N | Y |
| AWS Bedrock | Y | N |
| Google Gemini | N | N |
| **Provider count** | 1 | **5** |

### MCP (Model Context Protocol)

| Feature | Claude Code | D.U.H. |
|---|---|---|
| MCP client (stdio / SSE / HTTP / WS) | Y (3 transports) | Y (4 transports, ADR-040) |
| MCP tool discovery + execution | Y | Y |
| MCP Unicode normalization (GlassWorm) | N | **Y** |
| MCP subprocess sandboxing | N | **Y** |
| MCP hash-pinning (MCPoison defense) | N | **Y** |

---

## Security Comparison

This is where the architectures diverge most significantly.

### Layer 1 — Vulnerability Monitoring

| Scanner | Claude Code | D.U.H. |
|---|---|---|
| Python SAST (ruff S-rules) | N | **Y** |
| Dependency audit (pip-audit) | N | **Y** |
| Secret scanning (detect-secrets) | N | **Y** |
| SBOM generation (CycloneDX) | N | **Y** |
| CVE-2025-59536 (project-file RCE) | N | **Y** |
| CVE-2025-54136 (MCP tool poisoning) | N | **Y** |
| CVE-2025-59532 (sandbox bypass) | N | **Y** |
| CVE-2026-35022 (command injection) | N | **Y** |
| OAuth hardening violations | N | **Y** |
| SARIF output for GitHub Code Scanning | N | **Y** |
| Exception management (alias/scope/expiry) | N | **Y** |
| CI template generation (3 tiers) | N | **Y** |
| **Total scanners** | 0 | **13** |

### Layer 2 — Runtime Hardening (ADR-054)

| Feature | Claude Code | D.U.H. |
|---|---|---|
| Taint propagation (`UntrustedStr`) | **N** | **Y** |
| Confirmation token gating on dangerous tools | **N** | **Y** |
| Lethal trifecta capability check | **N** | **Y** |
| MCP Unicode normalization | **N** | **Y** |
| Per-hook filesystem namespacing | **N** | **Y** |
| PEP 578 audit hook bridge | **N** | **Y** |
| Signed plugin manifests (TOFU + sigstore-ready) | Y (partial) | **Y** |
| Plugin trust store + revocation | N | **Y** |
| Provider differential fuzzer (property tests) | N | **Y** |

**Taint propagation** is the most significant gap. D.U.H.'s `UntrustedStr` subclass tags every
string with its origin (`user_input`, `model_output`, `tool_output`, `file_content`,
`mcp_output`, `network`) and propagates the tag through all string operations. Strings arriving
from the model that reach `Bash`, `Write`, or `Edit` are blocked unless the user has explicitly
confirmed. Claude Code has no equivalent mechanism.

**Confirmation tokens** are HMAC-bound. A model cannot fabricate a valid token; it must be issued
by the D.U.H. runtime after user approval. This prevents prompt-injection attacks that trick the
model into self-approving dangerous tool calls.

**Lethal trifecta check** requires explicit acknowledgement (`--i-understand-the-lethal-trifecta`)
before running a session that simultaneously has read-private-data, read-untrusted-input, and
network-egress capabilities — the combination that enables exfiltration attacks.

### Layer 3 — Sandboxing

| Feature | Claude Code | D.U.H. |
|---|---|---|
| macOS Seatbelt (`sandbox-exec`) | Partial | **Y** |
| Linux Landlock (syscall-level) | N | **Y** |
| Network policy (block outbound unless allowed) | N | **Y** |
| Approval mode (`suggest` / `auto-edit` / `full-auto`) | Y | Y |

---

## Architecture Comparison

| Dimension | Claude Code | D.U.H. |
|---|---|---|
| **Language** | TypeScript | Python 3.12 |
| **Architecture pattern** | Monolith + React hooks (Ink) | Hexagonal (ports & adapters) |
| **Source LOC** | ~512,000 | 24,327 |
| **Test:code ratio** | Internal | **2.3:1** (55K tests / 24K source) |
| **ADRs** | Proprietary | **54** (all public) |
| **Kernel isolation** | Entangled with React | **Strict** — kernel never imports providers |
| **Dependency injection** | React context/hooks | Explicit `Deps` dataclass |
| **Error handling** | Events + React error boundaries | Events + metadata flags |
| **Provider interface** | Monolithic adapter | Abstract `.stream()` async generator port |

Claude Code is a mature, battle-tested production system built over several years by a large team.
D.U.H. is a clean-room design that prioritises correctness, testability, and security depth over
feature breadth. The hexagonal architecture means any provider, tool, or approver can be swapped
without touching the agentic loop — a property Claude Code's monolith does not have.

---

## Cost Comparison

| | Claude Code | D.U.H. |
|---|---|---|
| **Software cost** | Free (CLI) | Free (Apache 2.0) |
| **Provider cost** | Anthropic API charges | API charges for Anthropic/OpenAI; **free** with Ollama |
| **Lock-in** | Must use Anthropic | Use any provider; switch mid-session with `/model` |
| **OAuth (no API key)** | N/A | ChatGPT Plus/Pro users can authenticate via PKCE OAuth — no API key required |

With Ollama, D.U.H. runs entirely locally with no API charges. The stub provider eliminates all
charges for CI pipelines and offline development.

---

## Hook System

| Feature | Claude Code | D.U.H. |
|---|---|---|
| PreToolUse / PostToolUse | Y | Y |
| SessionStart / SessionEnd | Y | Y |
| Hook blocking (refuse or rewrite tool input) | Y | Y (ADR-045) |
| Per-hook FS namespacing | N | **Y** (ADR-054) |
| PEP 578 audit hook bridge | N | **Y** |
| Total event types | 29+ | 29 |

---

## SDK Compatibility

D.U.H. speaks the Claude Agent SDK NDJSON (`stream-json`) protocol natively. Pass
`--output-format stream-json` and D.U.H. emits the same event envelope Claude Code emits —
making it a drop-in replacement wherever the `claude` binary is expected, including CI harness
runners and SDK test fixtures.

---

## Summary

Choose **Claude Code** if you:
- Only use Anthropic models
- Need the most mature, battle-tested production tool
- Want the widest tool surface and 60+ agent coordination types
- Prefer a polished Ink TUI with vim keybindings

Choose **D.U.H.** if you:
- Need provider flexibility (Claude, OpenAI, ChatGPT, Ollama, or all at once)
- Are running locally or in CI with no API budget (Ollama + stub provider)
- Require deep, auditable security (taint propagation, confirmation tokens, 13 scanners)
- Want open-source with documented ADRs for every design decision
- Are building on the Claude Agent SDK and need a compatible open-source harness
- Prefer a lean, testable Python codebase over a 512K-LOC TypeScript monolith
