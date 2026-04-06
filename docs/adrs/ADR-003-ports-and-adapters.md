# ADR-003: Ports and Adapters

**Status**: Accepted  
**Date**: 2026-04-07

## Context

The kernel must work with any LLM provider, any tool transport, any persistence backend, and any approval mechanism. The Clean Architecture dependency rule: source code dependencies point inward. The kernel never imports a provider SDK.

## Decision

Define 5 ports (abstract interfaces). Each port has one or more adapters (concrete implementations).

### Ports

```python
# ports/provider.py
class ModelProvider(Protocol):
    async def stream(self, messages, system_prompt, tools, **kwargs) -> AsyncGenerator[dict]:
        """Stream model responses. Yields text_delta, tool_use, assistant events."""

# ports/executor.py  
class ToolExecutor(Protocol):
    async def run(self, tool_name, input, context) -> str | dict:
        """Execute a tool by name. Returns the result."""

# ports/approver.py
class ApprovalGate(Protocol):
    async def check(self, tool_name, input) -> dict:
        """Check if tool use is approved. Returns {allowed: bool, reason?: str}."""

# ports/store.py
class SessionStore(Protocol):
    async def save(self, session_id, messages) -> None:
        """Persist a conversation."""
    async def load(self, session_id) -> list[Message] | None:
        """Load a conversation."""

# ports/context.py
class ContextManager(Protocol):
    async def compact(self, messages, token_limit) -> list[Message]:
        """Compact messages to fit within token limit."""
```

### Adapters (planned)

| Port | Adapter | Status |
|------|---------|--------|
| ModelProvider | `adapters/anthropic.py` | Next |
| ModelProvider | `adapters/openai.py` | Future |
| ModelProvider | `adapters/ollama.py` | Future |
| ToolExecutor | `adapters/native_executor.py` | Next |
| ToolExecutor | `adapters/mcp_executor.py` | Future |
| ApprovalGate | `adapters/auto_approver.py` | Next |
| ApprovalGate | `adapters/interactive_approver.py` | Next |
| SessionStore | `adapters/file_store.py` | Next |
| SessionStore | `adapters/sqlite_store.py` | Future |
| ContextManager | `adapters/simple_compactor.py` | Next |

## Consequences

- Adding a new LLM provider = one file implementing ModelProvider
- Testing the kernel = injecting fake adapters via Deps
- No circular dependencies between kernel and adapters
- Each adapter can be developed and released independently
