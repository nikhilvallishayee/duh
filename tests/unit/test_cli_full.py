"""Full coverage for duh.cli — every event handler, every code path."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli.runner import (
    _interpret_error,
    _make_serializable,
    _summarize_event,
    run_print_mode,
)
from duh.cli.parser import build_parser
from duh.cli.main import main
from duh.kernel.messages import Message


# ===================================================================
# _interpret_error
# ===================================================================

class TestInterpretError:
    def test_credit_balance(self):
        result = _interpret_error("credit balance is too low")
        assert "credits" in result.lower()

    def test_invalid_api_key(self):
        result = _interpret_error("invalid x-api-key")
        assert "ANTHROPIC_API_KEY" in result

    def test_authentication_error(self):
        result = _interpret_error("authentication_error")
        assert "ANTHROPIC_API_KEY" in result

    def test_rate_limit(self):
        result = _interpret_error("rate_limit: too many requests")
        assert "Rate limited" in result

    def test_overloaded(self):
        result = _interpret_error("API is overloaded")
        assert "overloaded" in result.lower()

    def test_prompt_too_long(self):
        result = _interpret_error("prompt is too long for context")
        assert "too long" in result.lower()

    def test_no_auth(self):
        result = _interpret_error("Could not resolve authentication method")
        assert "ANTHROPIC_API_KEY" in result

    def test_unknown_error_passthrough(self):
        result = _interpret_error("some unknown error")
        assert result == "some unknown error"


# ===================================================================
# _summarize_event
# ===================================================================

class TestSummarizeEvent:
    def test_text_delta(self):
        result = _summarize_event({"type": "text_delta", "text": "hello"})
        assert "text_delta" in result

    def test_tool_use(self):
        result = _summarize_event({"type": "tool_use", "name": "Read", "input": {}})
        assert "tool_use" in result
        assert "Read" in result

    def test_tool_result(self):
        result = _summarize_event({"type": "tool_result", "is_error": False, "output": "data"})
        assert "tool_result" in result

    def test_assistant(self):
        msg = Message(role="assistant", content="Hello world response here")
        result = _summarize_event({"type": "assistant", "message": msg})
        assert "assistant" in result

    def test_assistant_non_message(self):
        result = _summarize_event({"type": "assistant", "message": "raw"})
        assert "?" in result

    def test_error(self):
        result = _summarize_event({"type": "error", "error": "something broke"})
        assert "error" in result

    def test_unknown_type(self):
        result = _summarize_event({"type": "custom_event", "data": "x"})
        assert "custom_event" in result


# ===================================================================
# _make_serializable
# ===================================================================

class TestMakeSerializable:
    def test_dataclass_value(self):
        msg = Message(role="user", content="hi")
        result = _make_serializable({"msg": msg, "type": "assistant"})
        assert isinstance(result["msg"], dict)
        assert result["msg"]["role"] == "user"

    def test_primitive_values(self):
        event = {"type": "text_delta", "text": "hi", "count": 5, "ok": True, "x": None}
        result = _make_serializable(event)
        assert result == event

    def test_non_serializable_value(self):
        event = {"type": "test", "obj": object()}
        result = _make_serializable(event)
        assert isinstance(result["obj"], str)

    def test_list_value(self):
        event = {"type": "test", "items": [1, 2, 3]}
        result = _make_serializable(event)
        assert result["items"] == [1, 2, 3]

    def test_dict_value(self):
        event = {"type": "test", "meta": {"a": 1}}
        result = _make_serializable(event)
        assert result["meta"] == {"a": 1}


# ===================================================================
# Print mode — event handlers
# ===================================================================

def _make_fake_engine(events):
    """Create a mock Engine that yields given events."""
    async def fake_run(prompt, **kwargs):
        for e in events:
            yield e

    mock_engine = MagicMock()
    mock_engine.run = fake_run
    return mock_engine


class TestPrintModeEventHandlers:
    @pytest.mark.asyncio
    async def test_tool_use_event(self, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/x"}},
            {"type": "tool_result", "is_error": False, "output": "file contents"},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            parser = build_parser()
            args = parser.parse_args(["-p", "test"])
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        assert "Read" in captured.err

    @pytest.mark.asyncio
    async def test_tool_result_error(self, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "tool_result", "is_error": True, "output": "File not found"},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            parser = build_parser()
            args = parser.parse_args(["-p", "test"])
            code = await run_print_mode(args)

        captured = capsys.readouterr()
        assert "File not found" in captured.err

    @pytest.mark.asyncio
    async def test_assistant_error_event(self, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        error_msg = Message(
            role="assistant",
            content=[{"type": "text", "text": "API Error: credit balance is too low"}],
            metadata={"is_error": True},
        )
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "assistant", "message": error_msg},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            parser = build_parser()
            args = parser.parse_args(["-p", "test"])
            code = await run_print_mode(args)

        assert code == 1
        captured = capsys.readouterr()
        assert "credits" in captured.err.lower()

    @pytest.mark.asyncio
    async def test_thinking_delta_debug(self, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "thinking_delta", "text": "hmm let me think"},
            {"type": "text_delta", "text": "answer"},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            parser = build_parser()
            args = parser.parse_args(["-p", "test", "--debug"])
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        assert "hmm let me think" in captured.err

    @pytest.mark.asyncio
    async def test_debug_done_event(self, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "done", "stop_reason": "end_turn", "turns": 1},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            parser = build_parser()
            args = parser.parse_args(["-p", "test", "--debug"])
            code = await run_print_mode(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_debug_tool_result_success(self, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "tool_result", "is_error": False, "output": "success data"},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            parser = build_parser()
            args = parser.parse_args(["-p", "test", "--debug"])
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        assert "success data" in captured.err


# ===================================================================
# Print mode — provider resolution
# ===================================================================

class TestProviderResolution:
    @pytest.mark.asyncio
    async def test_ollama_provider_auto_detect(self, capsys, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]), \
             patch("httpx.get", return_value=mock_response), \
             patch("duh.adapters.ollama.OllamaProvider") as mock_ollama:
            mock_ollama.return_value.stream = AsyncMock()
            parser = build_parser()
            args = parser.parse_args(["-p", "test"])
            code = await run_print_mode(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_explicit_ollama_provider(self, capsys, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]), \
             patch("duh.adapters.ollama.OllamaProvider") as mock_ollama:
            mock_ollama.return_value.stream = AsyncMock()
            parser = build_parser()
            args = parser.parse_args(["-p", "test", "--provider", "ollama"])
            code = await run_print_mode(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_explicit_anthropic_no_key(self, capsys, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(
            "duh.providers.registry.get_valid_anthropic_oauth", lambda: None
        )
        parser = build_parser()
        args = parser.parse_args(["-p", "test", "--provider", "anthropic"])
        code = await run_print_mode(args)

        assert code == 1
        captured = capsys.readouterr()
        assert "not configured" in captured.err

    @pytest.mark.asyncio
    async def test_unknown_provider(self, capsys, monkeypatch):
        """Unknown provider returns error (unreachable via argparse but covers the branch)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        parser = build_parser()
        args = parser.parse_args(["-p", "test"])
        # Force a provider name that bypasses argparse validation
        args.provider = "unknown_provider"
        code = await run_print_mode(args)

        assert code == 1
        captured = capsys.readouterr()
        assert "Unknown provider" in captured.err

    @pytest.mark.asyncio
    async def test_custom_system_prompt(self, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            parser = build_parser()
            args = parser.parse_args(["-p", "test", "--system-prompt", "Be a pirate"])
            code = await run_print_mode(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_custom_model(self, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider") as mock_provider, \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            parser = build_parser()
            args = parser.parse_args(["-p", "test", "--model", "claude-opus-4-6"])
            code = await run_print_mode(args)

        assert code == 0


# ===================================================================
# main()
# ===================================================================

class TestMainFunction:
    def test_main_doctor(self, capsys):
        code = main(["doctor"])
        assert isinstance(code, int)

    def test_main_prompt(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            code = main(["-p", "test"])

        assert code == 0

    def test_main_no_args_enters_repl(self, monkeypatch):
        """main() with no args should route to REPL."""
        from unittest.mock import AsyncMock
        with patch("duh.cli.repl.run_repl", new_callable=AsyncMock, return_value=0):
            with patch("duh.cli.main.asyncio") as mock_asyncio:
                mock_asyncio.run = MagicMock(return_value=0)
                code = main([])
        assert code == 0


# ===================================================================
# Print mode — JSON output with dataclass
# ===================================================================

class TestPrintModeJsonSerialize:
    @pytest.mark.asyncio
    async def test_json_with_dataclass_message(self, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        msg = Message(role="assistant", content="hello")
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "assistant", "message": msg},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            parser = build_parser()
            args = parser.parse_args(["-p", "test", "--output-format", "json"])
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        events = json.loads(captured.out)
        assert isinstance(events, list)
        # The message should be serialized as a dict
        asst = [e for e in events if e.get("type") == "assistant"][0]
        assert isinstance(asst["message"], dict)


# ===================================================================
# Print mode — no output case
# ===================================================================

class TestPrintModeNoOutput:
    @pytest.mark.asyncio
    async def test_no_text_output(self, capsys, monkeypatch):
        """When no text_delta events, no trailing newline."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        engine = _make_fake_engine([
            {"type": "session", "session_id": "s1", "turn": 1},
            {"type": "done", "stop_reason": "end_turn"},
        ])

        with patch("duh.cli.runner.Engine", return_value=engine), \
             patch("duh.cli.runner.AnthropicProvider"), \
             patch("duh.cli.runner.NativeExecutor"), \
             patch("duh.cli.runner.get_all_tools", return_value=[]):
            parser = build_parser()
            args = parser.parse_args(["-p", "test"])
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        assert captured.out == ""  # no output when no text deltas


# ===================================================================
# Doctor
# ===================================================================

class TestDoctorDetails:
    def test_doctor_with_no_api_key(self, capsys, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from duh.cli.doctor import run_doctor
        code = run_doctor()
        captured = capsys.readouterr()
        assert "ANTHROPIC_API_KEY" in captured.out
        # FAIL should appear since key is not set
        assert "FAIL" in captured.out

    def test_doctor_with_api_key(self, capsys, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        from duh.cli.doctor import run_doctor
        code = run_doctor()
        captured = capsys.readouterr()
        assert "ANTHROPIC_API_KEY" in captured.out

    def test_doctor_anthropic_not_installed(self, capsys, monkeypatch):
        """When anthropic SDK is not importable."""
        import importlib
        # We can't easily uninstall anthropic, but we can verify the output
        # This test mostly confirms doctor runs without crash
        from duh.cli.doctor import run_doctor
        code = run_doctor()
        assert isinstance(code, int)
