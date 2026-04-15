# Tools Reference

D.U.H. ships with 27 built-in tools organized into eight categories. Every tool follows the same async interface: `call(input, context) -> ToolResult`. Tools declare their capabilities via the security trifecta system, and each exposes a JSON Schema for input validation.

---

## File Operations

### Read

Read a file from disk and return its contents with line numbers (1-based, tab-separated). Renders `.ipynb` notebooks in a human-readable cell format instead of raw JSON. Files larger than 50 MB must be read with `offset`/`limit`.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | yes | Absolute path to the file to read |
| `offset` | integer | no | Line number to start reading from (0-based). Default: 0 |
| `limit` | integer | no | Maximum number of lines to return. Omit to read entire file |

```json
{
  "file_path": "/home/user/project/src/main.py",
  "offset": 10,
  "limit": 50
}
```

---

### Write

Write content to a file. Creates parent directories if they do not exist. Content size is capped at 50 MB. Reports git dirty state after writing.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | yes | Absolute path to the file to write |
| `content` | string | yes | The content to write to the file |

```json
{
  "file_path": "/home/user/project/src/config.py",
  "content": "DEBUG = True\nLOG_LEVEL = 'info'\n"
}
```

---

### Edit

Replace an exact occurrence of `old_string` with `new_string` in a file. Fails if the old string is not found or matches more than once (unless `replace_all` is set). Returns a unified diff of the change.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | yes | Absolute path to the file to edit |
| `old_string` | string | yes | The exact text to find and replace |
| `new_string` | string | yes | The replacement text |
| `replace_all` | boolean | no | If true, replace all occurrences. Default: false |

```json
{
  "file_path": "/home/user/project/src/main.py",
  "old_string": "DEBUG = False",
  "new_string": "DEBUG = True"
}
```

---

### MultiEdit

Apply multiple edits to one or more files in a single call. Each edit performs the same exact-string replacement logic as Edit. Edits are applied sequentially; a failing edit is recorded but does not block the rest. Validates file permissions upfront before applying any changes.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `edits` | array | yes | List of edit objects, each with `file_path`, `old_string`, `new_string` |

Each edit object:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file_path` | string | yes | Absolute path to the file |
| `old_string` | string | yes | Exact text to find |
| `new_string` | string | yes | Replacement text |

```json
{
  "edits": [
    {
      "file_path": "/home/user/project/src/a.py",
      "old_string": "import os",
      "new_string": "import os\nimport sys"
    },
    {
      "file_path": "/home/user/project/src/b.py",
      "old_string": "v1",
      "new_string": "v2"
    }
  ]
}
```

---

### NotebookEdit

Edit, insert, or delete cells in Jupyter `.ipynb` notebooks. Preserves all notebook metadata, outputs, and kernel info.

- **Modify**: provide `cell_index` and `new_source`
- **Insert (append)**: set `cell_index` to `-1` with `new_source`
- **Delete**: provide `cell_index` with `new_source` set to `null`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `notebook_path` | string | yes | Absolute path to the `.ipynb` file |
| `cell_index` | integer | yes | Index of the cell to modify/delete. Use `-1` to append |
| `new_source` | string or null | no | New cell content. `null` deletes the cell |
| `cell_type` | string | no | `"code"` or `"markdown"` (used when inserting). Default: `"code"` |

```json
{
  "notebook_path": "/home/user/notebooks/analysis.ipynb",
  "cell_index": -1,
  "new_source": "import pandas as pd\ndf = pd.read_csv('data.csv')",
  "cell_type": "code"
}
```

---

## Search

### Glob

Find files matching a glob pattern. Returns matching file paths sorted alphabetically, filtered to files only (directories excluded).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pattern` | string | yes | Glob pattern to match (e.g. `**/*.py`) |
| `path` | string | no | Directory to search in. Defaults to working directory |

```json
{
  "pattern": "**/*.py",
  "path": "/home/user/project/src"
}
```

---

### Grep

Search file contents using a regular expression. Searches a single file or recursively through a directory. Returns matching lines in `filepath:lineno:line` format.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pattern` | string | yes | Regular expression pattern to search for |
| `path` | string | no | File or directory to search in. Defaults to working directory |
| `glob` | string | no | Glob filter for files when searching a directory (e.g. `*.py`) |
| `case_insensitive` | boolean | no | If true, search case-insensitively. Default: false |

```json
{
  "pattern": "def\\s+test_",
  "path": "/home/user/project/tests",
  "glob": "*.py",
  "case_insensitive": false
}
```

---

### ToolSearch

Progressive tool disclosure: search for tools by keyword or select specific tools by name to load their full JSON Schema on demand. Deferred tools (e.g. MCP tools, LSP) appear by name only in the system prompt until their schema is loaded via this tool.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | no | Keyword search across tool names and descriptions. Prefix with `select:` to load schemas |
| `select` | string | no | Comma-separated tool names to load full schemas for |
| `max_results` | integer | no | Maximum search results. Default: 5 (range: 1-50) |

```json
{
  "query": "select:LSP,WebFetch"
}
```

```json
{
  "query": "docker container",
  "max_results": 3
}
```

---

### LSP

Query language-server-style information via static analysis (no running LSP server required). Uses `ast.parse` for Python files and regex-based heuristics for other languages. This tool is registered as a deferred tool -- load its schema via ToolSearch before calling.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | yes | `"definition"`, `"references"`, `"hover"`, or `"symbols"` |
| `file` | string | yes | Path to the file to analyze |
| `line` | integer | no | 1-based line number of the symbol (required for definition/references/hover) |
| `character` | integer | no | 0-based character offset on the line |

**Actions:**
- `definition` -- find where a symbol is defined (class, function, variable)
- `references` -- find all usages of a symbol in the file
- `hover` -- show function signature and docstring
- `symbols` -- list all top-level symbols in the file

```json
{
  "action": "symbols",
  "file": "/home/user/project/src/main.py"
}
```

```json
{
  "action": "definition",
  "file": "/home/user/project/src/main.py",
  "line": 42,
  "character": 8
}
```

---

### TestImpact

Analyze which test files are affected by changed source files. Auto-detects changes from `git diff` or accepts an explicit file list. Finds affected tests via import scanning and naming convention matching (`src/foo.py` -> `tests/test_foo.py`). Returns a ready-to-run `pytest` command.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `changed_files` | array of strings | no | List of changed file paths (relative to project root). Auto-detects from git if omitted |

```json
{
  "changed_files": ["duh/tools/bash.py", "duh/kernel/tool.py"]
}
```

```json
{}
```

---

## Shell

### Bash

Execute a shell command via asyncio subprocess and return stdout/stderr. Supports `bash` on Unix and PowerShell on Windows. Commands prefixed with `bg:` are submitted to a background job queue and return immediately with a job ID. Dangerous commands are blocked by the built-in security classifier. Output exceeding the tool output limit is truncated.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | yes | The shell command to execute. Prefix with `bg:` for background execution |
| `timeout` | integer | no | Timeout in seconds. Default: 120 |
| `shell` | string | no | Shell backend: `"auto"` (default), `"bash"`, or `"powershell"` |

```json
{
  "command": "pytest tests/ -q --tb=short",
  "timeout": 300
}
```

```json
{
  "command": "bg: npm run build",
  "timeout": 600
}
```

---

## Web

### WebFetch

Fetch a URL and return its text content. HTML tags are automatically stripped. Follows redirects. Content is truncated at 100 KB.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | The URL to fetch (must start with `http://` or `https://`) |
| `prompt` | string | no | Optional hint for what to extract from the page |

```json
{
  "url": "https://docs.python.org/3/library/asyncio.html",
  "prompt": "Find the section about creating tasks"
}
```

---

### WebSearch

Search the web for a query. Requires either `SERPER_API_KEY` or `TAVILY_API_KEY` to be set in the environment. Returns the top 5 results with titles, links, and snippets.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | yes | The search query |

```json
{
  "query": "python asyncio best practices 2025"
}
```

---

### HTTP

Send an HTTP request and return the status code, key headers, and response body. Supports GET, POST, PUT, DELETE, and PATCH. JSON responses are auto-detected and pretty-printed. Response bodies are truncated at 10 KB.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `method` | string | yes | HTTP method: `GET`, `POST`, `PUT`, `DELETE`, or `PATCH` |
| `url` | string | yes | The URL to send the request to (must start with `http://` or `https://`) |
| `headers` | object | no | Request headers as key-value pairs (e.g. `{"Authorization": "Bearer ..."}`) |
| `body` | string | no | Request body (sent as-is) |
| `timeout` | integer | no | Request timeout in seconds. Default: 30 |

```json
{
  "method": "POST",
  "url": "http://localhost:8000/api/items",
  "headers": {"Content-Type": "application/json"},
  "body": "{\"name\": \"widget\", \"count\": 5}"
}
```

---

## Multi-Agent

### Agent

Spawn a subagent to handle a task independently. The subagent gets its own conversation and can use all tools (Read, Bash, Grep, etc.) but cannot spawn further agents (max depth = 1). Use for research, coding, or planning subtasks.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `prompt` | string | yes | The task for the subagent |
| `agent_type` | string | no | Agent specialization: `"general"`, `"coder"`, `"researcher"`, `"planner"`, `"reviewer"`, `"subagent"`. Default: `"general"` |
| `model` | string | no | Model for the subagent: `"haiku"`, `"sonnet"`, `"opus"`, `"inherit"`. Defaults to the agent type's preferred model |

```json
{
  "prompt": "Research the best Python async HTTP client libraries and compare their performance characteristics",
  "agent_type": "researcher",
  "model": "sonnet"
}
```

---

### Swarm

Spawn multiple subagents in parallel using `asyncio.gather`. Each child gets its own conversation and full tool access but cannot spawn further agents or swarms. Results from all agents are collected and returned together. Maximum 5 tasks per swarm.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `tasks` | array | yes | List of task objects (1-5 items) |

Each task object:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | yes | The task for the subagent |
| `agent_type` | string | no | Specialization. Default: `"general"` |
| `model` | string | no | Model: `"haiku"`, `"sonnet"`, `"opus"`, `"inherit"`. Default: `"inherit"` |

```json
{
  "tasks": [
    {
      "prompt": "Review src/auth.py for security issues",
      "agent_type": "reviewer"
    },
    {
      "prompt": "Write unit tests for src/auth.py",
      "agent_type": "coder",
      "model": "sonnet"
    },
    {
      "prompt": "Check test coverage for the auth module",
      "agent_type": "researcher"
    }
  ]
}
```

---

## Memory

### MemoryStore

Save a fact about the codebase for future sessions. Facts are stored per-project at `~/.config/duh/memory/<project-hash>/facts.jsonl`. Duplicate keys overwrite previous entries.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `key` | string | yes | Short identifier for the fact (e.g. `"auth-pattern"`, `"db-schema-version"`). Used for deduplication |
| `value` | string | yes | The fact or learning to remember |
| `tags` | array of strings | no | Optional tags for categorization (e.g. `["auth", "security"]`) |

```json
{
  "key": "auth-pattern",
  "value": "JWT with refresh tokens stored in httponly cookies. Refresh endpoint at /api/auth/refresh.",
  "tags": ["auth", "security", "api"]
}
```

---

### MemoryRecall

Search previously saved facts about the codebase by keyword. Returns matching entries with their keys, values, tags, and timestamps.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | yes | Keyword or phrase to search for in saved facts |
| `limit` | integer | no | Maximum number of results. Default: 10 (range: 1-50) |

```json
{
  "query": "authentication",
  "limit": 5
}
```

---

## Development

### Task

In-session task and todo management. Create, update, or list tasks to track work as a visible checklist. Tasks have statuses: `pending`, `in_progress`, or `completed`.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | yes | `"create"`, `"update"`, or `"list"` |
| `description` | string | create | Task description (required for `create`) |
| `task_id` | string | update | Task ID (required for `update`) |
| `status` | string | update | New status (required for `update`): `"pending"`, `"in_progress"`, `"completed"` |

```json
{
  "action": "create",
  "description": "Implement input validation for the /api/users endpoint"
}
```

```json
{
  "action": "update",
  "task_id": "task-001",
  "status": "completed"
}
```

---

### TodoWrite

Structured checklist management. Create or batch-update a todo list with fine-grained status tracking. Each item has an id, text, and status. All todos are managed in memory for the current session.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `todos` | array | yes | List of todo items |

Each todo item:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique todo identifier |
| `text` | string | yes | Todo description |
| `status` | string | yes | `"pending"`, `"in_progress"`, `"done"`, `"blocked"`, `"cancelled"` |

```json
{
  "todos": [
    {"id": "1", "text": "Fix the null pointer in auth handler", "status": "done"},
    {"id": "2", "text": "Write tests for edge cases", "status": "in_progress"},
    {"id": "3", "text": "Update API docs", "status": "pending"}
  ]
}
```

---

### EnterWorktree

Create a new git worktree with an isolated branch and switch the engine's working directory into it. Prevents nesting (cannot enter a worktree while already inside one). Stores worktree state in context metadata so other tools are aware.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `branch` | string | no | Branch name for the worktree. Auto-generates a unique name if omitted |
| `path` | string | no | Filesystem path for the worktree. Defaults to `/tmp/duh-worktrees/<branch>` |

```json
{
  "branch": "feature/add-auth",
  "path": "/tmp/duh-worktrees/feature-add-auth"
}
```

---

### ExitWorktree

Leave a git worktree and restore the original working directory. Optionally removes the worktree on exit. Only works when inside a worktree created by EnterWorktree.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `cleanup` | boolean | no | Remove the worktree after exiting. Default: true |

```json
{
  "cleanup": true
}
```

---

### Skill

Invoke a skill by name. Skills are reusable prompt templates for common workflows (e.g. commit, review-pr). The `$ARGUMENTS` placeholder in the skill template is substituted with the provided args.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `skill` | string | yes | The skill name to invoke (e.g. `"commit"`, `"review-pr"`) |
| `args` | string | no | Arguments to pass to the skill template |

```json
{
  "skill": "commit",
  "args": "-m 'Fix null check in auth handler'"
}
```

---

### AskUserQuestion

Block execution and prompt the user for a response. Use when clarification or a decision is needed from the user. Returns the user's answer as the tool output.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `question` | string | yes | The question to ask the user |

```json
{
  "question": "The tests directory contains both unit/ and integration/ subdirs. Which should I add the new test to?"
}
```

---

### GitHub

Interact with GitHub pull requests via the `gh` CLI. Requires the GitHub CLI to be installed and authenticated (`gh auth login`).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | yes | `"pr_list"`, `"pr_create"`, `"pr_view"`, `"pr_diff"`, `"pr_checks"` |
| `number` | integer | pr_view/diff/checks | PR number |
| `title` | string | pr_create | PR title |
| `body` | string | no | PR body/description (for `pr_create`) |
| `base` | string | no | Base branch for `pr_create` |
| `state` | string | no | Filter by state for `pr_list`: `"open"`, `"closed"`, `"merged"`, `"all"`. Default: `"open"` |
| `limit` | integer | no | Max PRs to list. Default: 30 |

```json
{
  "action": "pr_list",
  "state": "open",
  "limit": 10
}
```

```json
{
  "action": "pr_create",
  "title": "Fix auth token refresh race condition",
  "body": "Adds mutex around token refresh to prevent concurrent refreshes.",
  "base": "main"
}
```

---

### Docker

Manage Docker containers via the `docker` CLI. Supports build, run, ps, logs, exec, and images. Requires Docker to be installed.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | yes | `"build"`, `"run"`, `"ps"`, `"logs"`, `"exec"`, `"images"` |
| `tag` | string | build | Image tag |
| `path` | string | build | Build context path |
| `image` | string | run | Image name |
| `command` | string | no | Command to run inside the container (for `run`/`exec`) |
| `container` | string | logs/exec | Container ID or name |
| `mount_cwd` | boolean | no | Mount current working directory into the container (for `run`). Default: false |
| `tail` | integer | no | Number of log lines to tail (for `logs`). Default: 50 |

```json
{
  "action": "build",
  "tag": "myapp:latest",
  "path": "."
}
```

```json
{
  "action": "run",
  "image": "python:3.12-slim",
  "command": "python -c 'print(\"hello\")'",
  "mount_cwd": true
}
```

---

### Database

Execute read-only SQL queries against a SQLite database. Only SELECT statements are allowed -- write operations are blocked. Results are truncated at 100 rows. Connection defaults to the `DATABASE_URL` environment variable.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | yes | `"query"` (read-only SELECT), `"schema"` (table columns/types), `"tables"` (list all tables) |
| `sql` | string | query | SQL query (only SELECT). Required for `query` action |
| `table` | string | schema | Table name. Required for `schema` action |
| `connection_string` | string | no | Path to SQLite database (`.db`, `.sqlite`, `.sqlite3`, or `:memory:`). Defaults to `DATABASE_URL` env var |

```json
{
  "action": "tables",
  "connection_string": "/home/user/project/data/app.db"
}
```

```json
{
  "action": "query",
  "sql": "SELECT id, name, email FROM users WHERE active = 1 LIMIT 20",
  "connection_string": "/home/user/project/data/app.db"
}
```

```json
{
  "action": "schema",
  "table": "users",
  "connection_string": "/home/user/project/data/app.db"
}
```

---

## Tool Index

| # | Tool | Category | Read-Only | Description |
|---|------|----------|-----------|-------------|
| 1 | Read | File Operations | yes | Read a file with line numbers |
| 2 | Write | File Operations | no | Write content to a file |
| 3 | Edit | File Operations | no | Replace exact string in a file |
| 4 | MultiEdit | File Operations | no | Batch edits across multiple files |
| 5 | NotebookEdit | File Operations | no | Edit Jupyter notebook cells |
| 6 | Glob | Search | yes | Find files by glob pattern |
| 7 | Grep | Search | yes | Search file contents with regex |
| 8 | ToolSearch | Search | yes | Discover and load deferred tool schemas |
| 9 | LSP | Search | yes | Static analysis: definition, references, hover, symbols (deferred) |
| 10 | TestImpact | Search | yes | Find tests affected by changed files |
| 11 | Bash | Shell | no | Execute shell commands |
| 12 | WebFetch | Web | yes | Fetch a URL and return text content |
| 13 | WebSearch | Web | yes | Search the web (requires API key) |
| 14 | HTTP | Web | no | Send HTTP requests for API testing |
| 15 | Agent | Multi-Agent | no | Spawn a single subagent |
| 16 | Swarm | Multi-Agent | no | Spawn multiple subagents in parallel |
| 17 | MemoryStore | Memory | no | Save a fact for future sessions |
| 18 | MemoryRecall | Memory | yes | Search previously saved facts |
| 19 | Task | Development | no | In-session task tracking |
| 20 | TodoWrite | Development | no | Structured checklist management |
| 21 | EnterWorktree | Development | no | Create and enter a git worktree |
| 22 | ExitWorktree | Development | no | Leave and optionally remove a git worktree |
| 23 | Skill | Development | yes | Invoke a reusable prompt template |
| 24 | AskUserQuestion | Development | yes | Prompt the user for input |
| 25 | GitHub | Development | no | GitHub PR workflow via gh CLI |
| 26 | Docker | Development | no | Docker container management |
| 27 | Database | Development | yes | Read-only SQLite queries |
