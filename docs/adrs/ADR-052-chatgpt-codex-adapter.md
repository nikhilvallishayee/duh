# ADR-052: ChatGPT Codex Adapter

## Status
Accepted — 2026-04-14

## Context
Codex-family models (`gpt-5.2-codex`, `gpt-5.1-codex`, `gpt-5.1-codex-max`,
`gpt-5.1-codex-mini`) are served only through the ChatGPT subscription
backend (`https://chatgpt.com/backend-api/codex/responses`), not the
standard OpenAI API. They use the experimental "Responses" protocol, which
has a different request/response shape from both Anthropic and OpenAI Chat
Completions.

We already have `duh/adapters/openai.py` for the standard Chat Completions
API. Bolting Codex onto it would conflate two auth modes, two protocols,
and two URL families into one class. That fails Kent Beck's "reveals
intention" rule, and makes streaming-specific edge cases harder to test in
isolation.

## Decision
Add a second OpenAI adapter, `duh/adapters/openai_chatgpt.py`, targeted
specifically at the ChatGPT/Codex Responses endpoint:

- Class `OpenAIChatGPTProvider` implementing the same `.stream()` async
  generator contract every other provider adapter uses (ADR-009).
- Pulls tokens through `duh.auth.openai_chatgpt.get_valid_openai_chatgpt_oauth`
  (see ADR-051) — no direct credential handling in the adapter itself.
- Translates D.U.H.'s message/content/tool-use blocks into the Responses
  API's `input`/`output`/`function_call` shape.
- Streams SSE events (`response.output_text.delta`,
  `response.completed`, `function_call_arguments.delta`, etc.) and
  incrementally accumulates a final assistant message plus any function
  calls that came through in pieces.
- Falls back to fetching the full response by ID
  (`/responses/{id}`) if the stream ends without meaningful content —
  this guards against stream truncation and CDN buffering quirks.
- Reports streaming failures as a single assistant message with
  `metadata.is_error=True`, so the kernel loop can surface them without
  special-casing this provider.

Model selection (API key vs. ChatGPT subscription) is handled one layer up,
in `duh/providers/registry.py::resolve_openai_auth_mode`, which inspects
the requested model name, available OAuth, and available API keys.

## Consequences

### Positive
- Codex models work inside D.U.H. with ChatGPT Plus/Pro credentials.
- The adapter is isolated: `duh/adapters/openai.py` stays small and
  API-key-only; changes to one don't risk breaking the other.
- Function-call streaming is handled with per-item accumulation that
  tolerates out-of-order deltas.
- Errors surface through the normal assistant-message-with-is_error
  channel, so existing error-handling code paths apply.

### Negative
- One more adapter to keep up with OpenAI schema changes. We accept the
  duplication here because the two protocols genuinely diverge.
- The ChatGPT backend URL, SSE event names, and "Responses" schema are
  experimental — breakage risk is real. Mitigated by keeping the adapter
  small (~580 LOC) and covered by unit tests against canned SSE fixtures.

### Risks
- OpenAI could change the SSE event schema between release trains. Tests
  against fixture payloads catch the most common regressions.
- The ChatGPT backend rate-limits aggressively. The kernel's existing
  `_is_fallback_error` path in `engine.py` kicks in on overload and swaps
  to the configured fallback model — no adapter-specific retries needed.

## Implementation Notes
- File: `duh/adapters/openai_chatgpt.py`.
- Depends on: `duh.auth.openai_chatgpt` (ADR-051), `duh.kernel.messages`.
- Registered in `duh/providers/registry.py::build_model_backend` under the
  `chatgpt` auth mode for the `openai` provider.
- Unit tests in `tests/unit/test_openai_chatgpt_adapter.py` cover happy
  path streaming, function-call accumulation, fetch-by-id fallback, and
  error surfacing.
- Related: ADR-009 (provider adapter contract), ADR-051 (OAuth auth).
