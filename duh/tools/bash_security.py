"""Bash command security — classify and filter dangerous shell commands.

Provides a focused set of ~25 patterns covering the most critical attack
vectors: filesystem destruction, fork bombs, permission escalation, raw
device writes, pipe-to-shell, and arbitrary code execution.

Usage from BashTool:
    from duh.tools.bash_security import classify_command
    result = classify_command(cmd)
    if result["risk"] == "dangerous": ...
"""

from __future__ import annotations

import re
from typing import TypedDict


class Classification(TypedDict):
    risk: str       # "safe" | "moderate" | "dangerous"
    reason: str


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

_DangerousPattern = tuple[re.Pattern[str], str]

# Each entry: (compiled regex, human-readable reason)
DANGEROUS_PATTERNS: list[_DangerousPattern] = [
    # -- Filesystem destruction --
    (re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f|rm\s+.*-[a-zA-Z]*f[a-zA-Z]*r",
                re.IGNORECASE),
     "Recursive forced deletion (rm -rf)"),
    (re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*\s+/\s*$|\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*\s+/[^a-zA-Z]",
                re.IGNORECASE),
     "Recursive deletion of root filesystem"),
    (re.compile(r"\brm\s+.*--no-preserve-root"),
     "Removal with --no-preserve-root"),

    # -- Disk destruction --
    (re.compile(r"\bdd\s+.*if=/dev/(zero|random|urandom)"),
     "Disk overwrite via dd with /dev/zero or /dev/random"),
    (re.compile(r"\bmkfs\b"),
     "Filesystem format command (mkfs)"),
    (re.compile(r">\s*/dev/[sh]d[a-z]"),
     "Raw device write via redirection"),

    # -- Fork bomb --
    (re.compile(r":\(\)\s*\{.*\|.*&\s*\}\s*;"),
     "Fork bomb (:(){ :|:& };:)"),
    (re.compile(r"\bfork\s*bomb|bomb\(\)\s*\{"),
     "Fork bomb variant"),

    # -- Permission destruction --
    (re.compile(r"\bchmod\s+.*-[a-zA-Z]*R[a-zA-Z]*\s+777\s+/"),
     "Recursive chmod 777 on root"),
    (re.compile(r"\bchown\s+.*-[a-zA-Z]*R[a-zA-Z]*\s+.*\s+/\s*$"),
     "Recursive chown on root"),

    # -- Pipe to shell (remote code execution) --
    (re.compile(r"\bcurl\b.*\|\s*(ba)?sh\b"),
     "Piping curl output to shell (curl | bash)"),
    (re.compile(r"\bwget\b.*\|\s*(ba)?sh\b"),
     "Piping wget output to shell (wget | sh)"),
    (re.compile(r"\bcurl\b.*\|\s*sudo\b"),
     "Piping curl output to sudo"),
    (re.compile(r"\bwget\b.*\|\s*sudo\b"),
     "Piping wget output to sudo"),

    # -- Arbitrary code execution --
    (re.compile(r"\beval\s+"),
     "Arbitrary code execution via eval"),
    (re.compile(r"\bexec\s+[0-9]*[<>]"),
     "File descriptor manipulation via exec"),

    # -- Sudo without explicit approval --
    (re.compile(r"\bsudo\b"),
     "Command requires elevated privileges (sudo)"),

    # -- System destruction --
    (re.compile(r">\s*/dev/null\s*2>&1\s*&\s*disown|nohup.*rm\s"),
     "Background destructive command with disown"),
    (re.compile(r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b"),
     "System shutdown/reboot command"),
    (re.compile(r"\bsysctl\s+-w\b"),
     "Kernel parameter modification via sysctl"),

    # -- Dangerous overwrite patterns --
    (re.compile(r">\s*/etc/passwd|>\s*/etc/shadow"),
     "Overwriting system authentication files"),
    (re.compile(r"\bmv\s+/etc|mv\s+/usr|mv\s+/bin|mv\s+/sbin"),
     "Moving critical system directories"),

    # -- History/log tampering --
    (re.compile(r"\bunset\s+HISTFILE|\bexport\s+HISTSIZE=0|\bhistory\s+-c\b"),
     "Shell history tampering"),

    # -- Network exfiltration --
    (re.compile(r"\bnc\s+.*-[a-zA-Z]*e\s"),
     "Netcat with command execution flag (-e)"),
    (re.compile(r"/dev/tcp/"),
     "Raw TCP connection via /dev/tcp"),

    # -- Python/Perl/Ruby one-liner execution --
    (re.compile(r"\bpython[23]?\s+-c\s+.*(?:import\s+os|subprocess|__import__)"),
     "Python one-liner with system access"),
]


# Moderate-risk patterns: not blocked, but flagged with a warning.
MODERATE_PATTERNS: list[_DangerousPattern] = [
    (re.compile(r"\bchmod\b"),
     "Changing file permissions"),
    (re.compile(r"\bchown\b"),
     "Changing file ownership"),
    (re.compile(r"\brm\s+-"),
     "Removing files with flags"),
    (re.compile(r"\bkill\s+-9\b"),
     "Force-killing a process"),
    (re.compile(r"\bpkill\b|\bkillall\b"),
     "Killing processes by name"),
    (re.compile(r"\biptables\b|\bnft\b"),
     "Modifying firewall rules"),
    (re.compile(r"\bcrontab\b"),
     "Modifying scheduled tasks"),
    (re.compile(r"\bsystemctl\s+(start|stop|restart|enable|disable)\b"),
     "Managing system services"),
    (re.compile(r"\bdocker\s+rm\b|\bdocker\s+rmi\b"),
     "Removing Docker containers/images"),
    (re.compile(r"\bgit\s+(push\s+.*--force|reset\s+--hard|clean\s+-f)"),
     "Destructive git operation"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_command(cmd: str) -> Classification:
    """Classify a shell command by risk level.

    Returns a dict with:
        risk: "safe" | "moderate" | "dangerous"
        reason: human-readable explanation (empty string for safe commands)
    """
    if not cmd or not cmd.strip():
        return {"risk": "safe", "reason": ""}

    # Check dangerous patterns first
    for pattern, reason in DANGEROUS_PATTERNS:
        if pattern.search(cmd):
            return {"risk": "dangerous", "reason": reason}

    # Check moderate patterns
    for pattern, reason in MODERATE_PATTERNS:
        if pattern.search(cmd):
            return {"risk": "moderate", "reason": reason}

    return {"risk": "safe", "reason": ""}


def is_dangerous(cmd: str) -> bool:
    """Quick check: does this command match any dangerous pattern?"""
    return classify_command(cmd)["risk"] == "dangerous"
