# ADR-028: Env Var Allowlist and Binary Hijack Detection

**Status**: Accepted  
**Date**: 2026-04-08  
**Implemented**: 2026-04-08

## Context

`bash_security.py` classifies commands using regex patterns but has no awareness of environment variable manipulation. Attackers can inject malicious shared libraries via `LD_PRELOAD`, `DYLD_INSERT_LIBRARIES`, or similar vars to hijack any subsequent process. This is a critical privilege-escalation vector — a single `export LD_PRELOAD=/tmp/evil.so` before an innocent-looking command gives the attacker code execution inside that process.

Additionally, env var assignments can smuggle dangerous behavior past command classification. `PATH=/tmp/evil:$PATH git push` looks like a git command but runs attacker-controlled binaries. The current classifier sees `git push` and approves it.

## Decision

Add three components to bash security:

### 1. Safe Env Var Allowlist (~80 vars)

A curated set of environment variables that are safe to set. Includes `HOME`, `PATH` (with path validation), `LANG`, `TZ`, `EDITOR`, `TERM`, `NODE_ENV`, `PYTHONPATH`, `VIRTUAL_ENV`, standard CI vars, and similar. Any `export`/assignment of a var not on the list triggers review.

### 2. Binary Hijack Regex

Pattern-match against known dangerous prefixes:

```python
HIJACK_PATTERNS = re.compile(
    r"^(LD_|DYLD_|_JAVA_OPTIONS|PYTHONSTARTUP|PERL5OPT|RUBYOPT|NODE_OPTIONS)"
)
```

Any assignment to these vars is classified as `dangerous` regardless of context.

### 3. Integration with classify_command()

Before command classification, scan for env var assignments (both `export FOO=bar` and inline `FOO=bar cmd`). If a hijack var is found, escalate to dangerous. If an unknown var is found, escalate to needs-review. This runs before the existing command classification so that `LD_PRELOAD=x safe_cmd` is caught.

## Consequences

### Positive
- Blocks the most common library injection attacks on macOS and Linux
- No false positives for standard developer env vars (HOME, PATH, EDITOR, etc.)
- Composable with existing classification — adds a pre-pass, doesn't replace anything

### Negative
- The allowlist requires maintenance as new standard vars emerge
- Custom project-specific env vars will trigger review until added to a project allowlist

### Risks
- Allowlist may be incomplete for niche toolchains — mitigated by project-level overrides in config
- Inline env var parsing may miss complex shell quoting — mitigated by the AST parser (ADR-034)
