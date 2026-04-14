# ADR-038: Three-Tier Approval Model

**Status:** Accepted — implemented 2026-04-15
**Date**: 2026-04-08

## Context

D.U.H.'s current approval model is binary: either the user is asked for every write operation (`default` mode) or everything is auto-approved (`bypass` mode). This is too coarse for real workflows:

- **Beginners** want to approve everything but find the constant prompts exhausting
- **Experienced users** want autonomous operation but fear destructive git commands
- **CI/automated** environments need full autonomy with safety rails

OpenAI Codex introduced a three-tier model (Suggest/AutoEdit/FullAuto) that maps well to these use cases. The reference TS harness has a similar graduated trust model.

## Decision

Replace the binary approval with a `TieredApprover` implementing three modes:

### Tier 1: Suggest (safest)

The model can only read and suggest changes. Writes are presented as diffs for user approval. No tool execution without explicit confirmation.

```python
class SuggestApprover(ApprovalGate):
    async def check(self, tool_name: str, input: dict) -> ApprovalResult:
        if is_read_only(tool_name):
            return ApprovalResult(allowed=True)
        return ApprovalResult(
            allowed=False,
            needs_user_confirmation=True,
            preview=generate_diff_preview(tool_name, input),
        )
```

### Tier 2: AutoEdit (balanced)

Read tools and file edits within the project directory are auto-approved. Bash commands, network access, and operations outside the project require confirmation. This is the expected default for most users.

```python
class AutoEditApprover(ApprovalGate):
    async def check(self, tool_name: str, input: dict) -> ApprovalResult:
        if is_read_only(tool_name):
            return ApprovalResult(allowed=True)
        if is_file_edit(tool_name) and is_within_project(input):
            return ApprovalResult(allowed=True)
        return ApprovalResult(allowed=False, needs_user_confirmation=True)
```

### Tier 3: FullAuto (maximum autonomy)

Everything auto-approved except:
- **Destructive git operations**: `git push --force`, `git reset --hard`, `git clean -f`
- **Network access**: Disabled entirely in FullAuto to prevent exfiltration

```python
GIT_DESTRUCTIVE = re.compile(
    r"git\s+(push\s+--force|reset\s+--hard|clean\s+-[a-zA-Z]*f|branch\s+-D)"
)
```

### Git Safety Check

All tiers share a git safety check. Before any bash command containing `git`, verify:
- Not a force push to main/master
- Not a hard reset without explicit user intent
- Not deleting the current branch

This check runs even in FullAuto — these are irreversible operations that warrant a pause.

### Mode Selection

Set via CLI flag (`--approval suggest|autoedit|fullauto`) or config file. Can be changed mid-session via `/mode` command.

## Consequences

### Positive
- Users choose their trust level instead of binary safe/unsafe
- AutoEdit handles 80% of interactions without prompts
- FullAuto enables CI/automation while blocking the worst footguns
- Git safety check prevents the most common catastrophic mistakes

### Negative
- Three modes is more complex to document and explain than two
- Mode boundaries may feel arbitrary for edge cases (why is Bash in Tier 2 but Edit isn't?)

### Risks
- FullAuto with network disabled may break legitimate workflows (npm install, git push) — users must explicitly enable network for FullAuto if needed
- AutoEdit's "within project" check depends on correct project root detection

## Implementation Notes

- `duh/adapters/approvers.py` — `TieredApprover` + `ApprovalMode` enum
  (`SUGGEST` / `AUTO_EDIT` / `FULL_AUTO`).
- `TieredApprover.__init__` emits a `UserWarning` if `AUTO_EDIT` or `FULL_AUTO` is used
  outside a git repository.
- `--approval-mode` CLI flag: `duh/cli/parser.py`; config field: `duh/config.py`;
  wired into `duh/cli/repl.py` and `duh/cli/runner.py`.
- The separate git safety check ("block force push / hard reset across all tiers") is
  still a TODO — dangerous git commands are caught today via
  `duh/tools/bash_security.py` only.
