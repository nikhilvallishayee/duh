# D.U.H. — D.U.H. is a Universal Harness

> An evolving, open-source alternative to Claude Code, OpenCode, Codex, and Cline.

Provider-agnostic AI coding harness. Use Claude, GPT, Gemini, Ollama, or any model through one clean interface. SDK-compatible — drop-in replacement for Claude Code in any Claude Agent SDK app.

**Status**: Actively evolving. 954 tests, 19 ADRs, 3 providers, full SDK compatibility.

## Why D.U.H.?

Every AI coding tool locks you into one model, one vendor, one way of working. D.U.H. is the harness between you and *any* model — it handles the agentic loop, tool execution, safety, sessions, skills, hooks, MCP, and REPL so you can swap providers without rewriting your workflow.

## Quick Start

```bash
# With Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
duh -p "fix the bug in auth.py"

# With OpenAI
export OPENAI_API_KEY=sk-...
duh -p "add error handling to the API" --provider openai

# With local models (no API key, no cost)
ollama serve && ollama pull qwen2.5-coder:7b
duh -p "what files are here?" --provider ollama

# Interactive REPL
duh
# duh> /help
# duh> explain this codebase
# duh> /model claude-opus-4-6
# duh> refactor the auth module
# duh> /exit
```

## What You Can Do

```bash
# Read, analyze, and modify code
duh -p "find all TODO comments and create a summary"

# Multi-turn agentic workflows (reads files, runs tools, iterates)
duh -p "add input validation to the API endpoints" --dangerously-skip-permissions

# Use as a Claude Agent SDK backend (drop-in for Claude Code)
CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK=1 python my_app.py  # uses duh via cli_path

# Stream-JSON protocol for programmatic control
echo '{"type":"user","message":{"role":"user","content":"hello"}}' | \
  duh --input-format stream-json --output-format stream-json

# Skills auto-discovery (loads from .claude/skills/ and .duh/skills/)
cd my-project  # has .claude/skills/deploy/SKILL.md
duh -p "deploy to staging"  # skill auto-invoked

# MCP server integration
# Configure in .duh/settings.json, tools auto-discovered
duh -p "open example.com and take a screenshot" --dangerously-skip-permissions

# Hook system — log, enforce policy, automate
# Configure PreToolUse/PostToolUse hooks in .duh/settings.json

# Doctor diagnostics
duh doctor
```

## Comparison

| | **D.U.H.** | Claude Code | OpenCode | Cline | Aider | Claw-code |
|---|---|---|---|---|---|---|
| Language | Python | TypeScript | TypeScript | TypeScript | Python | Python+Rust |
| Open source | Yes (Apache 2.0) | No | Yes | Yes | Yes | Yes (legal grey) |
| Multi-provider | **3 built-in** | Anthropic only | 75+ via config | Multi | Multi | Multi |
| Local models | **Ollama native** | No | Yes | Yes | Yes | Yes |
| SDK compatible | **Yes** | N/A (is the SDK) | No | No | No | Partial |
| MCP support | **Adapter** | Full | Config | Extension | None | Partial |
| Hooks system | **6 events** | Hooks | None | None | None | None |
| Skills | **.claude/ + .duh/** | .claude/ only | None | None | None | None |
| Safety layers | **3** | 3 | 1 | 1 | 0 | Unknown |
| tool_choice | **Uniform** | Yes | No | No | No | No |
| REPL | **Yes (readline)** | Yes | Yes | IDE | Terminal | Yes |
| Tests | **954** | Internal | Unknown | Unknown | Yes | Minimal |

### vs Claude Code

D.U.H. is not a clone — it's a clean-room harness that speaks the same protocol. Skills built for Claude Code work in D.U.H. The Claude Agent SDK can use D.U.H. as a drop-in backend. But D.U.H. also works with OpenAI, Ollama, and any future provider.

### vs Claw-code

Claw-code (119K GitHub stars) is a viral clean-room rewrite born from Anthropic's npm source leak. D.U.H. predates it and takes a different approach: ports-and-adapters architecture, rigorous testing (954 tests), SDK protocol compatibility, and a focus on being a *harness* rather than a *clone*.

### vs OpenCode / Aider / Cline

These are excellent tools. D.U.H. differentiates on: (1) Claude Agent SDK compatibility for programmatic use, (2) hooks system for policy enforcement and automation, (3) skill format parity with Claude Code, (4) hexagonal architecture with clean kernel/adapter separation.

## SDK Compatibility

D.U.H. speaks the same NDJSON stream-json protocol as Claude Code. Any app using the Claude Agent SDK can switch to D.U.H.:

```python
from claude_agent_sdk import ClaudeAgentOptions, query, AssistantMessage, TextBlock

async for message in query(
    prompt="What is 2+2?",
    options=ClaudeAgentOptions(
        cli_path="/path/to/duh-sdk-shim",
        max_turns=1,
    )
):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(block.text)  # "4"
```

Works with the Universal Companion API, claude-flow orchestration, and any Claude Agent SDK consumer.

## Architecture

```
duh/
  kernel/         # The agentic loop (provider-agnostic)
    loop.py       # async generator: prompt -> model -> tool -> result
    engine.py     # session lifecycle wrapper
    messages.py   # Message data model
    deps.py       # Injectable dependencies
    skill.py      # Skill loading (.claude/ + .duh/)
    memory.py     # Per-project memory

  ports/          # Abstract interfaces
    provider.py   # ModelProvider protocol
    executor.py   # ToolExecutor protocol
    approver.py   # ApprovalGate protocol

  adapters/       # Provider wrappers
    anthropic.py  # Claude (Sonnet, Opus, Haiku)
    openai.py     # GPT-4o, o1, any OpenAI-compatible API
    ollama.py     # Local models (Llama, Qwen, Mistral)
    mcp_executor.py  # MCP server transport

  tools/          # 9 core tools
    read, write, edit, bash, glob, grep, skill, tool_search, agent

  cli/            # CLI interface
    main.py       # Entry point + signal handling
    parser.py     # Argument parsing
    runner.py     # Print mode
    repl.py       # Interactive REPL with /slash commands
    sdk_runner.py # Stream-JSON NDJSON protocol
    ndjson.py     # NDJSON helpers

  hooks.py        # Hook system (6 events, shell + function executors)
  config.py       # 4-layer config precedence
  plugins.py      # Plugin discovery
  agents.py       # Multi-agent support
```

## Providers

| Provider | Status | Models |
|----------|--------|--------|
| **Anthropic** | Working | Claude Sonnet 4.6, Opus 4.6, Haiku 4.5 |
| **OpenAI** | Working | GPT-4o, o1, any OpenAI-compatible API |
| **Ollama** | Working | Any local model (Qwen, Llama, Mistral, etc.) |
| **Google Gemini** | Planned | Via Vertex or direct API |
| **litellm** | Planned | 100+ models via one adapter |

Auto-detection: `ANTHROPIC_API_KEY` > `OPENAI_API_KEY` > Ollama > error.

## Skills

D.U.H. loads skills from both Claude Code and D.U.H. directories:

```
Load order (last wins by name):
1. ~/.claude/skills/         # Claude Code user-global
2. ~/.config/duh/skills/     # D.U.H. user-global
3. .claude/skills/           # Claude Code project-local
4. .duh/skills/              # D.U.H. project-local (highest priority)
```

Both flat (`skill.md`) and directory (`skill-name/SKILL.md`) layouts supported. All Claude Code frontmatter fields work: `name`, `description`, `when-to-use`, `allowed-tools`, `model`, `context`, `paths`.

## CLI Reference

```
duh                                     # interactive REPL
duh -p "prompt"                         # print mode
duh -p "prompt" --model gpt-4o          # specific model
duh -p "prompt" --provider openai       # force provider
duh -p "prompt" --tool-choice none      # text only, no tools
duh -p "prompt" --dangerously-skip-permissions
duh -p "prompt" --output-format json    # JSON array output
duh -p "prompt" --output-format stream-json  # NDJSON streaming
duh --input-format stream-json          # SDK mode (stdin NDJSON)
duh -p "prompt" --max-turns 5           # limit iterations
duh -p "prompt" --debug                 # full event tracing
duh -p "prompt" --system-prompt "..."   # override system prompt
duh -p "prompt" --fallback-model haiku  # fallback on overload
duh doctor                              # diagnostics
duh --version
```

## Design Decisions

19 ADRs document every architectural choice. See [docs/adrs/](docs/adrs/).

## Contributing

Every change follows: write test > watch fail > write code > refactor > verify > commit.

## License

Apache 2.0
