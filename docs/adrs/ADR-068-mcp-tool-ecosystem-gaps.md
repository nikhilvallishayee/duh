# ADR-068: MCP and Tool Ecosystem — Competitive Gaps

**Status:** Proposed — 2026-04-15
**Date:** 2026-04-15
**Related:** ADR-004 (tool protocol), ADR-010 (MCP integration), ADR-014 (plugin architecture), ADR-018 (progressive disclosure), ADR-025 (ecosystem tools), ADR-032 (MCP session management), ADR-040 (multi-transport MCP), ADR-065 (competitive positioning)

## Context

MCP (Model Context Protocol) is becoming the standard for tool extensibility across AI coding agent CLIs. Every major agent now either supports MCP natively or has a comparable extension mechanism. The competitive landscape has moved beyond "does it support MCP?" to "how well does it support MCP?"

D.U.H. implemented MCP in three waves: basic stdio transport (ADR-010), session lifecycle management (ADR-032), and multi-transport SSE/HTTP/WebSocket (ADR-040). This gives us functional parity for connecting to MCP servers and executing tools. But functional parity is table stakes. The gaps are in the operational layer: validation, caching, monitoring, metrics, and configuration granularity.

This ADR catalogs what competitors do, identifies where D.U.H. falls short, and proposes a prioritized remediation plan.

## Competitive Landscape

### Claude Code

MCP is the primary extension mechanism. Key capabilities:

- **Three transports**: stdio, SSE, and streamable HTTP (the latest MCP specification transport that supersedes SSE for new deployments)
- **Auto-discovery**: On startup, connects to all configured MCP servers and discovers tools. Tools appear in the system prompt alongside native tools with no user action required.
- **Tool pinning**: Users can pin specific MCP tools to always be eagerly loaded (never deferred), ensuring they appear in every conversation's tool list regardless of the deferred-tools system.
- **Schema validation**: Tool inputs are validated against the JSON Schema returned by the MCP server's `listTools` response before dispatching the call. Malformed inputs are rejected client-side with a clear error, saving a round trip.
- **Scoped configuration**: MCP servers can be configured at project level (`.claude/settings.json`) or user level (`~/.claude/settings.json`), with project configs taking precedence.

### GitHub Copilot CLI

Takes a different approach — no MCP, but a rich extension marketplace:

- **Extension marketplace**: Extensions are installed from GitHub's marketplace. Each extension can provide tools, slash commands, and context providers.
- **Built-in tools**: Ships with a curated set of tools (file ops, terminal, GitHub integration) that cover most workflows without extensions.
- **Extension sandboxing**: Extensions run in a restricted environment with explicit capability declarations (file access, network, etc.).
- **Proprietary protocol**: Uses its own tool protocol rather than MCP. Extensions must target the Copilot extension API specifically.

### Codex CLI

Focuses on simplicity and sandboxing:

- **Custom tool definitions**: Tools defined in configuration files with JSON Schema inputs. No MCP.
- **Sandboxed execution**: All tool execution happens inside a sandbox (container or restricted subprocess). Tools cannot escape the sandbox boundary.
- **Stateless tools**: Each tool invocation is independent — no persistent connections, no session management. Simpler model but limits what tools can do.
- **Network isolation**: By default, tools have no network access. Network must be explicitly granted per tool or per session.

### Gemini CLI

Recently added MCP support alongside its own extension system:

- **MCP support**: Connects to MCP servers via stdio and SSE transports. Configuration in `GEMINI.md` or project settings.
- **Extension system**: Separate from MCP — extensions are installed as packages and provide tools via Gemini's own protocol.
- **Per-project tool configuration**: Tools can be enabled/disabled per project directory. Gemini auto-switches tool sets when you change directories.
- **Automatic reconnection**: Handles MCP server restarts transparently, similar to D.U.H.'s ADR-032 approach.

### OpenCode

The most configuration-driven approach:

- **Custom tool configs in opencode.json**: Every tool (native and custom) is configured via a single JSON file. Custom tools define their command, schema, timeout, and description.
- **Per-tool timeout configuration**: Each tool can have its own timeout specified in the config file, overriding the global default.
- **Tool result display**: Configurable how tool results are shown — full output, truncated, or summary.
- **Provider-agnostic tool execution**: Tools are decoupled from the AI provider, same as D.U.H.

## D.U.H. Current State

### What works

| Capability | Status | ADR |
|------------|--------|-----|
| MCP stdio transport | Implemented | ADR-010 |
| MCP SSE transport | Implemented | ADR-040 |
| MCP HTTP transport | Implemented | ADR-040 |
| MCP WebSocket transport | Implemented | ADR-040 |
| Tool naming convention (`mcp__server__tool`) | Implemented | ADR-010 |
| MCP session expiry detection + auto-reconnect | Implemented | ADR-032 |
| Circuit breaker (degraded server exclusion) | Implemented | ADR-032 |
| Unicode safety validation at handshake | Implemented | ADR-010 impl |
| MCP subprocess sandboxing (Seatbelt/Landlock) | Implemented | ADR-010 impl |
| Progressive tool disclosure (deferred loading) | Implemented | ADR-018 |
| Plugin system (entry_points + directory) | Implemented | ADR-014 |
| 25 built-in native tools | Implemented | ADR-004, ADR-025 |
| Per-tool timeout defaults (hardcoded dict) | Implemented | ADR-004 |
| Parallel MCP server connection at startup | Implemented | ADR-040 |

### What is missing

Seven gaps, ordered by impact.

## Gap Analysis

### Gap 1: MCP Streamable HTTP Transport

**What it is**: The MCP specification introduced streamable HTTP as a successor to SSE for remote servers. It uses standard HTTP POST with optional streaming responses via chunked transfer encoding. Unlike SSE (which requires a persistent GET connection plus separate POST endpoints), streamable HTTP is a single bidirectional channel that works through standard HTTP infrastructure — proxies, load balancers, CDNs — without special configuration.

**Who has it**: Claude Code supports it natively. The MCP Python SDK added `StreamableHTTPTransport` in v1.3.

**D.U.H. status**: ADR-040 implemented SSE, plain HTTP, and WebSocket. Streamable HTTP was not in the MCP spec when ADR-040 was written.

**Impact**: Medium-high. New MCP servers are increasingly deploying streamable HTTP as their primary remote transport. SSE will work for existing servers but new ones may only offer streamable HTTP.

**Proposed fix**: Add `StreamableHTTPTransport` to `duh/adapters/mcp_transports.py`. Configuration: `transport = "streamable-http"` in `duh.toml`. The transport protocol interface (ADR-040) is already generic enough — this is an additive implementation.

### Gap 2: Tool Schema Validation at Registration

**What it is**: When an MCP server returns tool definitions via `listTools`, the input schemas are accepted as-is and passed to the LLM. No validation that the schemas are valid JSON Schema, that required fields are present, or that the schema structure is well-formed. Invalid schemas cause confusing runtime failures when the model generates a tool call that the server rejects.

**Who has it**: Claude Code validates MCP tool schemas at registration time. If a schema is malformed, the tool is excluded with a warning rather than silently failing later.

**D.U.H. status**: The `MCPExecutor.connect()` method stores `inputSchema` from MCP tools directly without validation. Unicode content is validated (suspicious characters in descriptions), but schema structure is not.

**Impact**: Medium. Bad schemas from buggy MCP servers cause failures that are hard to diagnose — the error surfaces as a model retry loop, not as a clear "this tool has a broken schema" message.

**Proposed fix**: Add a `_validate_tool_schema(schema: dict) -> list[str]` function in `mcp_executor.py` that checks:
1. Top-level `type` is `"object"` (MCP requirement)
2. `properties` is a dict (if present)
3. `required` entries exist in `properties`
4. No unsupported JSON Schema keywords that the LLM can't handle
5. Nested `$ref` pointers are resolved or rejected

Tools with invalid schemas are logged as warnings and excluded from the tool index, same as the Unicode validation pattern.

### Gap 3: Tool Result Caching

**What it is**: Many tool calls are idempotent within a short window. Reading the same file twice in 10 seconds returns the same content. Glob patterns on an unchanged directory return the same results. Caching these results avoids redundant I/O and network calls, especially for MCP tools where each call is a subprocess or HTTP round trip.

**Who has it**: Claude Code caches Read results for unchanged files (stat-based invalidation). Copilot CLI stores large tool outputs to disk and references them by ID.

**D.U.H. status**: No caching. Every tool call executes from scratch. The `NativeExecutor.run()` method calls the tool, truncates output, and returns. The `MCPExecutor.run()` method dispatches to the MCP server every time.

**Impact**: Medium. File re-reads are the most common case — the model frequently reads the same file multiple times in a conversation. Each read is a full I/O operation. For MCP tools, the cost is higher (subprocess IPC or HTTP round trip).

**Proposed fix**: Add a `ToolResultCache` class with:
- Key: `(tool_name, frozen_input_dict)` tuple
- Value: `(result, timestamp, invalidation_key)`
- TTL: Configurable per tool (default 30s for Read/Glob, 0 for Bash/Write/Edit)
- Invalidation: For file tools, use `os.stat()` mtime. For other tools, TTL-only.
- Scope: Per-session (cleared on session end)
- Only cache read-only tools (`is_read_only = True`)

The cache sits in the executor layer, not in individual tools. Tools remain unaware of caching.

### Gap 4: Per-Tool Timeout Configuration

**What it is**: D.U.H. has per-tool timeout defaults in `TOOL_TIMEOUTS` (a hardcoded dict in `duh/kernel/tool.py`), but these cannot be overridden by the user. A user running a slow test suite needs Bash timeout > 5 minutes. A user with a slow MCP server needs its timeout > 30 seconds. Currently, the only option is editing source code.

**Who has it**: OpenCode allows per-tool timeout in `opencode.json`. Claude Code allows timeout configuration for MCP servers.

**D.U.H. status**: `TOOL_TIMEOUTS` is a compile-time dict. `MCPServerConfig` has a `timeout` field but it controls connection timeout, not per-tool-call timeout. The `get_tool_timeout()` function has no config override path.

**Impact**: Medium. Power users hit this when running long builds, complex database queries, or slow MCP servers. The default 120s is usually fine, but when it isn't, the only workaround is `--timeout` flags that don't exist.

**Proposed fix**: Extend `duh.toml` configuration:

```toml
[tools.timeouts]
Bash = 600        # 10 min for long builds
Read = 30         # default
"mcp__slow-server__*" = 120  # all tools from this server

[tools.timeouts._default]
native = 120
mcp = 60
```

The `get_tool_timeout()` function checks config overrides first, then falls back to `TOOL_TIMEOUTS`, then to `DEFAULT_TIMEOUT`. Glob patterns in tool names allow server-wide MCP overrides.

### Gap 5: Tool Metrics and Analytics

**What it is**: Tracking which tools are called, how often, how long they take, and how often they fail. This data enables: identifying slow tools that need optimization, finding tools the model misuses, detecting MCP servers that are flaky, and informing which tools to eager-load vs defer.

**Who has it**: Claude Code tracks tool usage metrics internally for telemetry. Copilot CLI tracks extension performance. OpenCode shows tool result statistics in its TUI.

**D.U.H. status**: No tool-level metrics. The `NativeExecutor` and `MCPExecutor` execute tools and return results. No timing, no error rate tracking, no usage counts. The hook system (ADR-013) could theoretically capture this via pre/post-tool hooks, but no hooks are configured for metrics collection.

**Impact**: Medium-low for users (they don't see metrics), but medium-high for development. Without metrics, we cannot answer: "Which tools are slow?", "Which MCP servers are flaky?", "Should ToolSearch be eager-loaded?"

**Proposed fix**: Add a `ToolMetrics` collector:

```python
@dataclass
class ToolCallMetric:
    tool_name: str
    started_at: float
    duration_ms: float
    success: bool
    error_type: str | None = None
    input_size: int = 0
    output_size: int = 0

class ToolMetrics:
    def record(self, metric: ToolCallMetric) -> None: ...
    def summary(self) -> dict[str, Any]: ...
    def slowest(self, n: int = 5) -> list[ToolCallMetric]: ...
    def error_rate(self, tool_name: str) -> float: ...
```

Instrumentation added to `NativeExecutor.run()` and `MCPExecutor.run()`. Metrics exposed via `/metrics` slash command and available to the hook system. Metrics are per-session (not persisted across sessions unless the user opts in).

### Gap 6: MCP Server Health Monitoring

**What it is**: Proactive detection of MCP server health issues before they cause tool call failures. Currently, D.U.H. only detects problems reactively — when a tool call fails, the error counter increments, and after 3 failures the server is degraded (ADR-032). There is no heartbeat, no periodic health check, no server status dashboard.

**Who has it**: Claude Code monitors MCP server health and shows server status in its UI. Gemini CLI reconnects proactively when it detects connection degradation.

**D.U.H. status**: Reactive only. The circuit breaker in `MCPExecutor` tracks `_error_counts` and `_degraded` servers, but only in response to failed tool calls. A server could be dead for 10 minutes before the model happens to call one of its tools.

**Impact**: Low-medium. Most MCP servers are either local (stdio, very reliable) or remote (SSE/HTTP, where network issues are visible). The impact is felt mainly in long-running sessions where a remote server goes down between tool calls.

**Proposed fix**: Add an optional background health check:

```python
class MCPHealthMonitor:
    def __init__(self, executor: MCPExecutor, interval: float = 60.0):
        ...

    async def start(self) -> None:
        """Start periodic health checks for all connected servers."""
        ...

    async def _check_server(self, server_name: str) -> bool:
        """Ping the server with a lightweight listTools call."""
        ...
```

- For stdio servers: check if the subprocess is still alive (`process.returncode is None`)
- For remote servers: send a lightweight `ping` or `listTools` RPC
- If a server fails health check, attempt reconnection before the next tool call
- Health status available via `/mcp status` slash command
- Disabled by default (opt-in via `[mcp] health_check = true` in `duh.toml`)

### Gap 7: Eager vs Lazy Tool Discovery

**What it is**: When D.U.H. connects to MCP servers at startup (via `connect_all()`), it discovers all tools eagerly — every server is connected and every tool is enumerated before the session begins. This is correct for a small number of local servers, but scales poorly when there are many remote servers or slow servers.

**Who has it**: Claude Code eagerly discovers tools from all servers at startup but defers schema loading (ADR-018 equivalent). Tool pinning lets users override the deferred behavior for specific tools. Gemini CLI lazily connects to MCP servers — connection happens on first tool call to that server.

**D.U.H. status**: ADR-040 implements progressive connection batching (local first, then remote, with concurrency limits). ADR-018 implements deferred schema loading (names in system prompt, full schema on demand). But all servers are connected at startup — there is no option to defer connection to a server until its tools are actually needed.

**Impact**: Low. D.U.H.'s progressive batching (ADR-040) already mitigates the startup latency issue for most cases. This is an optimization for users with many configured MCP servers.

**Proposed fix**: Add a `lazy` flag to MCP server configuration:

```toml
[[mcp.servers]]
name = "rarely-used"
command = "npx @some/rare-server"
lazy = true  # Don't connect until a tool from this server is called
```

Lazy servers register their tool names (from a cached manifest or from the previous session's discovery) as deferred tools. On first tool call, the server is connected, tools are discovered, and the call proceeds. Subsequent calls use the live connection.

## Priority Matrix

| Gap | Impact | Effort | Priority | Dependency |
|-----|--------|--------|----------|------------|
| 1. Streamable HTTP transport | Medium-high | Low (additive transport) | **P0** | ADR-040 |
| 2. Schema validation | Medium | Low (validation function) | **P0** | ADR-010 |
| 3. Tool result caching | Medium | Medium (cache + invalidation) | **P1** | ADR-004 |
| 4. Per-tool timeout config | Medium | Low (config plumbing) | **P1** | ADR-004, ADR-015 |
| 5. Tool metrics | Medium-low to medium-high | Medium (instrumentation) | **P1** | ADR-004 |
| 6. MCP health monitoring | Low-medium | Medium (background task) | **P2** | ADR-032 |
| 7. Lazy server discovery | Low | Low-medium (caching) | **P2** | ADR-018, ADR-040 |

## Decision

Adopt all seven gaps as planned work, prioritized as above.

**P0 (next wave)**: Streamable HTTP transport and schema validation. Both are low-effort, high-signal improvements. Streamable HTTP keeps D.U.H. compatible with the evolving MCP ecosystem. Schema validation prevents a class of confusing runtime failures.

**P1 (following wave)**: Tool result caching, per-tool timeout configuration, and tool metrics. These are operational improvements that compound — metrics inform caching policy, timeouts prevent wasted tokens on stuck tools.

**P2 (when needed)**: Health monitoring and lazy discovery. These matter at scale but are not blocking any immediate use case.

## Consequences

### Positive
- Streamable HTTP ensures D.U.H. can connect to next-generation MCP servers
- Schema validation catches bad MCP servers at handshake time, not at runtime
- Tool result caching reduces I/O and latency for the most common tool patterns
- Per-tool timeouts give power users control without source code edits
- Tool metrics provide data-driven insight into tool ecosystem health
- Health monitoring reduces surprise failures in long-running sessions
- Lazy discovery improves startup time for heavily-configured environments

### Negative
- Seven new subsystems add code and maintenance surface
- Caching introduces cache invalidation complexity (the hard problem)
- Metrics collection adds per-call overhead (mitigated: ~0.1ms of timing code)
- Health monitoring background task consumes resources even when not needed (mitigated: opt-in)

### Risks
- Streamable HTTP spec may evolve further — mitigated by abstracting behind the Transport protocol
- Tool result caching may serve stale data — mitigated by conservative TTLs and stat-based invalidation for file tools
- Per-tool timeout glob patterns may surprise users — mitigated by explicit config, no implicit behavior changes

## Implementation Notes

Files to create or modify:

- `duh/adapters/mcp_transports.py` — Add `StreamableHTTPTransport` class (Gap 1)
- `duh/adapters/mcp_executor.py` — Add `_validate_tool_schema()` and call it during `connect()` (Gap 2)
- `duh/kernel/tool_cache.py` — New `ToolResultCache` class (Gap 3)
- `duh/kernel/tool.py` — Extend `get_tool_timeout()` to check config overrides (Gap 4)
- `duh/kernel/tool_metrics.py` — New `ToolMetrics` collector class (Gap 5)
- `duh/adapters/mcp_health.py` — New `MCPHealthMonitor` class (Gap 6)
- `duh/adapters/mcp_executor.py` — Add lazy connection support to `MCPExecutor` (Gap 7)
- `duh/adapters/native_executor.py` — Add cache and metrics instrumentation to `run()` (Gaps 3, 5)
