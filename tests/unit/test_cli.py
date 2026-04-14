"""Unit tests for the CLI — argument parsing, doctor, and mocked print mode."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch


import pytest

from duh.cli.parser import build_parser
from duh.cli.doctor import run_doctor
from duh.cli.main import main


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

class TestBuildParser:
    """Test argparse configuration."""

    def test_prompt_short_flag(self):
        parser = build_parser()
        args = parser.parse_args(["-p", "hello world"])
        assert args.prompt == "hello world"

    def test_prompt_long_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--prompt", "fix the bug"])
        assert args.prompt == "fix the bug"

    def test_model_default(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.model is None

    def test_model_override(self):
        parser = build_parser()
        args = parser.parse_args(["--model", "claude-opus-4-6"])
        assert args.model == "claude-opus-4-6"

    def test_max_turns_default(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.max_turns == 10

    def test_max_turns_override(self):
        parser = build_parser()
        args = parser.parse_args(["--max-turns", "5"])
        assert args.max_turns == 5

    def test_output_format_default(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.output_format == "text"

    def test_output_format_json(self):
        parser = build_parser()
        args = parser.parse_args(["--output-format", "json"])
        assert args.output_format == "json"

    def test_output_format_invalid(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--output-format", "xml"])

    def test_skip_permissions_default(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.dangerously_skip_permissions is False

    def test_skip_permissions_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--dangerously-skip-permissions"])
        assert args.dangerously_skip_permissions is True

    def test_system_prompt_override(self):
        parser = build_parser()
        args = parser.parse_args(["--system-prompt", "You are a poet."])
        assert args.system_prompt == "You are a poet."

    def test_doctor_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"

    def test_no_subcommand(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_prompt_is_none_by_default(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.prompt is None

    def test_parser_accepts_trifecta_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--i-understand-the-lethal-trifecta"])
        assert args.i_understand_the_lethal_trifecta is True

    def test_parser_trifecta_flag_defaults_false(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.i_understand_the_lethal_trifecta is False


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------

class TestVersion:
    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "0.3.0" in captured.out


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------

class TestHelp:
    def test_help_flag(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "duh" in captured.out.lower()
        assert "--prompt" in captured.out


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

class TestDoctor:
    def test_doctor_returns_exit_code(self):
        """Doctor runs without crashing and returns an int."""
        code = run_doctor()
        assert isinstance(code, int)

    def test_doctor_output(self, capsys):
        run_doctor()
        captured = capsys.readouterr()
        assert "Python version" in captured.out
        assert "ANTHROPIC_API_KEY" in captured.out
        assert "Tools available" in captured.out

    def test_doctor_via_main(self, capsys):
        code = main(["doctor"])
        assert isinstance(code, int)
        captured = capsys.readouterr()
        assert "Python version" in captured.out


# ---------------------------------------------------------------------------
# main() with no args → help (not REPL)
# ---------------------------------------------------------------------------

class TestMainNoArgs:
    def test_no_args_enters_repl(self, monkeypatch):
        """main() with no args should route to REPL."""
        from unittest.mock import AsyncMock, patch
        with patch("duh.cli.repl.run_repl", new_callable=AsyncMock, return_value=0):
            with patch("duh.cli.main.asyncio") as mock_asyncio:
                mock_asyncio.run = MagicMock(return_value=0)
                code = main([])
        assert code == 0


# ---------------------------------------------------------------------------
# Print mode with mocked provider
# ---------------------------------------------------------------------------

class TestPrintModeMocked:
    """Test print mode without real API calls."""

    @pytest.mark.asyncio
    async def test_print_mode_no_api_key(self, capsys, monkeypatch):
        """Print mode should fail gracefully when no API key is set."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Also block Ollama auto-detection
        monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(Exception("blocked")))
        from duh.cli.runner import run_print_mode

        parser = build_parser()
        args = parser.parse_args(["-p", "hello"])
        code = await run_print_mode(args)
        assert code == 1
        captured = capsys.readouterr()
        assert "ANTHROPIC_API_KEY" in captured.err

    @pytest.mark.asyncio
    async def test_print_mode_streams_text(self, capsys, monkeypatch):
        """Print mode streams text_delta events to stdout."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

        # Mock the Engine to yield canned events
        async def fake_run(prompt, **kwargs):
            yield {"type": "session", "session_id": "test", "turn": 1}
            yield {"type": "text_delta", "text": "Hello "}
            yield {"type": "text_delta", "text": "world"}
            yield {"type": "done", "stop_reason": "end_turn"}

        mock_engine_instance = MagicMock()
        mock_engine_instance.run = fake_run

        with patch("duh.cli.runner.Engine", return_value=mock_engine_instance), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            from duh.cli.runner import run_print_mode
            parser = build_parser()
            args = parser.parse_args(["-p", "test prompt"])
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        assert "Hello world" in captured.out

    @pytest.mark.asyncio
    async def test_print_mode_json_output(self, capsys, monkeypatch):
        """Print mode with --output-format json collects events."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

        async def fake_run(prompt, **kwargs):
            yield {"type": "session", "session_id": "test", "turn": 1}
            yield {"type": "text_delta", "text": "hi"}
            yield {"type": "done", "stop_reason": "end_turn"}

        mock_engine_instance = MagicMock()
        mock_engine_instance.run = fake_run

        with patch("duh.cli.runner.Engine", return_value=mock_engine_instance), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            from duh.cli.runner import run_print_mode
            import json
            parser = build_parser()
            args = parser.parse_args(["-p", "test", "--output-format", "json"])
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        events = json.loads(captured.out)
        assert isinstance(events, list)
        types = [e["type"] for e in events]
        assert "text_delta" in types

    @pytest.mark.asyncio
    async def test_print_mode_error_event(self, capsys, monkeypatch):
        """Error events go to stderr."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

        async def fake_run(prompt, **kwargs):
            yield {"type": "session", "session_id": "test", "turn": 1}
            yield {"type": "error", "error": "something broke"}
            yield {"type": "done", "stop_reason": "error"}

        mock_engine_instance = MagicMock()
        mock_engine_instance.run = fake_run

        with patch("duh.cli.runner.Engine", return_value=mock_engine_instance), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            from duh.cli.runner import run_print_mode
            parser = build_parser()
            args = parser.parse_args(["-p", "test"])
            code = await run_print_mode(args)

        assert code == 1  # errors return non-zero exit
        captured = capsys.readouterr()
        assert "something broke" in captured.err

    @pytest.mark.asyncio
    async def test_print_mode_skip_permissions(self, monkeypatch):
        """--dangerously-skip-permissions uses AutoApprover."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

        async def fake_run(prompt, **kwargs):
            yield {"type": "done", "stop_reason": "end_turn"}

        mock_engine_instance = MagicMock()
        mock_engine_instance.run = fake_run

        captured_approver = {}

        original_deps_init = None
        from duh.kernel.deps import Deps

        def capture_deps(*a, **kw):
            deps = Deps(*a, **kw)
            captured_approver["approve"] = deps.approve
            return deps

        with patch("duh.cli.runner.Engine", return_value=mock_engine_instance), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]), \
             patch("duh.cli.runner.Deps", side_effect=capture_deps):
            from duh.cli.runner import run_print_mode
            parser = build_parser()
            args = parser.parse_args(["-p", "test", "--dangerously-skip-permissions"])
            await run_print_mode(args)

        # The approve function should be from AutoApprover
        from duh.adapters.approvers import AutoApprover
        assert captured_approver.get("approve") is not None
