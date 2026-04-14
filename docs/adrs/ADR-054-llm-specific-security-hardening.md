# ADR-054: LLM-Specific Security Hardening — Taint Propagation, Confirmation Tokens, Lethal Trifecta

**Status:** Proposed — 2026-04-14
**Date:** 2026-04-14
**Full design:** [`docs/superpowers/specs/2026-04-14-llm-security-hardening-design.md`](../superpowers/specs/2026-04-14-llm-security-hardening-design.md)
**Prerequisite:** [ADR-053](ADR-053-continuous-vulnerability-monitoring.md)

## Context

ADR-053 ships a pluggable vulnerability monitoring module covering dependency CVEs, Python SAST, secret scanning, and D.U.H.-specific tactical checks (repo auto-load refusal, MCP hash-pinning, OAuth adapter lint, sandbox profile lint). That is a safety net, not a defense.

The 2024–2026 CVE corpus for LLM coding agents converges on a single root cause that no off-the-shelf scanner can catch: **the agent treats model output, file content, tool output, and MCP metadata as trusted capability material when it should be treated as untrusted data.** Every published RCE chain — Claude Code CVE-2025-59536, Cursor CurXecute, MCPoison, Codex CVE-2025-59532, EchoLeak, Supabase support-ticket exfil, GlassWorm, postmark-mcp, IDEsaster — reduces to this abstraction leak. ArXiv 2509.22040 shows 84% success for prompt-injection → command-execution across Copilot/Cursor; CaMeL (arXiv 2503.18813) and DataFilter (arXiv 2510.19207) both prescribe taint propagation as the fix.

ADR-053's custom scanners catch the **tactical** surface. The **architectural** surface — how untrusted strings flow through the context builder, the compactor, and the tool dispatch layer — needs its own ADR because it is a cross-cutting refactor, not a module addition.

## Decision

Harden D.U.H.'s runtime against LLM-specific attack patterns in eight independently useful workstreams. Each workstream has a clear interface with existing code and can ship without the others, but sequencing matters because later items depend on earlier ones.

### 1. Taint-propagating `UntrustedStr` (architectural keystone)

Introduce an `UntrustedStr` subclass of `str` that carries a `source` tag (`user_input` / `model_output` / `tool_output` / `file_content` / `mcp_output` / `network`). Taint propagates through concatenation, formatting, slicing, joining, and template rendering. The context builder stamps every incoming string; the compactor, prompt builder, and message serializer preserve the tag through every transformation. Dangerous tool calls (`Bash`, `Write`, `Edit`, `WebFetch`, `Docker`, `HTTP`) require untainted-origin or an explicit user confirmation token (#2). Pattern source: Google DeepMind's CaMeL and follow-on DataFilter work.

### 2. Confirmation-token gating

Dangerous tools cannot be invoked by model-output-origin events alone. A token (HMAC-bound to `(session_id, tool, input_hash)`) must accompany the tool_use block. Tokens are minted only by events whose origin tag is `user_input` — the REPL prompt, a `/continue`, an `AskUserQuestion` response, a `duh security confirm` command. Tokens expire on the next model turn and cannot be replayed. Neutralizes the entire indirect prompt injection → dangerous tool class.

### 3. Lethal trifecta capability matrix

Simon Willison's observation: read-private-data + read-untrusted-content + network-egress = exfiltration regardless of any individual tool being safe. On `SESSION_START`, compute the enabled tool set and refuse to start a session where all three capabilities are simultaneously present unless the user explicitly acknowledges with `--i-understand-the-lethal-trifecta` or `SecurityPolicy.trifecta_acknowledged: true` in config. The check is declarative: each tool declares its capabilities in its `is_*` property block, and the session builder computes the matrix.

### 4. Signed plugin / hook manifests

The 28-event hook bus is a privileged attack surface (PromptArmor 2026 "Hijacking Claude Code via injected marketplace plugins"). Require every plugin to ship a signed manifest (sigstore-style detached signature, TOFU + revocation list) declaring the hook events it subscribes to, the tools it observes, and the filesystem paths it reads/writes. Hooks cannot subscribe to `PRE_TOOL_USE` / `POST_TOOL_USE` unless the manifest has `can_observe_tools: true` AND the user explicitly ticked it at install. Verified at plugin load via `duh.plugins.verify_manifest(plugin_path)`.

### 5. Per-hook filesystem namespacing

A malicious logging hook on `POST_TOOL_USE` can drop data in a temp file that a different hook on `PRE_TOOL_USE` reads next turn — hook-to-hook lateral information flow. Each registered hook gets a private temp directory created at registration time and revoked after its event fires. Filesystem writes go through a helper that rewrites paths into the namespace; writes outside the namespace raise. Prevents hook-to-hook leakage without hurting legitimate logging.

### 6. `sys.addaudithook` telemetry bridge (PEP 578)

Install a Python audit hook at `Py_Initialize` time that observes `open`, `socket.connect`, `subprocess.Popen`, `os.exec*`, `compile`, `exec`, `ctypes.dlopen`, and `import` of `pickle`/`marshal`/`code`. Events are **telemetry only** — PEP 578 authors are explicit that audit hooks are not an enforcement mechanism. Events feed the existing hook bus as `AUDIT` events so user-defined SIEM rules can match. Enforcement remains Seatbelt/Landlock/seccomp. Document the telemetry-vs-enforcement distinction loudly.

### 7. Provider adapter differential fuzzer

Property-based test that, given the same tool_use JSON, all five provider adapters (Anthropic, OpenAI API, OpenAI ChatGPT/Codex, Ollama, stub) produce equivalent parsed `ToolUseBlock` objects. Catches the schema-confusion class where an attacker can craft a tool call that looks benign to the router and malicious to the executor — the bug class D.U.H. already fixed under commit 8ae4f8b (bare `.type` dict access). Fuzzer runs in CI nightly; a single mismatch blocks release.

### 8. MCP subprocess sandbox + Unicode normalization

Run MCP stdio server subprocesses under the same Seatbelt/Landlock profile as the `Bash` tool — they should not have broader capabilities than the commands they expose. On MCP handshake, NFKC-normalize all tool descriptions and reject descriptions that contain zero-width characters, Unicode Tag Characters, bidi overrides, or invisible variation selectors (GlassWorm attack class). Hash-pin approvals are already handled in ADR-053's `duh-mcp-pin` custom scanner; this ADR adds the Unicode layer and subprocess isolation.

## Consequences

### Positive
- Addresses the actual root cause of every published agent RCE in the 2024–2026 corpus.
- Puts D.U.H. materially ahead of Claude Code, Cursor, Codex CLI, Continue, and Aider on LLM-specific defense — the single dimension that matters most for agent security.
- All eight workstreams are independently useful; partial delivery still ships value.
- Confirmation tokens are a clean gate that doesn't require sandbox changes.
- Audit hooks (PEP 578) give runtime visibility without changing the execution model.
- Hook manifest model applies to D.U.H.'s own security module from ADR-053, enforcing a uniform capability story — no double standard for first-party plugins.

### Negative
- Taint propagation touches ~15 existing source files (`context_builder.py`, `messages.py`, `simple_compactor.py`, `model_compactor.py`, every tool's `check_permissions`, `loop.py`, REPL, SDK runner). Significant rewrite risk.
- Confirmation tokens add a small ergonomic tax: REPL-driven sessions work unchanged, but scripted / SDK sessions must thread confirmation through their own control channel.
- Signed manifests require a key ceremony, a trust store, and revocation tooling. This is real supply-chain infrastructure, not a one-week feature.
- Estimated 8–10 weeks of work; probably 2 ADR-054 phases shipped across two release cycles rather than a single push.

### Risks
- **Taint taint-tagging breaks string methods.** Every `str` subclass is a landmine in Python — `"%s" % untrusted` may drop the tag, `join`, `strip`, `split` each need wrappers. Mitigated by exhaustive test matrix against CPython's str method surface and a `TAINTED_STR_STRICT_MODE` env var for debugging.
- **Confirmation tokens desync.** If the REPL mints a token but the engine re-runs with a different `session_id`, the token is invalid. Mitigated by making session_id part of token derivation and adding a fallback "this session was restarted, re-confirm" flow.
- **Sigstore key management complexity.** Small projects adopting D.U.H. plugins may not have signing infrastructure. Mitigated by TOFU (first-use trust) + revocation list, so adoption cost is low and enforcement grows over time.
- **`sys.addaudithook` performance.** Global audit hooks fire on every `open`/`exec`/`import`; naive implementation can halve process throughput. Mitigated by a fast-path filter (`if event not in WATCHED: return`) and benchmarking as a release gate.
- **MCP subprocess isolation breaks legitimate server features.** MCP servers that need network access (weather, search) have to declare it; others get the same profile as `Bash`. May reject some third-party servers until they update.

## Implementation Notes

Follows ADR-053 sequencing — the prerequisite scanner layer must ship first so ADR-054 has findings to reason about and the runtime hook bindings already exist.

Workstreams map to implementation phases in this order (detail in the design spec):

| Phase | Workstream | Files touched | Rough size |
|---|---|---|---|
| 7.1 | `UntrustedStr` + context builder tagging | ~15 existing + 2 new | medium |
| 7.2 | Confirmation token plumbing | loop.py, engine.py, tool.py, REPL, SDK runner | small |
| 7.3 | Lethal trifecta capability matrix | session builder + tool property audit | small |
| 7.4 | Per-hook filesystem namespacing | hooks.py, plugin loader | small |
| 7.5 | `sys.addaudithook` bridge | kernel/audit.py (new) + hook event type | small |
| 7.6 | MCP Unicode + subprocess sandbox | mcp_executor.py + mcp_transports.py + sandbox integration | medium |
| 7.7 | Signed hook manifests + TOFU store | plugins.py, new key store, sigstore-python dep | medium |
| 7.8 | Provider adapter differential fuzzer | tests/property/test_provider_equivalence.py | small |

Depends on: ADR-036 (extended hooks), ADR-037 (platform sandboxing), ADR-040 (multi-transport MCP), ADR-045 (hook blocking), ADR-052 (ChatGPT Codex adapter), ADR-053 (continuous vulnerability monitoring).

Related research cited in design spec: CaMeL (arXiv 2503.18813), DataFilter (arXiv 2510.19207), "Your AI, My Shell" (arXiv 2509.22040), OWASP LLM Top 10 2025, OWASP Top 10 for Agentic Applications (Dec 2025), MITRE ATLAS v5.4, NIST AI RMF Agentic Profile.
