# ADR-066: Permission Model — Competitive Analysis and Design Gaps

**Status:** Proposed — 2026-04-15
**Date:** 2026-04-15
**Related:** ADR-005 (safety architecture), ADR-023 (safety hardening), ADR-037 (platform sandboxing), ADR-038 (tiered approval), ADR-054 (LLM security hardening)

## Context

D.U.H. has a robust, layered permission model: schema filtering (ADR-005 Layer 1), approval gates with a three-tier model (ADR-005 Layer 2, ADR-038), tool-level validation (ADR-005 Layer 3), bash command classification (ADR-023), env var allowlists (ADR-028), platform sandboxing via Seatbelt/Landlock (ADR-037), taint propagation and confirmation tokens (ADR-054), and the lethal trifecta check (ADR-054 workstream 3).

This is already more layered than most competitors. But a competitive analysis of every major agent CLI reveals four concrete gaps where D.U.H. falls short of the best-in-class behavior offered across the field.

## Competitive Analysis

### Claude Code

**Permission model:** Tiered approval with three modes — Suggest (model proposes, user confirms), Auto-Edit (file changes auto-approved, commands need approval), and Full Auto (everything approved with guardrails). Key features:

- **Per-tool allow/deny lists.** Users configure which specific tools (by name) or which specific command patterns (by regex) are permanently allowed or denied. Configuration persists across sessions in project settings. Example: "always allow `npm test`" or "never allow `rm -rf`". This eliminates repetitive approval prompts for trusted operations while maintaining hard blocks on dangerous ones.
- **Session-scoped permission escalation.** When a user approves a tool or command pattern during a session, the approval is remembered for the remainder of that session. The first `git commit` requires confirmation; subsequent `git commit` calls in the same conversation are auto-approved. This dramatically reduces prompt fatigue in interactive use.
- **Allowlist in project configuration.** Per-project `.claude/settings.json` can declare permanent tool permissions that apply to every session in that project. This is a declarative, version-controllable permission surface.

**What D.U.H. can learn:** Per-session permission memory and declarative per-project allow/deny lists.

### GitHub Copilot CLI

**Permission model:** Sandboxed execution with user approval prompts. Key features:

- **Preview-then-execute flow.** The CLI shows the user exactly what command it intends to run before execution. The user can approve, edit, or reject. This is conceptually similar to D.U.H.'s Suggest mode but is the *only* mode — there is no auto-approve.
- **No persistent approval memory.** Every command requires explicit approval. This is maximally safe but creates friction for repetitive tasks.
- **Scoped to shell commands.** Copilot CLI generates shell commands, not file edits. The permission surface is narrow: approve or reject a single command string.

**What D.U.H. can learn:** The preview-then-edit-then-execute flow (let the user modify the command before approving) is a UX pattern D.U.H.'s `InteractiveApprover` lacks. Currently it is binary y/n; there is no "approve with modifications."

### OpenAI Codex CLI

**Permission model:** Sandbox-first with three modes (Suggest, Auto-Edit, Full Auto — same tier names as D.U.H.'s ADR-038). Key features:

- **Network-disabled by default in Full Auto.** When running with maximum autonomy, network access is completely disabled at the OS level. This prevents data exfiltration even if the model is manipulated via prompt injection. The user must explicitly opt in to network access for automated sessions.
- **Container-based sandboxing.** Commands execute in an isolated container environment. File system access is restricted to the project directory. Process isolation prevents lateral movement. This is stronger than D.U.H.'s Seatbelt/Landlock approach, which operates at the process level within the host OS.
- **Structured handoff on compaction.** Not directly a permission feature, but Codex preserves permission decisions across context compaction — if the user approved a pattern, that approval survives summarization.

**What D.U.H. can learn:** Container-based execution as a sandboxing tier above Seatbelt/Landlock. D.U.H.'s ADR-037 mentions falling back to "no sandbox" on unsupported platforms; a container mode would provide cross-platform isolation.

### Gemini CLI

**Permission model:** Auto-approve with configurable guardrails. Key features:

- **Default auto-approve for most operations.** Gemini CLI takes an opinionated stance: the default mode trusts the model for file operations and shell commands. This reduces friction but increases risk.
- **Guardrail configuration.** Users can configure which categories of operations require confirmation. The guardrails are declarative, specified in configuration rather than at runtime.
- **No sandboxing layer.** Gemini CLI relies entirely on application-level permission checks. There is no OS-level enforcement (no Seatbelt, no Landlock, no container). This is a weaker security posture than D.U.H., Codex, or Claude Code.

**What D.U.H. can learn:** The declarative guardrail configuration model — rather than hardcoding tier boundaries in code, let users define their own tier boundaries in config. "Auto-approve Bash commands matching `npm *` and `pytest *`; ask for everything else."

### OpenCode

**Permission model:** Configurable approval modes with provider flexibility. Key features:

- **Mode switching at runtime.** Users can change the approval mode mid-session without restarting. D.U.H.'s ADR-038 mentions a `/mode` command for this, but the implementation is not yet wired.
- **Provider-agnostic permission model.** Permissions are defined independently of the AI provider. The same rules apply whether using Ollama locally or a cloud API. D.U.H. shares this property.
- **Auto-approve as the common default.** Like Gemini CLI, OpenCode defaults to trusting the model for most operations, with the user opting in to stricter modes.

**What D.U.H. can learn:** The emphasis on runtime mode switching as a first-class feature, not an afterthought.

## Summary Matrix

| Capability | Claude Code | Copilot CLI | Codex CLI | Gemini CLI | OpenCode | **D.U.H.** |
|---|---|---|---|---|---|---|
| Tiered approval modes | Yes (3 tiers) | No (1 mode) | Yes (3 tiers) | Partial | Yes | **Yes (ADR-038)** |
| Per-tool allow/deny lists | Yes | No | No | Partial | No | **No (GAP)** |
| Per-session permission memory | Yes | No | Partial | N/A (auto) | No | **No (GAP)** |
| Container/namespace sandbox | Yes (partial) | No | Yes | No | No | **No (GAP)** |
| OS-level sandbox (seatbelt/landlock) | No (public) | No | Yes | No | No | **Yes (ADR-037)** |
| Network policy enforcement | Partial | No | Yes (disabled by default) | No | No | **Partial (ADR-037)** |
| Preview-edit-approve flow | Yes | Yes | Yes | No | No | **No (GAP)** |
| Declarative permission config | Yes | No | No | Yes | Partial | **Partial (RuleApprover)** |
| Runtime mode switching | Yes | N/A | Yes | N/A | Yes | **Designed, not wired** |
| Taint propagation | No (public) | No | No | No | No | **Yes (ADR-054)** |
| Confirmation tokens | No (public) | No | No | No | No | **Yes (ADR-054)** |
| Lethal trifecta check | No (public) | No | No | No | No | **Yes (ADR-054)** |

## Identified Gaps

### Gap 1: Per-Session Permission Memory

**Current state:** Every tool call that requires approval goes through the `InteractiveApprover` or `TieredApprover`. If the user approves `Bash(command="pytest")` at minute 1, they are asked again at minute 5, and again at minute 12. There is no memory of previous approvals within a session.

**What's needed:** A `SessionPermissionCache` that sits between the tier logic and the user prompt. When a tool call is denied by tier rules and the user approves it, the cache records the approval pattern (tool name + optional input pattern). Subsequent matching calls in the same session skip the prompt. The cache is scoped to the session lifetime — it never persists to disk, so each new session starts clean.

```python
@dataclass
class SessionPermissionCache:
    """Remembers user approvals within a single session."""
    _approved_tools: set[str]             # "Bash" — blanket tool approval
    _approved_patterns: dict[str, list[re.Pattern]]  # "Bash" → [re"pytest.*"]
    _denied_tools: set[str]               # "Bash" with "never" response

    def check(self, tool_name: str, input: dict) -> Literal["approved", "denied", "unknown"]:
        ...

    def record(self, tool_name: str, input: dict, decision: str, scope: str) -> None:
        """Record a user decision. scope: 'once' | 'session' | 'always-deny'"""
        ...
```

The approval prompt changes from `Allow? [y/n]` to `Allow? [y]es / [a]lways for this session / [n]o / [N]ever`:
- **y**: Allow this one call
- **a**: Allow all calls to this tool (or this tool+pattern) for the session
- **n**: Deny this one call
- **N**: Deny all calls to this tool for the session

**Priority:** P0 — this is the single highest-impact UX improvement. Prompt fatigue is the primary reason users switch from Suggest/AutoEdit to FullAuto prematurely, weakening their security posture.

### Gap 2: Container/Namespace Sandboxing

**Current state:** ADR-037 implements Seatbelt (macOS) and Landlock (Linux) sandboxing. These are process-level restrictions applied to the duh process itself or to Bash subprocess invocations. They restrict filesystem paths and optionally network access. However, there is no full process isolation — the tool execution shares the host's process namespace, network stack, IPC, and PID space.

**What's needed:** An optional container-based execution mode where Bash tool calls (and optionally MCP server subprocesses) run inside a lightweight container or namespace. This provides:

1. **Filesystem isolation.** The container sees only the project directory (mounted read-write) and necessary system paths (mounted read-only). No access to `~/.ssh`, `~/.aws`, browser profile directories, or other sensitive host paths.
2. **Network isolation.** The container can be started with no network (matching Codex CLI's Full Auto behavior) or with a filtered network (DNS allowlist).
3. **Process isolation.** Fork bombs, runaway processes, and resource exhaustion are contained. The container has cgroup limits.
4. **Cross-platform.** Docker/Podman on Linux and macOS. Falls back to Seatbelt/Landlock if no container runtime is available.

```python
class SandboxTier(Enum):
    NONE = "none"           # No sandboxing (--dangerously-skip-permissions)
    OS_NATIVE = "os-native" # Seatbelt/Landlock (current ADR-037)
    CONTAINER = "container" # Docker/Podman namespace isolation
```

**Priority:** P2 — valuable for high-security environments but not blocking typical developer workflows. ADR-037's Seatbelt/Landlock covers the common case. Container mode is for when the threat model includes a compromised model actively attempting exfiltration.

### Gap 3: Network Policy Enforcement

**Current state:** ADR-037's `SandboxPolicy` has a `network_allowed: bool` field. ADR-038's FullAuto mode disables network. `NetworkPolicy` in `duh/adapters/sandbox/network.py` supports FULL/LIMITED/NONE modes with host allow/deny lists. However:

1. **Network policy is only enforced for the `WebFetch` tool at the application level.** Bash commands can make arbitrary network requests (curl, wget, nc, python -c "requests.get(...)") and the network policy does not apply. The Seatbelt profile can deny all network, but there is no middle ground — it is all-or-nothing at the OS level.
2. **No DNS-level filtering.** The allowed_hosts/denied_hosts check in `NetworkPolicy.is_request_allowed()` only applies to `WebFetch` URLs. A Bash command doing `curl evil.com` bypasses it entirely.
3. **No egress monitoring.** Even when network is allowed, there is no logging of what network requests tools actually make. The PEP 578 audit hook (ADR-054 workstream 6) covers `socket.connect` telemetry but is not yet integrated with the network policy.

**What's needed:**

1. **Seatbelt/Landlock network filtering with granularity.** On macOS, Seatbelt profiles can allow network to specific hosts (via `(allow network* (remote ip "..."))`). On Linux, Landlock v4 (kernel 6.7+) adds network port restrictions. Use these where available; fall back to all-or-nothing on older kernels.
2. **DNS-aware proxy for Bash commands.** In container mode (Gap 2), route all network traffic through a lightweight proxy that enforces the `NetworkPolicy` allow/deny list. In non-container mode, this is not feasible without iptables/pf rules (which require root).
3. **Audit hook integration.** Wire ADR-054's PEP 578 `socket.connect` telemetry to the `NetworkPolicy` checker, so at minimum unauthorized network access is logged even when it cannot be blocked at the OS level.

**Priority:** P1 — network exfiltration is the highest-impact attack vector for LLM agents. The gap between "network allowed" and "network denied" is too wide. A middle ground (allow specific hosts, deny everything else) is essential for production use.

### Gap 4: TUI Permission Prompt

**Current state:** D.U.H. has three UI tiers (ADR-011): Bare (readline), Rich (styled panels), and Full TUI (Textual widgets). The `InteractiveApprover` uses `builtins.input()` to show a `[y/n]` prompt on stderr. This works in Bare and Rich modes. In the Full TUI (`duh --tui`), permission prompts are not wired — the TUI currently assumes auto-approve behavior because there is no modal dialog for permission requests.

**What's needed:**

1. **TUI modal permission dialog.** When a tool call needs approval in the Textual TUI, display a modal overlay showing: tool name, input summary (with syntax highlighting for Bash commands), the file diff (for Edit/Write), and approve/deny/always buttons. The modal blocks the event loop until the user responds.
2. **Rich-mode enhanced prompt.** Upgrade the Rich renderer's permission prompt from plain `[y/n]` to a styled panel showing the same information as the TUI modal — tool name highlighted, input formatted, diff preview for file operations. Include the session-memory options from Gap 1 (`[y]es / [a]lways / [n]o / [N]ever`).
3. **Renderer protocol extension.** The `Renderer` protocol (ADR-011) already declares `render_permission_prompt(tool, input) -> str`. Implementations need to be upgraded to return richer responses than just "y"/"n", supporting the session-memory vocabulary.

```python
class PermissionResponse(Enum):
    ALLOW_ONCE = "y"
    ALLOW_SESSION = "a"    # Remember for this session
    DENY_ONCE = "n"
    DENY_SESSION = "N"     # Block for this session
    EDIT_AND_ALLOW = "e"   # Modify the input, then allow (Copilot pattern)
```

**Priority:** P1 — the TUI is shipped (ADR-011 Tier 2 is implemented) but permission prompts in TUI mode are broken. Users who run `duh --tui` effectively have no approval gate. This is a functional gap, not just a UX polish issue.

## Decision

Address the four gaps in priority order:

| Phase | Gap | Priority | Estimated Effort |
|---|---|---|---|
| 1 | Per-session permission memory | P0 | Small — new `SessionPermissionCache` class, prompt string changes, wiring into `TieredApprover` |
| 2 | TUI permission prompt | P1 | Medium — Textual modal widget, Rich prompt upgrade, renderer protocol extension |
| 3 | Network policy enforcement | P1 | Medium — Seatbelt host filtering, audit hook integration, proxy design for container mode |
| 4 | Container/namespace sandbox | P2 | Large — Docker/Podman integration, mount policy, network namespace, fallback logic |

Phase 1 should ship independently and immediately — it requires no architectural changes, only a cache layer in front of the existing approval gate.

Phases 2 and 3 can proceed in parallel.

Phase 4 depends on Phase 3 (the container's network policy needs the enforcement layer from Phase 3).

## Consequences

### Positive

- Eliminates the primary source of prompt fatigue (Gap 1), which is the single biggest complaint about interactive approval modes across all agent CLIs.
- Closes the TUI permission gap (Gap 4), making `duh --tui` safe for non-auto-approve modes.
- Network policy enforcement (Gap 3) addresses the highest-impact attack vector for LLM agents, putting D.U.H. on par with Codex CLI's network-disabled-by-default posture.
- Container sandboxing (Gap 2) provides defense-in-depth beyond what any competitor offers in an open-source CLI.
- Combined with D.U.H.'s existing advantages (taint propagation, confirmation tokens, lethal trifecta check), these four gaps are the remaining distance between D.U.H. and the most comprehensive permission model in the field.

### Negative

- Four new subsystems to maintain: session cache, TUI modal, network enforcement, container integration.
- Container mode adds a runtime dependency (Docker/Podman) that not all users will have.
- The session permission cache introduces state that must be correctly invalidated on mode changes, session restarts, and `/mode` switches.

### Risks

- **Session cache over-approval.** A user who types "a" (always) for `Bash` in a session has effectively switched to FullAuto for commands without realizing it. Mitigated by: (a) the "always" scope is per-tool, not global; (b) the session cache is displayed in the status bar so the user sees what they've approved; (c) the lethal trifecta check (ADR-054) still runs regardless of the cache.
- **Container startup latency.** Launching a container for each Bash call adds 200-500ms overhead. Mitigated by keeping a warm container per session (start once, exec into it for each command).
- **TUI modal blocks event loop.** If the user walks away during a permission prompt, the entire TUI freezes. Mitigated by adding a configurable timeout (default 5 minutes) after which the prompt auto-denies.

## Implementation Notes

This ADR is design-only. Implementation will be tracked in separate commits per phase. Key integration points:

- **Phase 1 (session cache):** New class in `duh/adapters/approvers.py`, wired into `TieredApprover.check()` as a pre-check before tier logic. Prompt string changes in `InteractiveApprover`.
- **Phase 2 (TUI prompt):** New widget in `duh/ui/widgets.py`, wired into `duh/ui/app.py` event handling. Rich prompt upgrade in `duh/adapters/renderers.py`. `PermissionResponse` enum in `duh/ports/renderer.py`.
- **Phase 3 (network enforcement):** Seatbelt host filtering in `duh/adapters/sandbox/seatbelt.py`. Audit hook bridge in `duh/kernel/audit.py` (ADR-054 workstream 6). `NetworkPolicy` integration with the hook bus.
- **Phase 4 (container sandbox):** New `duh/adapters/sandbox/container.py` module. `SandboxTier` enum in `duh/adapters/sandbox/policy.py`. Docker/Podman runtime detection and container lifecycle management.

Depends on: ADR-005, ADR-037, ADR-038. Complements: ADR-054 (taint propagation and confirmation tokens provide the architectural security layer; this ADR addresses the operational permission UX layer).
