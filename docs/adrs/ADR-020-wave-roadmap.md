# ADR-020: 5-Wave Feature Roadmap

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-07

## Context

D.U.H. v0.2.0 core is complete (47 commits, 1308 tests, 12 tools, 3 providers). The next phase addresses gaps identified in the honest architecture comparison. Features are organized into 5 waves prioritized by impact.

## Wave 1: Foundation Hardening (daily-driver blockers)

1. Error recovery & exponential backoff for provider API calls
2. Output size limits (100KB per tool, graceful truncation)
3. File permissions validation before Read/Write/Edit
4. Streaming error handling (partial responses, malformed JSON)
5. Session auto-save every turn (not just on exit)
6. Git safe-mode (detached HEAD, dirty tree warnings)
7. Fallback model integration (sonnet → haiku on overload)
8. Tool timeout enforcement (per-tool configurable timeouts)

## Wave 2: Developer Experience (friction reduction)

1. Diff preview in Edit output (unified diff format)
2. Context window health dashboard (/context command)
3. Smart history truncation (deduplicate file reads)
4. Conversation search (/search command)
5. Command history persistence (readline history file)
6. Tab completion for /slash commands and file paths
7. Prompt templates (reusable prompt patterns)
8. Brief mode (--brief flag, shorter responses)

## Wave 3: Advanced Features (Claude Code parity)

1. Worktree management (EnterWorktree, ExitWorktree)
2. Plan mode (/plan command, design-first workflows)
3. Notebook cell editing (.ipynb structured editing)
4. Cross-platform shell support (PowerShell on Windows)
5. Persistent memory across sessions (embeddings)
6. Async tool execution (background jobs)
7. Code coverage / test impact analysis
8. LSP integration (go-to-def, find-refs)

## Wave 4: Ecosystem Integration (external connections)

1. GitHub PR workflow tools (list, create, review)
2. Issue tracker integration (GitHub Issues / Jira)
3. API testing tool (HTTP client with auth)
4. Docker container integration
5. Database query tool (read-only SQL)
6. Slack/Discord notifications
7. Cloud storage (S3/GCS) integration
8. Remote triggers / scheduled tasks

## Wave 5: Production Readiness (deployment, compliance)

1. Structured logging (JSON, ELK/Datadog compatible)
2. Cost control & budget enforcement
3. Rate limiting & quota management
4. Audit logging (WHO/WHAT/WHEN immutable log)
5. Health checks & graceful degradation
6. CI/CD integration (GitHub Actions workflow)
7. Security audit & threat model documentation
8. PyPI packaging (pip install duh-cli)

## Decision

Execute waves in order. Each wave: write ADR → spawn 8 parallel agents → integrate → test → commit → next wave.
