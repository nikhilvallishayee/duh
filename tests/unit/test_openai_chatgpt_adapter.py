from __future__ import annotations

import json

import pytest

from duh.adapters.openai_chatgpt import (
    OpenAIChatGPTProvider,
    _accumulate_streamed_function_calls,
    _build_system_text,
    _extract_response_id,
    _extract_texts_from_event,
    _fetch_response_by_id,
    _has_meaningful_content_blocks,
    _merge_streamed_calls_into_response,
    _response_missing_content,
    _response_to_content_blocks,
    _to_responses_input,
    _to_responses_tools,
)
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


# ---------------------------------------------------------------------------
# Fake SSE client helper
# ---------------------------------------------------------------------------


def _make_fake_client(
    *,
    lines=None,
    status_code: int = 200,
    read_body: bytes = b"",
    get_status: int = 404,
    get_body=None,
    raise_on_stream=None,
):
    class _FakeResp:
        def __init__(self):
            self.status_code = status_code

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aread(self):
            return read_body

        async def aiter_lines(self):
            for ln in lines or []:
                yield ln

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, headers=None, json=None):
            if raise_on_stream is not None:
                raise raise_on_stream
            return _FakeResp()

        async def get(self, url, headers=None):
            class _R:
                status_code = get_status

                @staticmethod
                def json():
                    return get_body if get_body is not None else {}

            return _R()

    return _FakeClient


_UNSET = object()


def _patch_oauth(monkeypatch, value=_UNSET):
    if value is _UNSET:
        value = {"access_token": "tok", "account_id": "acct"}
    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.get_valid_openai_chatgpt_oauth", lambda: value
    )


# ---------------------------------------------------------------------------
# _build_system_text
# ---------------------------------------------------------------------------


def test_build_system_text_string():
    assert _build_system_text("hello") == "hello"


def test_build_system_text_list():
    assert _build_system_text(["a", "b"]) == "a\n\nb"


def test_build_system_text_empty_string():
    assert _build_system_text("") == ""


# ---------------------------------------------------------------------------
# _to_responses_input
# ---------------------------------------------------------------------------


def test_to_responses_input_list_system_prompt():
    items = _to_responses_input([], ["a", "b"])
    assert items[0]["role"] == "system"
    assert items[0]["content"][0]["text"] == "a\n\nb"


def test_to_responses_input_dict_message_string_content():
    items = _to_responses_input([{"role": "user", "content": "hi"}], "")
    assert items[0]["role"] == "user"
    assert items[0]["content"][0]["text"] == "hi"


def test_to_responses_input_empty_string_content_skipped():
    items = _to_responses_input([{"role": "user", "content": ""}], "")
    assert items == []


def test_to_responses_input_non_list_content_skipped():
    items = _to_responses_input([{"role": "user", "content": 123}], "")
    assert items == []


def test_to_responses_input_tool_use_block():
    msg = Message(
        role="assistant",
        content=[
            {"type": "tool_use", "id": "call_x", "name": "Bash", "input": {"cmd": "ls"}},
        ],
    )
    items = _to_responses_input([msg], "")
    tool_calls = [i for i in items if i.get("type") == "function_call"]
    assert tool_calls
    assert tool_calls[0]["call_id"] == "call_x"
    assert tool_calls[0]["name"] == "Bash"
    assert json.loads(tool_calls[0]["arguments"]) == {"cmd": "ls"}


def test_to_responses_input_mixed_text_and_tool_use():
    msg = Message(
        role="assistant",
        content=[
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
            {"type": "tool_use", "id": "c1", "name": "T", "input": {}},
        ],
    )
    items = _to_responses_input([msg], "")
    text_items = [i for i in items if i.get("type") == "message"]
    assert text_items
    assert text_items[0]["content"][0]["text"] == "hello world"
    assert any(i.get("type") == "function_call" for i in items)


def test_to_responses_input_non_dict_block_uses_dict_attr():
    class _B:
        def __init__(self):
            self.type = "text"
            self.text = "block-text"

    msg = Message(role="user", content=[_B()])
    items = _to_responses_input([msg], "")
    assert items and items[0]["content"][0]["text"] == "block-text"


def test_to_responses_input_text_block_empty_string_skipped():
    msg = Message(role="user", content=[{"type": "text", "text": ""}])
    items = _to_responses_input([msg], "")
    assert items == []


# ---------------------------------------------------------------------------
# _to_responses_tools
# ---------------------------------------------------------------------------


class _FakeTool:
    def __init__(self, name="", description="", input_schema=None):
        self.name = name
        self.description = description
        self.input_schema = input_schema


def test_to_responses_tools_empty():
    assert _to_responses_tools([]) == []


def test_to_responses_tools_skips_unnamed():
    out = _to_responses_tools([_FakeTool(name="")])
    assert out == []


def test_to_responses_tools_uses_description_and_schema():
    tool = _FakeTool(name="T", description="desc", input_schema={"type": "object", "properties": {}})
    out = _to_responses_tools([tool])
    assert out[0]["name"] == "T"
    assert out[0]["description"] == "desc"
    assert out[0]["parameters"] == {"type": "object", "properties": {}}


def test_to_responses_tools_default_schema_when_none():
    out = _to_responses_tools([_FakeTool(name="T", input_schema=None)])
    assert out[0]["parameters"] == {"type": "object"}


# ---------------------------------------------------------------------------
# _response_to_content_blocks
# ---------------------------------------------------------------------------


def test_response_to_content_blocks_none():
    assert _response_to_content_blocks(None) == [{"type": "text", "text": ""}]


def test_response_to_content_blocks_output_text_field():
    blocks = _response_to_content_blocks({"output_text": "hi"})
    assert blocks[0] == {"type": "text", "text": "hi"}


def test_response_to_content_blocks_message_refusal():
    resp = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "refusal", "refusal": "nope"}],
            }
        ]
    }
    blocks = _response_to_content_blocks(resp)
    assert any(b.get("text") == "nope" for b in blocks)


def test_response_to_content_blocks_message_summary_text():
    resp = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "summary_text", "text": "sum"}],
            }
        ]
    }
    blocks = _response_to_content_blocks(resp)
    assert any(b.get("text") == "sum" for b in blocks)


def test_response_to_content_blocks_top_level_output_text_item():
    resp = {"output": [{"type": "output_text", "text": "top"}]}
    blocks = _response_to_content_blocks(resp)
    assert any(b.get("text") == "top" for b in blocks)


def test_response_to_content_blocks_reasoning_summary():
    resp = {
        "output": [
            {
                "type": "reasoning",
                "summary": [{"text": "thinking..."}],
            }
        ]
    }
    blocks = _response_to_content_blocks(resp)
    assert any(b.get("text") == "thinking..." for b in blocks)


def test_response_to_content_blocks_function_call_args_str():
    resp = {
        "output": [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "F",
                "arguments": '{"x":1}',
            }
        ]
    }
    blocks = _response_to_content_blocks(resp)
    assert blocks[0]["type"] == "tool_use"
    assert blocks[0]["input"] == {"x": 1}


def test_response_to_content_blocks_function_call_args_dict():
    resp = {
        "output": [
            {
                "type": "function_call",
                "id": "c1",
                "name": "F",
                "arguments": {"x": 1},
            }
        ]
    }
    blocks = _response_to_content_blocks(resp)
    assert blocks[0]["type"] == "tool_use"
    assert blocks[0]["input"] == {"x": 1}
    assert blocks[0]["id"] == "c1"


def test_response_to_content_blocks_function_call_bad_args():
    resp = {
        "output": [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "F",
                "arguments": "not-json",
            }
        ]
    }
    blocks = _response_to_content_blocks(resp)
    assert blocks[0]["type"] == "tool_use"
    assert blocks[0]["input"] == {}


def test_response_to_content_blocks_non_dict_item_skipped():
    resp = {"output": ["not-a-dict", {"type": "message", "content": [{"type": "text", "text": "ok"}]}]}
    blocks = _response_to_content_blocks(resp)
    assert any(b.get("text") == "ok" for b in blocks)


def test_response_to_content_blocks_non_dict_content_skipped():
    resp = {
        "output": [
            {"type": "message", "content": ["str", {"type": "text", "text": "real"}]}
        ]
    }
    blocks = _response_to_content_blocks(resp)
    assert any(b.get("text") == "real" for b in blocks)


def test_response_to_content_blocks_empty_default():
    blocks = _response_to_content_blocks({})
    assert blocks == [{"type": "text", "text": ""}]


# ---------------------------------------------------------------------------
# _response_missing_content
# ---------------------------------------------------------------------------


def test_response_missing_content_none():
    assert _response_missing_content(None) is True


def test_response_missing_content_with_output_text():
    assert _response_missing_content({"output_text": "hi"}) is False


def test_response_missing_content_empty_output():
    assert _response_missing_content({"output": []}) is True


def test_response_missing_content_with_output():
    assert _response_missing_content({"output": [{"type": "message"}]}) is False


# ---------------------------------------------------------------------------
# _has_meaningful_content_blocks
# ---------------------------------------------------------------------------


def test_has_meaningful_content_blocks_empty_list():
    assert _has_meaningful_content_blocks([]) is False


def test_has_meaningful_content_blocks_only_empty_text():
    assert _has_meaningful_content_blocks([{"type": "text", "text": ""}]) is False


def test_has_meaningful_content_blocks_with_text():
    assert _has_meaningful_content_blocks([{"type": "text", "text": "hi"}]) is True


def test_has_meaningful_content_blocks_with_tool_use():
    assert _has_meaningful_content_blocks([{"type": "tool_use", "id": "x", "name": "n", "input": {}}]) is True


def test_has_meaningful_content_blocks_non_dict_skipped():
    assert _has_meaningful_content_blocks(["not-a-dict", {"type": "text", "text": "ok"}]) is True


# ---------------------------------------------------------------------------
# _extract_response_id
# ---------------------------------------------------------------------------


def test_extract_response_id_non_dict():
    assert _extract_response_id("nope") == ""  # type: ignore[arg-type]


def test_extract_response_id_top_level():
    assert _extract_response_id({"response_id": "abc"}) == "abc"


def test_extract_response_id_nested_response():
    assert _extract_response_id({"response": {"id": "rid"}}) == "rid"


def test_extract_response_id_missing():
    assert _extract_response_id({}) == ""


# ---------------------------------------------------------------------------
# _extract_texts_from_event
# ---------------------------------------------------------------------------


def test_extract_texts_non_dict():
    assert _extract_texts_from_event("x") == []  # type: ignore[arg-type]


def test_extract_texts_delta_str():
    assert _extract_texts_from_event({"delta": "hi"}) == ["hi"]


def test_extract_texts_delta_dict_text():
    assert _extract_texts_from_event({"delta": {"text": "hi"}}) == ["hi"]


def test_extract_texts_top_level_text_and_output_text():
    out = _extract_texts_from_event({"text": "a", "output_text": "b"})
    assert "a" in out and "b" in out


def test_extract_texts_item_output_text():
    out = _extract_texts_from_event({"item": {"type": "output_text", "text": "hi"}})
    assert out == ["hi"]


def test_extract_texts_item_message_content_list():
    out = _extract_texts_from_event(
        {
            "item": {
                "type": "message",
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "summary_text", "text": "b"},
                    "not-a-dict",
                    {"type": "other", "text": "skip"},
                ],
            }
        }
    )
    assert "a" in out
    assert "b" in out
    assert "skip" not in out


# ---------------------------------------------------------------------------
# _accumulate_streamed_function_calls
# ---------------------------------------------------------------------------


def test_accumulate_non_dict_event():
    calls: dict = {}
    itc: dict = {}
    _accumulate_streamed_function_calls("nope", calls, itc)  # type: ignore[arg-type]
    assert calls == {}


def test_accumulate_function_call_item_sets_name_and_args():
    calls: dict = {}
    itc: dict = {}
    event = {
        "type": "response.output_item.added",
        "item": {
            "id": "itm_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "Bash",
            "arguments": "{",
        },
    }
    _accumulate_streamed_function_calls(event, calls, itc)
    assert calls["call_1"]["name"] == "Bash"
    assert calls["call_1"]["arguments"] == "{"
    assert itc["itm_1"] == "call_1"


def test_accumulate_unknown_key_when_no_ids():
    calls: dict = {}
    itc: dict = {}
    event = {
        "type": "response.output_item.added",
        "item": {"type": "function_call", "name": "T"},
    }
    _accumulate_streamed_function_calls(event, calls, itc)
    assert any(k.startswith("_unknown_") for k in calls)


def test_accumulate_delta_with_item_id_maps_back_to_call():
    calls: dict = {}
    itc: dict = {}
    _accumulate_streamed_function_calls(
        {
            "type": "response.output_item.added",
            "item": {"id": "itm_1", "type": "function_call", "call_id": "call_1", "name": "B"},
        },
        calls,
        itc,
    )
    _accumulate_streamed_function_calls(
        {"type": "response.function_call_arguments.delta", "item_id": "itm_1", "delta": "{}"},
        calls,
        itc,
    )
    assert calls["call_1"]["arguments"].endswith("{}")


def test_accumulate_delta_with_call_id_only():
    calls: dict = {}
    itc: dict = {}
    _accumulate_streamed_function_calls(
        {"type": "response.function_call_arguments.delta", "call_id": "call_2", "delta": "ab"},
        calls,
        itc,
    )
    _accumulate_streamed_function_calls(
        {"type": "response.function_call_arguments.delta", "call_id": "call_2", "delta": "cd"},
        calls,
        itc,
    )
    assert calls["call_2"]["arguments"] == "abcd"


def test_accumulate_delta_arguments_final_string():
    calls: dict = {}
    itc: dict = {}
    _accumulate_streamed_function_calls(
        {
            "type": "response.function_call_arguments.done",
            "call_id": "call_3",
            "arguments": '{"final":true}',
        },
        calls,
        itc,
    )
    assert calls["call_3"]["arguments"] == '{"final":true}'


def test_accumulate_delta_no_ids_creates_unknown():
    calls: dict = {}
    itc: dict = {}
    _accumulate_streamed_function_calls(
        {"type": "response.function_call_arguments.delta", "delta": "x"},
        calls,
        itc,
    )
    assert any(k.startswith("_unknown_") for k in calls)


def test_accumulate_non_function_call_event_noop():
    calls: dict = {}
    itc: dict = {}
    _accumulate_streamed_function_calls(
        {"type": "response.output_text.delta", "delta": "hi"},
        calls,
        itc,
    )
    assert calls == {}


def test_accumulate_item_non_dict_skipped():
    calls: dict = {}
    itc: dict = {}
    _accumulate_streamed_function_calls(
        {"type": "response.output_item.added", "item": "not-a-dict"},
        calls,
        itc,
    )
    assert calls == {}


# ---------------------------------------------------------------------------
# _merge_streamed_calls_into_response
# ---------------------------------------------------------------------------


def test_merge_into_none_response():
    merged = _merge_streamed_calls_into_response(
        None, {"k": {"call_id": "c1", "name": "N", "arguments": "{}"}}
    )
    assert merged["status"] == "completed"
    assert merged["output"][0]["type"] == "function_call"
    assert merged["output"][0]["call_id"] == "c1"


def test_merge_skips_existing_call_ids():
    existing = {
        "output": [
            {"type": "function_call", "call_id": "c1", "name": "N", "arguments": "{}"},
        ],
        "status": "completed",
    }
    merged = _merge_streamed_calls_into_response(
        existing,
        {
            "c1": {"call_id": "c1", "name": "N", "arguments": "{}"},
            "c2": {"call_id": "c2", "name": "M", "arguments": "{}"},
        },
    )
    call_ids = [i.get("call_id") for i in merged["output"]]
    assert call_ids.count("c1") == 1
    assert "c2" in call_ids


def test_merge_empty_call_id_appended():
    merged = _merge_streamed_calls_into_response(
        {"output": []}, {"k": {"call_id": "", "name": "N", "arguments": "{}"}}
    )
    assert merged["output"][0]["call_id"] == ""


# ---------------------------------------------------------------------------
# _fetch_response_by_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_response_by_id_200(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None):
            class _R:
                status_code = 200

                @staticmethod
                def json():
                    return {"id": "r1"}

            return _R()

    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", _FakeClient)
    body = await _fetch_response_by_id("r1", {})
    assert body == {"id": "r1"}


@pytest.mark.asyncio
async def test_fetch_response_by_id_4xx(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None):
            class _R:
                status_code = 500

                @staticmethod
                def json():
                    return {}

            return _R()

    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", _FakeClient)
    body = await _fetch_response_by_id("r1", {})
    assert body is None


@pytest.mark.asyncio
async def test_fetch_response_by_id_exception(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", _FakeClient)
    body = await _fetch_response_by_id("r1", {})
    assert body is None


@pytest.mark.asyncio
async def test_fetch_response_by_id_non_dict_body(monkeypatch):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None):
            class _R:
                status_code = 200

                @staticmethod
                def json():
                    return ["not", "a", "dict"]

            return _R()

    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", _FakeClient)
    body = await _fetch_response_by_id("r1", {})
    assert body is None


# ---------------------------------------------------------------------------
# OpenAIChatGPTProvider.stream — auth & error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_missing_oauth_yields_error(monkeypatch):
    _patch_oauth(monkeypatch, value=None)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    assert len(events) == 1
    assert events[0]["message"].metadata.get("is_error") is True
    assert "auth required" in events[0]["message"].text


@pytest.mark.asyncio
async def test_stream_missing_access_token(monkeypatch):
    _patch_oauth(monkeypatch, value={"access_token": "", "account_id": "acct"})
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    assert events[0]["message"].metadata.get("is_error") is True
    assert "invalid" in events[0]["message"].text


@pytest.mark.asyncio
async def test_stream_missing_account_id(monkeypatch):
    _patch_oauth(monkeypatch, value={"access_token": "tok", "account_id": ""})
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    assert events[0]["message"].metadata.get("is_error") is True


@pytest.mark.asyncio
async def test_stream_http_4xx_yields_error(monkeypatch):
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[], status_code=401, read_body=b"Unauthorized"
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)

    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    assert events[0]["message"].metadata.get("is_error") is True
    assert "401" in events[0]["message"].text


@pytest.mark.asyncio
async def test_stream_response_error_event(monkeypatch):
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[
            'data: {"type":"response.error","error":{"message":"bad stuff"}}',
            "data: [DONE]",
        ]
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    assert events[-1]["message"].metadata.get("is_error") is True
    assert "bad stuff" in events[-1]["message"].text


@pytest.mark.asyncio
async def test_stream_response_error_event_string_error(monkeypatch):
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[
            'data: {"type":"response.error","error":"just-a-string"}',
            "data: [DONE]",
        ]
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    assert "just-a-string" in events[-1]["message"].text


@pytest.mark.asyncio
async def test_stream_plain_error_event(monkeypatch):
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[
            'data: {"type":"error","error":{"message":"oops"}}',
            "data: [DONE]",
        ]
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    assert events[-1]["message"].metadata.get("is_error") is True
    assert "oops" in events[-1]["message"].text


@pytest.mark.asyncio
async def test_stream_plain_error_event_string(monkeypatch):
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[
            'data: {"type":"error","error":"raw-str"}',
            "data: [DONE]",
        ]
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    assert "raw-str" in events[-1]["message"].text


@pytest.mark.asyncio
async def test_stream_output_text_done_yields_once(monkeypatch):
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[
            'data: {"type":"response.output_text.done","text":"done-text"}',
            'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"done-text"}]}]}}',
            "data: [DONE]",
        ]
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    deltas = [e for e in events if e.get("type") == "text_delta"]
    assert any(d.get("text") == "done-text" for d in deltas)


@pytest.mark.asyncio
async def test_stream_output_text_done_deduplicates(monkeypatch):
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[
            'data: {"type":"response.output_text.delta","delta":"hi"}',
            'data: {"type":"response.output_text.done","text":"hi"}',
            'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"hi"}]}]}}',
            "data: [DONE]",
        ]
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    deltas = [e for e in events if e.get("type") == "text_delta"]
    assert len(deltas) == 1


@pytest.mark.asyncio
async def test_stream_generic_delta_and_done_events(monkeypatch):
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[
            'data: {"type":"response.reasoning_summary_part.delta","delta":{"text":"reasoning-part"}}',
            'data: {"type":"response.function_call_arguments.delta","delta":"{\\"x\\":1}","item_id":"itm1"}',
            'data: {"type":"response.reasoning.done","text":"done-once"}',
            'data: {"type":"response.function_call_arguments.done","arguments":"{}"}',
            'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"final"}]}]}}',
            "data: [DONE]",
        ]
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    deltas = [e.get("text") for e in events if e.get("type") == "text_delta"]
    assert "reasoning-part" in deltas
    assert "done-once" in deltas


def _make_capture_client(captured: dict, lines: list[str]):
    class _FakeResp:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def aread(self):
            return b""

        async def aiter_lines(self):
            for ln in lines:
                yield ln

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def stream(self, method, url, headers=None, json=None):
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResp()

    return _FakeClient


_OK_LINES = [
    'data: {"type":"response.output_text.delta","delta":"hi"}',
    'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"hi"}]}]}}',
    "data: [DONE]",
]


@pytest.mark.asyncio
async def test_stream_tool_choice_any_maps_required(monkeypatch):
    captured: dict = {}
    _patch_oauth(monkeypatch)
    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.httpx.AsyncClient",
        _make_capture_client(captured, _OK_LINES),
    )
    provider = OpenAIChatGPTProvider()
    async for _ in provider.stream(
        messages=[Message(role="user", content="hi")],
        tools=[_FakeTool(name="T")],
        tool_choice="any",
        max_tokens=123,
    ):
        pass
    assert captured["json"]["tool_choice"] == "required"
    assert captured["json"]["max_output_tokens"] == 123
    assert captured["json"]["tools"][0]["name"] == "T"


@pytest.mark.asyncio
async def test_stream_tool_choice_auto_passthrough(monkeypatch):
    captured: dict = {}
    _patch_oauth(monkeypatch)
    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.httpx.AsyncClient",
        _make_capture_client(captured, _OK_LINES),
    )
    provider = OpenAIChatGPTProvider()
    async for _ in provider.stream(
        messages=[Message(role="user", content="hi")],
        tools=[_FakeTool(name="T")],
        tool_choice="auto",
    ):
        pass
    assert captured["json"]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_stream_tool_choice_none_passthrough(monkeypatch):
    captured: dict = {}
    _patch_oauth(monkeypatch)
    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.httpx.AsyncClient",
        _make_capture_client(captured, _OK_LINES),
    )
    provider = OpenAIChatGPTProvider()
    async for _ in provider.stream(
        messages=[Message(role="user", content="hi")],
        tools=[_FakeTool(name="T")],
        tool_choice="none",
    ):
        pass
    assert captured["json"]["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_stream_tool_choice_named_string(monkeypatch):
    captured: dict = {}
    _patch_oauth(monkeypatch)
    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.httpx.AsyncClient",
        _make_capture_client(captured, _OK_LINES),
    )
    provider = OpenAIChatGPTProvider()
    async for _ in provider.stream(
        messages=[Message(role="user", content="hi")],
        tools=[_FakeTool(name="T")],
        tool_choice="MyTool",
    ):
        pass
    assert captured["json"]["tool_choice"] == {"type": "function", "name": "MyTool"}


@pytest.mark.asyncio
async def test_stream_empty_content_yields_error(monkeypatch):
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[
            'data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":""}]}]}}',
            "data: [DONE]",
        ]
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    last = events[-1]
    assert last["message"].metadata.get("is_error") is True
    assert "ended without" in last["message"].text


@pytest.mark.asyncio
async def test_stream_exception_yields_error(monkeypatch):
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(raise_on_stream=RuntimeError("network fail"))
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    assert events[-1]["message"].metadata.get("is_error") is True
    assert "network fail" in events[-1]["message"].text


@pytest.mark.asyncio
async def test_stream_debug_mode_logs_to_stderr(monkeypatch, capsys):
    monkeypatch.setenv("DUH_OPENAI_CHATGPT_DEBUG", "1")
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[
            "",  # non-data line skipped
            "data: not-json",  # JSONDecodeError branch
            "data: ",  # empty raw
            "data: [DONE]",
            'data: {"type":"unknown.event","foo":"bar"}',  # debug_sse unhandled
            'data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"final"}]}]}}',
        ]
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    events = []
    async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
        events.append(ev)
    err = capsys.readouterr().err
    assert "openai-chatgpt" in err
    assert "unhandled=" in err


@pytest.mark.asyncio
async def test_stream_debug_mode_empty_content_logs(monkeypatch, capsys):
    monkeypatch.setenv("DUH_OPENAI_CHATGPT_DEBUG", "1")
    _patch_oauth(monkeypatch)
    client_cls = _make_fake_client(
        lines=[
            'data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":""}]}]}}',
            "data: [DONE]",
        ]
    )
    monkeypatch.setattr("duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls)
    provider = OpenAIChatGPTProvider()
    async for _ in provider.stream(messages=[Message(role="user", content="hi")]):
        pass
    err = capsys.readouterr().err
    assert "empty content" in err


@pytest.mark.asyncio
async def test_stream_default_instructions_when_empty(monkeypatch):
    captured: dict = {}
    _patch_oauth(monkeypatch)
    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.httpx.AsyncClient",
        _make_capture_client(
            captured,
            [
                'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}]}}',
                "data: [DONE]",
            ],
        ),
    )
    provider = OpenAIChatGPTProvider(model="")
    async for _ in provider.stream(
        messages=[Message(role="user", content="hi")], system_prompt=""
    ):
        pass
    assert captured["json"]["instructions"] == "You are a coding assistant."
    assert captured["json"]["model"] == "gpt-5.2-codex"
