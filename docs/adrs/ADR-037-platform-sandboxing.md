# ADR-037: Platform Sandboxing

**Status**: Proposed  
**Date**: 2026-04-08

## Context

D.U.H. currently has zero process-level sandboxing. When tools execute, they run with the full privileges of the duh process. A malicious or confused model can:

- Read any file on the filesystem (SSH keys, credentials, browser data)
- Make arbitrary network requests (data exfiltration)
- Execute any binary (cryptocurrency miners, reverse shells)

The approval gate (ADR-005) mitigates this at the application level, but defense-in-depth requires OS-level enforcement. If the approval gate has a bug, the sandbox is the last line of defense. OpenAI's Codex uses platform-native sandboxing (tofu/Landlock on Linux). This is the gold standard for tool execution isolation.

## Decision

Add platform-native sandboxing with an abstract policy layer:

### Policy Abstraction

```python
@dataclass
class SandboxPolicy:
    allowed_read_paths: list[str]    # Paths the process can read
    allowed_write_paths: list[str]   # Paths the process can write
    network_allowed: bool            # Whether outbound network is permitted
    allowed_executables: list[str]   # Binaries that can be exec'd
    max_processes: int = 50          # Fork bomb protection
    max_memory_mb: int = 2048        # OOM protection
```

### macOS: Seatbelt (sandbox-exec)

Use Apple's `sandbox-exec` with a generated SBPL profile:

```python
def generate_seatbelt_profile(policy: SandboxPolicy) -> str:
    """Generate Seatbelt Profile Language for macOS sandbox."""
    rules = ["(version 1)", "(deny default)"]
    for path in policy.allowed_read_paths:
        rules.append(f'(allow file-read* (subpath "{path}"))')
    for path in policy.allowed_write_paths:
        rules.append(f'(allow file-write* (subpath "{path}"))')
    if policy.network_allowed:
        rules.append("(allow network*)")
    return "\n".join(rules)
```

### Linux: Landlock

Use the Landlock LSM (Linux 5.13+) to restrict filesystem access:

```python
def apply_landlock(policy: SandboxPolicy) -> None:
    """Apply Landlock filesystem restrictions on Linux."""
    # Create ruleset with read/write path restrictions
    # Falls back to seccomp-bpf on older kernels
```

### Fallback

On platforms without native sandboxing support (older Linux, Windows), log a warning and rely on the approval gate alone. Sandboxing is defense-in-depth, not a hard requirement.

### Integration with Approval Modes

| Approval Mode | Sandbox |
|---------------|---------|
| `default` | Sandbox active, project dir + temp only |
| `plan` | Sandbox active, read-only everywhere |
| `auto` | Sandbox active, project dir + temp only |
| `bypass` | Sandbox relaxed (but still no homedir write) |

## Consequences

### Positive
- OS-enforced isolation — even buggy approval gates can't leak data
- Defense-in-depth for the most critical attack vectors (file exfil, network exfil)
- Abstract policy layer means new platforms can be added without changing core

### Negative
- macOS Seatbelt is deprecated (but still functional as of macOS 15)
- Landlock requires Linux 5.13+ — older systems fall back to no sandbox
- Testing sandboxed code paths requires platform-specific CI

### Risks
- Overly restrictive policies break legitimate tool operations — mitigated by defaulting to project directory access
- Seatbelt deprecation may require migration to a different macOS mechanism — mitigated by the abstract policy layer
