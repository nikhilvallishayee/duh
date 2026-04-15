# Frequently Asked Questions

## What is D.U.H.?

D.U.H. stands for **Duh is a Universal Harness**. It is a provider-agnostic, open-source AI coding agent. It is not a port or fork of any specific tool -- it is a clean-room implementation that extracts universal patterns from studying leading harnesses across TypeScript, Python, Go, and Rust. The kernel is under 5K lines of code with a strict ports-and-adapters architecture.

## Which providers are supported?

| Provider | Auth | Example Models |
|----------|------|---------------|
| **Anthropic** | `ANTHROPIC_API_KEY` env var | `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5` |
| **OpenAI (API)** | `OPENAI_API_KEY` env var | `gpt-4o`, `o1`, `o3` |
| **OpenAI (ChatGPT Codex)** | OAuth via `/connect openai` | `gpt-5.2-codex`, `gpt-5.1-codex`, `gpt-5.1-codex-mini` |
| **Ollama** | Local daemon, no key needed | Any locally-pulled model |
| **LiteLLM** | Depends on underlying provider | Any model LiteLLM supports |
| **Stub** | `DUH_STUB_PROVIDER=1` env var | Deterministic canned responses (for tests) |

Provider auto-detection works out of the box: set your API key, and D.U.H. picks the right provider. Use `--provider` to override.

## Is it compatible with CLAUDE.md?

Yes. D.U.H. reads instruction files from multiple standard locations:

- `CLAUDE.md` / `DUH.md` / `.duh/DUH.md` in your project (traverses from git root to cwd)
- `.claude/rules/*.md` / `.duh/rules/*.md` rule directories
- `AGENTS.md` (open standard)
- `~/.config/duh/DUH.md` for user-global instructions

All found instruction files are concatenated and injected into the system prompt. If you already have a `CLAUDE.md`, it just works.

## How do I resume a session?

```bash
# Resume the most recent session
duh --continue

# Resume a specific session by ID
duh --resume <session-id>
```

Sessions are stored as JSONL files in `~/.config/duh/sessions/`. Each turn is auto-saved, so sessions survive crashes.

## How do I control costs?

D.U.H. tracks token usage and estimated cost per provider.

```bash
# Set a maximum spend for this session
duh -p "refactor the API" --max-cost 1.50

# Set a maximum number of agentic turns
duh -p "fix tests" --max-turns 10
```

Inside the REPL, use `/cost` to see estimated spend and budget remaining. D.U.H. issues a warning at 80% of your budget and stops at 100%.

## How do I use multiple agents?

D.U.H. supports multi-agent workflows through three mechanisms:

**AgentTool** -- the model can spawn a child agent to perform a subtask:
```
The model calls Agent(prompt="fix the tests", agent_type="coder")
A child engine runs to completion and returns the result.
```

Built-in agent types: `general`, `coder`, `researcher`, `planner`.

**SwarmTool** -- coordinate multiple agents working in parallel on related subtasks.

**Worktree isolation** -- agents can work in separate git worktrees to avoid file conflicts during parallel edits:
```
Agent(prompt="refactor auth module", isolation="worktree")
```

See the [Multi-Agent Guide](Multi-Agent) for details.

## How is context managed?

D.U.H. uses automatic 4-tier context management:

1. **Token estimation** -- rough character-based estimation (~4 chars/token)
2. **Auto-compact trigger** -- fires when context exceeds 80% of the model's context window
3. **Smart deduplication** -- removes redundant file reads before compaction
4. **Model-summarized compaction** -- summarizes old conversation turns, falling back to tail-window truncation

This is fully automatic. You can also manually trigger compaction with the `/compact` REPL command or view context health with `/context`.

See [Context Management](Context-Management) for the full details.

## How do I add custom tools?

Add MCP (Model Context Protocol) servers to your project or user settings:

```json
// .duh/settings.json
{
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        },
        "my-custom-server": {
            "command": "python",
            "args": ["my_mcp_server.py"],
            "env": {"API_KEY": "..."}
        }
    }
}
```

MCP tools are discovered automatically and appear alongside built-in tools with a `mcp__<server>__<tool>` naming convention. D.U.H. supports stdio, SSE, HTTP, and WebSocket transports.

You can also pass MCP config directly:

```bash
duh --mcp-config mcp-servers.json -p "use the custom tools"
```

## How do I run in CI?

Use print mode with permissions bypassed:

```bash
duh -p "run the test suite and fix any failures" --dangerously-skip-permissions
```

For machine-readable output:

```bash
duh -p "check for security issues" --output-format json --dangerously-skip-permissions
```

Exit codes follow semantic conventions:
- `0` -- success
- `1` -- error (runtime failure, provider error)
- `2` -- usage error (bad flags, missing config)

For CI pipelines, combine with `--max-turns` and `--max-cost` to bound resource usage:

```bash
duh -p "fix lint errors" \
    --dangerously-skip-permissions \
    --max-turns 20 \
    --max-cost 2.00
```

## Is it open source?

Yes. D.U.H. is released under the **Apache 2.0** license. The full source is available at [github.com/nikhilvallishayee/duh](https://github.com/nikhilvallishayee/duh).

Contributions are welcome. The project uses a test-first development approach with 4000+ tests and 100% line coverage.
