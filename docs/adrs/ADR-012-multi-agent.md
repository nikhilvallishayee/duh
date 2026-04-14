# ADR-012: Multi-Agent

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-06

## Context

Production harnesses support subagent spawning: the model can call an agent tool to create a child agent that runs with its own conversation, tools, and context. Typical implementations include:

- A tool definition accepting prompt, agent type, model, isolation mode, and working directory
- Lifecycle orchestration: create context, resolve tools, build system prompt, run query loop
- Built-in agent types (general purpose, coder, researcher, planner, etc.)
- User-defined agents from frontmatter markdown files
- Worktree isolation via git worktrees for file-safe parallel work

### The core insight

Each agent is a new `Engine` with its own conversation history, system prompt, tool pool, and working directory. There is no special "agent framework." An agent is just another run of the same agentic loop. The parent sends a prompt; the child runs to completion; the result returns as a tool result.

### What D.U.H. keeps

| Typical feature | D.U.H. | Rationale |
|---------------------|--------|-----------|
| Agent = new Engine instance | Yes | Core pattern, simple and correct |
| Built-in agent types (general, coder, researcher, planner) | Yes | Useful defaults |
| User-defined agents (markdown frontmatter) | Future | Requires `.duh/agents/` convention |
| Worktree isolation | Yes (optional) | File safety for parallel agents |
| Fork subagent (prompt cache sharing) | No | Anthropic-specific optimization |
| Background/async agents | Yes | Essential for parallel work |
| Agent MCP servers | No | Too complex for v0.1 |
| Agent memory/snapshots | No | Complexity not justified yet |
| Coordinator mode | No | Orchestration is the caller's job |

## Decision

### 1. Engine-per-agent, no special framework

A subagent is created by instantiating a new `Engine` with:
- A system prompt tailored to the agent type
- A (possibly restricted) tool pool
- Its own conversation history (starting from the user's prompt)
- An optional working directory override

This is the same code path the CLI uses. No special agent runtime.

### 2. AgentTool is a regular tool

```python
class AgentTool:
    name = "Agent"
    description = "Spawn a subagent to perform a task"
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The task for the agent"},
            "agent_type": {
                "type": "string",
                "enum": ["general", "coder", "researcher", "planner"],
                "description": "Agent specialization",
                "default": "general",
            },
        },
        "required": ["prompt"],
    }
```

The model calls it like any other tool. The executor creates a child Engine, runs it to completion, and returns the result text.

### 3. Agent types are system prompt variations

Each agent type is a different system prompt. No code differences:

| Type | System prompt focus |
|------|-------------------|
| `general` | General-purpose coding assistant (default) |
| `coder` | Focus on writing clean, tested code |
| `researcher` | Focus on reading, searching, understanding code |
| `planner` | Focus on breaking down tasks, creating plans |

### 4. Worktree isolation (optional)

When `isolation="worktree"` is specified, the agent tool:
1. Creates a git worktree (`git worktree add`)
2. Sets the child Engine's cwd to the worktree
3. Runs the agent
4. Reports the worktree path in the result
5. Does NOT auto-merge (the parent decides)

This prevents parallel agents from conflicting on file writes.

### 5. Message routing

The parent sends a prompt to the child via the `AgentTool`. The child runs to completion. The child's final assistant message text is returned as the tool result. There is no bidirectional communication during execution.

Future work: a `SendMessage` tool for inter-agent communication (as some harnesses have for multi-agent swarms). Not needed for v0.1.

## Architecture

```
Parent Engine
  |
  AgentTool.run(prompt="fix the tests", agent_type="coder")
  |
  Child Engine (new instance)
  |  - system_prompt = CODER_PROMPT
  |  - tools = parent tools (or subset)
  |  - cwd = parent cwd (or worktree)
  |  - conversation = [user: "fix the tests"]
  |
  runs query loop to completion
  |
  returns final assistant text as tool result
```

## Consequences

- Adding agent types = adding system prompts, no code changes
- Agents use the same kernel, same tools, same everything
- No special agent framework to learn or maintain
- Worktree isolation gives file safety without complexity
- The model decides when to spawn agents (it calls the tool)
- Agent execution is synchronous from the parent's perspective (the tool call blocks until done)
- Background agents are future work (asyncio.create_task wrapping the engine run)

## Implementation Notes

- `duh/tools/agent_tool.py` — `AgentTool` that spawns a child `Engine`.
- `duh/tools/worktree.py` — git-worktree isolation helpers.
- `duh/kernel/job_queue.py` — supports background jobs driven by `/jobs` REPL command.
