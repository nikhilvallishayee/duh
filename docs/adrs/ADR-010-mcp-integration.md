# ADR-010: MCP Integration

**Status**: Accepted  
**Date**: 2026-04-06

## Context

The Model Context Protocol (MCP) is an open standard for connecting AI assistants to external tools, resources, and prompts via a JSON-RPC transport. Claude Code uses MCP as its primary extension mechanism: every MCP server exposes tools that get merged into the main tool pool as first-class citizens.

D.U.H. needs the same capability. MCP tools must be indistinguishable from native tools at the kernel level --- the kernel calls `ToolExecutor.run()` and does not care whether the tool is a Python class or an MCP server on the other end of a stdio pipe.

### What MCP provides

| Capability | Description |
|------------|-------------|
| **Tools** | Functions the model can call (JSON Schema input, JSON output) |
| **Resources** | Read-only data the model can reference (files, DB rows, API results) |
| **Prompts** | Reusable prompt templates the server can offer |

D.U.H. v0.1 focuses on **tools only**. Resources and prompts are future work.

### How Claude Code does it

Claude Code's `client.ts` (~700 lines) creates an `@modelcontextprotocol/sdk` `Client`, connects via `StdioClientTransport`, `SSEClientTransport`, or `StreamableHTTPClientTransport`, calls `client.listTools()` to discover tools, wraps each as an `MCPTool` (a `Tool` implementation), and merges them into the global tool pool. The `MCPConnectionManager` React context manages lifecycle (connect, reconnect, toggle, cleanup).

Key types from `types.ts`:
- Transport types: `stdio | sse | http | ws | sdk`
- Server states: `connected | failed | needs-auth | pending | disabled`
- Config: `McpServerConfig` discriminated union keyed on `type`

### What D.U.H. simplifies

Claude Code's MCP layer is ~2000 LOC across 20 files because it handles React context, OAuth, IDE transports, proxy routing, SSRF guards, and binary blob persistence. D.U.H. strips this to the essential: connect to stdio servers, discover tools, execute tool calls, handle errors. ~150 LOC.

## Decision

### 1. MCPExecutor is a ToolExecutor adapter

`duh/adapters/mcp_executor.py` implements the `ToolExecutor` port. It connects to one or more MCP servers, discovers their tools, and dispatches `run()` calls to the right server via the MCP protocol.

### 2. Transport: stdio first

Stdio is the simplest and most common MCP transport (every `npx` server, every local binary). SSE and HTTP transports are future work. The config format matches Claude Code's `mcpServers` shape:

```python
{
    "mcpServers": {
        "my-server": {
            "command": "npx",
            "args": ["-y", "my-mcp-server"],
            "env": {"API_KEY": "..."}
        }
    }
}
```

### 3. Tools are first-class

Once connected, MCP tools are merged into the tool pool with a `mcp__<server>__<tool>` naming convention. The kernel sees them as regular tools. The executor handles the MCP protocol details.

### 4. Graceful degradation

The `mcp` Python package is an optional dependency. If not installed, `MCPExecutor` raises a clear error on construction. The rest of D.U.H. works fine without it.

### 5. No React, no OAuth, no IDE transports

Claude Code's MCP layer handles IDE WebSocket connections, OAuth flows, SSRF guards, and React context. D.U.H. does none of that. A server is either connected or failed. Reconnection is explicit.

## Config format

```json
{
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {}
        },
        "github": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "ghp_..."}
        }
    }
}
```

## Architecture

```
Kernel
  |
  ToolExecutor.run("mcp__github__create_issue", {...})
  |
  MCPExecutor
  |  - looks up server "github" from tool name prefix
  |  - calls server.call_tool("create_issue", {...})
  |
  MCP Client (stdio subprocess)
  |
  MCP Server (npx process)
```

## Consequences

- Adding an MCP server = one config entry, zero code
- MCP tools appear alongside native tools in the tool list
- The kernel never knows or cares about MCP internals
- Optional dependency: `pip install duh-cli[mcp]` for MCP support
- Future transports (SSE, HTTP) are additive, not breaking
