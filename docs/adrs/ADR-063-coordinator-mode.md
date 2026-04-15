# ADR-063: Coordinator Mode — Multi-Agent Orchestration

**Status:** Proposed — 2026-04-16
**Date:** 2026-04-16
**Related:** ADR-012 (multi-agent), ADR-058 (resume modes)

## Context

Leading agent CLIs implement a coordinator/worker pattern:
- **Coordinator mode**: The main agent delegates tasks to worker subagents
- **Normal mode**: Single agent handles everything directly (current D.U.H. behavior)
- Sessions remember which mode they were in for resume

D.U.H. has ADR-012's `AgentTool` (spawn subagents) but no orchestration layer. The model manually decides when to spawn agents. A coordinator mode would make this systematic.

## Decision

### Two Modes

**Normal mode** (default): Single agent, same as today. AgentTool available but model decides when to use it.

**Coordinator mode** (`duh --coordinator`): The main agent becomes a task coordinator that:
1. Breaks the user's request into subtasks
2. Spawns specialized subagents for each subtask
3. Synthesizes results
4. Never executes tools directly (delegates everything)

### Coordinator System Prompt

```
You are a task coordinator. Break the user's request into independent subtasks
and delegate each to a specialized agent using the Agent tool. Never use tools
directly — always delegate. After all agents complete, synthesize their results
into a coherent response.

Agent types: coder, researcher, planner, reviewer
```

### SwarmTool (parallel agent execution)

For coordinator mode, add a `SwarmTool` that spawns multiple agents in parallel:

```python
class SwarmTool:
    """Spawn multiple agents in parallel and collect results."""
    input_schema = {
        "tasks": [{
            "prompt": str,
            "agent_type": str,  # general|coder|researcher|planner|reviewer
            "model": str,       # haiku|sonnet|opus|inherit
        }]
    }
```

Implementation: `asyncio.gather(*[run_agent(...) for t in tasks])`

### Mode Persistence

Store the mode in session metadata so `--continue` resumes in the same mode:
```python
engine._messages[0].metadata["coordinator_mode"] = True
```

## Implementation Plan

- [x] `SwarmTool` — parallel agent execution via asyncio.gather
- [x] Coordinator system prompt variant
- [x] `--coordinator` CLI flag
- [x] Mode persistence in session metadata
- [x] `/mode coordinator|normal` slash command

## Consequences

### Positive
- Systematic multi-agent orchestration
- Parallel execution of independent subtasks
- Clean separation: coordinator reasons, workers execute
- Follows industry best practice for multi-agent coordination

### Negative
- Cost multiplier: N agents = N× API calls
- Coordinator overhead: extra turn for task breakdown
- Workers may miss cross-task context
- Must handle partial failures (some agents succeed, others fail)
