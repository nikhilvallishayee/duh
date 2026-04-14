# ADR-025: Ecosystem Tools — GitHub, Docker, HTTP, Database, LSP

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-07

## Decision

D.U.H. includes ecosystem integration tools beyond core file/bash operations: GitHub PRs (via `gh` CLI), Docker containers, HTTP API testing, SQLite database queries, and LSP-style code navigation via static analysis.

## Tools

| Tool | Purpose | Backend |
|------|---------|---------|
| GitHubTool | PR list/create/view/diff/checks | `gh` CLI |
| DockerTool | build/run/ps/logs/exec/images | `docker` CLI |
| HTTPTool | GET/POST/PUT/DELETE with auth | httpx |
| DatabaseTool | Read-only SQL, schema, tables | sqlite3 |
| LSPTool | go-to-def, references, symbols, hover | ast/regex |
| TestImpactTool | Which tests to run after changes | git diff + import scan |
| NotebookEditTool | .ipynb cell editing | JSON |
| WorktreeTool | git worktree create/cleanup | git CLI |

## Design Principle

All ecosystem tools are optional — they degrade gracefully when their backend (gh, docker, etc.) is not installed. The tool returns a helpful install message rather than crashing.
