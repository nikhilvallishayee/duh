# OpenCode vs D.U.H. — Detailed Comparison

**Last updated:** 2026-04-14  
**Architecture data source:** [`docs/architecture-comparison.md`](../../../docs/architecture-comparison.md)

> Note: OpenCode is archived (no longer actively maintained). This comparison documents where
> D.U.H. stands relative to the OSS Go agent that was widely used as a Claude Code alternative.

---

## TL;DR

| | OpenCode | D.U.H. |
|---|---|---|
| **Status** | Archived | Active (production alpha) |
| **Language** | Go | Python 3.12 |
| **Providers** | 5 (Claude, OpenAI, Gemini, Bedrock, Azure) | 5 (Claude, OpenAI, ChatGPT/Codex OAuth, Ollama, Stub) |
| **Built-in tools** | ~10 | 26 |
| **Security depth** | Minimal | 3-layer (13 scanners + taint + sandbox) |
| **Architecture** | Go packages | Hexagonal (ports & adapters) |
| **Tests** | Unknown | 4,160 (2.3:1 test:code ratio) |
| **ADRs / design docs** | 0 | 54 |
| **SDK protocol compatibility** | N | Y (Claude Agent SDK NDJSON) |

---

## Feature Comparison

### Core Loop

| Feature | OpenCode | D.U.H. |
|---|---|---|
| Multi-turn agentic loop | Y | Y |
| Streaming text output | Y | Y |
| Thinking/reasoning blocks | N | **Y** |
| Max turns enforcement | Y | Y |
| Error recovery in loop | Partial | Y |
| Print mode (`-p` or equivalent) | N (REPL only) | **Y** |
| Interactive REPL | Y (Bubble Tea TUI) | Y (Rich TUI) |
| Session resume | Y (SQLite) | Y |
| Context compaction (truncation) | Y | Y |
| Context compaction (LLM summary) | N | **Y** |
| Ghost snapshots (`/snapshot`) | N | **Y** |
| Undo (`/undo`) | N | **Y** |
| Plan mode (`/plan`) | N | **Y** |

### Tools

| Tool | OpenCode | D.U.H. |
|---|---|---|
| Read / Write / Edit | Y | Y |
| MultiEdit | N | **Y** |
| Bash | Y | Y |
| Glob / Grep | Y | Y |
| WebSearch | N | **Y** (Serper + Tavily) |
| WebFetch | N | **Y** (taint-tagged) |
| Task (subagent spawn) | N | **Y** |
| Skill | N | **Y** |
| ToolSearch (deferred tools) | N | **Y** |
| NotebookEdit | N | **Y** |
| LSP integration | Y | Y |
| Docker | N | **Y** |
| Database | N | **Y** |
| HTTP | N | **Y** |
| GitHub (PR/issues) | N | **Y** |
| TestImpact | N | **Y** |
| TodoWrite | N | **Y** |
| AskUserQuestion | N | **Y** |
| EnterWorktree / ExitWorktree | N | **Y** |
| MemoryStore / MemoryRecall | N | **Y** |
| **Total built-in tools** | ~10 | **26** |

### Providers

| Provider | OpenCode | D.U.H. |
|---|---|---|
| Anthropic (Claude) | Y | Y |
| OpenAI (API key) | Y | Y |
| OpenAI ChatGPT / Codex (OAuth) | N | **Y** (PKCE, ADR-051/052) |
| Ollama (local, free) | N | **Y** |
| Stub (deterministic tests/CI) | N | **Y** |
| Google Gemini | Y | N |
| AWS Bedrock | Y | N |
| Azure OpenAI | Y | N |
| Provider auto-detection | Y | Y |
| **Provider count** | 5 | **5** |

Both tools support 5 providers, but the sets differ. OpenCode covers the cloud-only providers
(Gemini, Bedrock, Azure). D.U.H. covers ChatGPT/Codex OAuth (free ChatGPT Plus/Pro access
without an API key), Ollama (fully local/free), and a deterministic stub for CI — none of which
OpenCode has.

### MCP (Model Context Protocol)

| Feature | OpenCode | D.U.H. |
|---|---|---|
| MCP client support | N | **Y** (4 transports: stdio/SSE/HTTP/WS) |
| MCP tool discovery + execution | N | **Y** |
| MCP Unicode normalization (GlassWorm) | N | **Y** |
| MCP subprocess sandboxing | N | **Y** |
| MCP hash-pinning (MCPoison defense) | N | **Y** |

---

## Security Comparison

OpenCode has minimal security tooling — basic approval prompts and some command filtering.
D.U.H. has a 3-layer security architecture specifically designed around the 2024-2026 agent
CVE corpus.

### Layer 1 — Vulnerability Monitoring

| Feature | OpenCode | D.U.H. |
|---|---|---|
| Any vulnerability scanner | N | **Y** (13 scanners, 3 tiers) |
| Python SAST | N | **Y** (ruff S-rules) |
| Dependency audit | N | **Y** (pip-audit) |
| Secret scanning | N | **Y** (detect-secrets) |
| SBOM generation | N | **Y** (CycloneDX) |
| CVE replay fixtures | N | **Y** (4 CVEs) |
| SARIF output | N | **Y** |
| Exception management | N | **Y** |
| CI template generation | N | **Y** (3 tiers) |

### Layer 2 — Runtime Hardening

| Feature | OpenCode | D.U.H. |
|---|---|---|
| Taint propagation (`UntrustedStr`) | N | **Y** (ADR-054) |
| Confirmation token gating | N | **Y** (HMAC-bound) |
| Lethal trifecta capability check | N | **Y** |
| MCP Unicode normalization | N | **Y** |
| Per-hook filesystem namespacing | N | **Y** |
| PEP 578 audit hook bridge | N | **Y** |
| Signed plugin manifests | N | **Y** (TOFU + sigstore-ready) |
| Plugin trust store + revocation | N | **Y** |
| Provider differential fuzzer | N | **Y** |

### Layer 3 — Sandboxing

| Feature | OpenCode | D.U.H. |
|---|---|---|
| macOS Seatbelt (`sandbox-exec`) | N | **Y** |
| Linux Landlock (syscall-level) | N | **Y** |
| Network policy (block outbound) | N | **Y** |
| Approval mode control | Partial | **Y** |

---

## Architecture Comparison

| Dimension | OpenCode | D.U.H. |
|---|---|---|
| **Language** | Go | Python 3.12 |
| **Source LOC** | ~42,000 | 24,327 |
| **Test LOC** | Unknown | 55,438 |
| **Test:code ratio** | Unknown | **2.3:1** |
| **Architecture pattern** | Go packages | Hexagonal (ports & adapters) |
| **Kernel isolation** | Partial | **Strict** — kernel never imports providers |
| **ADRs** | 0 | **54** |
| **Dependency injection** | Go interfaces | Explicit `Deps` dataclass |
| **Error handling** | Go errors | Events + metadata flags |
| **Property-based testing** | N | **Y** (taint + provider fuzzer) |

D.U.H. is nearly half the size of OpenCode (24K vs 42K source LOC) but ships more than twice
the tools (26 vs ~10) and far deeper security. OpenCode's Go package structure is reasonable
but undocumented — there are zero ADRs. D.U.H. documents every design decision; the 54 ADRs
serve as a complete architectural history.

---

## Hook / Event System

| Feature | OpenCode | D.U.H. |
|---|---|---|
| PreToolUse / PostToolUse | N | **Y** |
| SessionStart / SessionEnd | N | **Y** |
| Hook blocking / input rewriting | N | **Y** (ADR-045) |
| Per-hook FS namespacing | N | **Y** |
| Shell command hooks | N | **Y** |
| Function hooks (Python callable) | N | **Y** |
| **Total event types** | 0 | **29** |

OpenCode has no hook system. Tool calls are processed but there is no lifecycle event API for
external scripts or plugins to intercept.

---

## CLI UX

| Feature | OpenCode | D.U.H. |
|---|---|---|
| Print mode (`-p`) | N | **Y** |
| Interactive REPL | Y (Bubble Tea) | Y (Rich) |
| Slash commands | Partial | **21** (/help, /model, /connect, /cost, /plan, /undo, /snapshot, ...) |
| OAuth connect flow (`/connect`) | N | **Y** |
| Doctor diagnostics | N | **Y** (`duh doctor`) |
| Security CLI (`duh security`) | N | **Y** |
| JSON / NDJSON output | N | **Y** |
| Debug / verbose mode | Partial | **Y** |
| Graceful shutdown (SIGTERM) | Partial | **Y** |

---

## SDK Protocol Compatibility

| Feature | OpenCode | D.U.H. |
|---|---|---|
| Claude Agent SDK NDJSON protocol | N | **Y** |
| Drop-in `claude` binary replacement | N | **Y** |
| Control request / response (initialize) | N | **Y** |

D.U.H. emits the full Claude Agent SDK `stream-json` NDJSON event envelope, letting it replace
the `claude` binary in any CI pipeline, SDK test harness, or automation script that expects
Claude Code's output format. OpenCode has no protocol compatibility layer.

---

## Skills and Plugins

| Feature | OpenCode | D.U.H. |
|---|---|---|
| `.claude/skills/` loading | N | **Y** |
| `.duh/skills/` loading | N | **Y** |
| Frontmatter parsing | N | **Y** |
| Plugin discovery (entry_points) | N | **Y** |
| Plugin signed manifests | N | **Y** (TOFU + sigstore-ready) |
| Plugin trust store + revocation | N | **Y** |

---

## Summary

OpenCode was a useful minimal OSS agent but is now archived. D.U.H. surpasses it across
almost every dimension: more tools, more providers, a real MCP stack, a 29-event hook system,
and a security architecture that has no equivalent in any other OSS agent.

Choose **D.U.H.** over OpenCode because:
- OpenCode is no longer maintained
- D.U.H. has 26 tools vs OpenCode's ~10
- D.U.H. has MCP support (4 transports); OpenCode has none
- D.U.H. has a 29-event hook system; OpenCode has none
- D.U.H. has the deepest security story in OSS (3 layers, 13 scanners, taint propagation)
- D.U.H. has a print mode, plan mode, undo, snapshots — OpenCode had none of these
- D.U.H. has 54 documented ADRs; OpenCode had 0
- D.U.H. is Claude Agent SDK compatible; OpenCode is not

The only area where OpenCode covered more ground is cloud provider diversity (Gemini, Bedrock,
Azure) — providers D.U.H. does not yet support. If those providers are required, OpenCode's
archived codebase is a starting point, but D.U.H.'s hexagonal adapter pattern makes adding
new providers straightforward.
