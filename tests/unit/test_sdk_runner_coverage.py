"""Extended coverage tests for duh/cli/sdk_runner.py.

Targets the 66% of uncovered lines: formatting edge cases, control protocol,
and the run_stream_json_mode async path (mocked engine, mocked stdin/stdout).
"""

from __future__ import annotations

import argparse
import io
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli.sdk_runner import (
    _format_assistant_message,
    _format_result,
    _format_user_tool_results,
    _handle_control_request,
    run_stream_json_mode,
)
from duh.kernel.messages import Message, TextBlock, ToolUseBlock


# ===================================================================
# _format_assistant_message — extra content-type branches
# ===================================================================

class TestFormatAssistantMessageCoverage:
    """Cover content-type branches not hit by existing tests."""

    def test_non_dict_non_dataclass_content_falls_back_to_str(self):
        """A plain object in the content list should be stringified."""
        msg = Message(role="assistant", content=[42, None, True])
        result = _format_assistant_message(msg, "s1", "m1")
        blocks = result["message"]["content"]
        assert len(blocks) == 3
        for b in blocks:
            assert b["type"] == "text"
        assert blocks[0]["text"] == "42"
        assert blocks[1]["text"] == "None"
        assert blocks[2]["text"] == "True"

    def test_dataclass_content_block_converted_via_asdict(self):
        """A dataclass block should be converted via dataclasses.asdict."""

        @dataclass(frozen=True)
        class CustomBlock:
            type: str = "custom"
            value: int = 99

        msg = Message(role="assistant", content=[CustomBlock()])
        result = _format_assistant_message(msg, "s1", "m1")
        blocks = result["message"]["content"]
        assert len(blocks) == 1
        assert blocks[0] == {"type": "custom", "value": 99}

    def test_mixed_content_types(self):
        """Dict, dataclass, and plain-value content in one list."""

        @dataclass(frozen=True)
        class InfoBlock:
            type: str = "info"
            detail: str = "hi"

        msg = Message(role="assistant", content=[
            {"type": "text", "text": "hello"},
            InfoBlock(),
            3.14,
        ])
        result = _format_assistant_message(msg, "s1", "m1")
        blocks = result["message"]["content"]
        assert len(blocks) == 3
        assert blocks[0] == {"type": "text", "text": "hello"}
        assert blocks[1] == {"type": "info", "detail": "hi"}
        assert blocks[2] == {"type": "text", "text": "3.14"}

    def test_usage_from_metadata(self):
        """Usage dict should pass through from metadata."""
        msg = Message(
            role="assistant", content="ok",
            metadata={"usage": {"input_tokens": 10, "output_tokens": 5}},
        )
        result = _format_assistant_message(msg, "s1", "m1")
        assert result["message"]["usage"] == {"input_tokens": 10, "output_tokens": 5}

    def test_default_stop_reason_and_usage(self):
        """With no metadata overrides, defaults apply."""
        msg = Message(role="assistant", content="test")
        result = _format_assistant_message(msg, "s1", "m1")
        assert result["message"]["stop_reason"] == "end_turn"
        assert result["message"]["usage"] == {}

    def test_message_id_preserved(self):
        """The message ID should be forwarded."""
        msg = Message(role="assistant", content="x", id="msg-42")
        result = _format_assistant_message(msg, "s1", "m1")
        assert result["message"]["id"] == "msg-42"


# ===================================================================
# _format_user_tool_results — edge cases
# ===================================================================

class TestFormatUserToolResultsCoverage:
    def test_empty_results_list(self):
        msg = _format_user_tool_results([], "s1")
        assert msg["message"]["content"] == []
        assert msg["type"] == "user"

    def test_missing_keys_use_defaults(self):
        """Fields missing from a tool result dict should get defaults."""
        results = [{}]  # no tool_use_id, output, or is_error
        msg = _format_user_tool_results(results, "s1")
        block = msg["message"]["content"][0]
        assert block["tool_use_id"] == ""
        assert block["content"] == ""
        assert block["is_error"] is False


# ===================================================================
# _format_result — field verification
# ===================================================================

class TestFormatResultCoverage:
    def test_custom_duration_and_stop_reason(self):
        r = _format_result("s1", duration_ms=1234, stop_reason="max_turns")
        assert r["duration_ms"] == 1234
        assert r["stop_reason"] == "max_turns"

    def test_default_values(self):
        r = _format_result("s1")
        assert r["duration_ms"] == 0
        assert r["duration_api_ms"] == 0
        assert r["total_cost_usd"] == 0
        assert r["num_turns"] == 0
        assert r["result"] == ""
        assert r["is_error"] is False
        assert r["subtype"] == "success"

    def test_session_id_propagated(self):
        r = _format_result("my-session")
        assert r["session_id"] == "my-session"


# ===================================================================
# _handle_control_request — subtypes
# ===================================================================

class TestHandleControlRequestCoverage:
    def test_initialize_has_protocol_version(self):
        msg = {
            "type": "control_request",
            "request_id": "r1",
            "request": {"subtype": "initialize"},
        }
        resp = _handle_control_request(msg)
        assert resp["response"]["response"]["protocol_version"] == "1.0"

    def test_unknown_subtype_returns_empty_response(self):
        msg = {
            "type": "control_request",
            "request_id": "r2",
            "request": {"subtype": "shutdown"},
        }
        resp = _handle_control_request(msg)
        assert resp["response"]["response"] == {}
        assert resp["response"]["subtype"] == "success"
        assert resp["response"]["request_id"] == "r2"

    def test_missing_request_id_defaults_empty(self):
        msg = {"type": "control_request", "request": {"subtype": "initialize"}}
        resp = _handle_control_request(msg)
        assert resp["response"]["request_id"] == ""

    def test_missing_request_defaults_empty(self):
        msg = {"type": "control_request"}
        resp = _handle_control_request(msg)
        # subtype will be "" → falls to default branch
        assert resp["response"]["response"] == {}


# ===================================================================
# run_stream_json_mode — integration-level tests with mocked engine
# ===================================================================

def _make_sdk_args(**overrides) -> argparse.Namespace:
    """Build a Namespace matching what the parser produces for SDK mode."""
    defaults = dict(
        debug=False,
        verbose=False,
        provider="anthropic",
        model="test-model",
        system_prompt=None,
        max_turns=10,
        dangerously_skip_permissions=True,
        output_format="stream-json",
        input_format="stream-json",
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestRunStreamJsonMode:
    """Test run_stream_json_mode with mocked engine and stdin/stdout."""

    @pytest.mark.asyncio
    async def test_no_provider_emits_error_result(self, monkeypatch):
        """When no provider can be resolved, return 1 and emit error result."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        captured_lines: list[str] = []
        fake_stdout = io.StringIO()

        def capture_write(obj, file=None):
            line = json.dumps(obj, default=str)
            captured_lines.append(line)

        args = _make_sdk_args(provider=None)

        # Block Ollama detection
        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write), \
             patch("httpx.get", side_effect=Exception("no ollama")):
            code = await run_stream_json_mode(args)

        assert code == 1
        assert len(captured_lines) >= 1
        result_msg = json.loads(captured_lines[0])
        assert result_msg["type"] == "result"
        assert result_msg["is_error"] is True
        assert "No provider" in result_msg["result"]

    @pytest.mark.asyncio
    async def test_openai_provider_with_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        captured_lines: list[dict[str, Any]] = []

        def capture_write(obj, file=None):
            captured_lines.append(obj)

        mock_engine = MagicMock()
        mock_engine.session_id = "test-session"
        mock_engine.messages = []

        args = _make_sdk_args(provider="openai", model="gpt-4o")

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write), \
             patch("duh.adapters.openai.OpenAIProvider"), \
             patch("duh.cli.sdk_runner.NativeExecutor"), \
             patch("duh.cli.sdk_runner.get_all_tools", return_value=[]), \
             patch("duh.cli.sdk_runner.Engine", return_value=mock_engine), \
             patch("sys.stdin", []):
            code = await run_stream_json_mode(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_anthropic_no_api_key_emits_error(self, monkeypatch):
        """Provider=anthropic but no key/oauth → error result."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(
            "duh.providers.registry.get_valid_anthropic_oauth", lambda: None
        )

        captured_lines: list[str] = []

        def capture_write(obj, file=None):
            captured_lines.append(json.dumps(obj, default=str))

        args = _make_sdk_args(provider="anthropic")

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write):
            code = await run_stream_json_mode(args)

        assert code == 1
        result_msg = json.loads(captured_lines[0])
        assert "not configured" in result_msg["result"]

    @pytest.mark.asyncio
    async def test_unknown_provider_emits_error(self):
        """Unknown provider name → error result."""
        captured_lines: list[str] = []

        def capture_write(obj, file=None):
            captured_lines.append(json.dumps(obj, default=str))

        args = _make_sdk_args(provider="google")

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write):
            code = await run_stream_json_mode(args)

        assert code == 1
        result_msg = json.loads(captured_lines[0])
        assert "Unknown provider" in result_msg["result"]

    @pytest.mark.asyncio
    async def test_happy_path_control_and_user_message(self, monkeypatch):
        """Full flow: initialize control, user message, assistant reply, done."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        captured_lines: list[dict[str, Any]] = []

        def capture_write(obj, file=None):
            captured_lines.append(obj)

        # Stdin: control_request → user message
        stdin_lines = [
            json.dumps({
                "type": "control_request",
                "request_id": "req-1",
                "request": {"subtype": "initialize"},
            }),
            json.dumps({
                "type": "user",
                "message": {"content": "hello"},
            }),
        ]

        assistant_msg = Message(role="assistant", content="Hi there!", id="msg-1")

        async def fake_engine_run(prompt, **kwargs):
            yield {"type": "assistant", "message": assistant_msg}
            yield {"type": "done", "turns": 1}

        mock_engine = MagicMock()
        mock_engine.run = fake_engine_run
        mock_engine.session_id = "test-session"
        mock_engine.messages = [assistant_msg]

        args = _make_sdk_args()

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write), \
             patch("duh.cli.sdk_runner.AnthropicProvider"), \
             patch("duh.cli.sdk_runner.NativeExecutor"), \
             patch("duh.cli.sdk_runner.get_all_tools", return_value=[]), \
             patch("duh.cli.sdk_runner.Engine", return_value=mock_engine), \
             patch("sys.stdin", stdin_lines):
            code = await run_stream_json_mode(args)

        assert code == 0
        # Should have: control_response, assistant message, result
        types = [m.get("type") for m in captured_lines]
        assert "control_response" in types
        assert "assistant" in types
        assert "result" in types

        # Check the result
        result_msg = next(m for m in captured_lines if m.get("type") == "result")
        assert result_msg["is_error"] is False
        assert result_msg["num_turns"] == 1

    @pytest.mark.asyncio
    async def test_tool_results_emitted_as_user_message(self, monkeypatch):
        """Tool results should be emitted as a user message at done."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        captured_lines: list[dict[str, Any]] = []

        def capture_write(obj, file=None):
            captured_lines.append(obj)

        stdin_lines = [
            json.dumps({
                "type": "user",
                "message": {"content": "read /tmp/x"},
            }),
        ]

        assistant_msg = Message(role="assistant", content="Reading file", id="msg-1")

        async def fake_engine_run(prompt, **kwargs):
            yield {"type": "assistant", "message": assistant_msg}
            yield {"type": "tool_result", "tool_use_id": "tu-1", "output": "file data", "is_error": False}
            yield {"type": "done", "turns": 1}

        mock_engine = MagicMock()
        mock_engine.run = fake_engine_run
        mock_engine.session_id = "test-session"
        mock_engine.messages = [assistant_msg]

        args = _make_sdk_args()

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write), \
             patch("duh.cli.sdk_runner.AnthropicProvider"), \
             patch("duh.cli.sdk_runner.NativeExecutor"), \
             patch("duh.cli.sdk_runner.get_all_tools", return_value=[]), \
             patch("duh.cli.sdk_runner.Engine", return_value=mock_engine), \
             patch("sys.stdin", stdin_lines):
            code = await run_stream_json_mode(args)

        assert code == 0
        types = [m.get("type") for m in captured_lines]
        assert "user" in types  # tool results emitted as user message

    @pytest.mark.asyncio
    async def test_error_event_sets_had_error(self, monkeypatch):
        """An error event from the engine should cause exit code 1."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        captured_lines: list[dict[str, Any]] = []

        def capture_write(obj, file=None):
            captured_lines.append(obj)

        stdin_lines = [
            json.dumps({
                "type": "user",
                "message": {"content": "fail"},
            }),
        ]

        async def fake_engine_run(prompt, **kwargs):
            yield {"type": "error", "error": "something broke"}
            yield {"type": "done", "turns": 0}

        mock_engine = MagicMock()
        mock_engine.run = fake_engine_run
        mock_engine.session_id = "test-session"
        mock_engine.messages = []

        args = _make_sdk_args()

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write), \
             patch("duh.cli.sdk_runner.AnthropicProvider"), \
             patch("duh.cli.sdk_runner.NativeExecutor"), \
             patch("duh.cli.sdk_runner.get_all_tools", return_value=[]), \
             patch("duh.cli.sdk_runner.Engine", return_value=mock_engine), \
             patch("sys.stdin", stdin_lines):
            code = await run_stream_json_mode(args)

        assert code == 1
        result_msg = next(m for m in captured_lines if m.get("type") == "result")
        assert result_msg["is_error"] is True

    @pytest.mark.asyncio
    async def test_control_response_is_skipped(self, monkeypatch):
        """control_response messages from stdin should be silently ignored."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        captured_lines: list[dict[str, Any]] = []

        def capture_write(obj, file=None):
            captured_lines.append(obj)

        stdin_lines = [
            json.dumps({"type": "control_response", "response": {}}),
        ]

        mock_engine = MagicMock()
        mock_engine.run = AsyncMock()  # should never be called
        mock_engine.session_id = "test-session"
        mock_engine.messages = []

        args = _make_sdk_args()

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write), \
             patch("duh.cli.sdk_runner.AnthropicProvider"), \
             patch("duh.cli.sdk_runner.NativeExecutor"), \
             patch("duh.cli.sdk_runner.get_all_tools", return_value=[]), \
             patch("duh.cli.sdk_runner.Engine", return_value=mock_engine), \
             patch("sys.stdin", stdin_lines):
            code = await run_stream_json_mode(args)

        assert code == 0
        # Only the final result message should appear
        assert len(captured_lines) == 1
        assert captured_lines[0]["type"] == "result"

    @pytest.mark.asyncio
    async def test_empty_user_content_skipped(self, monkeypatch):
        """A user message with empty content should be skipped."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        captured_lines: list[dict[str, Any]] = []

        def capture_write(obj, file=None):
            captured_lines.append(obj)

        stdin_lines = [
            json.dumps({"type": "user", "message": {"content": ""}}),
        ]

        mock_engine = MagicMock()
        mock_engine.run = AsyncMock()  # should never be called
        mock_engine.session_id = "test-session"
        mock_engine.messages = []

        args = _make_sdk_args()

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write), \
             patch("duh.cli.sdk_runner.AnthropicProvider"), \
             patch("duh.cli.sdk_runner.NativeExecutor"), \
             patch("duh.cli.sdk_runner.get_all_tools", return_value=[]), \
             patch("duh.cli.sdk_runner.Engine", return_value=mock_engine), \
             patch("sys.stdin", stdin_lines):
            code = await run_stream_json_mode(args)

        assert code == 0
        # Engine.run should not have been called
        mock_engine.run.assert_not_called()

    @pytest.mark.asyncio
    async def test_ollama_provider_auto_detection(self, monkeypatch):
        """When ANTHROPIC_API_KEY is absent and Ollama responds, use ollama."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        captured_lines: list[dict[str, Any]] = []

        def capture_write(obj, file=None):
            captured_lines.append(obj)

        stdin_lines: list[str] = []  # empty stdin → goes right to result

        mock_engine = MagicMock()
        mock_engine.session_id = "test-session"
        mock_engine.messages = []

        mock_ollama_provider = MagicMock()

        mock_httpx_response = MagicMock()
        mock_httpx_response.status_code = 200

        args = _make_sdk_args(provider=None)

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write), \
             patch("httpx.get", return_value=mock_httpx_response), \
             patch("duh.adapters.ollama.OllamaProvider", return_value=mock_ollama_provider), \
             patch("duh.cli.sdk_runner.NativeExecutor"), \
             patch("duh.cli.sdk_runner.get_all_tools", return_value=[]), \
             patch("duh.cli.sdk_runner.Engine", return_value=mock_engine), \
             patch("sys.stdin", stdin_lines):
            code = await run_stream_json_mode(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_tool_use_event_is_noop(self, monkeypatch):
        """tool_use events are part of assistant message — should be passed through."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        captured_lines: list[dict[str, Any]] = []

        def capture_write(obj, file=None):
            captured_lines.append(obj)

        stdin_lines = [
            json.dumps({"type": "user", "message": {"content": "test"}}),
        ]

        async def fake_engine_run(prompt, **kwargs):
            yield {"type": "tool_use", "name": "Read", "input": {"path": "/x"}}
            yield {"type": "done", "turns": 1}

        mock_engine = MagicMock()
        mock_engine.run = fake_engine_run
        mock_engine.session_id = "test-session"
        mock_engine.messages = []

        args = _make_sdk_args()

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write), \
             patch("duh.cli.sdk_runner.AnthropicProvider"), \
             patch("duh.cli.sdk_runner.NativeExecutor"), \
             patch("duh.cli.sdk_runner.get_all_tools", return_value=[]), \
             patch("duh.cli.sdk_runner.Engine", return_value=mock_engine), \
             patch("sys.stdin", stdin_lines):
            code = await run_stream_json_mode(args)

        assert code == 0
        # tool_use should not produce its own ndjson line
        types = [m.get("type") for m in captured_lines]
        assert "tool_use" not in types

    @pytest.mark.asyncio
    async def test_debug_mode_enables_logging(self, monkeypatch):
        """debug=True should configure logging to stderr."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        captured_lines: list[dict[str, Any]] = []

        def capture_write(obj, file=None):
            captured_lines.append(obj)

        mock_engine = MagicMock()
        mock_engine.session_id = "s"
        mock_engine.messages = []

        args = _make_sdk_args(debug=True)

        with patch("duh.cli.sdk_runner.ndjson_write", side_effect=capture_write), \
             patch("duh.cli.sdk_runner.AnthropicProvider"), \
             patch("duh.cli.sdk_runner.NativeExecutor"), \
             patch("duh.cli.sdk_runner.get_all_tools", return_value=[]), \
             patch("duh.cli.sdk_runner.Engine", return_value=mock_engine), \
             patch("sys.stdin", []), \
             patch("logging.basicConfig") as mock_basic_config:
            code = await run_stream_json_mode(args)

        assert code == 0
        mock_basic_config.assert_called_once()
