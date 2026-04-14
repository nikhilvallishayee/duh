# ADR-055: litellm Provider Adapter

**Status:** Accepted -- implemented 2026-04-15
**Date:** 2026-04-14
**Prerequisite:** [ADR-009](ADR-009-provider-adapters.md), [ADR-054](ADR-054-llm-specific-security-hardening.md)

## Context

D.U.H. supports five LLM providers natively: Anthropic (API key + OAuth),
OpenAI (API key), OpenAI ChatGPT/Codex (OAuth), Ollama (local), and Stub
(test/offline). Each adapter is hand-written to translate provider-specific
streaming formats into D.U.H.'s uniform event stream.

[litellm](https://github.com/BerriAI/litellm) provides a unified Python
interface to 100+ LLM providers -- Gemini, AWS Bedrock, Azure OpenAI, Groq,
Together, Fireworks, Mistral, Cohere, Deepseek, and many more -- via a single
`litellm.acompletion()` call that returns OpenAI-compatible streaming chunks.
Adding litellm as a sixth adapter gives D.U.H. instant access to every
provider litellm supports without writing individual adapters for each.

Key motivations:

1. **Breadth without maintenance burden.** Each new provider would otherwise
   require its own adapter, auth wiring, and streaming translation. litellm
   handles all of that upstream.
2. **Model string convention.** litellm uses `provider/model` strings
   (e.g., `gemini/gemini-2.5-flash`, `bedrock/claude-3-haiku-20240307`,
   `together_ai/meta-llama/Llama-3-70b-chat-hf`) which are unambiguous and
   easy to auto-detect.
3. **Env-var-based auth.** Each upstream provider uses its own env var
   (`GEMINI_API_KEY`, `AWS_ACCESS_KEY_ID`, `AZURE_API_KEY`, etc.) per
   litellm documentation. No new auth flows needed in D.U.H.

## Decision

Add a `LiteLLMProvider` adapter that:

- Wraps `litellm.acompletion(model=..., stream=True)` with the same
  `stream()` async generator contract as all other D.U.H. adapters.
- Maps litellm's OpenAI-compatible streaming chunks to D.U.H. events
  (`text_delta`, `tool_use`, `content_block_stop`, `assistant`,
  `message_stop`), reusing the same translation logic as `openai.py`.
- Provides `_parse_tool_use_block()` classmethod for ADR-054 provider
  differential fuzzer compatibility.
- Provides `_wrap_model_output()` for taint tagging (ADR-054 workstream 1).
- Handles the `tool_choice` parameter by translating D.U.H.'s vocabulary
  (`auto`, `any`, `none`, tool name) to OpenAI-format values.

### Registration and detection

- `--provider litellm` explicitly selects the adapter.
- Auto-detection: if the model string contains a `/` (litellm convention),
  `infer_provider_from_model()` returns `"litellm"`.
- The registry's `build_model_backend()` lazy-imports `LiteLLMProvider`
  and wires it up with no auth checks (litellm handles auth internally
  via env vars).

### Installation

litellm is an optional dependency:

```
pip install duh-cli[litellm]
```

The adapter gracefully degrades with an `ImportError` message if litellm
is not installed.

## Consequences

### Positive

- D.U.H. gains access to 100+ providers immediately.
- Users configure auth per litellm docs; D.U.H. needs no per-provider
  auth code for any litellm-supported backend.
- The adapter is thin (~150 lines) since litellm already returns
  OpenAI-compatible chunks.

### Negative

- litellm is a heavy dependency (pulls in tiktoken, tokenizers,
  aiohttp, etc.) -- hence optional-only.
- litellm version updates may change subtle streaming behaviors;
  the adapter must handle gracefully.
- Some litellm providers may not support tools or streaming; the
  adapter surfaces errors cleanly when this happens.

### Neutral

- The five existing native adapters remain unchanged. litellm is
  additive, not a replacement.
- Model auto-detection uses `"/"` as a heuristic; users who want a
  native provider with a slash in the model name can use `--provider`
  explicitly.
