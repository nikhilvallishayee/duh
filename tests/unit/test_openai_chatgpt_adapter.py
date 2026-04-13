from __future__ import annotations

import json

import pytest

from duh.adapters.openai_chatgpt import OpenAIChatGPTProvider
from duh.adapters.openai_chatgpt import _response_to_content_blocks, _to_responses_input
from duh.kernel.messages import Message


def test_to_responses_input_includes_system_prompt():
    items = _to_responses_input([], "System prompt")
    assert items[0]["role"] == "system"


def test_to_responses_input_maps_tool_result():
    user = Message(
        role="user",
        content=[
            {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"},
        ],
    )
    items = _to_responses_input([user], "")
    assert any(i.get("type") == "function_call_output" for i in items)


def test_response_to_content_blocks_maps_function_call():
    response = {
        "output": [
            {
                "type": "function_call",
                "call_id": "abc",
                "name": "Read",
                "arguments": '{"path":"x"}',
            }
        ]
    }
    blocks = _response_to_content_blocks(response)
    assert blocks[0]["type"] == "tool_use"
    assert blocks[0]["name"] == "Read"


@pytest.mark.asyncio
async def test_stream_sends_required_fields_and_parses_text_delta(monkeypatch):
    captured = {}

    class _FakeResp:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aread(self):
            return b""

        async def aiter_lines(self):
            yield 'data: {"type":"response.output_text.delta","delta":"Hello"}'
            yield 'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"Hello"}]}]}}'
            yield "data: [DONE]"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, headers=None, json=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResp()

    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.get_valid_openai_chatgpt_oauth",
        lambda: {"access_token": "tok", "account_id": "acct"},
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", _FakeClient)

    provider = OpenAIChatGPTProvider(model="gpt-5.2-codex")
    events = []
    async for ev in provider.stream(
        messages=[Message(role="user", content="Explain this repo")],
        system_prompt="System prompt text",
    ):
        events.append(ev)

    assert captured["method"] == "POST"
    assert captured["url"].endswith("/backend-api/codex/responses")
    assert captured["json"]["instructions"] == "System prompt text"
    assert captured["json"]["store"] is False
    assert "reasoning.encrypted_content" in captured["json"]["include"]
    assert any(e.get("type") == "text_delta" for e in events)
    assert any(e.get("type") == "assistant" for e in events)


@pytest.mark.asyncio
async def test_stream_falls_back_to_delta_text_when_no_completed_event(monkeypatch):
    class _FakeResp:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aread(self):
            return b""

        async def aiter_lines(self):
            yield 'data: {"type":"response.output_text.delta","delta":"Hello"}'
            yield "data: [DONE]"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, headers=None, json=None):
            return _FakeResp()

    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.get_valid_openai_chatgpt_oauth",
        lambda: {"access_token": "tok", "account_id": "acct"},
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", _FakeClient)

    provider = OpenAIChatGPTProvider(model="gpt-5.2-codex")
    events = []
    async for ev in provider.stream(
        messages=[Message(role="user", content="hi")],
        system_prompt="system",
    ):
        events.append(ev)

    assistant_events = [e for e in events if e.get("type") == "assistant"]
    assert assistant_events
    msg = assistant_events[-1]["message"]
    assert "Hello" in msg.text


@pytest.mark.asyncio
async def test_stream_fetches_final_response_by_id_when_completed_has_no_response(monkeypatch):
    captured = {"fetched": False}

    class _FakeResp:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aread(self):
            return b""

        async def aiter_lines(self):
            yield 'data: {"type":"response.created","response":{"id":"resp_123"}}'
            yield 'data: {"type":"response.completed"}'
            yield "data: [DONE]"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, headers=None, json=None):
            return _FakeResp()

        async def get(self, url, headers=None):
            captured["fetched"] = True

            class _R:
                status_code = 200

                @staticmethod
                def json():
                    return {
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "Fetched final content"}],
                            }
                        ]
                    }

            return _R()

    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.get_valid_openai_chatgpt_oauth",
        lambda: {"access_token": "tok", "account_id": "acct"},
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", _FakeClient)

    provider = OpenAIChatGPTProvider(model="gpt-5.2-codex")
    events = []
    async for ev in provider.stream(
        messages=[Message(role="user", content="hi")],
        system_prompt="system",
    ):
        events.append(ev)

    assert captured["fetched"] is True
    assistant_events = [e for e in events if e.get("type") == "assistant"]
    assert assistant_events
    assert "Fetched final content" in assistant_events[-1]["message"].text


@pytest.mark.asyncio
async def test_stream_fetches_final_response_when_completed_response_is_empty(monkeypatch):
    captured = {"fetched": False}

    class _FakeResp:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aread(self):
            return b""

        async def aiter_lines(self):
            yield 'data: {"type":"response.created","response":{"id":"resp_456"}}'
            yield 'data: {"type":"response.completed","response":{"id":"resp_456","status":"completed"}}'
            yield "data: [DONE]"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, headers=None, json=None):
            return _FakeResp()

        async def get(self, url, headers=None):
            captured["fetched"] = True

            class _R:
                status_code = 200

                @staticmethod
                def json():
                    return {
                        "output": [
                            {
                                "type": "message",
                                "content": [{"type": "output_text", "text": "Fetched from empty completed response"}],
                            }
                        ]
                    }

            return _R()

    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.get_valid_openai_chatgpt_oauth",
        lambda: {"access_token": "tok", "account_id": "acct"},
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", _FakeClient)

    provider = OpenAIChatGPTProvider(model="gpt-5.2-codex")
    events = []
    async for ev in provider.stream(
        messages=[Message(role="user", content="hi")],
        system_prompt="system",
    ):
        events.append(ev)

    assert captured["fetched"] is True
    assistant_events = [e for e in events if e.get("type") == "assistant"]
    assert assistant_events
    assert "Fetched from empty completed response" in assistant_events[-1]["message"].text


@pytest.mark.asyncio
async def test_stream_builds_tool_use_from_function_call_only_events(monkeypatch):
    class _FakeResp:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aread(self):
            return b""

        async def aiter_lines(self):
            yield "data: " + json.dumps(
                {
                    "type": "response.output_item.added",
                    "item": {
                        "id": "itm_1",
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "Bash",
                        "arguments": "{",
                    },
                }
            )
            yield "data: " + json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "itm_1",
                    "delta": '"command":"ls"',
                }
            )
            yield "data: " + json.dumps(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "itm_1",
                    "delta": "}",
                }
            )
            yield "data: " + json.dumps(
                {
                    "type": "response.completed",
                    "response": {"id": "resp_1", "status": "completed"},
                }
            )
            yield "data: [DONE]"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, headers=None, json=None):
            return _FakeResp()

        async def get(self, url, headers=None):
            class _R:
                status_code = 404

                @staticmethod
                def json():
                    return {}

            return _R()

    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.get_valid_openai_chatgpt_oauth",
        lambda: {"access_token": "tok", "account_id": "acct"},
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", _FakeClient)

    provider = OpenAIChatGPTProvider(model="gpt-5.2-codex")
    events = []
    async for ev in provider.stream(
        messages=[Message(role="user", content="list files")],
        system_prompt="system",
    ):
        events.append(ev)

    assistant_events = [e for e in events if e.get("type") == "assistant"]
    assert assistant_events
    blocks = assistant_events[-1]["message"].content
    tool_blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
    assert tool_blocks
    assert tool_blocks[0]["name"] == "Bash"
    assert tool_blocks[0]["input"].get("command") == "ls"
