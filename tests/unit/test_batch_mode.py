"""Tests for ADR-071 P1: duh batch subcommand."""

from __future__ import annotations

import asyncio
import os
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli.parser import build_parser


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestBatchParser:
    """The batch subcommand is accepted by the CLI parser."""

    def test_parser_accepts_batch_with_file(self):
        parser = build_parser()
        args = parser.parse_args(["batch", "prompts.txt"])
        assert args.command == "batch"
        assert args.file == "prompts.txt"

    def test_parser_batch_default_max_turns(self):
        parser = build_parser()
        args = parser.parse_args(["batch", "prompts.txt"])
        assert args.max_turns == 10

    def test_parser_batch_custom_max_turns(self):
        parser = build_parser()
        args = parser.parse_args(["batch", "prompts.txt", "--max-turns", "5"])
        assert args.max_turns == 5

    def test_parser_batch_model(self):
        parser = build_parser()
        args = parser.parse_args(["batch", "prompts.txt", "--model", "gpt-4o"])
        assert args.model == "gpt-4o"

    def test_parser_batch_output_dir(self):
        parser = build_parser()
        args = parser.parse_args(["batch", "prompts.txt", "--output-dir", "/tmp/out"])
        assert args.output_dir == "/tmp/out"

    def test_parser_batch_default_model_none(self):
        parser = build_parser()
        args = parser.parse_args(["batch", "prompts.txt"])
        assert args.model is None

    def test_parser_batch_default_output_dir_none(self):
        parser = build_parser()
        args = parser.parse_args(["batch", "prompts.txt"])
        assert args.output_dir is None


# ---------------------------------------------------------------------------
# Batch runner tests
# ---------------------------------------------------------------------------


class TestRunBatch:
    """The batch runner processes prompts from a file."""

    @pytest.mark.asyncio
    async def test_batch_file_not_found(self, tmp_path: Path):
        """Returns ERROR when the batch file does not exist."""
        from duh.cli.batch import run_batch

        parser = build_parser()
        args = parser.parse_args(["batch", str(tmp_path / "nonexistent.txt")])
        result = await run_batch(args)

        from duh.cli.exit_codes import ERROR
        assert result == ERROR

    @pytest.mark.asyncio
    async def test_batch_empty_file(self, tmp_path: Path):
        """Returns ERROR when the batch file has no prompts."""
        from duh.cli.batch import run_batch

        batch_file = tmp_path / "empty.txt"
        batch_file.write_text("# just a comment\n\n")

        parser = build_parser()
        args = parser.parse_args(["batch", str(batch_file)])
        result = await run_batch(args)

        from duh.cli.exit_codes import ERROR
        assert result == ERROR

    @pytest.mark.asyncio
    async def test_batch_filters_comments_and_blanks(self, tmp_path: Path):
        """Comments and blank lines are skipped."""
        from duh.cli.batch import run_batch

        batch_file = tmp_path / "mixed.txt"
        batch_file.write_text(textwrap.dedent("""\
            # This is a comment
            Hello world

            # Another comment
            What is Python?
        """))

        # We'll mock out the provider resolution and engine to count prompts
        prompts_seen: list[str] = []

        class FakeEngine:
            def __init__(self, **kwargs):
                self._session_id = "test-id"
                self._messages = []

            @property
            def session_id(self):
                return self._session_id

            async def run(self, prompt, **kw):
                prompts_seen.append(prompt)
                yield {"type": "text_delta", "text": f"Response to: {prompt}"}
                yield {"type": "done", "stop_reason": "end_turn", "turns": 1}

        fake_backend = MagicMock()
        fake_backend.ok = True
        fake_backend.model = "test-model"
        fake_backend.call_model = AsyncMock()

        with (
            patch("duh.cli.batch.resolve_provider_name", return_value="anthropic"),
            patch("duh.cli.batch.build_model_backend", return_value=fake_backend),
            patch("duh.cli.batch.Engine", FakeEngine),
        ):
            parser = build_parser()
            args = parser.parse_args(["batch", str(batch_file)])
            result = await run_batch(args)

        assert len(prompts_seen) == 2
        assert prompts_seen[0] == "Hello world"
        assert prompts_seen[1] == "What is Python?"

    @pytest.mark.asyncio
    async def test_batch_writes_to_output_dir(self, tmp_path: Path):
        """When --output-dir is given, results go to numbered files."""
        from duh.cli.batch import run_batch

        batch_file = tmp_path / "prompts.txt"
        batch_file.write_text("prompt one\nprompt two\n")
        out_dir = tmp_path / "output"

        class FakeEngine:
            def __init__(self, **kwargs):
                self._session_id = "test-id"
                self._messages = []

            @property
            def session_id(self):
                return self._session_id

            async def run(self, prompt, **kw):
                yield {"type": "text_delta", "text": f"Answer: {prompt}"}
                yield {"type": "done", "stop_reason": "end_turn", "turns": 1}

        fake_backend = MagicMock()
        fake_backend.ok = True
        fake_backend.model = "test-model"
        fake_backend.call_model = AsyncMock()

        with (
            patch("duh.cli.batch.resolve_provider_name", return_value="anthropic"),
            patch("duh.cli.batch.build_model_backend", return_value=fake_backend),
            patch("duh.cli.batch.Engine", FakeEngine),
        ):
            parser = build_parser()
            args = parser.parse_args([
                "batch", str(batch_file), "--output-dir", str(out_dir),
            ])
            result = await run_batch(args)

        assert result == 0
        assert (out_dir / "0001.txt").exists()
        assert (out_dir / "0002.txt").exists()
        assert "prompt one" in (out_dir / "0001.txt").read_text()
        assert "prompt two" in (out_dir / "0002.txt").read_text()

    @pytest.mark.asyncio
    async def test_batch_returns_worst_exit_code(self, tmp_path: Path):
        """The worst exit code across all prompts is returned."""
        from duh.cli.batch import run_batch
        from duh.cli import exit_codes

        batch_file = tmp_path / "prompts.txt"
        batch_file.write_text("ok prompt\nbad prompt\n")

        call_count = 0

        class FakeEngine:
            def __init__(self, **kwargs):
                self._session_id = "test-id"
                self._messages = []

            @property
            def session_id(self):
                return self._session_id

            async def run(self, prompt, **kw):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    yield {"type": "error", "error": "rate_limit exceeded"}
                else:
                    yield {"type": "text_delta", "text": "ok"}
                yield {"type": "done", "stop_reason": "end_turn", "turns": 1}

        fake_backend = MagicMock()
        fake_backend.ok = True
        fake_backend.model = "test-model"
        fake_backend.call_model = AsyncMock()

        with (
            patch("duh.cli.batch.resolve_provider_name", return_value="anthropic"),
            patch("duh.cli.batch.build_model_backend", return_value=fake_backend),
            patch("duh.cli.batch.Engine", FakeEngine),
        ):
            parser = build_parser()
            args = parser.parse_args(["batch", str(batch_file)])
            result = await run_batch(args)

        # rate_limit -> PROVIDER_ERROR (4), which is worse than SUCCESS (0)
        assert result == exit_codes.PROVIDER_ERROR

    @pytest.mark.asyncio
    async def test_batch_no_provider(self, tmp_path: Path):
        """Returns PROVIDER_ERROR when no provider is available."""
        from duh.cli.batch import run_batch
        from duh.cli import exit_codes

        batch_file = tmp_path / "prompts.txt"
        batch_file.write_text("hello\n")

        with patch("duh.cli.batch.resolve_provider_name", return_value=None):
            parser = build_parser()
            args = parser.parse_args(["batch", str(batch_file)])
            result = await run_batch(args)

        assert result == exit_codes.PROVIDER_ERROR

    @pytest.mark.asyncio
    async def test_batch_stdout_output(self, tmp_path: Path, capsys):
        """Without --output-dir, results go to stdout with markers."""
        from duh.cli.batch import run_batch

        batch_file = tmp_path / "prompts.txt"
        batch_file.write_text("hello\n")

        class FakeEngine:
            def __init__(self, **kwargs):
                self._session_id = "test-id"
                self._messages = []

            @property
            def session_id(self):
                return self._session_id

            async def run(self, prompt, **kw):
                yield {"type": "text_delta", "text": "world"}
                yield {"type": "done", "stop_reason": "end_turn", "turns": 1}

        fake_backend = MagicMock()
        fake_backend.ok = True
        fake_backend.model = "test-model"
        fake_backend.call_model = AsyncMock()

        with (
            patch("duh.cli.batch.resolve_provider_name", return_value="anthropic"),
            patch("duh.cli.batch.build_model_backend", return_value=fake_backend),
            patch("duh.cli.batch.Engine", FakeEngine),
        ):
            parser = build_parser()
            args = parser.parse_args(["batch", str(batch_file)])
            result = await run_batch(args)

        assert result == 0
        captured = capsys.readouterr()
        assert "--- prompt 1/1 ---" in captured.out
        assert "world" in captured.out


# ---------------------------------------------------------------------------
# Main dispatch test
# ---------------------------------------------------------------------------


class TestBatchMainDispatch:
    """The batch command is wired into main()."""

    def test_main_dispatches_batch(self, tmp_path: Path):
        """main() routes 'batch' command to run_batch."""
        batch_file = tmp_path / "prompts.txt"
        batch_file.write_text("test\n")

        from duh.cli.main import main

        with patch("duh.cli.main.asyncio") as mock_asyncio:
            mock_asyncio.run = MagicMock(return_value=0)
            result = main(["batch", str(batch_file)])

        # Verify asyncio.run was called (the batch runner is async)
        assert mock_asyncio.run.called
