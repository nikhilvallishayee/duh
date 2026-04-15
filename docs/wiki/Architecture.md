# Architecture Overview

D.U.H. (the **D**efinitely **U**niversal **H**arness) is a provider-agnostic agentic coding harness. It implements the universal agentic cycle -- prompt, model, tool use, response -- in a clean, testable architecture built on ports-and-adapters (hexagonal architecture) with constructor-based dependency injection.

This document covers the system's structure, data flow, and key design decisions.

---

## 1. Design Philosophy

**First principles.** D.U.H. starts from the observation that every agentic coding tool runs the same loop: send messages to a model, receive tool calls, execute tools, feed results back. Rather than coupling that loop to a specific provider SDK, D.U.H. factors it into a pure kernel surrounded by swappable adapters.

**Ports and adapters.** The kernel defines port contracts (Python `Protocol` classes) for everything external: calling a model, executing tools, approving actions, compacting context, persisting sessions. Adapters implement those contracts for specific technologies. Tests swap in fakes. The kernel never imports a provider SDK.

**Provider-agnostic.** The same agentic loop runs against Anthropic, OpenAI, Ollama (local models), or any of 100+ providers via litellm. Switching providers is a one-line change at the wiring layer.

**Kent Beck's four rules of simple design:**
1. Passes all tests
2. Reveals intention through clear naming
3. Has no duplication
4. Has no unnecessary complexity

These rules are enforced by convention and cited in the module docstrings.

---

## 2. Kernel

The kernel lives in `duh/kernel/` and contains zero external dependencies. It defines the agentic cycle, the data model, and the dependency injection seams.

### loop.py -- The Agentic Cycle

The most important file in the project. `query()` is an async generator implementing:

```
prompt -> model -> [tool_use -> tool_result ->]* response
```

It receives events from `deps.call_model` and dispatches tool calls via `deps.run_tool`. Each iteration:

1. Calls the model with the current message history.
2. Yields streaming events (`text_delta`, `thinking_delta`, `content_block_start/stop`).
3. Extracts `tool_use` blocks from the assistant response.
4. If no tool use, yields `done` and returns.
5. For each tool use block:
   - Checks the approval gate (`deps.approve`).
   - Checks the confirmation gate (`deps.confirm_gate`) for taint-blocked dangerous tools.
   - Executes the tool via `deps.run_tool`.
   - Yields `tool_result` events.
   - Logs to the audit logger (`deps.audit_logger`).
6. Packs all tool results into a single user message (required by the Anthropic API).
7. Loops back to step 1.

Safety valves: a configurable `max_turns` limit (default 1000), an 80KB truncation cap on individual tool results, and a grace turn at the limit where the model summarizes without tools.

### engine.py -- Session Lifecycle

`Engine` wraps the query loop with session-level concerns:

- **Message history**: maintains the canonical conversation across multiple `run()` calls.
- **Token counting**: tracks input/output tokens per turn with model-calibrated estimation, preferring real usage data from providers when available.
- **Cost tracking**: estimates session cost, enforces budget limits (warning at 80%, hard stop at 100%).
- **Auto-compaction**: triggers compaction when context exceeds 80% of the model's limit. Uses progressive targets on prompt-too-long retries (70% -> 50% -> 30%).
- **Context gate**: blocks new queries at 95% capacity after compaction has been attempted.
- **Fallback model**: retries with a secondary model on overload/rate-limit errors.
- **Session persistence**: auto-saves to a `SessionStore` after each turn.
- **Trifecta check**: refuses to start sessions with the lethal capability combination (see Security).
- **Hook emission**: fires lifecycle hooks (SETUP, TASK_CREATED, PRE_COMPACT, POST_COMPACT, TASK_COMPLETED).
- **Cache tracking**: monitors prompt cache hit rates and detects unexpected cache breaks.

### messages.py -- Data Model

The lingua franca of the system. Every message flowing through the kernel is a `Message` dataclass:

```python
@dataclass
class Message:
    role: str              # "user" | "assistant" | "system"
    content: str | list[ContentBlock]
    id: str                # UUID
    timestamp: str         # ISO 8601
    metadata: dict         # provider-specific (stop_reason, usage, etc.)
```

Content blocks are frozen dataclasses: `TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `ThinkingBlock`, `ImageBlock`. Provider adapters translate to/from these. The kernel never sees provider-specific content formats.

Helper functions enforce API constraints:
- `merge_consecutive()` -- merges adjacent same-role messages.
- `validate_alternation()` -- inserts synthetic messages to guarantee strict user/assistant alternation.

### deps.py -- Dependency Injection

The `Deps` dataclass holds every external dependency as a callable:

| Field | Type | Purpose |
|-------|------|---------|
| `call_model` | `AsyncGenerator` | Stream model responses |
| `run_tool` | `Awaitable` | Execute a tool by name |
| `approve` | `Awaitable` | Check if a tool call is permitted |
| `compact` | `Awaitable` | Compact messages when context is full |
| `confirm_gate` | `Callable` | Block tainted dangerous tool calls |
| `hook_registry` | `HookRegistry` | Lifecycle event hooks |
| `audit_logger` | `AuditLogger` | Structured audit logging |
| `uuid` | `Callable` | UUID generator (injectable for deterministic tests) |
| `session_id` | `str` | Session identifier for audit/tracing |

Tests swap any of these with fakes. The kernel never imports a provider SDK directly.

---

## 3. Adapters

Adapters live in `duh/adapters/` and implement the port contracts defined by the kernel.

### Provider Adapters

All providers implement the same streaming interface (`async def stream(*, messages, system_prompt, model, tools, thinking, tool_choice, ...) -> AsyncGenerator[dict, None]`) and produce the same D.U.H. uniform events: `text_delta`, `thinking_delta`, `content_block_start/stop`, `assistant` (final message).

| Adapter | SDK | Models | Notes |
|---------|-----|--------|-------|
| `AnthropicProvider` | `anthropic` | Claude (Opus, Sonnet, Haiku) | Native tool_choice, extended thinking, prompt caching (ADR-061) |
| `OpenAIProvider` | `openai` | GPT-4o, o1, o3, any compatible API | Also works with vLLM, Together via `base_url` |
| `OllamaProvider` | `httpx` (raw HTTP) | Llama, Qwen, Mistral, etc. | Local models, JSON text fallback extraction for models without structured tool calls |
| `LiteLLMProvider` | `litellm` | 100+ providers (Gemini, Bedrock, Azure, Groq, etc.) | Unified interface, optional dependency |

All providers tag output with `UntrustedStr(text, TaintSource.MODEL_OUTPUT)` for taint tracking. All implement `_parse_tool_use_block()` returning a canonical `ParsedToolUse` to ensure identical parsing across providers. All use exponential backoff for transient errors and handle mid-stream disconnects by yielding partial assistant messages.

### Tool Executors

| Executor | Purpose |
|----------|---------|
| `NativeExecutor` | Runs Python `Tool` objects directly. Validates input, enforces per-tool timeouts, tracks file operations, maintains an undo stack for Write/Edit, truncates output at 100KB, optionally redacts secrets. |
| `MCPExecutor` | Connects to MCP (Model Context Protocol) servers via stdio/SSE/HTTP/WebSocket transports. Discovers tools at connection time, namespaces them as `mcp__<server>__<tool>`, validates Unicode safety of descriptions, validates schemas, implements circuit breaker (degrades after 3 consecutive failures), handles session expiry/reconnection. |

### Compaction Adapters

The `duh/adapters/compact/` package implements a 4-tier compaction pipeline orchestrated by `AdaptiveCompactor`:

| Tier | Strategy | Threshold | Cost | Description |
|------|----------|-----------|------|-------------|
| 1 | `MicroCompactor` | Always | Free | Structural pruning: strips whitespace, truncates large tool results |
| 2 | `SnipCompactor` | 75% | Free | Removes low-value messages (old tool results, redundant exchanges) |
| 3 | `DedupCompactor` | Always | Free | Deduplicates repeated content across messages |
| 4 | `SummarizeCompactor` | 85% | Model call | Sends old messages to the model for summarization |

The orchestrator runs tiers in order, stopping as soon as context fits within budget. A circuit breaker halts after 3 consecutive tier failures. An output buffer (20K tokens) is reserved before compaction to ensure room for the model's response.

### Approvers

Four approval gate implementations:

| Approver | Behavior |
|----------|----------|
| `AutoApprover` | Allows everything. For sandboxed environments. |
| `InteractiveApprover` | Prompts the user (y/a/n/N) with session-level caching. |
| `RuleApprover` | Deny by tool name, command pattern, or path restriction (resolves symlinks). |
| `TieredApprover` | 3-tier model: SUGGEST (reads auto, writes/commands need approval), AUTO_EDIT (reads+writes auto, commands need approval), FULL_AUTO (everything auto). Git safety check blocks destructive git commands across all tiers. |

### File Store

`FileStore` implements the `SessionStore` port using JSONL files under `~/.config/duh/sessions/<project-hash>/`. Features:
- One JSON line per message.
- Atomic writes via temp-file-then-rename.
- 64MB session cap.
- Project-scoped directories (hashed from git root).
- ADR-057 migration: auto-repairs sessions with broken role alternation on load.

---

## 4. Tools

### Tool Protocol

Every tool implements a minimal contract defined in `duh/kernel/tool.py`:

```python
class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema

    async def call(self, input: dict, context: ToolContext) -> ToolResult: ...
```

Optional attributes: `is_read_only`, `is_destructive`, `capabilities` (for trifecta classification), `check_permissions()`.

`ToolContext` provides runtime context: `cwd`, `tool_use_id`, `session_id`, `abort_signal`, `permissions`, `sandbox_policy`, `confirm_token`.

`ToolResult` carries `output` (str or list), `is_error` flag, and `metadata` dict.

Per-tool timeouts are configured in `TOOL_TIMEOUTS` (e.g., Bash: 300s, Read: 30s, Grep: 60s, default: 120s).

### Built-in Tools (25+)

Registered via `duh/tools/registry.py` with graceful degradation (missing imports are silently skipped):

| Category | Tools |
|----------|-------|
| **File I/O** | Read, Write, Edit, MultiEdit, Glob, Grep, NotebookEdit |
| **Execution** | Bash |
| **Search** | WebFetch, WebSearch, ToolSearch |
| **Meta** | Skill, Task (todo tracking) |
| **Git** | EnterWorktree, ExitWorktree, GitHub |
| **Data** | Database (read-only SQL), HTTP (API testing), Docker |
| **Memory** | MemoryStore, MemoryRecall |
| **Analysis** | TestImpact |
| **Agent** | Agent (subagent spawning), Swarm (parallel subagents) |
| **Interactive** | AskUserQuestion, TodoWrite |

### Deferred Loading

Heavy tools (like LSP) are registered as `DeferredTool` objects with the `ToolSearch` meta-tool. They carry name, description, and schema but are not loaded into memory until the model requests their full definition via ToolSearch.

### Schema Validation

All tool schemas are validated at registration time (ADR-068). `SchemaValidationError` catches critical issues (missing `type: object`, invalid property types, phantom `required` references). Warnings are logged but never block registration.

MCP tool schemas are additionally validated at discovery time during the handshake.

---

## 5. Multi-Agent

### AgentTool

The `Agent` tool lets the model spawn a subagent. The child gets:
- The parent's `deps` (same model provider, same tool executor, same approver).
- The parent's tool list **minus AgentTool itself** (prevents infinite recursion).
- Its own fresh `Engine` and conversation.
- A type-specific system prompt (from the constitution module).

Maximum nesting depth: 1 (children cannot spawn grandchildren).

Agent types are system prompt overlays with default model preferences:

| Type | Default Model | Focus |
|------|--------------|-------|
| `general` | inherit | General-purpose coding |
| `coder` | sonnet | Writing clean, tested code |
| `researcher` | haiku | Reading, searching, understanding |
| `planner` | opus | Breaking down tasks, creating plans |
| `reviewer` | sonnet | Code review |
| `subagent` | inherit | Delegated tasks |

### SwarmTool

The `Swarm` tool spawns 1-5 subagents **in parallel** via `asyncio.gather()`. Each child is independent with its own conversation. Results are collected and formatted with per-task status (OK/ERROR), turn count, and output.

Child tools exclude both Agent and Swarm to prevent recursive spawning.

### Coordinator Mode

Agents are standard `Engine` instances with specialized system prompts. There is no special agent framework -- an agent is just another run of the same agentic loop. The `run_agent()` function in `duh/agents.py` creates an Engine, runs the prompt to completion, and returns an `AgentResult` with the final text, turn count, and any error.

---

## 6. Context Management

### 4-Tier Compaction Pipeline

Context management follows a progressive strategy with four triggers:

| Usage | Action | Cost |
|-------|--------|------|
| < 75% | No action | -- |
| 75% | Snip fires (structural pruning) | Free |
| 80% | Auto-compact (full 4-tier pipeline) | Tier 4 may use model |
| 95% | Context gate blocks new queries | -- |

On prompt-too-long errors, progressive compaction targets ratchet down: 70% -> 50% -> 30% on successive retries (up to 3 retries).

Post-compaction, the file tracker rebuilds context about recently-read files so the model retains awareness of the working set.

### Context Gate

`ContextGate` (ADR-059) blocks new queries at 95% context usage. It fires **after** auto-compaction has been attempted, so compaction always gets a chance first. The user sees a message instructing them to run `/compact`.

### Prompt Caching (ADR-061)

The Anthropic adapter implements three levels of caching:

1. **System prompt caching**: The system prompt is wrapped with `cache_control: {type: "ephemeral"}` so the API caches it across turns (~90% savings on repeated system prompts).
2. **Message prefix caching**: The second-to-last message is marked as the prefix boundary, telling the API that everything up to that point is stable and cacheable.
3. **Cache hit tracking**: `CacheTracker` monitors `cache_creation_input_tokens` and `cache_read_input_tokens` across turns. It detects unexpected cache breaks (> 40% ratio drop between turns without compaction) and reports stats in the cost summary.

Compaction notifies the tracker to suppress false-positive break detection after intentional prefix changes.

---

## 7. Security

### Trifecta Check (ADR-054)

At session start, the engine computes the union of all tool capabilities and checks for the **lethal trifecta**: `READ_PRIVATE + READ_UNTRUSTED + NETWORK_EGRESS`. This is the classic exfiltration vector (Simon Willison's observation) -- data read from private sources can be smuggled out via untrusted content through network egress.

If all three are active without explicit acknowledgement, the session refuses to start. Users must either disable one capability or pass `--i-understand-the-lethal-trifecta`.

Capability flags: `READ_PRIVATE`, `READ_UNTRUSTED`, `NETWORK_EGRESS`, `FS_WRITE`, `EXEC`.

### Taint Tracking (ADR-054, workstream 7.1)

`UntrustedStr` is a `str` subclass that carries a `TaintSource` tag through all string operations. Every method (`__add__`, `format`, `join`, `replace`, `split`, `upper`, etc.) is overridden to propagate or merge the taint source.

Sources: `USER_INPUT` (untainted), `MODEL_OUTPUT` (tainted), `TOOL_OUTPUT` (tainted), `FILE_CONTENT` (tainted), `MCP_OUTPUT` (tainted), `NETWORK` (tainted), `SYSTEM` (untainted).

Merge rule: tainted wins over untainted. If any operand is tainted, the result is tainted.

The confirmation gate uses taint information to block dangerous tool calls that trace back through tainted data (e.g., a Bash command derived from model output).

Debug mode (`DUH_TAINT_DEBUG=1`) logs every taint-propagating operation. Strict mode (`DUH_TAINT_STRICT=1`) raises `TaintLossError` on any silent tag drop.

### Confirmation Gates (ADR-054, workstream 7.2)

`ConfirmationMinter` produces HMAC-bound, single-use tokens for dangerous tool calls. Tokens are:
- Session-bound (tied to session ID).
- Tool-bound (tied to the specific tool name).
- Input-bound (tied to the SHA-256 hash of the input).
- Time-bound (expire after 5 minutes).
- Single-use (tracked in an issued set).

Only user-origin events can mint tokens. The confirmation gate in the query loop checks tokens before executing tools classified as dangerous.

### Audit Logging (ADR-072)

`AuditLogger` writes append-only JSONL to `~/.config/duh/audit.jsonl`. Every tool invocation records:
- Timestamp (ISO 8601)
- Session ID
- Tool name
- Tool input (auto-redacted: keys containing "key", "token", "secret", "password", "credential", "auth" are replaced with `[REDACTED]`; values > 500 chars are truncated)
- Result status (`ok`, `error`, `denied`)
- Duration (ms)

### Sandbox Policies

MCP servers can be sandboxed via OS-level mechanisms:
- **macOS**: Seatbelt (`sandbox-exec`) profiles.
- **Linux**: Landlock LSM.

`SandboxPolicy` defines writable paths, readable paths, and network access. `MCPManifest` declares server capabilities. The sandbox is applied at the subprocess level when starting stdio-based MCP servers.

### Additional Security

- **Git safety**: `TieredApprover` blocks destructive git commands (`push --force`, `reset --hard`, `clean -f`, `branch -D`) across all approval tiers, including FULL_AUTO.
- **MCP Unicode validation**: At handshake time, tool descriptions are scanned for zero-width characters, bidi overrides, Unicode Tag Characters, and invisible variation selectors (GlassWorm-style injection attacks).
- **Schema validation**: Tool schemas are validated at registration (native) and discovery (MCP) to prevent malformed schemas from causing cryptic API errors downstream.
- **Secret redaction**: `redact_secrets()` strips sensitive patterns from tool output before it enters the conversation.
- **Path traversal prevention**: `RuleApprover` resolves symlinks and `..` before checking path restrictions.

---

## 8. Session Persistence

### Storage Format

Sessions are stored as JSONL files (one JSON object per line per message) under `~/.config/duh/sessions/<project-hash>/`. The project hash is derived from the git root directory (or cwd if not in a repo), giving per-project session isolation.

### Message Flow (ADR-057)

The engine captures both assistant messages and tool_result user messages into the canonical history. This ensures that on resume, the message list has correct user/assistant alternation including the intermediate tool-result turns. The `tool_result_message` event type carries these internal messages from the loop to the engine without exposing them to the UI.

### Resume Modes

Sessions can be resumed by loading a previous session ID. On load, `FileStore` detects and auto-migrates sessions with broken role alternation (consecutive assistant messages, a bug pattern from pre-ADR-057 sessions). Migration applies `validate_alternation()` once and the corrected version is persisted on the next save.

### Safety

- Atomic writes: temp-file-then-rename prevents corruption on crash.
- 64MB session cap: prevents runaway session files.
- Append-only delta saves: only new messages since last save are written.

---

## 9. Data Flow Diagram

```
                            ┌──────────────────────────────────────┐
                            │              User / CLI              │
                            └──────────────┬───────────────────────┘
                                           │ prompt
                                           ▼
                            ┌──────────────────────────────────────┐
                            │           Engine (session)           │
                            │                                      │
                            │  • message history                   │
                            │  • token/cost tracking               │
                            │  • auto-compact (80%)                │
                            │  • context gate (95%)                │
                            │  • budget enforcement                │
                            │  • fallback model retry              │
                            │  • session persistence               │
                            └──────────────┬───────────────────────┘
                                           │ messages + deps
                                           ▼
┌─────────────┐         ┌──────────────────────────────────────┐
│  Approvers   │◄────────│         query() Loop (kernel)        │
│              │         │                                      │
│ • Tiered     │ allow/  │  prompt ──► model ──► [tool_use ──►  │
│ • Rule       │  deny   │                       tool_result]*  │
│ • Interactive│────────►│            ──► response               │
│ • Auto       │         └────┬─────────────────┬───────────────┘
└─────────────┘              │                 │
                  call_model │                 │ run_tool
                             ▼                 ▼
              ┌──────────────────┐  ┌──────────────────────────┐
              │    Providers     │  │     Tool Executors        │
              │                  │  │                           │
              │ • Anthropic      │  │ ┌──────────────────────┐  │
              │ • OpenAI         │  │ │   NativeExecutor     │  │
              │ • Ollama (local) │  │ │ Read, Write, Edit,   │  │
              │ • LiteLLM (100+)│  │ │ Bash, Grep, Glob,    │  │
              └──────────────────┘  │ │ Agent, Swarm, ...    │  │
                                    │ └──────────────────────┘  │
                                    │ ┌──────────────────────┐  │
                                    │ │    MCPExecutor        │  │
                                    │ │ mcp__server__tool     │  │
                                    │ │ stdio/sse/http/ws     │  │
                                    │ └──────────────────────┘  │
                                    └──────────────────────────┘

              ┌──────────────────┐  ┌──────────────────────────┐
              │   Compaction     │  │      Security             │
              │                  │  │                           │
              │ 1. Micro (free)  │  │ • Trifecta check         │
              │ 2. Snip  (75%)  │  │ • Taint tracking          │
              │ 3. Dedup (free)  │  │ • Confirmation tokens     │
              │ 4. Summary (85%) │  │ • Audit logging (JSONL)   │
              └──────────────────┘  │ • Sandbox (Seatbelt/LL)   │
                                    │ • Git safety              │
              ┌──────────────────┐  │ • MCP Unicode validation  │
              │  Session Store   │  └──────────────────────────┘
              │                  │
              │ JSONL per-project│
              │ ~/.config/duh/  │
              │ sessions/<hash>/ │
              └──────────────────┘
```

**Event flow through the loop:**

```
User ──prompt──► Engine.run()
                    │
                    ├──► query(messages, deps)
                    │       │
                    │       ├──► deps.call_model(messages, system_prompt, tools)
                    │       │       │
                    │       │       ├──yield── text_delta ──► UI
                    │       │       ├──yield── thinking_delta ──► UI
                    │       │       └──yield── assistant (final message)
                    │       │
                    │       ├── extract tool_use blocks
                    │       │
                    │       ├──► deps.approve(tool_name, input) ──► allow/deny
                    │       ├──► deps.confirm_gate(tool_name, input) ──► allow/block
                    │       ├──► deps.run_tool(tool_name, input) ──► output
                    │       ├──► deps.audit_logger.log_tool_call(...)
                    │       │
                    │       ├──yield── tool_use ──► UI
                    │       ├──yield── tool_result ──► UI
                    │       ├──yield── tool_result_message ──► Engine (history)
                    │       │
                    │       └── loop back to call_model (next turn)
                    │
                    ├──► session_store.save(session_id, messages)
                    └──yield── done ──► UI
```
