# ADR-075: Drop LiteLLM for first-class providers — native SDKs with full cache_control + thinking

**Status**: Proposed
**Date**: 2026-04-18
**Supersedes / revises**: ADR-009 (provider adapters), ADR-070 (multi-model provider gaps)

## Context

D.U.H. currently uses LiteLLM (`duh/adapters/litellm_provider.py`) as a single adapter for everything that isn't Anthropic, OpenAI ChatGPT OAuth, or Ollama. That covers Gemini, Groq, Cerebras, Together, Mistral, Cohere, and the long tail.

Two problems have emerged.

### 1. LiteLLM has become a supply-chain / RCE magnet

Documented incidents in 2024–2026:

- **Supply-chain compromise, March 24, 2026**: versions **1.82.7 and 1.82.8** shipped malicious payloads after [TeamPCP stole the maintainer's PyPI credentials](https://www.sonatype.com/blog/compromised-litellm-pypi-package-delivers-multi-stage-credential-stealer) via a prior Trivy compromise. Three-stage attack: credential harvest → Kubernetes lateral movement → persistent backdoor. Affected any direct or transitive install during that window.
- **CVE-2026-40217** (CVSS 8.8, RCE via bytecode rewriting at `/guardrails/test_custom_code`), affects through 2026-04-08.
- **CVE-2026-35029 / 35030** (CVSS 9.4) — auth bypass + privilege escalation in the proxy server (not exploitable in SDK-only use, but ships with the same package).
- Multiple 2024–2025 CVEs in proxy admin UI (path traversal, SSTI, SSRF).

D.U.H. currently pins `litellm>=1.0,<2` — wide enough to have pulled in the compromised 1.82.7 / 1.82.8 window for any user who did `pip install -U`. We're currently on **1.83.8** (after the malicious window). That's a rearview fix, not a forward one.

### 2. LiteLLM's normalization layer loses provider-specific fidelity

LiteLLM presents a single OpenAI-shaped API over every backend. That's convenient, but it **obscures or drops** features that matter for agent workloads:

- **Anthropic `cache_control` markers** — LiteLLM passes some through but the surface is inconsistent and undocumented
- **Gemini 2.5 `thinking_budget`** — not exposed in LiteLLM's completion params
- **Gemini explicit cache objects** (`client.caches.create()`) — LiteLLM routes all traffic through completions, no concept of a persistent cache object
- **Gemini system instructions vs. conversation system role** — LiteLLM flattens both into `messages[0].role="system"`, losing the distinction
- **Groq rate-limit headers** — stripped by the normalizer; we can't adapt to `X-RateLimit-Remaining` or tune batch size

We pay the CVE tax and lose the features we want.

### 3. Transitive dependency cost

`litellm==1.83.8` pulls **12 direct requirements**: `aiohttp`, `click`, `fastuuid`, `httpx`, `importlib-metadata`, `jinja2`, `jsonschema`, `openai`, `pydantic`, `python-dotenv`, `tiktoken`, `tokenizers`. Transitively that's ~80 packages. Each one is another supply-chain exposure surface. Most of what LiteLLM does is translation; the proxy-server machinery we don't use ships anyway.

## Decision

Write native D.U.H. adapters for every provider we actively ship as default. Keep LiteLLM as an **opt-in fallback** (`--provider litellm`) for the long tail, but no longer in the default install path.

### Native adapters to ship in this ADR

| Provider | SDK | New adapter file | Status |
|----------|-----|------------------|--------|
| Anthropic | `anthropic` | `duh/adapters/anthropic.py` | ✅ already native |
| OpenAI API | `openai` | `duh/adapters/openai.py` | ✅ already native (via ChatGPT OAuth adapter) |
| Ollama | httpx | `duh/adapters/ollama.py` | ✅ already native |
| **Gemini** | `google-genai` | `duh/adapters/gemini.py` | 🟨 **new** — this ADR |
| **Groq** | `groq` | `duh/adapters/groq.py` | 🟨 **new** — this ADR |
| Cerebras | `cerebras-cloud-sdk` | `duh/adapters/cerebras.py` | Optional, deferred |
| LiteLLM (fallback) | `litellm` | `duh/adapters/litellm_provider.py` | Demoted to opt-in |

### Feature parity contract

Every native adapter MUST provide:

1. **Streaming via `.stream(messages, tools, …) -> AsyncGenerator[dict, None]`** yielding the D.U.H. uniform event shape (`text_delta`, `tool_use`, `thinking_delta`, `usage_delta`, `done`, `error`). Same contract as `AnthropicProvider.stream()`.
2. **Tool calling** with `ParsedToolUse` parity — the differential fuzzer (ADR-054 §9) already enforces this.
3. **Cache control** where the provider supports it:
   - Anthropic: already working
   - Gemini: `client.caches.create()` for persistent caches, `cached_content=` on streaming calls
   - OpenAI: native prompt caching (they do it automatically but we expose cache_hit in usage)
4. **Thinking tokens** where the provider supports it:
   - Anthropic 4.6 Opus/Sonnet: already working
   - Gemini 2.5 Pro/Flash: `thinking_config=ThinkingConfig(thinking_budget=-1)` for dynamic, or an int budget
5. **Usage reporting** with `cache_read_input_tokens` / `cache_creation_input_tokens` surfaced in the `done` event so `CacheTracker` (PERF-6) can monitor hit rate.
6. **Taint wrapping**: every provider output wrapped via `UntrustedStr(text, TaintSource.MODEL_OUTPUT)` — same as the existing adapters.
7. **Backoff**: wrap network calls in `with_backoff(...)` from `duh.kernel.backoff` for retry on 429/5xx.
8. **Secret redaction in errors**: never print raw API key in exception messages.

### Migration strategy

1. **Add adapters** (this ADR) without removing LiteLLM:
   - Ship `GeminiProvider` and `GroqProvider`
   - Wire into provider auto-detection (`duh/providers/registry.py`): a model name starting with `gemini/` or `groq/` routes to the native adapter, not LiteLLM
   - LiteLLM stays as the catch-all for everything else

2. **Default config migration**: `~/.config/duh/config.json` still valid, but `--provider litellm` now means "use the fallback adapter" (explicit), while `gemini/gemini-2.5-pro` alone gets the native adapter.

3. **Deprecation notice in CLI**: when the user explicitly picks `--provider litellm`, emit a single-line warning: `"LiteLLM adapter is in fallback mode; check for native provider support."`

4. **Pyproject**: move `litellm` from the default install into an `extras` group (`duh-cli[litellm]`). Keep `google-genai` and `groq` as required for `[default]` / `[all]`.

5. **Sessions**: any model string containing `groq/…` or `gemini/…` automatically routes to native; no session migration needed. Bare strings (`gemini-2.5-pro` without prefix) resolve via registry the same way.

6. **Docs**: update `Provider-Setup.md` in the wiki to note the adapter per provider and the new default.

### Test parity (mandatory)

- Add `GeminiProvider` and `GroqProvider` to the existing **provider differential fuzzer** (`tests/property/test_provider_equivalence.py`). All five (Anthropic, OpenAI, Ollama, Gemini, Groq) must produce identical `ParsedToolUse` structures for the same tool_use event.
- Add VCR fixtures (ADR-070): `tests/fixtures/vcr/gemini_*.jsonl`, `tests/fixtures/vcr/groq_*.jsonl` for deterministic replay.
- Integration test: start a fresh session, send "hi", verify streaming assistant response arrives via the native adapter (not LiteLLM) with the expected event sequence.

## Consequences

### Positive

- **Supply-chain surface reduced** by ~12 transitive deps for users who don't explicitly install `[litellm]`.
- **CVE-2026-40217 / 35029 / 35030 exposure gone** by default — opt-in only.
- **Cache control works correctly** on Gemini and Anthropic (hit rate trackable in `/context`).
- **Thinking budget configurable** for Gemini 2.5 Pro — user can trade off latency vs. quality.
- **Rate limit headers visible** for Groq — allows future auto-throttle.
- **Debuggability**: direct provider errors, not wrapped through a normalizer. Error messages cite the real endpoint.
- **Each adapter is ~300 LOC max** — small, auditable, testable in isolation.

### Negative

- Three new adapters to maintain (Gemini, Groq, optional Cerebras). Low ongoing cost because the SDKs are stable and we're not chasing features.
- User confusion during migration: `gemini/gemini-2.5-pro` now resolves differently depending on what's installed. Mitigation: provider registry always prefers native over LiteLLM when both are available, with a startup log line naming the resolved adapter.
- LiteLLM will still be used by anyone who adds `--provider litellm` or pulls in a long-tail model. We still pin to `>=1.83.8` but the user has opted in.

### Neutral

- `google-genai` package is the official Google SDK for Gemini (supersedes the older `google-generativeai`). Small footprint, actively maintained. Ships grpcio + protobuf — both vendor-managed, much smaller attack surface than LiteLLM.
- `groq` is the official Groq SDK — ~1 dep (httpx, which we already have).

## References

- [LiteLLM supply-chain compromise (Sonatype, March 2026)](https://www.sonatype.com/blog/compromised-litellm-pypi-package-delivers-multi-stage-credential-stealer)
- [LiteLLM RCE CVE-2026-40217 (TheHackerWire, April 2026)](https://www.thehackerwire.com/litellm-rce-via-bytecode-rewriting-cve-2026-40217/)
- [LiteLLM triple-threat auth bypass + RCE](https://securityonline.info/litellm-security-vulnerability-auth-bypass-rce-patch/)
- [Snyk analysis of Trivy → LiteLLM backdoor chain](https://snyk.io/blog/poisoned-security-scanner-backdooring-litellm/)
- [Trend Micro research on LiteLLM supply chain](https://www.trendmicro.com/en_us/research/26/c/inside-litellm-supply-chain-compromise.html)
- ADR-009 (provider adapter contract)
- ADR-054 §9 (provider differential fuzzer)
- ADR-070 (multi-model provider gaps, VCR fixtures)
- Google `google-genai` docs: https://ai.google.dev/gemini-api/docs (native cache + thinking config)
