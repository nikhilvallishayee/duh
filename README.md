# D.U.H. — D.U.H. is a Universal Harness

[![CI](https://github.com/nikhilvallishayee/duh/actions/workflows/ci.yml/badge.svg)](https://github.com/nikhilvallishayee/duh/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/duh-cli?color=blue)](https://pypi.org/project/duh-cli/)
[![Tests](https://img.shields.io/badge/tests-3642%20passing-brightgreen)]()
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)]()
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

A universal, open-source AI coding agent. One harness, any provider — Anthropic Claude, OpenAI API, ChatGPT Plus/Pro subscription (Codex-family models), local Ollama, or a deterministic stub for tests. Drop-in compatible with the Claude Agent SDK NDJSON protocol, so it can replace `claude` wherever that binary is expected.

## Install

```bash
# From source (recommended during alpha)
git clone https://github.com/nikhilvallishayee/duh
cd duh
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# From PyPI
pip install duh-cli                    # core: Anthropic + Ollama + httpx
pip install 'duh-cli[openai]'          # + OpenAI API key provider
pip install 'duh-cli[rich]'            # + Rich TUI rendering
pip install 'duh-cli[bridge]'          # + WebSocket remote bridge
pip install 'duh-cli[attachments]'     # + PDF attachment support (pdfplumber)
pip install 'duh-cli[all]'             # everything above
```

## Quick start

```bash
duh -p "fix the bug in auth.py"                                  # print mode, auto-detect provider
duh --provider anthropic --model claude-sonnet-4-6 -p "hello"    # force Claude
duh --provider openai --model gpt-5.2-codex -p "refactor db"     # ChatGPT Plus/Pro Codex (OAuth)
duh                                                              # interactive REPL
duh doctor                                                       # diagnostics + health checks
```

## Providers

| Provider | Auth | Example models |
|---|---|---|
| **Anthropic** | `ANTHROPIC_API_KEY` env var or `/connect anthropic` | `claude-sonnet-4-6`, `claude-opus-4-6`, `claude-haiku-4-5` |
| **OpenAI API** | `OPENAI_API_KEY` env var or `/connect openai` (API key) | `gpt-4o`, `o1`, `o3` |
| **OpenAI ChatGPT** (Codex) | `/connect openai` → PKCE OAuth against `auth.openai.com`; tokens stored in `~/.config/duh/auth.json` (0600). See [ADR-051](docs/adrs/ADR-051-oauth-provider-authentication.md), [ADR-052](docs/adrs/ADR-052-chatgpt-codex-adapter.md). | `gpt-5.2-codex`, `gpt-5.1-codex`, `gpt-5.1-codex-max`, `gpt-5.1-codex-mini` |
| **Ollama** | Local daemon on `localhost:11434`, no key | Any locally-pulled model |
| **Stub** | `DUH_STUB_PROVIDER=1` | Deterministic canned response for tests / offline runs |

Provider and model auto-detect: give `--model gpt-5.2-codex` and D.U.H. will route to the ChatGPT/Codex Responses endpoint if OAuth exists; otherwise it falls back to the standard OpenAI Chat Completions adapter with your API key.

## Slash commands (REPL)

`/help` `/model` `/connect` `/models` `/cost` `/status` `/context` `/changes` `/git` `/tasks` `/brief` `/search` `/template` `/plan` `/pr` `/undo` `/jobs` `/health` `/clear` `/compact` `/snapshot` `/exit`

Run `/help` in the REPL for the full description of each command. Highlights: `/connect openai` runs the ChatGPT OAuth flow; `/snapshot` takes a ghost filesystem snapshot you can `apply` or `discard`; `/plan` switches to design-first two-phase execution; `/pr list|view|diff|checks` integrates with `gh`.

## Built-in tools

`Read`, `Write`, `Edit`, `MultiEdit`, `Bash`, `Glob`, `Grep`, `Skill`, `ToolSearch`, `WebFetch`, `WebSearch`, `Task`, `EnterWorktree`, `ExitWorktree`, `NotebookEdit`, `TestImpact`, `MemoryStore`, `MemoryRecall`, `HTTP`, `Docker`, `Database`, `GitHub`, `TodoWrite`, `AskUserQuestion`, plus `LSP` (deferred, loaded via `ToolSearch`).

## Sandboxing

Shell commands can be wrapped by the host OS sandbox: **macOS Seatbelt** (`sandbox-exec` profiles), **Linux Landlock** (syscall-level filesystem access control), and a network policy layer that blocks outbound traffic unless explicitly allowed (see `duh/adapters/sandbox/`). Approval behaviour is controlled with `--approval-mode suggest|auto-edit|full-auto` (reads only / reads+writes / everything), with `--dangerously-skip-permissions` as the hard bypass.

## MCP (Model Context Protocol)

MCP servers connect via four transports: **stdio** (via the `mcp` SDK), **SSE**, **streamable HTTP**, and **WebSocket**. Configure with `--mcp-config <file-or-json>`. See [ADR-010](docs/adrs/ADR-010-mcp-integration.md) and [ADR-040](docs/adrs/ADR-040-multi-transport-mcp.md).

## Hooks

**28 lifecycle events** (6 original + 22 extended) including `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `PermissionRequest`, `PreCompact`, `PostCompact`, `FileChanged`, `SubagentStart`, `Elicitation`, and more. Hooks support **glob matchers**, **blocking semantics** (a hook can refuse a tool call or rewrite its input), and both shell-command and Python-callable handlers. See [ADR-013](docs/adrs/ADR-013-hook-system.md), [ADR-036](docs/adrs/ADR-036-extended-hooks.md), [ADR-044](docs/adrs/ADR-044-hook-event-emission.md), [ADR-045](docs/adrs/ADR-045-hook-blocking-semantics.md).

## Tests and coverage

**3642 tests, 100% line coverage**, ~26s on a laptop. CI runs on GitHub Actions with a `--cov-fail-under=85` floor (current actual: 100%).

```bash
.venv/bin/python -m pytest tests/                         # full suite
.venv/bin/python -m pytest tests/ --cov=duh               # with coverage
DUH_STUB_PROVIDER=1 .venv/bin/python -m pytest tests/     # force stub provider
```

## Selected ADRs

| # | Topic |
|---|---|
| [001](docs/adrs/ADR-001-project-vision.md) | Project vision: provider-agnostic, <5K LOC kernel |
| [005](docs/adrs/ADR-005-safety-architecture.md) | Safety architecture (schema + approval + patterns) |
| [009](docs/adrs/ADR-009-provider-adapters.md) | Provider adapter contract (`.stream()` async generator) |
| [021](docs/adrs/ADR-021-sdk-protocol.md) | Claude Agent SDK NDJSON protocol compatibility |
| [037](docs/adrs/ADR-037-platform-sandboxing.md) | Seatbelt / Landlock platform sandboxing |
| [039](docs/adrs/ADR-039-ghost-snapshots.md) | Ghost snapshots for `/snapshot` |
| [040](docs/adrs/ADR-040-multi-transport-mcp.md) | MCP stdio / SSE / HTTP / WebSocket transports |
| [045](docs/adrs/ADR-045-hook-blocking-semantics.md) | Hook blocking + input rewriting |
| [051](docs/adrs/ADR-051-oauth-provider-authentication.md) | OAuth provider authentication (ChatGPT Plus/Pro) |
| [052](docs/adrs/ADR-052-chatgpt-codex-adapter.md) | ChatGPT Codex adapter (Responses API) |

Full list: [docs/adrs/](docs/adrs/) (52 ADRs).

## Development

```bash
git clone https://github.com/nikhilvallishayee/duh
cd duh
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
.venv/bin/python -m pytest tests/

# Run with coverage
.venv/bin/python -m pytest tests/ --cov=duh --cov-report=term

# Offline / deterministic mode (no real provider calls)
export DUH_STUB_PROVIDER=1
duh -p "hello"     # → "stub-ok"
```

Source layout:

```
duh/
  kernel/        # agentic loop, sessions, tasks, plan mode, undo, skills, memory
  ports/         # abstract provider / tool / approver interfaces
  adapters/      # anthropic, openai, openai_chatgpt, ollama, stub, mcp_executor, mcp_transports
    sandbox/     # seatbelt, landlock, network policy
  auth/          # credential store + OpenAI ChatGPT PKCE OAuth
  providers/     # provider registry + model resolution
  tools/         # 25+ built-in tools (see list above)
  cli/           # parser, main, runner, repl, sdk_runner, ndjson
  bridge/        # optional WebSocket remote bridge
  ui/            # Rich TUI rendering
  hooks.py       # 28-event hook system
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
