"""Bash command security — classify and filter dangerous shell commands.

Provides a focused set of patterns covering the most critical attack vectors:
filesystem destruction, fork bombs, permission escalation, raw device writes,
pipe-to-shell, and arbitrary code execution.

Cross-platform: includes both Unix (bash/sh) and Windows (PowerShell) patterns.
The ``shell`` parameter on :func:`classify_command` controls which pattern set
is applied.

Usage from BashTool:
    from duh.tools.bash_security import classify_command
    result = classify_command(cmd)
    if result["risk"] == "dangerous": ...
"""

from __future__ import annotations

import logging
import re
from typing import TypedDict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env var allowlist + binary hijack detection
# ---------------------------------------------------------------------------

# Vars that are safe to set in shell commands.
# Based on a comprehensive 166-var allowlist, distilled to the most common.
SAFE_ENV_VARS: frozenset[str] = frozenset({
    # Shell basics
    "PATH", "HOME", "USER", "SHELL", "TERM", "LANG", "LC_ALL", "LC_CTYPE",
    "EDITOR", "VISUAL", "PAGER", "COLORTERM", "CLICOLOR", "CLICOLOR_FORCE",
    "NO_COLOR", "FORCE_COLOR", "TERM_PROGRAM", "COLUMNS", "LINES",
    # Build tools
    "CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS", "PKG_CONFIG_PATH",
    "CMAKE_PREFIX_PATH", "MAKEFLAGS", "DESTDIR",
    # Go
    "GOPATH", "GOROOT", "GOBIN", "GOPROXY", "GOFLAGS", "GOEXPERIMENT",
    "CGO_ENABLED", "GOARCH", "GOOS",
    # Rust
    "CARGO_HOME", "RUSTUP_HOME", "RUST_LOG", "RUST_BACKTRACE",
    "RUSTFLAGS", "CARGO_TARGET_DIR",
    # Node.js
    "NODE_ENV", "NODE_OPTIONS", "NODE_PATH", "NPM_CONFIG_PREFIX",
    "YARN_CACHE_FOLDER", "NVM_DIR",
    # Python
    "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
    "VIRTUAL_ENV", "CONDA_PREFIX", "PIP_INDEX_URL",
    # Java / JVM
    "JAVA_HOME", "CLASSPATH", "MAVEN_HOME", "GRADLE_HOME",
    # Ruby
    "GEM_HOME", "GEM_PATH", "BUNDLE_PATH", "RBENV_ROOT",
    # Docker / containers
    "DOCKER_HOST", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME",
    # CI
    "CI", "GITHUB_ACTIONS", "GITLAB_CI", "CIRCLECI", "JENKINS_URL",
    "GITHUB_TOKEN", "GITHUB_REPOSITORY",
    # Git
    "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL", "GIT_LFS_SKIP_SMUDGE",
    # AWS (non-secret — credentials handled separately)
    "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE",
    "AWS_DEFAULT_OUTPUT",
    # Misc
    "TZ", "DISPLAY", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "TMPDIR", "TEMP", "TMP",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "KUBECONFIG", "ANSIBLE_CONFIG",
})


# Regex matching env vars that enable binary hijacking.
# These allow overriding which shared libraries get loaded — a critical
# attack vector on Unix systems.
BINARY_HIJACK_RE = re.compile(
    r"^(LD_|DYLD_|LIBPATH|SHLIB_PATH|LIB_PATH)"
)


def is_env_var_safe(name: str) -> bool:
    """Check if an environment variable name is safe to set.

    Returns True for known-safe vars, False for hijack vars,
    True for unknown vars (permissive by default — we only block known-bad).
    """
    if BINARY_HIJACK_RE.match(name):
        return False
    return True  # permissive: only block known-dangerous patterns


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
    # SEC-LOW-1: anchor eval to command-position only (start of string,
    # or after a command separator / pipe / subshell opener). This avoids
    # false positives on arguments like ``git commit -m "eval tests"`` or
    # on prefixed identifiers like ``safe_eval`` / ``my_eval``.
    (re.compile(r"(?:^|[;&|`(\n]|\$\()\s*\beval\s+"),
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

    # -- Perl/Ruby/Node one-liner code execution --
    (re.compile(r"\bperl\s+-e\b"),
     "Perl one-liner code execution (perl -e)"),
    (re.compile(r"\bruby\s+-e\b"),
     "Ruby one-liner code execution (ruby -e)"),
    (re.compile(r"\bnode\s+-e\b"),
     "Node one-liner code execution (node -e)"),

    # -- find -exec dangerous combos --
    (re.compile(r"\bfind\b.*-exec\s+rm\b"),
     "Dangerous find/exec deletion (find -exec rm)"),
    (re.compile(r"\bfind\b.*-exec\s+chmod\b"),
     "Dangerous find/exec permission change (find -exec chmod)"),

    # -- Docker privilege escalation --
    (re.compile(r"\bdocker\s+run\b.*--privileged\b"),
     "Docker container with full host privileges (--privileged)"),

    # -- Process substitution remote code execution --
    (re.compile(r"\bsource\s+<\(\s*curl\b"),
     "Process substitution RCE (source <(curl ...))"),
    (re.compile(r"\bsource\s+<\(\s*wget\b"),
     "Process substitution RCE (source <(wget ...))"),

    # -- Binary hijack via env var injection --
    (re.compile(r"\bLD_PRELOAD\s*=|\bexport\s+LD_PRELOAD\b"),
     "Binary hijack via LD_PRELOAD"),
    (re.compile(r"\bDYLD_INSERT_LIBRARIES\s*=|\bexport\s+DYLD_INSERT_LIBRARIES\b"),
     "Binary hijack via DYLD_INSERT_LIBRARIES"),
    (re.compile(r"\bLD_LIBRARY_PATH\s*=.*\.\./|\bexport\s+LD_LIBRARY_PATH\b.*\.\./"),
     "Suspicious LD_LIBRARY_PATH with path traversal"),
    (re.compile(r"\bDYLD_LIBRARY_PATH\s*=|\bexport\s+DYLD_LIBRARY_PATH\b"),
     "Binary hijack via DYLD_LIBRARY_PATH"),
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

    # -- In-place file modification --
    (re.compile(r"\bsed\s+-i\b"),
     "In-place file modification (sed -i)"),

    # -- awk with system() calls --
    (re.compile(r"\bawk\b.*\bsystem\s*\("),
     "awk with system() call"),
]


# ---------------------------------------------------------------------------
# PowerShell-specific patterns
# ---------------------------------------------------------------------------

PS_DANGEROUS_PATTERNS: list[_DangerousPattern] = [
    # -- Filesystem destruction --
    (re.compile(r"\bRemove-Item\b.*-Recurse.*-Force", re.IGNORECASE),
     "Recursive forced deletion (Remove-Item -Recurse -Force)"),
    (re.compile(r"\bRemove-Item\b.*-Force.*-Recurse", re.IGNORECASE),
     "Recursive forced deletion (Remove-Item -Force -Recurse)"),
    (re.compile(r"\bri\s+.*-Recurse.*-Force|\bri\s+.*-Force.*-Recurse",
                re.IGNORECASE),
     "Recursive forced deletion via ri alias"),
    (re.compile(r"\bdel\s+.*-Recurse.*-Force|\bdel\s+.*-Force.*-Recurse",
                re.IGNORECASE),
     "Recursive forced deletion via del alias"),
    (re.compile(r"\brd\s+/s\s+/q", re.IGNORECASE),
     "Recursive forced deletion via rd /s /q"),

    # -- Disk / volume destruction --
    (re.compile(r"\bFormat-Volume\b", re.IGNORECASE),
     "Volume format command (Format-Volume)"),
    (re.compile(r"\bClear-Disk\b", re.IGNORECASE),
     "Disk wipe command (Clear-Disk)"),
    (re.compile(r"\bInitialize-Disk\b", re.IGNORECASE),
     "Disk initialization command (Initialize-Disk)"),

    # -- Process destruction --
    (re.compile(r"\bStop-Process\b.*-Force", re.IGNORECASE),
     "Force-killing processes (Stop-Process -Force)"),
    (re.compile(r"\bkill\b.*-Force", re.IGNORECASE),
     "Force-killing processes (kill -Force)"),

    # -- Remote code execution --
    # NOTE: download-cradle pattern must come BEFORE the general iex pattern
    # so the more specific match wins.
    (re.compile(
        r"\bInvoke-WebRequest\b.*\|\s*Invoke-Expression"
        r"|\bInvoke-RestMethod\b.*\|\s*Invoke-Expression"
        r"|\biwr\b.*\|\s*iex"
        r"|\birm\b.*\|\s*iex",
        re.IGNORECASE),
     "Piping web download to Invoke-Expression (download-cradle)"),
    (re.compile(r"\bInvoke-Expression\b|\biex\b", re.IGNORECASE),
     "Arbitrary code execution via Invoke-Expression (iex)"),

    # -- Execution policy bypass --
    (re.compile(r"\bSet-ExecutionPolicy\s+Unrestricted", re.IGNORECASE),
     "Disabling execution policy (Set-ExecutionPolicy Unrestricted)"),
    (re.compile(r"-ExecutionPolicy\s+Bypass", re.IGNORECASE),
     "Bypassing execution policy (-ExecutionPolicy Bypass)"),

    # -- System shutdown --
    (re.compile(r"\bStop-Computer\b|\bRestart-Computer\b", re.IGNORECASE),
     "System shutdown/restart command"),

    # -- Registry destruction --
    (re.compile(r"\bRemove-ItemProperty\b.*HKLM:", re.IGNORECASE),
     "Removing registry properties from HKLM"),
    (re.compile(r"\bRemove-Item\b.*HKLM:", re.IGNORECASE),
     "Removing registry keys from HKLM"),

    # -- Service manipulation --
    (re.compile(r"\bStop-Service\b.*-Force", re.IGNORECASE),
     "Force-stopping services (Stop-Service -Force)"),
]

PS_MODERATE_PATTERNS: list[_DangerousPattern] = [
    (re.compile(r"\bRemove-Item\b", re.IGNORECASE),
     "Removing files or directories"),
    (re.compile(r"\bStop-Process\b", re.IGNORECASE),
     "Stopping a process"),
    (re.compile(r"\bStop-Service\b", re.IGNORECASE),
     "Stopping a service"),
    (re.compile(r"\bSet-ExecutionPolicy\b", re.IGNORECASE),
     "Changing execution policy"),
    (re.compile(r"\bRestart-Service\b", re.IGNORECASE),
     "Restarting a service"),
    (re.compile(r"\bSet-ItemProperty\b.*HKLM:", re.IGNORECASE),
     "Modifying registry (HKLM)"),
    (re.compile(r"\bgit\s+(push\s+.*--force|reset\s+--hard|clean\s+-f)",
                re.IGNORECASE),
     "Destructive git operation"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_command(cmd: str, *, shell: str = "bash") -> Classification:
    """Classify a shell command by risk level.

    Uses the AST parser for structural analysis (pipes, &&, ||, ;,
    subshells).  Falls back to regex-only if AST parsing fails.

    Parameters
    ----------
    cmd:
        The raw command string to classify.
    shell:
        Which shell the command targets: ``"bash"`` (default) or
        ``"powershell"``.

    Returns a dict with:
        risk: "safe" | "moderate" | "dangerous"
        reason: human-readable explanation (empty string for safe commands)
    """
    if not cmd or not cmd.strip():
        return {"risk": "safe", "reason": ""}

    # Try AST-based structural classification first
    try:
        from duh.tools.bash_ast import ast_classify
        return ast_classify(cmd, shell=shell)
    except Exception as exc:
        # SEC-MEDIUM-2: don't silently swallow parser failures — log them
        # at WARNING so operators can spot pathological commands or upstream
        # parser regressions. We still fall back to the regex-only path so a
        # broken parser cannot disable security classification entirely.
        logger.warning(
            "bash_ast classifier failed (%s: %s); falling back to regex-only "
            "classification for command: %s",
            type(exc).__name__,
            exc,
            cmd[:200],
        )

    # Fallback: flat regex scan over the entire command string
    return _regex_classify(cmd, shell=shell)


def _regex_classify(cmd: str, *, shell: str = "bash") -> Classification:
    """Classify a command using regex patterns only (no structural analysis).

    This is the original classification logic, now extracted as a fallback
    for when the AST parser is unavailable or fails.
    """
    if not cmd or not cmd.strip():
        return {"risk": "safe", "reason": ""}

    # Build the pattern lists based on which shell is in use
    if shell == "powershell":
        dangerous = list(PS_DANGEROUS_PATTERNS) + list(DANGEROUS_PATTERNS)
        moderate = list(PS_MODERATE_PATTERNS) + list(MODERATE_PATTERNS)
    else:
        dangerous = DANGEROUS_PATTERNS
        moderate = MODERATE_PATTERNS

    # Check dangerous patterns first
    for pattern, reason in dangerous:
        if pattern.search(cmd):
            return {"risk": "dangerous", "reason": reason}

    # Check moderate patterns
    for pattern, reason in moderate:
        if pattern.search(cmd):
            return {"risk": "moderate", "reason": reason}

    return {"risk": "safe", "reason": ""}


def is_dangerous(cmd: str, *, shell: str = "bash") -> bool:
    """Quick check: does this command match any dangerous pattern?"""
    return classify_command(cmd, shell=shell)["risk"] == "dangerous"
