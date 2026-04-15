"""PR review mode for D.U.H. CLI.

Fetches a pull-request diff via ``gh`` and runs the agent with a
review-oriented system prompt.  This is a stub (ADR-071 P0) that
establishes the CLI surface; full review logic will follow in P1.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys

from duh.cli import exit_codes


def _fetch_diff(pr: int, repo: str | None = None) -> tuple[str | None, str | None]:
    """Shell out to ``gh pr diff`` and return (diff_text, error)."""
    cmd = ["gh", "pr", "diff", str(pr)]
    if repo:
        cmd.extend(["--repo", repo])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None, result.stderr.strip() or f"gh exited with code {result.returncode}"
        return result.stdout, None
    except FileNotFoundError:
        return None, "gh CLI not found. Install GitHub CLI: https://cli.github.com/"
    except subprocess.TimeoutExpired:
        return None, "gh pr diff timed out after 30 seconds"


async def run_review(args: argparse.Namespace) -> int:
    """Review a PR by fetching its diff and running the agent.

    Steps:
      1. Run ``gh pr diff {args.pr}`` to get the diff.
      2. Build a review prompt with the diff.
      3. Run engine with review system prompt.
      4. Output the review.

    Returns a semantic exit code.
    """
    diff, err = _fetch_diff(args.pr, getattr(args, "repo", None))
    if err:
        sys.stderr.write(f"Error fetching PR diff: {err}\n")
        return exit_codes.ERROR

    if not diff or not diff.strip():
        sys.stderr.write(f"PR #{args.pr} has an empty diff.\n")
        return exit_codes.ERROR

    # Build a one-shot prompt that contains the diff
    review_prompt = (
        f"Review the following pull request diff (PR #{args.pr}). "
        "Provide a concise code review covering correctness, style, "
        "security, and potential improvements.\n\n"
        f"```diff\n{diff}\n```"
    )

    # Re-use run_print_mode with the synthesised prompt
    from duh.cli.runner import run_print_mode

    # Graft the review prompt onto args so run_print_mode picks it up
    args.prompt = review_prompt
    # Ensure print mode defaults that make sense for review
    if not getattr(args, "output_format", None):
        args.output_format = "text"
    return await run_print_mode(args)
