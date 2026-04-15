# Getting Started with D.U.H.

**D.U.H. -- Duh is a Universal Harness.** A provider-agnostic AI coding agent that connects any LLM to your codebase with full tool use, session management, and a terminal UI.

---

## 1. Installation

D.U.H. requires **Python 3.12 or later**.

```bash
pip install duh-cli
```

Verify the installation:

```bash
duh --version
```

You should see output like:

```
duh 0.5.0
```

### Installing from source

If you prefer to install from the repository:

```bash
git clone https://github.com/nikhilvallishayee/duh.git
cd duh
pip install -e .
```

For development (includes pytest, hypothesis, and other test tooling):

```bash
pip install -e ".[dev]"
```

---

## 2. Quick Start

### One-shot prompt (print mode)

Run a single prompt and get the answer printed to stdout:

```bash
duh -p "explain this codebase"
```

This is the fastest way to ask a question. D.U.H. reads your project files, sends them as context, and prints the response. No interactive session needed.

### Interactive TUI

Launch the full terminal UI for an ongoing conversation:

```bash
duh --tui
```

The TUI gives you a rich interface with syntax-highlighted code blocks, a scrollable message log, token/cost tracking in the status bar, and slash commands for controlling the session.

### Interactive REPL (readline)

If you prefer a lightweight readline-based prompt:

```bash
duh
```

Running `duh` with no arguments drops you into the REPL. It supports tab-completion for slash commands and persists your command history across sessions.

---

## 3. API Key Setup

D.U.H. needs credentials for at least one LLM provider. The simplest way is to set an environment variable.

### Environment variable (recommended for quick setup)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Add this to your shell profile (`~/.zshrc`, `~/.bashrc`, etc.) so it persists.

### D.U.H. auth store (via /connect)

You can also store credentials through the interactive REPL or TUI. Once inside a session, run:

```
/connect anthropic
```

You will be prompted for your API key. D.U.H. saves it securely to `~/.config/duh/` so you do not need an environment variable.

For OpenAI, you have two options:

```
/connect openai
```

This prompts you to choose between:
1. **ChatGPT Plus/Pro login** -- OAuth-based, uses your existing ChatGPT subscription
2. **API key** -- standard OpenAI API key

Both methods persist credentials locally so future sessions pick them up automatically.

### How auto-detection works

When you start D.U.H. without specifying a provider, it checks in this order:

1. `ANTHROPIC_API_KEY` environment variable or saved Anthropic key
2. `OPENAI_API_KEY` environment variable, saved OpenAI key, or ChatGPT OAuth token
3. Local Ollama server (probes `localhost:11434`)

The first provider found becomes the default.

---

## 4. Provider Options

D.U.H. works with multiple LLM providers. Use the `--provider` flag to choose one explicitly, or let auto-detection handle it.

### Anthropic (default when available)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
duh --tui
```

Default model: `claude-sonnet-4-6`. To use a different model:

```bash
duh --tui --model claude-opus-4-6
```

### OpenAI

```bash
export OPENAI_API_KEY="sk-..."
duh --tui --provider openai --model gpt-4o
```

Or connect via ChatGPT subscription for Codex models:

```bash
duh --tui --provider openai --model gpt-5.2-codex
```

### Ollama (local, no API key needed)

Run models locally with [Ollama](https://ollama.com/). Make sure `ollama serve` is running, then:

```bash
duh --tui --provider ollama --model llama3
```

No API key required -- D.U.H. connects to Ollama on `localhost:11434`.

### LiteLLM (any provider)

[LiteLLM](https://github.com/BerriAI/litellm) acts as a universal adapter. Use the `provider/model` syntax to route through any supported backend:

```bash
duh --tui --provider litellm --model gemini/gemini-2.5-flash
```

```bash
duh --tui --provider litellm --model bedrock/claude-3-haiku
```

Any model string containing a `/` is automatically routed through LiteLLM.

### Switching models mid-session

You do not need to restart. Inside any session, use:

```
/model claude-opus-4-6
```

Or list all available models for your connected providers:

```
/models
```

---

## 5. Your First Session

Here is a walkthrough of a real coding task using the TUI.

### Step 1: Launch in your project directory

```bash
cd ~/my-project
duh --tui
```

D.U.H. automatically picks up your project context -- it reads `DUH.md`, `CLAUDE.md`, and other instruction files from the project root.

### Step 2: Ask about the codebase

Type a question at the input prompt:

```
What does the authentication module do? Walk me through the flow.
```

D.U.H. has access to file tools (Read, Write, Edit, Glob, Grep, Bash) so it can explore your codebase, read files, and give grounded answers.

### Step 3: Make a change

Ask it to do something:

```
Add input validation to the /login endpoint. Use pydantic for the request body.
```

D.U.H. will propose file edits. Depending on your approval mode, you may be asked to confirm each change:

- **suggest** (default) -- reads are auto-approved, writes require confirmation
- **auto-edit** -- reads and writes are auto-approved, only destructive actions need confirmation
- **full-auto** -- everything auto-approved

Set approval mode at launch:

```bash
duh --tui --approval-mode auto-edit
```

### Step 4: Check cost

At any point, type:

```
/cost
```

This shows input tokens, output tokens, and estimated USD cost for the session.

### Step 5: Compact if needed

If you are in a long session and context is getting large:

```
/compact
```

This summarizes older messages to free up context window space while preserving the key information.

---

## 6. Resume Sessions

D.U.H. automatically saves sessions. You can pick up right where you left off.

### Continue the most recent session

```bash
duh --tui --continue
```

Or in the REPL:

```bash
duh --continue
```

The short flag also works:

```bash
duh -c
```

### Resume a specific session by ID

```bash
duh --resume abc123
```

Use `/sessions` inside any session to see a list of recent session IDs for the current project.

### Fork a session

Resume from a session but branch into a new one (the original stays untouched):

```bash
duh --tui --resume abc123 --fork-session
```

### Summarize on resume

If the session was long, you can summarize older messages to save context:

```bash
duh --tui --continue --summarize
```

---

## 7. Project Configuration

### DUH.md -- your project instructions

Create a `DUH.md` file in your project root to give D.U.H. standing instructions. These are injected into the system prompt every session.

```markdown
# DUH.md

## Project conventions
- Use Python 3.12+ type hints everywhere
- All API endpoints must have pydantic request/response models
- Tests go in tests/ using pytest

## Architecture notes
- FastAPI app in src/api/
- Database layer in src/db/ using SQLAlchemy
```

### CLAUDE.md compatibility

D.U.H. also reads `CLAUDE.md` files, so existing projects that use Claude Code's instruction format work out of the box. Loading order (lowest to highest precedence):

1. `~/.config/duh/DUH.md` -- user-global instructions
2. `DUH.md`, `.duh/DUH.md`, or `CLAUDE.md` per directory (from git root down to cwd)
3. `.duh/rules/*.md` and `.claude/rules/*.md` per directory
4. `AGENTS.md` per directory

Files loaded later take higher precedence -- the model pays more attention to them.

### @includes

Instruction files support `@path` references to pull in content from other files:

```markdown
# DUH.md

@./docs/api-conventions.md
@./docs/style-guide.md

## Additional project rules
...
```

Include rules:
- Relative paths: `@./path/to/file.md`
- Home paths: `@~/global-rules.md`
- Absolute paths: `@/etc/shared-rules.md`
- Supported extensions: `.md`, `.txt`, `.yaml`, `.yml`, `.toml`, `.json`, `.py`, `.ts`, `.js`, `.sh`
- Up to 5 levels of nesting
- Circular references are automatically detected and skipped
- Paths inside code fences (`` ``` ``) are ignored

### .duh/settings.json -- project config

For structured settings, create `.duh/settings.json` in your project:

```json
{
  "model": "claude-sonnet-4-6",
  "provider": "anthropic",
  "max_turns": 50,
  "max_cost": 5.0,
  "approval_mode": "auto-edit"
}
```

Settings precedence (highest wins):
1. CLI flags (`--model`, `--provider`, etc.)
2. Environment variables (`DUH_MODEL`, `DUH_PROVIDER`, `DUH_MAX_TURNS`, `DUH_MAX_COST`)
3. Project config (`.duh/settings.json`)
4. User config (`~/.config/duh/settings.json`)

---

## 8. Key Commands

These slash commands work in both the REPL and TUI.

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/model [name]` | Show current model, or switch to a new one |
| `/models` | List models for all connected providers |
| `/connect [provider]` | Connect a provider's auth (openai, anthropic) |
| `/cost` | Show token usage and estimated cost for this session |
| `/context` | Show context window token breakdown |
| `/compact` | Summarize older messages to free context space |
| `/style [name]` | Show or set output style: `default`, `concise`, or `verbose` |
| `/brief` | Toggle brief mode for shorter responses |
| `/memory` | Memory facts -- `list`, `search <q>`, `show <key>`, `delete <key>` |
| `/sessions` | List sessions for the current project |
| `/search <query>` | Search through messages in the current session |
| `/undo` | Undo the last file modification (Write or Edit) |
| `/plan <desc>` | Enter plan mode -- outline steps before executing |
| `/changes` | Show files touched in this session (with git diff stats) |
| `/tasks` | Show the task checklist |
| `/clear` | Clear conversation history display |
| `/exit` or `/quit` | Exit the session |

### Additional REPL-only commands

| Command | Description |
|---------|-------------|
| `/git` | Show git branch, status, and recent commits |
| `/pr` | GitHub PR operations: `list`, `view <n>`, `diff <n>`, `checks <n>` |
| `/template` | Prompt templates: `list`, `use <name>`, `<name> <prompt>` |
| `/jobs` | Background jobs: list or get results |
| `/health` | Run provider and MCP health checks |
| `/audit [N]` | Show recent audit log entries |
| `/snapshot` | Ghost snapshot: save, apply, or discard |
| `/attach <path>` | Attach a file to the next message |

---

## 9. Next Steps

Now that you are up and running, explore these topics:

- **[Architecture](../architecture-comparison.md)** -- how D.U.H. is built and how it compares to other tools
- **[ADRs](../adrs/)** -- architecture decision records explaining every design choice
- **[Security](../adrs/)** -- security scanning, sandboxing, and the lethal trifecta protection (ADR-053)
- **[MCP Servers](../adrs/)** -- connecting external tool servers via the Model Context Protocol

### Useful CLI flags reference

```bash
# Control output
duh --tui --output-style concise       # Shorter responses
duh --tui --brief                      # Brief mode

# Cost and resource limits
duh --tui --max-cost 2.0               # Stop at $2.00
duh --tui --max-turns 20               # Limit agentic turns

# Safety controls
duh --tui --approval-mode suggest      # Confirm writes (default)
duh --tui --approval-mode full-auto    # Auto-approve everything

# Debugging
duh --debug                            # Full event tracing to stderr
duh --log-json                         # Structured JSON logs

# Run diagnostics
duh doctor                             # Health checks for your setup
```

### Getting help

- [GitHub Issues](https://github.com/nikhilvallishayee/duh/issues) -- report bugs or request features
- `duh doctor` -- run diagnostics if something is not working
- `/health` -- check provider connectivity from inside a session
