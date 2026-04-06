# ADR-003: Ports and Adapters

**Status**: Accepted  
**Date**: 2026-04-07

## Context

The kernel must work with any LLM provider, any tool transport, any persistence backend, and any approval mechanism. The Clean Architecture dependency rule: source code dependencies point inward. The kernel never imports a provider SDK.

**Critical distinction**: providers (Anthropic, OpenAI, Ollama) each have their own SDK, streaming format, tool calling convention, and error shapes. None of them provide a uniform interface. The **port** defines what D.U.H. expects; the **adapter** is the wrapper WE write to translate each provider's native format into our uniform events.

```
Provider SDK (their code, their format)
    ↓
Adapter (our wrapper, translates to our format)
    ↓
Port (our interface contract)
    ↓
Kernel (consumes uniform events)
```

## Decision

Define 5 ports (abstract interfaces). Each port has one or more adapters (concrete wrappers we write).

### Ports

Ports define what the kernel EXPECTS. They are abstract protocols. The kernel depends ONLY on these — never on provider SDKs.

```python
# ports/provider.py — what WE expect from any LLM
class ModelProvider(Protocol):
    async def stream(self, messages, system_prompt, tools, **kwargs) -> AsyncGenerator[dict]:
        """Stream model responses in D.U.H.'s uniform event format."""

# ports/executor.py — what WE expect from tool execution
class ToolExecutor(Protocol):
    async def run(self, tool_name, input, context) -> str | dict:
        """Execute a tool by name. Returns the result."""

# ports/approver.py — what WE expect from permission checks
class ApprovalGate(Protocol):
    async def check(self, tool_name, input) -> dict:
        """Check if tool use is approved. Returns {allowed: bool, reason?: str}."""

# ports/store.py — what WE expect from persistence
class SessionStore(Protocol):
    async def save(self, session_id, messages) -> None: ...
    async def load(self, session_id) -> list | None: ...
    async def list_sessions(self) -> list: ...
    async def delete(self, session_id) -> bool: ...

# ports/context.py — what WE expect from context management
class ContextManager(Protocol):
    async def compact(self, messages, token_limit) -> list: ...
    def estimate_tokens(self, messages) -> int: ...
```

### Adapters (wrappers WE write)

Each adapter imports a provider SDK and translates to/from our port interface.

| Port | Adapter | What it wraps | Status |
|------|---------|---------------|--------|
| ModelProvider | `adapters/anthropic.py` | `anthropic` Python SDK → D.U.H. events | Next |
| ModelProvider | `adapters/openai.py` | `openai` Python SDK → D.U.H. events | Future |
| ModelProvider | `adapters/ollama.py` | Ollama HTTP API → D.U.H. events | Future |
| ModelProvider | `adapters/litellm.py` | litellm (100+ models) → D.U.H. events | Future |
| ToolExecutor | `adapters/native_executor.py` | Python Tool objects → results | Next |
| ToolExecutor | `adapters/mcp_executor.py` | MCP server protocol → results | Future |
| ApprovalGate | `adapters/auto_approver.py` | Always allows (sandbox mode) | Next |
| ApprovalGate | `adapters/interactive_approver.py` | Terminal y/n prompt | Next |
| ApprovalGate | `adapters/rule_approver.py` | Config-based deny rules | Future |
| SessionStore | `adapters/file_store.py` | JSONL files on disk | Next |
| SessionStore | `adapters/sqlite_store.py` | SQLite database | Future |
| ContextManager | `adapters/simple_compactor.py` | Token estimation + truncation | Next |

### Example: Anthropic adapter translates SDK → uniform events

```python
# adapters/anthropic.py
class AnthropicProvider:
    """Wraps the anthropic Python SDK to produce D.U.H. uniform events."""
    
    def __init__(self, api_key: str):
        import anthropic  # SDK import ONLY in adapter, never in kernel
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
    
    async def stream(self, *, messages, system_prompt, tools, **kwargs):
        # 1. Translate D.U.H. messages → Anthropic API format
        api_messages = self._to_api_format(messages)
        
        # 2. Call Anthropic's streaming API (their format)
        async with self._client.messages.stream(...) as stream:
            async for event in stream:
                # 3. Translate Anthropic events → D.U.H. uniform events
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield {"type": "text_delta", "text": event.delta.text}
                    elif event.delta.type == "thinking_delta":
                        yield {"type": "thinking_delta", "text": event.delta.thinking}
                # ... etc
        
        # 4. Yield final assistant message in D.U.H. format
        yield {"type": "assistant", "message": self._to_duh_message(final)}
```

## Consequences

- Adding a new LLM provider = writing one adapter file that imports their SDK
- The kernel is tested with fake adapters (zero network calls)
- No circular dependencies between kernel and adapters
- Each adapter can be developed, tested, and released independently
- Provider-specific quirks (tool calling format, thinking tokens, beta headers) are isolated in adapters
- Users choose their provider at runtime, not compile time
