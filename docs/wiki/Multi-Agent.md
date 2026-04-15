# Multi-Agent

## Overview

D.U.H. supports spawning subagents -- child instances of the same agentic loop, each with its own conversation, system prompt, and tool access. There is no separate agent framework. An agent is just another run of the Engine, which means every capability available to the main agent (Read, Bash, Grep, Edit, etc.) is available to children.

Multi-agent is useful when:

- A task has independent subtasks that benefit from parallel execution (e.g., research one module while coding another).
- You want a specialized system prompt for a subtask (e.g., a reviewer perspective on code you just wrote).
- The main agent would otherwise context-thrash between unrelated concerns.

For single-file edits, simple bug fixes, or quick questions, a single agent is faster and cheaper. Multi-agent adds latency and API cost proportional to the number of agents spawned.

## Agent Types

Each agent type is a system prompt overlay on top of D.U.H.'s base constitution. The overlays live in `duh/constitution.py` under `AGENT_OVERLAYS`.

| Type | Specialization | Default Model |
|------|---------------|---------------|
| `general` | General-purpose coding assistant. Uses the base constitution with no overlay. | `inherit` (parent's model) |
| `coder` | Writing clean, correct, well-tested code. Reads existing code to understand patterns before writing. Follows TDD for bug fixes. | `sonnet` |
| `researcher` | Reading, searching, and understanding code. Uses Glob, Grep, and Read extensively. Summarizes findings with file paths and line numbers. Does not modify files unless explicitly asked. | `haiku` |
| `planner` | Breaking down complex tasks into clear, actionable steps. Analyzes the codebase, creates concrete plans with specific files. Does not implement -- just plans. | `opus` |
| `reviewer` | Reviewing code for correctness, security, and quality. Prioritizes bugs, then security, then regressions, then missing tests, then style. Cites file and line. | `sonnet` |
| `subagent` | Generic delegated task execution. Executes directly without re-delegating. Uses absolute file paths. | `inherit` (parent's model) |

The `model` field on any agent can be overridden to `haiku`, `sonnet`, `opus`, or `inherit`. When set to `inherit` (or omitted), the agent uses its type's default from the table above. If the default is also `inherit`, the parent's model is used unchanged.

## AgentTool

The `Agent` tool lets the model spawn a single subagent. The model calls it like any other tool.

### Input Schema

```json
{
  "prompt": "string (required) -- the task for the subagent",
  "agent_type": "string (optional) -- one of: general, coder, researcher, planner, reviewer, subagent",
  "model": "string (optional) -- one of: haiku, sonnet, opus, inherit"
}
```

Only `prompt` is required. If `agent_type` is omitted, it defaults to `general`. If `model` is omitted, the agent type's default model is used.

### How It Works

1. The model calls the `Agent` tool with a prompt and optional type/model.
2. D.U.H. creates a new `Engine` instance with the agent type's system prompt.
3. The child engine runs the prompt to completion (up to 50 turns).
4. The final assistant text is returned to the parent as the tool result.

### Example

The model might call:

```json
{
  "prompt": "Search the codebase for all uses of deprecated_function() and list each file:line",
  "agent_type": "researcher",
  "model": "haiku"
}
```

This spawns a researcher agent running on Haiku. The researcher uses Glob, Grep, and Read to find all call sites, then returns a summary. The parent receives the summary as a tool result and continues its conversation.

## SwarmTool

The `Swarm` tool spawns multiple subagents in parallel using `asyncio.gather`. It accepts 1 to 5 tasks and runs them concurrently.

### Input Schema

```json
{
  "tasks": [
    {
      "prompt": "string (required) -- the task for this subagent",
      "agent_type": "string (optional) -- general, coder, researcher, planner, reviewer, subagent",
      "model": "string (optional) -- haiku, sonnet, opus, inherit"
    }
  ]
}
```

`tasks` is required and must contain 1-5 items. Each item follows the same schema as `AgentTool`.

### Partial Failure Handling

Swarm uses `asyncio.gather(*coros, return_exceptions=True)`, so individual agent failures do not crash the entire swarm. The output reports each task's status independently:

- If a task succeeds: status is `OK` with the turn count and result text.
- If a task fails: status is `ERROR` with the error message.
- If all tasks fail: the overall `ToolResult` is marked as an error.
- If at least one task succeeds: the overall result is not an error, even if some tasks failed.

### Output Format

```
--- Task 1/3 [researcher] ---
Prompt: Find all TODO comments in src/
Status: OK (4 turns)
Result:
Found 12 TODOs across 8 files...

--- Task 2/3 [coder] ---
Prompt: Add input validation to parse_config()
Status: OK (7 turns)
Result:
Added validation with early returns...

--- Task 3/3 [reviewer] ---
Prompt: Review the changes in src/auth.py
Status: ERROR
Error: context window exceeded
```

## Coordinator Mode

Coordinator mode changes the main agent's behavior: instead of executing tools directly, it becomes a task coordinator that delegates all work to subagents via the Swarm tool.

### Activation

**CLI flag:**

```
duh --coordinator
```

**Runtime command (TUI only):**

```
/mode coordinator
```

To switch back:

```
/mode normal
```

To check current mode:

```
/mode
```

### What Changes

When coordinator mode is active, the coordinator system prompt is prepended to the main agent's system prompt. The coordinator prompt instructs the agent to:

1. Break the user's request into independent subtasks (2-5 tasks).
2. Delegate each subtask to a specialized subagent using the Swarm tool.
3. Synthesize results from all subagents into a coherent response.
4. Never use file tools (Read, Write, Edit, Bash, etc.) directly.

The coordinator chooses agent types based on the subtask: `coder` for implementation, `researcher` for analysis, `planner` for design, `reviewer` for quality checks.

### Mode Persistence

The mode is persisted in session metadata (`coordinator_mode` flag on the first message), so `--continue` resumes in the same mode the session was using.

## How It Works

### Tool Inheritance

Child agents receive the parent's deps (call_model, run_tool, approve) and the parent's tool list, with agent-spawning tools removed:

- **AgentTool** children get all parent tools **minus Agent** (prevents spawning grandchildren).
- **SwarmTool** children get all parent tools **minus Agent and Swarm** (same prevention).

This means child agents can Read, Write, Edit, Bash, Grep, Glob -- everything the parent can do except spawn further agents.

### Recursion Prevention

Agent nesting is hard-capped at depth 1. A parent can spawn children, but children cannot spawn grandchildren. This is enforced by stripping the Agent and Swarm tools from the child's tool list. The constant `MAX_AGENT_DEPTH = 1` in `agent_tool.py` documents this design decision.

### Engine Isolation

Each child agent gets:

- Its own `Engine` instance with a fresh conversation history.
- Its own system prompt (the agent type's constitution overlay).
- The parent's model (or an override if specified).
- The parent's working directory.
- A maximum of 50 turns (or the agent type's max, whichever is lower).

Children do not share conversation context with each other or with the parent. The only information flow is: parent sends a prompt, child returns a result.

## Examples

### Research + Code in Parallel

Use the Swarm tool to research and implement simultaneously:

```json
{
  "tasks": [
    {
      "prompt": "Read src/auth/ and document how the token refresh flow works. List all files involved and the call chain.",
      "agent_type": "researcher",
      "model": "haiku"
    },
    {
      "prompt": "Add rate limiting to the /api/login endpoint in src/api/routes.py. Use the existing RateLimiter class from src/middleware/.",
      "agent_type": "coder",
      "model": "sonnet"
    }
  ]
}
```

The researcher reads and summarizes while the coder implements. Both run concurrently.

### Review Before Commit

Use the Agent tool to get a review of changes before committing:

```json
{
  "prompt": "Review the staged git changes (run `git diff --cached`). Check for bugs, security issues, and missing error handling.",
  "agent_type": "reviewer"
}
```

The reviewer agent runs `git diff --cached`, analyzes the diff, and returns findings. The parent can then address issues before committing.

### Multi-File Refactor with Coordinator

Start D.U.H. in coordinator mode:

```
duh --coordinator
```

Then ask: "Rename the `UserManager` class to `AccountService` across the entire codebase and update all tests."

The coordinator will:

1. Spawn a researcher to find all files referencing `UserManager`.
2. Spawn a planner to create the rename plan (which files, which imports, which tests).
3. Spawn a coder to execute the renames.
4. Spawn a reviewer to verify nothing was missed.

All independent steps run in parallel via the Swarm tool.

## Cost Considerations

Each subagent is a separate API conversation. Costs scale linearly:

| Scenario | API Calls |
|----------|-----------|
| Single agent (normal mode) | 1 conversation |
| Agent tool (one subagent) | 1 parent + 1 child = 2 conversations |
| Swarm with 3 tasks | 1 parent + 3 children = 4 conversations |
| Coordinator with 4 subtasks | 1 coordinator + 4 workers = 5 conversations |

Each conversation may use multiple turns internally, and each turn is an API call. A 5-agent swarm where each agent uses 5 turns is 25 API calls total (plus the parent's turns).

**Model selection matters.** The default model assignments are tuned for cost/quality tradeoffs:

- `haiku` for researchers -- fast, cheap, sufficient for reading and searching.
- `sonnet` for coders and reviewers -- balanced quality and speed.
- `opus` for planners -- complex reasoning benefits from the strongest model.
- `inherit` for general/subagent -- uses whatever the parent is using.

Override with the `model` field when the defaults don't fit. Use `haiku` aggressively for simple subtasks to keep costs down.

## Limitations

1. **No shared state between agents.** Each child agent has its own conversation. Agent A cannot see what Agent B found. The parent must synthesize across results.

2. **No cross-agent context.** If a researcher discovers something relevant to the coder's task, there is no mechanism for the researcher to notify the coder mid-execution. The parent receives both results after completion.

3. **Maximum 5 parallel tasks.** The Swarm tool enforces `maxItems: 5` on the tasks array. For more than 5 subtasks, use multiple Swarm calls sequentially.

4. **No grandchildren.** Agent nesting is capped at depth 1. A child agent cannot spawn its own subagents. This prevents runaway recursive spawning.

5. **Context window per agent.** Each child agent has its own context window. Long tasks that produce large outputs may hit the context limit independently.

6. **Coordinator overhead.** In coordinator mode, the coordinator uses an extra turn to break down the task before any work begins. For simple tasks, this overhead is pure waste -- use normal mode instead.

7. **Partial failure visibility.** While the Swarm tool reports per-task success/failure, the parent model must interpret these results and decide how to proceed. There is no automatic retry mechanism.
