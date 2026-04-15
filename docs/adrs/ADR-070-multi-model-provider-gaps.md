# ADR-070: Multi-Model and Provider Strategy — Competitive Gaps

**Status:** Proposed
**Date:** 2026-04-15
**Prerequisite:** [ADR-009](ADR-009-provider-adapters.md), [ADR-022](ADR-022-token-cost-control.md), [ADR-055](ADR-055-litellm-provider.md), [ADR-065](ADR-065-competitive-positioning.md)

## Context

Every major agent CLI now treats model selection and provider management as a first-class competitive axis. Users expect to pick the right model for the right task, switch mid-session without losing context, and understand what each model costs them. D.U.H.'s provider adapter layer (ADR-009) gives it a strong foundation — Anthropic, OpenAI, OpenAI ChatGPT/Codex, Ollama, and litellm adapters already work — but the layer above the adapters (model lifecycle, capability awareness, cost tracking, routing) has significant gaps compared to the field.

This ADR maps the competitive landscape, audits D.U.H.'s current state, and identifies the gaps that matter most.

## Competitive Landscape

### Claude Code
- **Provider:** Anthropic-only. No third-party models.
- **Model selection:** Opus, Sonnet, Haiku via CLI flag or `/model` command. Model changes take effect on the next turn.
- **Fallback:** Automatic downgrade to a secondary model on overload (429/529). Seamless to the user.
- **Extended thinking:** Budget-based and adaptive modes. Thinking tokens visible in UI.
- **Cost:** Token usage displayed. No per-provider cost breakdown (single provider, so unnecessary).
- **Limitations:** Vendor lock-in. No local models. No cost comparison across providers.

### GitHub Copilot CLI
- **Provider:** GitHub/OpenAI backend. Model selection available (GPT-4o, GPT-4, etc.).
- **Model selection:** Flag-based, not mid-session switchable.
- **Strengths:** Tight IDE integration, background operations.
- **Limitations:** Closed ecosystem. No local models. No provider diversity.

### Codex CLI
- **Provider:** OpenAI primary, but supports any OpenAI-compatible endpoint via `base_url` override. Local models work through compatible servers.
- **Model selection:** CLI flag. Model choice persists for session duration.
- **Strengths:** Open architecture via base_url. Local model support through compatibility layer.
- **Limitations:** OpenAI-shaped API assumed. No native adapter for non-OpenAI providers. No mid-session switching.

### Gemini CLI
- **Provider:** Google-only. Gemini 2.5 Pro and Flash.
- **Model selection:** Flag or config. Flash for speed, Pro for capability.
- **Strengths:** Tight integration with Google ecosystem. Context caching. Large context windows (1M+).
- **Limitations:** Single vendor. No fallback to non-Google models.

### OpenCode
- **Provider:** Truly provider-agnostic. Ollama, Anthropic, OpenAI, Groq, and others out of the box.
- **Model hot-switching:** `/model` command changes model mid-session with immediate effect. The next turn uses the new model.
- **Dual-agent routing:** Build agent (code changes) vs Plan agent (strategy) can use different models — e.g., Haiku for fast edits, Opus for architecture.
- **Strengths:** Best-in-class provider flexibility. Per-agent model assignment. Configuration-driven.
- **Limitations:** No automatic routing (user must choose). No cost tracking. No capability detection.

### Summary Matrix

| Capability | Claude Code | Copilot CLI | Codex CLI | Gemini CLI | OpenCode | **D.U.H. (current)** |
|------------|------------|-------------|-----------|------------|----------|----------------------|
| Multi-provider | No | No | Partial | No | Yes | **Yes** |
| Mid-session model switch | Yes | No | No | No | Yes | **Partial** |
| Overload fallback | Yes | Unknown | No | Unknown | No | **Yes** |
| Extended thinking | Yes | No | No | Yes | No | **Yes** |
| Cost tracking | Basic | No | No | No | No | **Basic** |
| Capability detection | Internal | N/A | N/A | N/A | No | **No** |
| Per-turn model routing | No | No | No | No | Manual | **No** |
| Local model optimization | N/A | N/A | Via base_url | N/A | Yes | **Basic** |

## D.U.H. Current State (What Works)

### Provider adapters (ADR-009)
Five native adapters plus litellm for 100+ providers. Each translates to the uniform event stream. Adding a provider is a single-file exercise. This is a genuine competitive advantage — only OpenCode matches it, and D.U.H.'s adapter architecture is cleaner.

### Model fallback on overload (ADR-022, engine.py)
The engine detects overloaded/rate-limited errors and retries once with `fallback_model`. This matches Claude Code's behavior and exceeds Codex CLI and OpenCode.

### `/model` command (repl.py)
The REPL `/model <name>` command updates `engine._config.model` and attempts to swap the underlying provider backend via `_switch_backend_for_model()`. The provider swap is best-effort — if auth is missing, the old backend stays active and a warning is shown. This works for basic switching.

### Cost estimation (tokens.py)
`_MODEL_PRICING` table maps models to per-million-token input/output costs. `estimate_cost()` resolves pricing via exact match, then pattern matching (opus, sonnet, haiku, gpt-4, etc.), then falls back to Sonnet pricing for unknowns. `/cost` command shows session spend.

### Context limits (tokens.py)
`MODEL_CONTEXT_LIMITS` maps known models to their context window size. `get_context_limit()` resolves via exact match, then pattern matching, then a 100K default.

## Gap Analysis

### Gap 1: Model hot-switching does not update system prompt
**Severity:** High
**What happens:** `/model gpt-4o` changes the model string and swaps the provider backend, but the system prompt — built once at REPL startup from `SYSTEM_PROMPT`, git context, brief mode, and instruction files — is never regenerated. A system prompt optimized for Claude (with Anthropic-specific phrasing, tool-use conventions, or thinking instructions) continues to be sent to an OpenAI model that interprets it differently.

**What competitors do:** Claude Code regenerates prompts per-model. OpenCode maintains separate system prompt templates per provider.

**Fix:** When `/model` triggers a provider change (not just a model change within the same provider), rebuild the system prompt. This requires the system prompt builder to accept the target model/provider as a parameter rather than being a one-shot function at startup.

### Gap 2: No provider-specific optimizations
**Severity:** Medium
**What happens:** All providers are called with the same streaming pattern. But providers have different optimal behaviors:
- OpenAI supports request batching (multiple messages in one API call for non-interactive use cases like agent subtasks).
- Anthropic supports prompt caching (`cache_control` headers) that dramatically reduces cost on repeated tool schemas.
- Ollama benefits from `keep_alive` to avoid cold-start latency on repeated calls.
- Streaming chunk sizes and timing vary — some providers buffer aggressively, others stream token-by-token.

**What competitors do:** Claude Code heavily optimizes for Anthropic's caching. Codex CLI optimizes for OpenAI's response format. Each tool optimizes for its home provider.

**Fix:** Add an optional `provider_hints` mechanism where adapters can advertise capabilities (supports_batching, supports_caching, optimal_keep_alive) and the engine can use them. Start with prompt caching for Anthropic (ADR-061) and keep_alive for Ollama.

### Gap 3: No cost tracking per provider
**Severity:** Medium
**What happens:** `_MODEL_PRICING` is a static table with hardcoded prices. When a user switches from Claude Sonnet ($3/$15 per 1M) to GPT-4o ($2.50/$10 per 1M) to Ollama ($0/$0), the cost tracker updates correctly — but it does not show a per-provider breakdown, cumulative cost by provider, or cost comparison ("this turn would have cost X with provider Y"). For users optimizing spend across providers, this is invisible.

**What competitors do:** No competitor does this well. This is a greenfield opportunity.

**Fix:**
1. Track `(provider, model, input_tokens, output_tokens, cost)` per turn instead of just aggregating totals.
2. `/cost` command shows a per-provider breakdown table.
3. Optional: show "cost if you'd used model X" comparison (requires knowing token counts only, not re-running the query).

### Gap 4: No model capability detection
**Severity:** High
**What happens:** D.U.H. sends the same request shape to every model — tools, thinking configuration, system prompt. But models differ:
- **Tool use:** Claude Sonnet/Opus and GPT-4o support tool calling. Many Ollama models and older OpenAI models do not. Sending tool schemas to a model that ignores them wastes context tokens and can cause errors.
- **Thinking/reasoning:** Only Claude Sonnet 4.6, Opus 4.6, and (differently) OpenAI o1/o3 support extended thinking. Sending `thinking` config to GPT-4o is a no-op at best, an error at worst.
- **Vision:** Some models accept image content blocks, others reject them.
- **Structured output:** Some models support JSON mode or structured output guarantees, others do not.

Currently, D.U.H. hardcodes thinking configuration in the Anthropic adapter based on model name patterns. There is no general capability registry.

**What competitors do:** Claude Code knows its models' capabilities intimately (single provider). OpenCode leaves it to the user to know what works.

**Fix:** Add a `ModelCapabilities` dataclass:
```python
@dataclass
class ModelCapabilities:
    supports_tools: bool = True
    supports_thinking: bool = False
    supports_vision: bool = False
    supports_structured_output: bool = False
    max_output_tokens: int = 4096
    context_window: int = 100_000
```
Populate from a registry (static table + pattern matching, same approach as `_MODEL_PRICING`). The engine consults capabilities before building the request — omit tools if not supported, omit thinking if not supported, warn on vision content if not supported.

### Gap 5: No automatic context-based model downgrade
**Severity:** Medium
**What happens:** If a user is on a model with a small context window (e.g., GPT-4o-mini at 128K, or an Ollama model at 8K) and the conversation grows beyond the window, D.U.H.'s auto-compaction triggers. But compaction is lossy. A smarter strategy: detect that the context is approaching the model's limit and suggest (or auto-switch to) a larger-context model.

**What competitors do:** Claude Code compacts aggressively but does not switch models. No competitor auto-switches based on context size.

**Fix:** Before compaction, check: is there a configured model with a larger context window that the user has authenticated? If so, surface a suggestion: "Context approaching 128K limit for gpt-4o-mini. Switch to claude-sonnet-4-6 (1M context) with `/model claude-sonnet-4-6`?" Auto-switching should be opt-in via config, never silent.

### Gap 6: No multi-provider per-turn routing
**Severity:** High (competitive differentiator)
**What happens:** D.U.H. uses one model for the entire session (with manual `/model` switching). But many tasks have turns of wildly different complexity:
- Turn 1: "Read this file and tell me what it does" — cheap model is fine (Haiku, GPT-4o-mini, local Ollama).
- Turn 2: "Refactor the authentication module to use OAuth2" — needs a strong model (Opus, GPT-4o).
- Turn 3: "Add a docstring to this function" — cheap model again.

Sending every turn to Opus wastes money. Sending every turn to Haiku produces poor results on complex turns.

**What competitors do:** OpenCode's dual-agent approach (Build vs Plan) is a manual version of this. Claude Code's internal routing between Sonnet and Haiku for sub-agent tasks is an automated version. No CLI tool exposes this as a user-facing feature.

**Fix:** Add a `routing` config section:
```toml
[routing]
strategy = "auto"          # "auto" | "manual" | "fixed"
simple_model = "claude-haiku-4-5"
complex_model = "claude-opus-4-6"
threshold = "medium"       # complexity threshold for upgrade
```
With `strategy = "auto"`, the engine estimates turn complexity (heuristics: length of user message, presence of code blocks, number of files referenced, keywords like "refactor"/"architect"/"design") and routes to the appropriate model. With `strategy = "manual"`, each turn shows a `[H/O]` indicator and the user can override. This is a genuine differentiator — no competitor offers it at the per-turn level.

### Gap 7: No local model optimization
**Severity:** Low-Medium
**What happens:** The Ollama adapter sends requests identically to how it would send to a cloud API. But local models have different characteristics:
- **Context window detection:** Ollama's `/api/show` endpoint reports the model's actual context window. D.U.H. currently defaults to 100K for unknown models, which may be wildly wrong (many local models are 4K-32K).
- **Quantization awareness:** A Q4_K_M quantized model behaves differently from a full-precision model. Tool calling reliability drops with heavy quantization. D.U.H. does not adjust expectations.
- **GPU/CPU detection:** Local inference speed varies by 10-100x depending on hardware. Timeout defaults should adapt.
- **Keep-alive:** Ollama supports `keep_alive` to control how long a model stays loaded in memory. Repeated calls benefit from keeping the model warm.

**What competitors do:** OpenCode has native Ollama integration with some awareness. Codex CLI delegates to the user via base_url configuration.

**Fix:**
1. Query Ollama's `/api/show` on adapter init to get actual context window, parameter count, and quantization level. Use real context window instead of default.
2. Set `keep_alive` to a reasonable value (e.g., "10m") for interactive sessions.
3. Adjust tool-calling behavior: if model is small (<7B) or heavily quantized, consider sending tools as system-prompt instructions rather than native tool schemas, which small models handle poorly.

### Gap 8: Pricing table staleness
**Severity:** Low
**What happens:** `_MODEL_PRICING` is a hardcoded dict. When providers change prices (which happens frequently — OpenAI has changed GPT-4 pricing three times), D.U.H. shows incorrect cost estimates until a code update. For litellm-proxied models, pricing is entirely absent (falls back to Sonnet pricing, which may be 10x off).

**What competitors do:** No competitor does this dynamically. All use hardcoded tables or skip cost tracking entirely.

**Fix:**
1. Short term: add a fallback pricing lookup via litellm's `model_cost` dict (litellm maintains an up-to-date pricing database).
2. Medium term: add a `/cost update` command or config option that fetches current pricing from a public API or bundled JSON file that updates more frequently than code releases.

## Prioritization

| Priority | Gap | Impact | Effort | Rationale |
|----------|-----|--------|--------|-----------|
| **P0** | Gap 1: System prompt on model switch | High | Low | Bug-level issue. Sending Anthropic-tuned prompts to OpenAI models causes degraded behavior. Small fix with large impact. |
| **P0** | Gap 4: Capability detection | High | Medium | Prevents errors (tools sent to non-tool models), enables all other gaps. Foundation piece. |
| **P1** | Gap 6: Per-turn routing | High | High | Strongest competitive differentiator. No other CLI offers this. Requires Gap 4 first. |
| **P1** | Gap 3: Per-provider cost tracking | Medium | Low | Low effort, high user value for multi-provider users. Straightforward data structure change. |
| **P2** | Gap 2: Provider-specific optimizations | Medium | Medium | Performance and cost wins, but per-provider work. Start with prompt caching (ADR-061). |
| **P2** | Gap 5: Context-based model suggestion | Medium | Medium | Nice UX, but auto-compaction already handles the hard failure case. |
| **P3** | Gap 7: Local model optimization | Low-Med | Medium | Matters only for Ollama users. Growing audience but not majority. |
| **P3** | Gap 8: Pricing staleness | Low | Low | Incremental improvement. Current pattern matching covers most cases. |

## Decision

1. **Implement Gap 1 (system prompt rebuild on provider switch) immediately.** This is a correctness bug, not a feature. The system prompt builder should accept model/provider as parameters, and `/model` should call it when the provider changes.

2. **Implement Gap 4 (ModelCapabilities registry) as the next infrastructure piece.** A static capability table with pattern matching (same approach as pricing and context limits) gives the engine enough information to make smart decisions. Every other gap benefits from this.

3. **Design Gap 6 (per-turn routing) as a follow-on ADR.** This is the highest-value competitive differentiator but requires careful UX design. The heuristic complexity estimator needs to be good enough to avoid annoying mis-routes.

4. **Implement Gap 3 (per-provider cost breakdown) alongside normal work.** Small change to the token tracking dataclass — store `(provider, model)` per turn instead of just totals.

5. **Defer Gaps 2, 5, 7, 8 to future ADRs.** Valuable but lower urgency than the foundation pieces.

## Consequences

### Positive
- D.U.H. becomes the only agent CLI with intelligent per-turn model routing — a genuine "universal harness" feature that justifies the name.
- Capability detection prevents entire classes of errors (tools on non-tool models, thinking on non-thinking models).
- System prompt rebuild on switch fixes a real correctness issue that currently degrades cross-provider experience.
- Per-provider cost tracking gives multi-provider users visibility no other tool offers.

### Negative
- ModelCapabilities registry is another static table to maintain (same maintenance burden as pricing and context limits).
- Per-turn routing adds complexity to the engine loop and introduces a new failure mode (mis-routing).
- System prompt rebuild on switch adds latency to the `/model` command (instruction file re-reading, git context).

### Risks
- Complexity estimation heuristics for per-turn routing may be unreliable, causing user frustration. Mitigation: start with `strategy = "manual"` as default, make `"auto"` opt-in until heuristics are validated.
- Provider-specific optimizations could create maintenance burden proportional to the number of providers. Mitigation: only optimize for the top 3 providers (Anthropic, OpenAI, Ollama) natively; let litellm handle the long tail.
- Capability detection may lag behind model releases. Mitigation: safe defaults (assume tools supported, assume thinking not supported) and pattern matching that errs on the side of compatibility.

## Implementation Notes

### Files affected (Gap 1 — system prompt rebuild)
- `duh/cli/repl.py` — extract system prompt building into a callable function, invoke on provider change
- `duh/ui/app.py` — same for TUI path

### Files affected (Gap 4 — capability detection)
- `duh/kernel/tokens.py` or new `duh/kernel/capabilities.py` — `ModelCapabilities` dataclass and registry
- `duh/kernel/engine.py` — consult capabilities before building API request

### Files affected (Gap 3 — per-provider cost tracking)
- `duh/kernel/tokens.py` — extend tracking to include provider/model per turn
- `duh/cli/repl.py` — update `/cost` display

### Related ADRs
- [ADR-009](ADR-009-provider-adapters.md) — Provider adapter architecture (foundation)
- [ADR-022](ADR-022-token-cost-control.md) — Token counting and cost control (extended by Gap 3)
- [ADR-055](ADR-055-litellm-provider.md) — litellm integration (Gap 8 leverages litellm's pricing data)
- [ADR-061](ADR-061-prompt-cache-optimization.md) — Prompt cache optimization (Gap 2 for Anthropic)
- [ADR-065](ADR-065-competitive-positioning.md) — Competitive positioning (this ADR deepens the provider axis)
