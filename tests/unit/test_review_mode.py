"""Tests for duh.cli.review — PR review mode stub (ADR-071 P0)."""

from __future__ import annotations

import argparse
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli import exit_codes
from duh.cli.review import _fetch_diff, run_review


# ------------------------------------------------------------------
# _fetch_diff
# ------------------------------------------------------------------

class TestFetchDiff:
    def test_success(self, monkeypatch):
        fake_result = subprocess.CompletedProcess(
            args=["gh", "pr", "diff", "42"],
            returncode=0,
            stdout="diff --git a/foo.py b/foo.py\n+hello\n",
            stderr="",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        diff, err = _fetch_diff(42)
        assert diff is not None
        assert "foo.py" in diff
        assert err is None

    def test_with_repo(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="diff", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _fetch_diff(7, repo="owner/repo")
        assert "--repo" in calls[0]
        assert "owner/repo" in calls[0]

    def test_gh_not_found(self, monkeypatch):
        def raise_fnf(*a, **kw):
            raise FileNotFoundError("gh")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        diff, err = _fetch_diff(1)
        assert diff is None
        assert "gh CLI not found" in err

    def test_gh_nonzero_exit(self, monkeypatch):
        fake_result = subprocess.CompletedProcess(
            args=["gh", "pr", "diff", "99"],
            returncode=1,
            stdout="",
            stderr="not found",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        diff, err = _fetch_diff(99)
        assert diff is None
        assert "not found" in err

    def test_gh_nonzero_exit_no_stderr(self, monkeypatch):
        fake_result = subprocess.CompletedProcess(
            args=["gh", "pr", "diff", "99"],
            returncode=2,
            stdout="",
            stderr="",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        diff, err = _fetch_diff(99)
        assert diff is None
        assert "code 2" in err

    def test_timeout(self, monkeypatch):
        def raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        diff, err = _fetch_diff(5)
        assert diff is None
        assert "timed out" in err


# ------------------------------------------------------------------
# run_review
# ------------------------------------------------------------------

def _make_review_args(**overrides):
    defaults = dict(
        pr=42,
        repo=None,
        debug=False,
        verbose=False,
        provider="anthropic",
        model=None,
        fallback_model=None,
        max_turns=10,
        max_cost=None,
        dangerously_skip_permissions=True,
        permission_mode=None,
        output_format="text",
        input_format="text",
        system_prompt=None,
        system_prompt_file=None,
        tool_choice=None,
        continue_session=False,
        resume=None,
        session_id=None,
        brief=False,
        coordinator=False,
        allowedTools=None,
        disallowedTools=None,
        mcp_config=None,
        summarize=False,
        log_json=False,
        max_thinking_tokens=None,
        i_understand_the_lethal_trifecta=False,
        command="review",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestRunReview:
    @pytest.mark.asyncio
    async def test_diff_fetch_error(self, monkeypatch):
        """If gh fails, run_review returns ERROR."""
        def raise_fnf(*a, **kw):
            raise FileNotFoundError("gh")

        monkeypatch.setattr(subprocess, "run", raise_fnf)
        args = _make_review_args()
        code = await run_review(args)
        assert code == exit_codes.ERROR

    @pytest.mark.asyncio
    async def test_empty_diff(self, monkeypatch):
        fake_result = subprocess.CompletedProcess(
            args=["gh", "pr", "diff", "42"],
            returncode=0,
            stdout="   ",
            stderr="",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)
        args = _make_review_args()
        code = await run_review(args)
        assert code == exit_codes.ERROR

    @pytest.mark.asyncio
    async def test_success_delegates_to_run_print_mode(self, monkeypatch):
        """On valid diff, run_review calls run_print_mode with the review prompt."""
        fake_result = subprocess.CompletedProcess(
            args=["gh", "pr", "diff", "42"],
            returncode=0,
            stdout="diff --git a/x.py b/x.py\n+pass\n",
            stderr="",
        )
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        captured_args = {}

        async def fake_run_print_mode(a):
            captured_args["prompt"] = a.prompt
            return exit_codes.SUCCESS

        monkeypatch.setattr("duh.cli.runner.run_print_mode", fake_run_print_mode)
        args = _make_review_args()
        code = await run_review(args)
        assert code == exit_codes.SUCCESS
        assert "PR #42" in captured_args["prompt"]
        assert "diff --git" in captured_args["prompt"]


# ------------------------------------------------------------------
# Parser integration: "review" subcommand exists
# ------------------------------------------------------------------

class TestReviewParser:
    def test_review_subcommand_parses(self):
        from duh.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args(["review", "--pr", "123"])
        assert args.command == "review"
        assert args.pr == 123
        assert args.repo is None

    def test_review_with_repo(self):
        from duh.cli.parser import build_parser

        parser = build_parser()
        args = parser.parse_args(["review", "--pr", "7", "--repo", "org/repo"])
        assert args.pr == 7
        assert args.repo == "org/repo"

    def test_review_requires_pr(self):
        from duh.cli.parser import build_parser

        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["review"])


# ------------------------------------------------------------------
# main.py dispatch: "review" command is handled
# ------------------------------------------------------------------

class TestMainDispatch:
    def test_review_command_dispatches(self, monkeypatch):
        """Verify that main() routes 'review' to run_review."""
        from duh.cli.main import main as cli_main

        async def fake_run_review(args):
            return exit_codes.SUCCESS

        monkeypatch.setattr("duh.cli.review.run_review", fake_run_review)
        code = cli_main(["review", "--pr", "1"])
        assert code == exit_codes.SUCCESS
