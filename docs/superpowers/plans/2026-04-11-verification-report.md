# Verification Report: ADRs 028-042 vs Implementation

**Date**: 2026-04-11  
**Scope**: ADRs 028-042 and Phase 1-4 implementation plans

---

## ADR Implementation Status

### Fully Implemented (no significant gaps)

| ADR | Title | Files |
|-----|-------|-------|
| 028 | Env Var Allowlist | `duh/tools/bash_security.py` |
| 034 | Bash AST Parser | `duh/tools/bash_ast.py`, integrated into `bash_security.py` |
| 037 | Platform Sandboxing | `duh/adapters/sandbox/{policy,seatbelt,landlock,network}.py` |
| 040 | Multi-Transport MCP | `duh/adapters/mcp_transports.py` (SSE, HTTP, WebSocket) |

### Implemented with Minor Gaps

| ADR | Title | Gap |
|-----|-------|-----|
| 029 | File Caps | `MAX_SESSION_BYTES` constant defined in `file_store.py` but never enforced during `save()`. Read and write caps work correctly. |
| 030 | Graceful Shutdown | Core `ShutdownHandler` works (SIGINT/SIGTERM, timeout-bounded callbacks). Missing: second-signal force exit, SIGQUIT stack dump. Callback order is FIFO (registration order), not LIFO as ADR specifies. |
| 031 | PTL Retry | Detection + retry loop works. ADR specifies progressive targets (70%/50%/30%); implementation uses fixed 70% on every retry. |
| 032 | MCP Session | Session expiry detection + single reconnect works. Circuit breaker reconnects on 3 consecutive errors but does not mark servers as `degraded` or remove their tools from the active schema. |
| 035 | Advanced Compaction | `strip_images`, `partial_compact`, and `restore_context` all exist. However, `strip_images` removes ALL images (ADR says keep last 3 turns intact). The staged pipeline (strip images first, then partial removal, then aggressive) is not implemented as ordered stages with early-exit; instead `compact()` always strips images then does tail-window. |
| 038 | Tiered Approval | `TieredApprover` with SUGGEST/AUTO_EDIT/FULL_AUTO works. Missing: shared git safety check (blocking `git push --force`, `git reset --hard` in all tiers). Mode names use underscores (`AUTO_EDIT`) vs ADR's CamelCase (`AutoEdit`). |
| 041 | Attachments | `AttachmentManager` with type detection, image encoding, PDF extraction works. `MAX_ATTACHMENT_SIZE` is 10MB vs ADR's 20MB. CLI integration points not implemented: no `Ctrl+V` paste, no `/attach` command, no drag-drop, no `@image:` inline syntax. |
| 042 | Remote Bridge | `BridgeServer` with WebSocket relay, token auth, session management works. Default port is 8765 vs ADR's 9120. Missing: rate limiting (10 msg/s), max client cap (5), auto-generated token on startup, `--remote-bridge-public` warning flag. |

### Implemented with Significant Gaps

| ADR | Title | Gap |
|-----|-------|-----|
| 033 | QueryGuard | `QueryGuard` FSM is correctly implemented with generation tracking. However, it is **not wired** into the REPL or engine loop. No code calls `reserve()`, `try_start()`, or `end()` outside of tests. The `cancel_on_new` option is not implemented. Uses synchronous methods (no asyncio.Lock) unlike ADR's async design. |
| 036 | Extended Hooks | 22 new `HookEvent` enum members added to `hooks.py`. However, almost none are emitted from the codebase. Only `SESSION_START` and `SESSION_END` are fired (from `cli/runner.py`). None of the new events (PERMISSION_REQUEST, PERMISSION_DENIED, PRE_COMPACT, POST_COMPACT, USER_PROMPT_SUBMIT, STATUS_LINE, etc.) have emit calls wired into the engine, REPL, or compactor. |
| 039 | Ghost Snapshots | Implemented as `ReadOnlyExecutor` (blocks all writes) + `SnapshotSession` (deep-copies messages). This is significantly simpler than the ADR's design which specified a `GhostExecutor` with an in-memory filesystem overlay that captures writes and supports merge-to-disk. No overlay dict, no merge path for applying ghost writes, no turn limits (50 max / warning at 40), no file/size caps (20 files / 10MB). `/snapshot` command is registered in REPL but the full ghost execution loop (running queries against the snapshot) is not wired. |

---

## Plan vs Implementation Audit

### Phase 1: Quick Wins (`2026-04-08-phase1-quick-wins.md`)

| Task | Plan File | Actual File | Status |
|------|-----------|-------------|--------|
| Env var allowlist | `duh/tools/bash_security.py` | Same | Complete, code matches plan |
| File caps (read) | `duh/tools/read.py` | Same | Complete, MAX_FILE_READ_BYTES=50MB |
| File caps (write) | `duh/tools/write.py` | Same | Complete, MAX_FILE_WRITE_BYTES=50MB |
| File caps (session) | `duh/adapters/file_store.py` | Same | Constant defined, enforcement missing |
| Graceful shutdown | `duh/kernel/signals.py` | Same | Core done, LIFO/SIGQUIT gaps |
| PTL retry | `duh/kernel/engine.py` | Same | Core done, progressive targets gap |
| MCP session expiry | `duh/adapters/mcp_executor.py` | Same | Core done, degraded-mode gap |
| QueryGuard | `duh/kernel/query_guard.py` | Same | FSM done, not wired into REPL |
| Wire QueryGuard into REPL | `duh/cli/repl.py` | Not done | Plan said to modify repl.py; no QueryGuard usage found |
| Tests | `tests/unit/test_*.py` | All created | All planned test files exist |

### Phase 2: Core Safety (`2026-04-08-phase2-core-safety.md`)

| Task | Plan File | Actual File | Status |
|------|-----------|-------------|--------|
| Bash AST parser | `duh/tools/bash_ast.py` | Same | Complete |
| Wire AST into bash_security | `duh/tools/bash_security.py` | Same | Complete |
| Partial compaction | `duh/adapters/simple_compactor.py` | Same | Complete |
| Image stripping | `duh/adapters/simple_compactor.py` | Same | Simplified (no keep_recent) |
| Post-compact restore | `duh/adapters/simple_compactor.py` | Same | Complete |
| Extended hook events | `duh/hooks.py` | Same | Enum members added, not emitted |
| Fire hooks from engine | `duh/kernel/engine.py` | Not done | No hook emit calls in engine |
| Fire hooks from REPL | `duh/cli/repl.py` | Not done | No USER_PROMPT_SUBMIT/STATUS_LINE |
| Tests | `tests/unit/test_*.py` | All created | All planned test files exist |

### Phase 3: Codex Steals (`2026-04-08-phase3-codex-steals.md`)

| Task | Plan File | Actual File | Status |
|------|-----------|-------------|--------|
| Sandbox policy | `duh/adapters/sandbox/policy.py` | Same | Complete |
| Seatbelt adapter | `duh/adapters/sandbox/seatbelt.py` | Same | Complete |
| Landlock adapter | `duh/adapters/sandbox/landlock.py` | Same | Complete |
| Network policy | `duh/adapters/sandbox/network.py` | Same | Complete |
| Tiered approver | `duh/adapters/approvers.py` | Same | Complete (minor gaps) |
| Ghost snapshot | `duh/kernel/snapshot.py` | Same | Simplified vs ADR design |
| Wire sandbox into BashTool | `duh/tools/bash.py` | Same | Complete |
| Add sandbox_policy to ToolContext | `duh/kernel/tool.py` | Same | Complete |
| Add --approval-mode flag | `duh/cli/parser.py` | Same | Complete |
| Add approval_mode to config | `duh/config.py` | Same | Complete |
| Wire into REPL | `duh/cli/repl.py` | Same | TieredApprover wired; /snapshot registered |
| Tests | `tests/unit/test_*.py` | All created | All planned test files exist |

### Phase 4: Feature Parity (`2026-04-08-phase4-feature-parity.md`)

| Task | Plan File | Actual File | Status |
|------|-----------|-------------|--------|
| MCP transports | `duh/adapters/mcp_transports.py` | Same | Complete (SSE, HTTP, WebSocket) |
| Transport factory in executor | `duh/adapters/mcp_executor.py` | Same | Complete |
| Attachment system | `duh/kernel/attachments.py` | Same | Complete (size cap differs) |
| ImageBlock in messages | `duh/kernel/messages.py` | Same | Complete |
| Bridge protocol | `duh/bridge/protocol.py` | Same | Complete |
| Bridge session relay | `duh/bridge/session_relay.py` | Same | Complete |
| Bridge server | `duh/bridge/server.py` | Same | Complete (port/limits differ) |
| Bridge CLI subcommand | `duh/cli/parser.py` + `duh/cli/main.py` | Same | Complete |
| pyproject.toml optional deps | `pyproject.toml` | Not verified | Plan said to add bridge/attachments groups |
| Tests | `tests/unit/test_*.py` | All created | All planned test files exist |

---

## Discrepancies Summary

### Constants that differ from ADR specs

| Item | ADR Value | Implementation Value | File |
|------|-----------|---------------------|------|
| Attachment max size | 20 MB | 10 MB | `duh/kernel/attachments.py` |
| Bridge default port | 9120 | 8765 | `duh/bridge/server.py` |
| Shutdown callback order | LIFO | FIFO | `duh/kernel/signals.py` |
| Shutdown default timeout | 5.0s | 1.5s | `duh/kernel/signals.py` |
| PTL compaction targets | [0.70, 0.50, 0.30] | Fixed 0.70 | `duh/kernel/engine.py` |
| QueryGuard | async with Lock | synchronous (no Lock) | `duh/kernel/query_guard.py` |

### Features designed but not wired

1. **QueryGuard** -- FSM exists but nothing calls it
2. **Extended hooks** -- 22 enum members exist but no emit calls for new events
3. **Ghost snapshot merge** -- `/snapshot apply` registered in REPL but no overlay filesystem to merge
4. **Session size cap** -- `MAX_SESSION_BYTES` defined but `save()` never checks it

### Features simplified from ADR design

1. **Ghost snapshots** -- Read-only blocking instead of overlay filesystem
2. **Image stripping** -- Strips all images instead of keeping last 3 turns
3. **MCP circuit breaker** -- Reconnects but no degraded state or tool removal
4. **PTL retry** -- Fixed target instead of progressive reduction

---

## Recommended Next Steps

1. **Wire QueryGuard into the engine/REPL loop** -- This is the highest-priority gap. The FSM is correct but unused; a concurrent query bug will occur without it.

2. **Add hook emit calls** -- The 22 new events are defined but fire-and-forget emit calls need to be added at the actual trigger points in engine.py, repl.py, and the compactor.

3. **Enforce MAX_SESSION_BYTES** -- Add a size check in `FileStore.save()` that triggers compaction or truncation when the session exceeds 64MB.

4. **Implement progressive PTL compaction** -- Change the retry loop from fixed 70% to [0.70, 0.50, 0.30] as designed.

5. **Add keep_recent to strip_images** -- Preserve images in the last 3 turns to avoid removing content the model actively needs.

6. **Reconcile constant discrepancies** -- Decide whether to update the ADRs or the code for attachment size (10 vs 20MB), bridge port (8765 vs 9120), shutdown timeout (1.5 vs 5.0s), and callback order (FIFO vs LIFO).

7. **Ghost snapshot overlay** -- The current read-only implementation is functional but limited. The ADR's overlay design would enable "try this refactor" workflows where the model can write speculatively and the user can merge results.

8. **MCP degraded mode** -- After 3 consecutive reconnection failures, mark the server degraded and remove its tools from the active schema (as designed in ADR-032).
