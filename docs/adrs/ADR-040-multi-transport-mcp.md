# ADR-040: Multi-Transport MCP

**Status**: Proposed  
**Date**: 2026-04-08

## Context

D.U.H.'s MCP integration (ADR-010) only supports stdio transport — the MCP server runs as a subprocess and communicates via stdin/stdout. This limits MCP to local processes.

Real-world MCP usage increasingly requires remote transports:
- **SSE (Server-Sent Events)**: The MCP specification's standard HTTP transport for remote servers
- **HTTP**: Simple request/response for stateless tool servers
- **WebSocket**: Bidirectional streaming for high-throughput or long-running tools

The reference TS harness supports stdio and SSE natively. The MCP specification defines SSE as the standard remote transport.

## Decision

Add a transport abstraction layer to the MCP client:

### Transport Interface

```python
class MCPTransport(Protocol):
    async def connect(self) -> None: ...
    async def send(self, message: dict) -> None: ...
    async def receive(self) -> dict: ...
    async def close(self) -> None: ...
    
    @property
    def is_connected(self) -> bool: ...
```

### Implementations

| Transport | Class | Use Case |
|-----------|-------|----------|
| stdio | `StdioTransport` | Local subprocess (existing) |
| SSE | `SSETransport` | Remote MCP servers (spec-standard) |
| HTTP | `HTTPTransport` | Stateless tool endpoints |
| WebSocket | `WebSocketTransport` | Bidirectional streaming |

### Configuration

Extend `MCPServerConfig` with transport fields:

```python
@dataclass
class MCPServerConfig:
    name: str
    command: str | None = None        # stdio: subprocess command
    transport: str = "stdio"          # "stdio" | "sse" | "http" | "websocket"
    url: str | None = None            # Remote URL for non-stdio transports
    headers: dict[str, str] | None = None  # Auth headers for remote
    timeout: float = 30.0             # Connection timeout
```

Config file example (`duh.toml`):

```toml
[[mcp.servers]]
name = "local-tools"
command = "npx @some/mcp-server"

[[mcp.servers]]
name = "remote-search"
transport = "sse"
url = "https://mcp.example.com/sse"
headers = { Authorization = "Bearer ${MCP_TOKEN}" }
```

### Progressive Connection Batching

On startup, connect to MCP servers in parallel with a concurrency limit of 5. Stdio servers start first (fastest), then remote transports. If a remote server fails to connect within its timeout, it is marked as `unavailable` and its tools are excluded — the session starts without waiting.

### SSE Implementation Notes

SSE transport follows the MCP specification:
- POST requests for client-to-server messages
- SSE stream for server-to-client messages
- Session ID tracked via response headers
- Automatic reconnection on stream interruption (integrates with ADR-032)

## Consequences

### Positive
- Unlocks remote MCP servers — the primary growth vector for MCP ecosystem
- SSE support follows the MCP specification directly
- Progressive connection means slow remote servers don't block startup
- Transport abstraction makes future transports trivial to add

### Negative
- Remote transports add network dependency — latency and failures become more common
- SSE and WebSocket require additional dependencies (`aiohttp` or `httpx`)
- Auth header management adds configuration complexity

### Risks
- Remote MCP servers introduce a data exfiltration path — mitigated by sandboxing (ADR-037) network policy and explicit user configuration
- SSE reconnection storms under poor network — mitigated by exponential backoff in ADR-032
