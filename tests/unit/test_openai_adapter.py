"""Tests for the OpenAI adapter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.kernel.messages import Message, TextBlock, ToolUseBlock


# ===================================================================
# Message conversion
# ===================================================================

class TestToOpenAIMessages:
    def test_system_prompt_string(self):
        from duh.adapters.openai import _to_openai_messages
        msgs = _to_openai_messages([], "You are helpful")
        assert msgs[0] == {"role": "system", "content": "You are helpful"}

    def test_system_prompt_list(self):
        from duh.adapters.openai import _to_openai_messages
        msgs = _to_openai_messages([], ["Line 1", "Line 2"])
        assert msgs[0]["role"] == "system"
        assert "Line 1" in msgs[0]["content"]
        assert "Line 2" in msgs[0]["content"]

    def test_empty_system_prompt(self):
        from duh.adapters.openai import _to_openai_messages
        msgs = _to_openai_messages([], "")
        assert len(msgs) == 0

    def test_user_message_string(self):
        from duh.adapters.openai import _to_openai_messages
        user = Message(role="user", content="hello")
        msgs = _to_openai_messages([user], "")
        assert msgs[0] == {"role": "user", "content": "hello"}

    def test_assistant_message_string(self):
        from duh.adapters.openai import _to_openai_messages
        asst = Message(role="assistant", content="world")
        msgs = _to_openai_messages([asst], "")
        assert msgs[0] == {"role": "assistant", "content": "world"}

    def test_assistant_message_with_tool_use(self):
        from duh.adapters.openai import _to_openai_messages
        asst = Message(role="assistant", content=[
            {"type": "text", "text": "Let me read that"},
            {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"path": "/tmp/x"}},
        ])
        msgs = _to_openai_messages([asst], "")
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] == "Let me read that"
        assert len(msgs[0]["tool_calls"]) == 1
        assert msgs[0]["tool_calls"][0]["function"]["name"] == "Read"

    def test_tool_result_becomes_tool_message(self):
        from duh.adapters.openai import _to_openai_messages
        user = Message(role="user", content=[
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "file data"},
        ])
        msgs = _to_openai_messages([user], "")
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["tool_call_id"] == "tu_1"
        assert msgs[0]["content"] == "file data"


# ===================================================================
# Tool conversion
# ===================================================================

class TestToOpenAITools:
    def test_converts_tools(self):
        from duh.adapters.openai import _to_openai_tools

        class FakeTool:
            name = "Read"
            description = "Read a file"
            input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}

        tools = _to_openai_tools([FakeTool()])
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "Read"
        assert tools[0]["function"]["description"] == "Read a file"

    def test_skips_unnamed_tools(self):
        from duh.adapters.openai import _to_openai_tools

        class NoName:
            name = ""
            description = "no name"
            input_schema = {}

        assert _to_openai_tools([NoName()]) == []


# ===================================================================
# Provider
# ===================================================================

class TestOpenAIProvider:
    def test_init_with_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("openai.AsyncOpenAI"):
            from duh.adapters.openai import OpenAIProvider
            provider = OpenAIProvider(api_key="sk-test")
            assert provider._default_model == "gpt-4o"

    def test_init_custom_model(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("openai.AsyncOpenAI"):
            from duh.adapters.openai import OpenAIProvider
            provider = OpenAIProvider(api_key="sk-test", model="o1")
            assert provider._default_model == "o1"


# ===================================================================
# Provider in parser
# ===================================================================

class TestParserOpenAI:
    def test_openai_provider_choice(self):
        from duh.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["--provider", "openai", "-p", "hi"])
        assert args.provider == "openai"
