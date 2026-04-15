# ADR-009: Provider Adapters

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-07

## Context

Every LLM provider has its own SDK, streaming format, tool calling convention, error shapes, and authentication method. Single-provider harnesses import the SDK directly throughout the codebase. D.U.H. isolates provider specifics behind the `ModelProvider` port (ADR-003) so the kernel never sees a provider SDK.

### Patterns from provider integrations

1. **Streaming**: Provider SDKs yield event objects with different shapes and naming conventions.

2. **Thinking**: Some models support extended thinking with configurable token budgets or adaptive modes.

3. **Tool schemas**: Internal tool objects must be translated to each provider's expected format.

4. **Token counting**: API-based exact counting with rough estimation (chars / 4) as fallback.

5. **Error handling**: Map API errors to user-friendly messages. Retry with exponential backoff. Handle `prompt_too_long`, `rate_limit`, `overloaded`, `authentication_error`.

6. **Beta headers**: Manage SDK beta feature flags as providers evolve.

## Decision

Implement provider adapters that translate each provider's native format into D.U.H.'s uniform event stream. Each adapter is a class implementing the `ModelProvider` protocol.

### Uniform Event Format

All providers produce the same event types:

```python
{"type": "text_delta", "text": "..."}           # streaming text
{"type": "thinking_delta", "text": "..."}       # streaming thinking
{"type": "input_json_delta", "partial_json": "..."} # streaming tool input
{"type": "content_block_start", ...}            # block boundary
{"type": "content_block_stop", ...}             # block boundary
{"type": "assistant", "message": Message}       # complete response
```

The `assistant` event carries a `Message` dataclass with:
- `role`: always "assistant"
- `content`: list of normalized content blocks (dicts, not SDK objects)
- `metadata`: `{model, stop_reason, usage: {input_tokens, output_tokens}}`

### Anthropic Adapter

```python
class AnthropicProvider:
    def __init__(self, api_key, model="claude-sonnet-4-6", max_retries=2, timeout=600):
        import anthropic  # SDK import only here
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
```

Translation map (Anthropic SDK → D.U.H. events):

| SDK Event | D.U.H. Event | Notes |
|-----------|-------------|-------|
| `message_start` | (internal) | Extract usage |
| `content_block_start` | `content_block_start` | Pass through |
| `content_block_delta[text_delta]` | `text_delta` | Extract `.delta.text` |
| `content_block_delta[thinking_delta]` | `thinking_delta` | Extract `.delta.thinking` |
| `content_block_delta[input_json_delta]` | `input_json_delta` | Extract `.delta.partial_json` |
| `content_block_delta[signature_delta]` | (ignored) | Not needed |
| `content_block_stop` | `content_block_stop` | Pass through |
| `message_delta` | (internal) | Update output_tokens |
| `message_stop` | (internal) | — |
| Final message | `assistant` | Normalized to Message |

Thinking configuration:
- `{type: "adaptive"}` for Opus 4.6 and Sonnet 4.6 (model name contains "opus-4-6" or "sonnet-4-6")
- `{type: "enabled", budget_tokens: N}` for other models when thinking is requested
- Omitted when thinking is disabled

Error handling:
- SDK exceptions → `assistant` event with `metadata.is_error = True`
- Error text preserved for the CLI error interpreter (ADR-008)

### Ollama Adapter

```python
class OllamaProvider:
    def __init__(self, model="qwen2.5-coder:1.5b", base_url="http://localhost:11434"):
        # No SDK — uses httpx directly
```

Translation map (Ollama HTTP → D.U.H. events):

| Ollama Response | D.U.H. Event | Notes |
|----------------|-------------|-------|
| `{"message": {"content": "..."}}` | `text_delta` | Streaming chunk |
| `{"message": {"tool_calls": [...]}}` | (accumulated) | Collected for final message |
| `{"done": true}` | `assistant` | Builds final Message |
| `{"error": "..."}` | `assistant` (error) | `metadata.is_error = True` |
| HTTP 404 | `assistant` (error) | "Model not found. Pull it first" |
| Connection refused | `assistant` (error) | "Is Ollama running? ollama serve" |

Tool calling format translation:
- D.U.H. tools → `{"type": "function", "function": {name, description, parameters}}`
- Ollama tool_calls → D.U.H. `tool_use` blocks with synthetic IDs

### Future Adapters

| Adapter | SDK | Status | Notes |
|---------|-----|--------|-------|
| `openai.py` | `openai` | Future | OpenAI, Azure OpenAI |
| `litellm.py` | `litellm` | Future | 100+ models via unified interface |
| `hf_local.py` | `transformers` | Future | Local HuggingFace models |
| `bedrock.py` | `boto3` | Future | AWS Bedrock |
| `vertex.py` | `google-cloud` | Future | Google Vertex AI |

Each future adapter follows the same pattern:
1. Import the provider SDK in `__init__`
2. Translate messages to provider format in `stream()`
3. Parse streaming response into uniform events
4. Yield final `assistant` event with normalized `Message`
5. Handle provider-specific errors with actionable messages

### Content Block Normalization

All adapters normalize SDK content block objects to plain dicts before yielding:

```python
def _block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict): return block
    if hasattr(block, "model_dump"): return block.model_dump()  # Pydantic
    # Manual extraction for non-Pydantic SDK objects
    d = {"type": getattr(block, "type", "unknown")}
    for attr in ("text", "thinking", "id", "name", "input", "signature"):
        val = getattr(block, attr, None)
        if val is not None:
            d[attr] = val
    return d
```

### API Message Translation

Adapters sanitize outgoing messages to only include fields the provider's API accepts:

```python
ALLOWED = {
    "text": {"type", "text"},
    "tool_use": {"type", "id", "name", "input"},
    "tool_result": {"type", "tool_use_id", "content", "is_error"},
    "thinking": {"type", "thinking", "signature"},
}
```

This prevents D.U.H. metadata fields (id, timestamp, etc.) from leaking into API requests and causing validation errors.

## Consequences

- Adding a new provider = one file that imports their SDK and translates to/from uniform events
- The kernel never imports any provider SDK — all translation lives in adapters
- Provider-specific quirks (thinking format, tool calling convention, beta headers) are isolated
- Error messages are actionable — each adapter translates errors into "what to do" hints
- The same tool definitions work across all providers (translated per-adapter)
- Testing an adapter requires only mocking its SDK client, not the kernel

## Implementation Notes

Concrete provider adapters on main:
- `duh/adapters/anthropic.py` — Anthropic Messages API (streaming, thinking, tool use)
- `duh/adapters/openai.py` — OpenAI Chat Completions / Azure / any OpenAI-compatible `base_url`
- `duh/adapters/openai_chatgpt.py` — ChatGPT/Codex Responses API (ADR-052)
- `duh/adapters/ollama.py` — Ollama local HTTP
- `duh/adapters/stub_provider.py` — deterministic stub for tests

Selection and authentication are handled by `duh/providers/registry.py` in conjunction
with `duh/auth/` (ADR-051).
