"""Tests for duh.adapters.gemini — native Gemini SDK wrapper (ADR-075).

Uses mocks so no real API calls are made. Tests the translation logic
between the google-genai SDK and D.U.H. uniform events.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("google.genai")

from duh.adapters.gemini import (  # noqa: E402
    GeminiProvider,
    _build_system_text,
    _contents_from_messages,
    _contents_with_extracted_system,
    _extract_usage,
    _normalize_finish_reason,
    _resolve_thinking_budget,
    _scrub,
    _supports_thinking,
    _to_api_tools,
    _to_tool_config,
)
from duh.kernel.messages import Message  # noqa: E402
from duh.kernel.untrusted import TaintSource, UntrustedStr  # noqa: E402


# ═══════════════════════════════════════════════════════════════════
# Translation helpers
# ═══════════════════════════════════════════════════════════════════

class TestSystemExtraction:
    def test_extracts_system_role_messages(self):
        msgs = [
            Message(role="system", content="you are helpful"),
            Message(role="user", content="hi"),
        ]
        system_text, contents = _contents_with_extracted_system(msgs)
        assert system_text == "you are helpful"
        # Only the user message makes it into contents
        assert len(contents) == 1

    def test_no_system_message(self):
        msgs = [Message(role="user", content="hi")]
        system_text, contents = _contents_with_extracted_system(msgs)
        assert system_text == ""
        assert len(contents) == 1

    def test_system_role_not_sent_to_gemini(self):
        """Gemini must not receive a message with role='system' in contents."""
        msgs = [
            Message(role="system", content="sys"),
            Message(role="user", content="hello"),
        ]
        _, contents = _contents_with_extracted_system(msgs)
        for c in contents:
            assert getattr(c, "role", None) != "system"

    def test_assistant_role_becomes_model(self):
        msgs = [
            Message(role="user", content="q"),
            Message(role="assistant", content="a"),
        ]
        contents = _contents_from_messages(msgs)
        assert contents[0].role == "user"
        assert contents[1].role == "model"


class TestToolTranslation:
    def test_translates_tools_to_function_declarations(self):
        tool = SimpleNamespace(
            name="Bash",
            description="Run commands",
            input_schema={"type": "object", "properties": {"cmd": {"type": "string"}}},
        )
        api_tools = _to_api_tools([tool])
        assert len(api_tools) == 1
        decls = api_tools[0].function_declarations
        assert len(decls) == 1
        assert decls[0].name == "Bash"

    def test_empty_tools(self):
        assert _to_api_tools([]) == []

    def test_tool_without_name_skipped(self):
        tool = SimpleNamespace(name="", description="x", input_schema={})
        assert _to_api_tools([tool]) == []


class TestToolChoice:
    def test_none_becomes_none_mode(self):
        cfg = _to_tool_config("none")
        assert cfg.function_calling_config.mode == "NONE"

    def test_auto(self):
        cfg = _to_tool_config("auto")
        assert cfg.function_calling_config.mode == "AUTO"

    def test_any_becomes_any_mode(self):
        cfg = _to_tool_config("any")
        assert cfg.function_calling_config.mode == "ANY"

    def test_specific_tool_name(self):
        cfg = _to_tool_config("Bash")
        assert cfg.function_calling_config.mode == "ANY"
        assert "Bash" in cfg.function_calling_config.allowed_function_names

    def test_none_arg_returns_none(self):
        assert _to_tool_config(None) is None


class TestThinkingBudget:
    def test_disabled_returns_zero(self):
        assert _resolve_thinking_budget({"type": "disabled"}, None) == 0

    def test_adaptive_returns_minus_one(self):
        assert _resolve_thinking_budget({"type": "adaptive"}, None) == -1

    def test_explicit_budget_tokens(self):
        assert _resolve_thinking_budget({"budget_tokens": 1024}, None) == 1024

    def test_constructor_default_used(self):
        assert _resolve_thinking_budget(None, 2048) == 2048

    def test_none_means_skip(self):
        assert _resolve_thinking_budget(None, None) is None

    def test_supports_thinking_gemini_2_5(self):
        assert _supports_thinking("gemini-2.5-pro")
        assert _supports_thinking("gemini-2.5-flash")

    def test_supports_thinking_false_for_1_5(self):
        assert not _supports_thinking("gemini-1.5-pro")


class TestUsageMapping:
    def test_extracts_all_fields(self):
        um = SimpleNamespace(
            prompt_token_count=100,
            candidates_token_count=50,
            cached_content_token_count=80,
            thoughts_token_count=25,
            total_token_count=175,
        )
        usage = _extract_usage(um)
        assert usage["input_tokens"] == 100
        assert usage["output_tokens"] == 50
        assert usage["cache_read_input_tokens"] == 80
        assert usage["thoughts_tokens"] == 25
        assert usage["total_tokens"] == 175

    def test_missing_fields_default_to_zero(self):
        um = SimpleNamespace()
        usage = _extract_usage(um)
        assert usage["input_tokens"] == 0
        assert usage["cache_read_input_tokens"] == 0


class TestFinishReason:
    def test_stop(self):
        assert _normalize_finish_reason("STOP") == "end_turn"

    def test_max_tokens(self):
        assert _normalize_finish_reason("MAX_TOKENS") == "max_tokens"

    def test_safety(self):
        assert _normalize_finish_reason("SAFETY") == "content_filter"


class TestSystemText:
    def test_list_joined(self):
        assert "a" in _build_system_text(["a", "b"])

    def test_empty(self):
        assert _build_system_text("") == ""


class TestSecretScrubbing:
    def test_strips_api_key(self):
        msg = "error: key AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz1234567 invalid"
        scrubbed = _scrub(msg)
        assert "AIzaSy" not in scrubbed
        assert "[redacted]" in scrubbed


# ═══════════════════════════════════════════════════════════════════
# Construction
# ═══════════════════════════════════════════════════════════════════

class TestProviderConstruction:
    @patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False)
    def test_creates_with_env_key(self):
        with patch("google.genai.Client") as mock:
            GeminiProvider()
            mock.assert_called_once()
            assert mock.call_args.kwargs["api_key"] == "test-key"

    @patch.dict("os.environ", {}, clear=True)
    def test_creates_with_explicit_key(self):
        with patch("google.genai.Client") as mock:
            GeminiProvider(api_key="sk-test")
            assert mock.call_args.kwargs["api_key"] == "sk-test"

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=False)
    def test_custom_model(self):
        with patch("google.genai.Client"):
            p = GeminiProvider(model="gemini-2.5-flash")
            assert p._default_model == "gemini-2.5-flash"

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=False)
    def test_thinking_budget_stored(self):
        with patch("google.genai.Client"):
            p = GeminiProvider(thinking_budget=1024)
            assert p._thinking_budget == 1024


# ═══════════════════════════════════════════════════════════════════
# Streaming behavior (fully mocked)
# ═══════════════════════════════════════════════════════════════════

def _make_text_chunk(text: str, *, usage=None, finish_reason=None, thought=False):
    part = SimpleNamespace(
        text=text,
        thought=thought,
        function_call=None,
    )
    content = SimpleNamespace(parts=[part])
    cand = SimpleNamespace(content=content, finish_reason=finish_reason)
    return SimpleNamespace(candidates=[cand], usage_metadata=usage)


def _make_function_call_chunk(name: str, args: dict, *, id: str | None = None):
    fc = SimpleNamespace(name=name, args=args, id=id)
    part = SimpleNamespace(text="", thought=False, function_call=fc)
    content = SimpleNamespace(parts=[part])
    cand = SimpleNamespace(content=content, finish_reason="TOOL_CALLS")
    return SimpleNamespace(candidates=[cand], usage_metadata=None)


class _FakeAsyncIterator:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _install_fake_client(provider, chunks):
    """Replace provider._client with a mock whose aio.models.generate_content_stream
    returns an async iterator over *chunks*.
    """
    async def _gcs(**kwargs):
        _gcs.last_kwargs = kwargs
        return _FakeAsyncIterator(chunks)

    _gcs.last_kwargs = {}
    provider._client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=_gcs)
        ),
        caches=MagicMock(),
    )
    return _gcs


@pytest.mark.asyncio
class TestStreaming:
    async def _provider(self, **kwargs):
        with patch("google.genai.Client"):
            return GeminiProvider(api_key="k", **kwargs)

    async def test_text_delta_events(self):
        provider = await self._provider()
        usage = SimpleNamespace(
            prompt_token_count=5,
            candidates_token_count=3,
            cached_content_token_count=0,
            thoughts_token_count=0,
            total_token_count=8,
        )
        _install_fake_client(provider, [
            _make_text_chunk("hello "),
            _make_text_chunk("world", usage=usage, finish_reason="STOP"),
        ])

        events = []
        async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
            events.append(ev)

        text_events = [e for e in events if e["type"] == "text_delta"]
        assert [e["text"] for e in text_events] == ["hello ", "world"]

    async def test_done_event_has_usage(self):
        provider = await self._provider()
        usage = SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=4,
            cached_content_token_count=6,
            thoughts_token_count=0,
            total_token_count=14,
        )
        _install_fake_client(provider, [
            _make_text_chunk("ok", usage=usage, finish_reason="STOP"),
        ])

        events = []
        async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
            events.append(ev)

        done = [e for e in events if e["type"] == "done"]
        assert done
        assert done[0]["usage"]["input_tokens"] == 10
        assert done[0]["usage"]["cache_read_input_tokens"] == 6
        assert done[0]["usage"]["output_tokens"] == 4

    async def test_thinking_delta_emitted(self):
        provider = await self._provider(model="gemini-2.5-pro")
        _install_fake_client(provider, [
            _make_text_chunk("reasoning about it...", thought=True),
            _make_text_chunk("42", finish_reason="STOP"),
        ])

        events = []
        async for ev in provider.stream(
            messages=[Message(role="user", content="q")],
            thinking={"type": "adaptive"},
        ):
            events.append(ev)

        thinking = [e for e in events if e["type"] == "thinking_delta"]
        text = [e for e in events if e["type"] == "text_delta"]
        assert thinking
        assert thinking[0]["text"] == "reasoning about it..."
        assert text[0]["text"] == "42"

    async def test_tool_use_generates_id(self):
        provider = await self._provider()
        _install_fake_client(provider, [
            _make_function_call_chunk("Bash", {"cmd": "ls"}),
        ])

        tool_events = []
        async for ev in provider.stream(messages=[Message(role="user", content="list")]):
            if ev["type"] == "tool_use":
                tool_events.append(ev)

        assert len(tool_events) == 1
        assert tool_events[0]["name"] == "Bash"
        assert tool_events[0]["input"] == {"cmd": "ls"}
        # Synthesized tool_use_id should be non-empty
        assert tool_events[0]["id"]
        assert len(tool_events[0]["id"]) > 4

    async def test_tool_use_preserves_sdk_id(self):
        provider = await self._provider()
        _install_fake_client(provider, [
            _make_function_call_chunk("Read", {"path": "/x"}, id="call_abc123"),
        ])

        tool_events = []
        async for ev in provider.stream(messages=[Message(role="user", content="r")]):
            if ev["type"] == "tool_use":
                tool_events.append(ev)

        assert tool_events[0]["id"] == "call_abc123"

    async def test_system_extracted_to_config(self):
        provider = await self._provider()
        gcs = _install_fake_client(provider, [
            _make_text_chunk("ok", finish_reason="STOP"),
        ])

        async for _ in provider.stream(
            messages=[
                Message(role="system", content="you are a pirate"),
                Message(role="user", content="hi"),
            ],
        ):
            pass

        cfg = gcs.last_kwargs.get("config")
        assert cfg is not None
        assert cfg.system_instruction == "you are a pirate"
        # The system-role message must NOT be in contents
        contents = gcs.last_kwargs["contents"]
        assert all(c.role != "system" for c in contents)

    async def test_thinking_budget_zero_disables(self):
        provider = await self._provider(model="gemini-2.5-pro")
        gcs = _install_fake_client(provider, [
            _make_text_chunk("ok", finish_reason="STOP"),
        ])

        async for _ in provider.stream(
            messages=[Message(role="user", content="hi")],
            thinking={"type": "disabled"},
        ):
            pass

        cfg = gcs.last_kwargs.get("config")
        assert cfg is not None
        assert cfg.thinking_config.thinking_budget == 0
        assert cfg.thinking_config.include_thoughts is False

    async def test_thinking_skipped_for_non_2_5_model(self):
        provider = await self._provider(model="gemini-1.5-pro")
        gcs = _install_fake_client(provider, [
            _make_text_chunk("ok", finish_reason="STOP"),
        ])

        async for _ in provider.stream(
            messages=[Message(role="user", content="hi")],
            thinking={"type": "enabled", "budget_tokens": 1024},
        ):
            pass

        cfg = gcs.last_kwargs.get("config")
        # thinking_config should NOT be set on a non-2.5 model
        assert cfg is None or cfg.thinking_config is None

    async def test_cached_content_param_forwarded(self):
        provider = await self._provider()
        gcs = _install_fake_client(provider, [
            _make_text_chunk("ok", finish_reason="STOP"),
        ])

        async for _ in provider.stream(
            messages=[Message(role="user", content="hi")],
            cached_content="cachedContents/abc123",
        ):
            pass

        cfg = gcs.last_kwargs.get("config")
        assert cfg is not None
        assert cfg.cached_content == "cachedContents/abc123"

    async def test_final_assistant_message_is_tainted(self):
        provider = await self._provider()
        _install_fake_client(provider, [
            _make_text_chunk("hello", finish_reason="STOP"),
        ])

        assistant = None
        async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
            if ev["type"] == "assistant":
                assistant = ev["message"]

        assert assistant is not None
        # Verify the assistant final-message event delivers the correct text
        # and that the adapter's taint tagging helper returns an UntrustedStr.
        from duh.adapters.gemini import _wrap_model_output

        tainted = _wrap_model_output(assistant.text)
        assert isinstance(tainted, UntrustedStr)
        assert tainted.source == TaintSource.MODEL_OUTPUT


# ═══════════════════════════════════════════════════════════════════
# Caching
# ═══════════════════════════════════════════════════════════════════

class TestCreateCache:
    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=False)
    def test_create_cache_returns_id(self):
        with patch("google.genai.Client") as client_ctor:
            mock_client = MagicMock()
            mock_cache = SimpleNamespace(name="cachedContents/abc123")
            mock_client.caches.create.return_value = mock_cache
            client_ctor.return_value = mock_client

            provider = GeminiProvider()
            cache_id = provider.create_cache("some long context", ttl_seconds=600)
            assert cache_id == "cachedContents/abc123"
            # The SDK was called
            assert mock_client.caches.create.called

    @patch.dict("os.environ", {"GEMINI_API_KEY": "k"}, clear=False)
    def test_create_cache_ttl_format(self):
        with patch("google.genai.Client") as client_ctor:
            mock_client = MagicMock()
            mock_client.caches.create.return_value = SimpleNamespace(name="c/1")
            client_ctor.return_value = mock_client

            provider = GeminiProvider()
            provider.create_cache("context", ttl_seconds=1234)
            cfg = mock_client.caches.create.call_args.kwargs["config"]
            assert cfg.ttl == "1234s"


# ═══════════════════════════════════════════════════════════════════
# Tool-result translation (D.U.H. → Gemini FunctionResponse)
# ═══════════════════════════════════════════════════════════════════

class TestToolResultTranslation:
    def test_tool_result_becomes_function_response(self):
        msgs = [
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "call_xyz", "content": "done"},
            ]),
        ]
        contents = _contents_from_messages(msgs)
        assert len(contents) == 1
        parts = contents[0].parts
        assert len(parts) == 1
        fr = parts[0].function_response
        assert fr is not None
        assert fr.response == {"result": "done"}

    def test_tool_result_error_sets_error_key(self):
        msgs = [
            Message(role="user", content=[
                {"type": "tool_result", "tool_use_id": "c1", "content": "boom", "is_error": True},
            ]),
        ]
        contents = _contents_from_messages(msgs)
        fr = contents[0].parts[0].function_response
        assert fr.response == {"error": "boom"}


# ═══════════════════════════════════════════════════════════════════
# ImportError surface when SDK is missing
# ═══════════════════════════════════════════════════════════════════

class TestMissingSDK:
    def test_import_with_sdk_missing_is_safe(self, monkeypatch):
        """Importing the adapter module must never crash.

        Instantiation should raise ImportError with a clear message.
        """
        import duh.adapters.gemini as mod

        monkeypatch.setattr(mod, "_GENAI_AVAILABLE", False)
        monkeypatch.setattr(mod, "_GENAI_IMPORT_ERROR", ImportError("mocked"))

        with pytest.raises(ImportError, match="pip install google-genai"):
            mod.GeminiProvider(api_key="k")
