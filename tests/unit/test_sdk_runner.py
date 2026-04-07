"""Tests for SDK stream-json runner."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from duh.cli.parser import build_parser
from duh.cli.sdk_runner import (
    _format_assistant_message,
    _format_result,
    _format_user_tool_results,
    _handle_control_request,
)
from duh.kernel.messages import Message, TextBlock, ToolUseBlock


# ===================================================================
# Message formatting
# ===================================================================

class TestFormatAssistantMessage:
    def test_string_content(self):
        msg = Message(role="assistant", content="Hello world")
        result = _format_assistant_message(msg, "session-1", "claude-sonnet-4-6")
        assert result["type"] == "assistant"
        assert result["session_id"] == "session-1"
        assert result["message"]["model"] == "claude-sonnet-4-6"
        blocks = result["message"]["content"]
        assert len(blocks) == 1
        assert blocks[0] == {"type": "text", "text": "Hello world"}

    def test_empty_string_content(self):
        msg = Message(role="assistant", content="")
        result = _format_assistant_message(msg, "s1", "m1")
        assert result["message"]["content"] == []

    def test_list_content_with_text_block(self):
        msg = Message(role="assistant", content=[
            TextBlock(text="Hello"),
        ])
        result = _format_assistant_message(msg, "s1", "m1")
        blocks = result["message"]["content"]
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == "Hello"

    def test_list_content_with_tool_use_block(self):
        msg = Message(role="assistant", content=[
            TextBlock(text="Let me read that"),
            ToolUseBlock(id="tu_1", name="Read", input={"path": "/tmp/x"}),
        ])
        result = _format_assistant_message(msg, "s1", "m1")
        blocks = result["message"]["content"]
        assert len(blocks) == 2
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["name"] == "Read"

    def test_dict_content_blocks(self):
        msg = Message(role="assistant", content=[
            {"type": "text", "text": "hello"},
        ])
        result = _format_assistant_message(msg, "s1", "m1")
        blocks = result["message"]["content"]
        assert blocks == [{"type": "text", "text": "hello"}]

    def test_has_uuid(self):
        msg = Message(role="assistant", content="hi")
        result = _format_assistant_message(msg, "s1", "m1")
        assert "uuid" in result
        assert isinstance(result["uuid"], str)

    def test_stop_reason_from_metadata(self):
        msg = Message(role="assistant", content="hi",
                      metadata={"stop_reason": "max_tokens"})
        result = _format_assistant_message(msg, "s1", "m1")
        assert result["message"]["stop_reason"] == "max_tokens"


class TestFormatUserToolResults:
    def test_single_result(self):
        results = [{"tool_use_id": "tu_1", "output": "file contents", "is_error": False}]
        msg = _format_user_tool_results(results, "s1")
        assert msg["type"] == "user"
        blocks = msg["message"]["content"]
        assert len(blocks) == 1
        assert blocks[0]["type"] == "tool_result"
        assert blocks[0]["tool_use_id"] == "tu_1"
        assert blocks[0]["content"] == "file contents"

    def test_multiple_results(self):
        results = [
            {"tool_use_id": "tu_1", "output": "ok", "is_error": False},
            {"tool_use_id": "tu_2", "output": "err", "is_error": True},
        ]
        msg = _format_user_tool_results(results, "s1")
        blocks = msg["message"]["content"]
        assert len(blocks) == 2
        assert blocks[1]["is_error"] is True

    def test_has_session_and_uuid(self):
        msg = _format_user_tool_results([], "session-42")
        assert msg["session_id"] == "session-42"
        assert "uuid" in msg


class TestFormatResult:
    def test_success_result(self):
        r = _format_result("s1", num_turns=3, result_text="Done")
        assert r["type"] == "result"
        assert r["subtype"] == "success"
        assert r["is_error"] is False
        assert r["num_turns"] == 3
        assert r["result"] == "Done"

    def test_error_result(self):
        r = _format_result("s1", is_error=True, result_text="Failed")
        assert r["subtype"] == "error"
        assert r["is_error"] is True

    def test_has_required_fields(self):
        r = _format_result("s1")
        required = ["type", "subtype", "duration_ms", "duration_api_ms",
                     "is_error", "num_turns", "session_id", "stop_reason",
                     "total_cost_usd", "usage", "result", "uuid"]
        for field in required:
            assert field in r, f"Missing field: {field}"


# ===================================================================
# Control protocol
# ===================================================================

class TestHandleControlRequest:
    def test_initialize_request(self):
        msg = {
            "type": "control_request",
            "request_id": "req_1",
            "request": {"subtype": "initialize"},
        }
        resp = _handle_control_request(msg)
        assert resp["type"] == "control_response"
        assert resp["response"]["subtype"] == "success"
        assert resp["response"]["request_id"] == "req_1"
        assert "protocol_version" in resp["response"]["response"]

    def test_unknown_subtype(self):
        msg = {
            "type": "control_request",
            "request_id": "req_2",
            "request": {"subtype": "something_else"},
        }
        resp = _handle_control_request(msg)
        assert resp["response"]["subtype"] == "success"
        assert resp["response"]["request_id"] == "req_2"


# ===================================================================
# Parser flags
# ===================================================================

class TestParserStreamJson:
    def test_output_format_stream_json(self):
        parser = build_parser()
        args = parser.parse_args(["--output-format", "stream-json"])
        assert args.output_format == "stream-json"

    def test_input_format_stream_json(self):
        parser = build_parser()
        args = parser.parse_args(["--input-format", "stream-json"])
        assert args.input_format == "stream-json"

    def test_input_format_default(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.input_format == "text"

    def test_verbose_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--verbose"])
        assert args.verbose is True

    def test_sdk_mode_flags(self):
        """SDK launches with these exact flags."""
        parser = build_parser()
        args = parser.parse_args([
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--system-prompt", "You are helpful",
            "--max-turns", "20",
        ])
        assert args.output_format == "stream-json"
        assert args.input_format == "stream-json"
        assert args.verbose is True
        assert args.system_prompt == "You are helpful"
        assert args.max_turns == 20


# ===================================================================
# Main routing
# ===================================================================

class TestMainStreamJsonRouting:
    def test_stream_json_input_routes_to_sdk_runner(self, monkeypatch):
        """--input-format stream-json should route to run_stream_json_mode."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        mock_run = MagicMock(return_value=0)
        with patch("duh.cli.main.asyncio") as mock_asyncio:
            mock_asyncio.run = mock_run
            with patch("duh.cli.sdk_runner.run_stream_json_mode") as mock_sdk:
                mock_sdk.return_value = 0  # won't actually be used since asyncio.run is mocked
                from duh.cli.main import main
                # We need to mock asyncio.run to call our mock
                # The actual routing happens in main()
                code = main(["--input-format", "stream-json"])

        # asyncio.run should have been called (routing to sdk_runner)
        assert mock_run.called
