"""Pure resolve() decision function — no state beyond args."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from duh.security.config import SecurityPolicy
from duh.security.engine import FindingStore
from duh.security.exceptions import ExceptionStore
from duh.security.finding import Finding


@dataclass(frozen=True, slots=True)
class ToolUseEvent:
    tool: str
    cwd: Path


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: Literal["allow", "warn", "block"]
    reason: str
    findings: tuple[Finding, ...]
    remediation: str | None


_DANGEROUS_TOOLS: frozenset[str] = frozenset({
    "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "WebFetch", "Docker", "HTTP",
})


def resolve(
    event: ToolUseEvent,
    policy: SecurityPolicy,
    findings: FindingStore,
    exceptions: ExceptionStore,
    *,
    at: datetime | None = None,
) -> PolicyDecision:
    now = at or datetime.now(tz=timezone.utc)
    active = [
        f for f in findings.active(scope=event.cwd)
        if not exceptions.covers(f, at=now)
    ]
    fail_set = set(policy.fail_on or ())
    report_set = set(policy.report_on or ())
    blocking = [f for f in active if f.severity in fail_set]
    warning = [f for f in active if f.severity in report_set and f not in blocking]

    if event.tool in _DANGEROUS_TOOLS and blocking:
        top = blocking[0]
        fixed = top.fixed_in or "unknown"
        remediation = (
            f"Fix {top.id} (fixed in {fixed}) or add exception:\n"
            f"  duh security exception add {top.id} --reason='...' --expires=YYYY-MM-DD"
        )
        return PolicyDecision(
            action="block",
            reason=f"{len(blocking)} unresolved {top.severity.value} finding(s)",
            findings=tuple(blocking),
            remediation=remediation,
        )

    if warning:
        return PolicyDecision(
            action="warn",
            reason=f"{len(warning)} finding(s) below block threshold",
            findings=tuple(warning),
            remediation=None,
        )

    return PolicyDecision(
        action="allow",
        reason="clear",
        findings=(),
        remediation=None,
    )
