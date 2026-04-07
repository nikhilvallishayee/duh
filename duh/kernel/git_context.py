"""Git context awareness for D.U.H.

Collects repository metadata (branch, recent commits, working tree status)
and formats it for injection into the system prompt.  Gracefully returns
``None`` when the working directory is not inside a git repository or when
``git`` is not installed.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def _run_git(cmd: list[str], cwd: str) -> Optional[str]:
    """Run a git command and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + cmd,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _detect_main_branch(cwd: str) -> str:
    """Detect whether the repo uses 'main' or 'master' as its primary branch."""
    # Check for remote HEAD reference first
    symbolic = _run_git(
        ["symbolic-ref", "refs/remotes/origin/HEAD", "--short"], cwd
    )
    if symbolic:
        # e.g. "origin/main" -> "main"
        return symbolic.split("/", 1)[-1]

    # Fall back: check if 'main' or 'master' branch exists locally
    branches = _run_git(["branch", "--list", "main", "master"], cwd)
    if branches:
        for candidate in ("main", "master"):
            if candidate in branches:
                return candidate

    return "main"  # default assumption


def get_git_context(cwd: str) -> Optional[str]:
    """Return a formatted git context string, or None if not in a git repo.

    Collects:
        - Current branch name
        - Recent commits (last 5, one-line format)
        - Working tree status (first 20 lines of ``git status --short``)
        - Main/default branch name

    Returns:
        A multi-line string suitable for system prompt injection, or
        ``None`` when *cwd* is not inside a git work tree.
    """
    # Quick check: are we inside a git repo?
    inside = _run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    if inside != "true":
        return None

    # Collect pieces -------------------------------------------------------
    branch = _run_git(["branch", "--show-current"], cwd) or "(detached HEAD)"

    main_branch = _detect_main_branch(cwd)

    log = _run_git(["log", "--oneline", "-5"], cwd) or "(no commits yet)"

    raw_status = _run_git(["status", "--short"], cwd)
    if raw_status:
        status_lines = raw_status.splitlines()
        if len(status_lines) > 20:
            status = "\n".join(status_lines[:20]) + f"\n... and {len(status_lines) - 20} more"
        else:
            status = raw_status
    else:
        status = "(clean)"

    # Format ---------------------------------------------------------------
    lines = [
        "<git-context>",
        f"Current branch: {branch}",
        f"Main branch: {main_branch}",
        "",
        "Recent commits:",
        log,
        "",
        "Working tree status:",
        status,
        "</git-context>",
    ]
    return "\n".join(lines)
