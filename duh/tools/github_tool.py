"""GitHubTool — interact with GitHub PRs via the `gh` CLI.

Wraps common `gh pr` subcommands (list, create, view, diff, checks)
so the model can work with pull requests without raw Bash.

Requires the GitHub CLI (``gh``) to be installed and authenticated.
If ``gh`` is not found, returns a helpful install message.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult

_GH_MISSING_MSG = (
    "GitHub CLI (gh) is not installed or not on PATH.\n"
    "Install it with: brew install gh\n"
    "Then authenticate with: gh auth login"
)

_SUBPROCESS_TIMEOUT = 30  # seconds


def _gh_available() -> bool:
    """Return True if the ``gh`` binary is on PATH."""
    return shutil.which("gh") is not None


def _run_gh(args: list[str], *, cwd: str = ".") -> tuple[str, str, int]:
    """Run a ``gh`` command and return (stdout, stderr, returncode)."""
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            cwd=cwd,
        )
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return "", f"gh command timed out after {_SUBPROCESS_TIMEOUT}s", 1
    except OSError as exc:
        return "", f"Failed to run gh: {exc}", 1


class GitHubTool:
    """Interact with GitHub pull requests via the gh CLI."""

    name = "GitHub"
    description = (
        "Interact with GitHub pull requests using the gh CLI. "
        "Supports listing, creating, viewing, diffing, and checking PRs."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["pr_list", "pr_create", "pr_view", "pr_diff", "pr_checks"],
                "description": "The PR action to perform.",
            },
            "number": {
                "type": "integer",
                "description": "PR number (required for pr_view, pr_diff, pr_checks).",
            },
            "title": {
                "type": "string",
                "description": "PR title (required for pr_create).",
            },
            "body": {
                "type": "string",
                "description": "PR body/description (optional for pr_create).",
            },
            "base": {
                "type": "string",
                "description": "Base branch for pr_create (optional, defaults to repo default).",
            },
            "state": {
                "type": "string",
                "enum": ["open", "closed", "merged", "all"],
                "description": "Filter PRs by state (optional for pr_list, default: open).",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of PRs to list (optional for pr_list, default: 30).",
            },
        },
        "required": ["action"],
    }

    @property
    def is_read_only(self) -> bool:
        return False  # pr_create mutates

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        if not _gh_available():
            return ToolResult(output=_GH_MISSING_MSG, is_error=True)

        action = input.get("action", "")
        cwd = context.cwd or "."

        if action == "pr_list":
            return self._pr_list(input, cwd)
        elif action == "pr_create":
            return self._pr_create(input, cwd)
        elif action == "pr_view":
            return self._pr_view(input, cwd)
        elif action == "pr_diff":
            return self._pr_diff(input, cwd)
        elif action == "pr_checks":
            return self._pr_checks(input, cwd)
        else:
            return ToolResult(
                output=f"Unknown action: {action!r}. "
                f"Expected one of: pr_list, pr_create, pr_view, pr_diff, pr_checks.",
                is_error=True,
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _pr_list(self, input: dict[str, Any], cwd: str) -> ToolResult:
        args = ["pr", "list", "--json", "number,title,state,author"]
        state = input.get("state")
        if state:
            args.extend(["--state", state])
        limit = input.get("limit")
        if limit:
            args.extend(["--limit", str(limit)])

        stdout, stderr, rc = _run_gh(args, cwd=cwd)
        if rc != 0:
            return ToolResult(output=f"gh pr list failed: {stderr.strip()}", is_error=True)

        try:
            prs = json.loads(stdout)
        except json.JSONDecodeError:
            return ToolResult(output=stdout or "No output from gh pr list.", is_error=False)

        if not prs:
            return ToolResult(output="No pull requests found.", is_error=False)

        lines: list[str] = []
        for pr in prs:
            author = pr.get("author", {})
            login = author.get("login", "unknown") if isinstance(author, dict) else str(author)
            lines.append(
                f"#{pr.get('number', '?')} [{pr.get('state', '?')}] "
                f"{pr.get('title', '(no title)')} (by {login})"
            )
        return ToolResult(
            output="\n".join(lines),
            metadata={"count": len(prs), "action": "pr_list"},
        )

    def _pr_create(self, input: dict[str, Any], cwd: str) -> ToolResult:
        title = input.get("title", "").strip()
        if not title:
            return ToolResult(output="title is required for pr_create.", is_error=True)

        args = ["pr", "create", "--title", title]
        body = input.get("body", "")
        if body:
            args.extend(["--body", body])
        base = input.get("base", "")
        if base:
            args.extend(["--base", base])

        stdout, stderr, rc = _run_gh(args, cwd=cwd)
        if rc != 0:
            return ToolResult(output=f"gh pr create failed: {stderr.strip()}", is_error=True)
        return ToolResult(
            output=stdout.strip() or "PR created.",
            metadata={"action": "pr_create"},
        )

    def _pr_view(self, input: dict[str, Any], cwd: str) -> ToolResult:
        number = input.get("number")
        if number is None:
            return ToolResult(output="number is required for pr_view.", is_error=True)

        args = ["pr", "view", str(number), "--json", "title,body,state,reviews"]
        stdout, stderr, rc = _run_gh(args, cwd=cwd)
        if rc != 0:
            return ToolResult(output=f"gh pr view failed: {stderr.strip()}", is_error=True)

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return ToolResult(output=stdout or "No output from gh pr view.", is_error=False)

        parts = [
            f"PR #{number}: {data.get('title', '(no title)')}",
            f"State: {data.get('state', 'unknown')}",
        ]
        body = data.get("body", "")
        if body:
            parts.append(f"\n{body}")
        reviews = data.get("reviews", [])
        if reviews:
            parts.append(f"\nReviews ({len(reviews)}):")
            for r in reviews:
                parts.append(
                    f"  - {r.get('author', {}).get('login', '?')}: {r.get('state', '?')}"
                )
        return ToolResult(
            output="\n".join(parts),
            metadata={"action": "pr_view", "number": number},
        )

    def _pr_diff(self, input: dict[str, Any], cwd: str) -> ToolResult:
        number = input.get("number")
        if number is None:
            return ToolResult(output="number is required for pr_diff.", is_error=True)

        args = ["pr", "diff", str(number)]
        stdout, stderr, rc = _run_gh(args, cwd=cwd)
        if rc != 0:
            return ToolResult(output=f"gh pr diff failed: {stderr.strip()}", is_error=True)
        return ToolResult(
            output=stdout or "No diff output.",
            metadata={"action": "pr_diff", "number": number},
        )

    def _pr_checks(self, input: dict[str, Any], cwd: str) -> ToolResult:
        number = input.get("number")
        if number is None:
            return ToolResult(output="number is required for pr_checks.", is_error=True)

        args = ["pr", "checks", str(number)]
        stdout, stderr, rc = _run_gh(args, cwd=cwd)
        if rc != 0:
            return ToolResult(output=f"gh pr checks failed: {stderr.strip()}", is_error=True)
        return ToolResult(
            output=stdout.strip() or "No checks found.",
            metadata={"action": "pr_checks", "number": number},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        action = input.get("action", "")
        # Creating PRs needs explicit approval; reads are safe
        if action == "pr_create":
            return {"allowed": True, "needs_approval": True}
        return {"allowed": True}
