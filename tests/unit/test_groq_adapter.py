"""Unit tests for duh.adapters.groq — native Groq SDK adapter (ADR-075).

These tests mock the ``groq.AsyncGroq`` client; no real API calls are made.
They exercise the translation layer between Groq's OpenAI-Chat-Completions
streaming shape and D.U.H.'s uniform event schema, plus the extras the
adapter adds on top (rate-limit header capture, ``groq/`` namespace stripping,
taint wrapping, backoff on 429, API-key redaction in errors).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip the module entirely if the optional ``groq`` SDK isn't installed —
# ADR-075 treats it as a ship-by-default but recoverable dependency.
pytest.importorskip("groq")

from duh.adapters.groq import (  # noqa: E402
    GroqProvider,
    _build_content_blocks,
    _extract_rate_limit_headers,
    _redact_api_key,
    _strip_namespace,
    _to_groq_messages,
    _to_groq_tools,
)
from duh.kernel.messages import Message  # noqa: E402
from duh.kernel.untrusted import TaintSource, UntrustedStr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers for fake streaming chunks
# ---------------------------------------------------------------------------

def _make_text_chunk(text: str, finish_reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=text, tool_calls=None),
            finish_reason=finish_reason,
        )],
        usage=None,
    )


def _make_tool_chunks(
    *,
    tc_id: str,
    name: str,
    arg_pieces: list[str],
) -> list[SimpleNamespace]:
    """Simulate Groq tool-call streaming: name first, arguments in pieces."""
    chunks: list[SimpleNamespace] = []
    # First chunk: the id + function name.
    chunks.append(SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(
                content=None,
                tool_calls=[SimpleNamespace(
                    index=0,
                    id=tc_id,
                    function=SimpleNamespace(name=name, arguments=""),
                )],
            ),
            finish_reason=None,
        )],
        usage=None,
    ))
    # Subsequent chunks: incremental arguments JSON.
    for piece in arg_pieces:
        chunks.append(SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[SimpleNamespace(
                        index=0,
                        id=None,
                        function=SimpleNamespace(name=None, arguments=piece),
                    )],
                ),
                finish_reason=None,
            )],
            usage=None,
        ))
    return chunks


def _make_final_chunk(
    *,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=None, tool_calls=None),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


class _FakeAsyncIter:
    """Minimal async iterator for a list of chunks."""

    def __init__(self, chunks: list[SimpleNamespace]):
        self._chunks = list(chunks)
        self._i = 0

    def __aiter__(self) -> "_FakeAsyncIter":
        return self

    async def __anext__(self) -> SimpleNamespace:
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._i]
        self._i += 1
        return c


def _make_raw_response(
    chunks: list[SimpleNamespace],
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a mock ``with_raw_response.create(...)`` return value."""
    raw = MagicMock()
    raw.headers = headers or {
        "x-ratelimit-limit-tokens": "6000",
        "x-ratelimit-remaining-tokens": "5997",
        "x-ratelimit-remaining-requests": "14400",
        "x-groq-region": "us-east-1",
    }
    raw.parse = AsyncMock(return_value=_FakeAsyncIter(chunks))
    return raw


def _patch_client(provider: GroqProvider, chunks: list[SimpleNamespace], headers: dict[str, str] | None = None) -> MagicMock:
    """Install the fake streaming response on ``provider._client``.

    Returns the ``.create`` mock so tests can assert on the kwargs passed.
    """
    raw_create = AsyncMock(return_value=_make_raw_response(chunks, headers))
    provider._client.chat.completions.with_raw_response.create = raw_create
    return raw_create


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_strip_namespace_removes_groq_prefix(self):
        assert _strip_namespace("groq/llama-3.3-70b-versatile") == "llama-3.3-70b-versatile"

    def test_strip_namespace_leaves_bare_name(self):
        assert _strip_namespace("llama-3.3-70b-versatile") == "llama-3.3-70b-versatile"

    def test_redact_api_key_scrubs_gsk(self):
        s = _redact_api_key("Error: key=gsk_abcdef1234567890 rejected")
        assert "gsk_abcdef1234567890" not in s
        assert "***REDACTED***" in s

    def test_redact_api_key_scrubs_query_fragment(self):
        s = _redact_api_key("https://api.groq.com/x?api_key=secret-xyz&other=1")
        assert "secret-xyz" not in s
        assert "***REDACTED***" in s

    def test_extract_rate_limit_headers_picks_known_keys(self):
        raw = MagicMock()
        raw.headers = {
            "x-ratelimit-remaining-tokens": "5000",
            "x-groq-region": "us-east-1",
            "content-type": "application/json",
        }
        meta = _extract_rate_limit_headers(raw)
        assert meta["x-ratelimit-remaining-tokens"] == "5000"
        assert meta["x-groq-region"] == "us-east-1"
        # Non rate-limit headers are NOT surfaced.
        assert "content-type" not in meta

    def test_extract_rate_limit_headers_no_headers(self):
        raw = MagicMock(spec=[])  # no headers attr
        assert _extract_rate_limit_headers(raw) == {}


# ---------------------------------------------------------------------------
# Message / tool conversion
# ---------------------------------------------------------------------------

class TestToGroqMessages:
    def test_system_prompt_prepended(self):
        msgs = _to_groq_messages([], "you are helpful")
        assert msgs[0] == {"role": "system", "content": "you are helpful"}

    def test_system_prompt_list_joined(self):
        msgs = _to_groq_messages([], ["line 1", "line 2"])
        assert msgs[0]["role"] == "system"
        assert "line 1" in msgs[0]["content"]
        assert "line 2" in msgs[0]["content"]

    def test_empty_system_prompt_skipped(self):
        msgs = _to_groq_messages([Message(role="user", content="hi")], "")
        assert msgs[0]["role"] == "user"

    def test_user_message_string(self):
        out = _to_groq_messages([Message(role="user", content="ping")], "")
        assert out == [{"role": "user", "content": "ping"}]

    def test_assistant_tool_use_block(self):
        asst = Message(role="assistant", content=[
            {"type": "text", "text": "let me look"},
            {"type": "tool_use", "id": "tc_1", "name": "Read", "input": {"p": "/x"}},
        ])
        out = _to_groq_messages([asst], "")
        assert out[0]["role"] == "assistant"
        assert out[0]["content"] == "let me look"
        assert out[0]["tool_calls"][0]["function"]["name"] == "Read"

    def test_tool_result_becomes_tool_role(self):
        user = Message(role="user", content=[
            {"type": "tool_result", "tool_use_id": "tc_1", "content": "data"},
        ])
        out = _to_groq_messages([user], "")
        assert out[0] == {
            "role": "tool",
            "tool_call_id": "tc_1",
            "content": "data",
        }


class TestToGroqTools:
    def test_converts_duck_typed_tool(self):
        class FakeTool:
            name = "Read"
            description = "Read a file"
            input_schema = {"type": "object"}

        tools = _to_groq_tools([FakeTool()])
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "Read"
        assert tools[0]["function"]["parameters"] == {"type": "object"}

    def test_passes_through_openai_shape(self):
        existing = {"type": "function", "function": {"name": "X", "parameters": {}}}
        tools = _to_groq_tools([existing])
        assert tools == [existing]

    def test_skips_unnamed(self):
        class NoName:
            name = ""
            description = "x"
            input_schema = {}

        assert _to_groq_tools([NoName()]) == []


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------

class TestGroqProviderInit:
    def test_reads_api_key_env(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_fromenv")
        p = GroqProvider()
        assert p._default_model == "llama-3.3-70b-versatile"
        assert p._client is not None

    def test_explicit_api_key_wins(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        p = GroqProvider(api_key="gsk_explicit", model="llama-3.1-8b-instant")
        assert p._default_model == "llama-3.1-8b-instant"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

class TestStreaming:
    def _make_provider(self, monkeypatch) -> GroqProvider:
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        return GroqProvider(model="groq/llama-3.3-70b-versatile")

    @pytest.mark.asyncio
    async def test_stream_yields_text_delta_events(self, monkeypatch):
        provider = self._make_provider(monkeypatch)
        chunks = [
            _make_text_chunk("hello "),
            _make_text_chunk("world"),
            _make_final_chunk(prompt_tokens=5, completion_tokens=2),
        ]
        _patch_client(provider, chunks)

        events = [e async for e in provider.stream(messages=[Message(role="user", content="hi")])]
        text_deltas = [e for e in events if e["type"] == "text_delta"]
        assert len(text_deltas) == 2
        joined = "".join(str(e["text"]) for e in text_deltas)
        assert joined == "hello world"

    @pytest.mark.asyncio
    async def test_stream_text_delta_is_tainted_model_output(self, monkeypatch):
        provider = self._make_provider(monkeypatch)
        _patch_client(provider, [_make_text_chunk("boo"), _make_final_chunk()])

        events = [e async for e in provider.stream(messages=[Message(role="user", content="x")])]
        td = [e for e in events if e["type"] == "text_delta"][0]
        assert isinstance(td["text"], UntrustedStr)
        assert td["text"].source == TaintSource.MODEL_OUTPUT

    @pytest.mark.asyncio
    async def test_stream_buffers_tool_call_to_single_event(self, monkeypatch):
        provider = self._make_provider(monkeypatch)
        chunks: list[SimpleNamespace] = []
        chunks.extend(_make_tool_chunks(
            tc_id="call_abc",
            name="Read",
            arg_pieces=['{"path":', '"/tmp/', 'x.txt"}'],
        ))
        chunks.append(_make_final_chunk(finish_reason="tool_calls"))
        _patch_client(provider, chunks)

        events = [e async for e in provider.stream(messages=[Message(role="user", content="read x")])]
        tool_events = [e for e in events if e["type"] == "tool_use"]
        assert len(tool_events) == 1
        tu = tool_events[0]
        assert tu["id"] == "call_abc"
        assert tu["name"] == "Read"
        assert tu["input"] == {"path": "/tmp/x.txt"}

    @pytest.mark.asyncio
    async def test_stream_strips_groq_prefix_before_api_call(self, monkeypatch):
        provider = self._make_provider(monkeypatch)
        create = _patch_client(provider, [_make_text_chunk("k"), _make_final_chunk()])

        async for _ in provider.stream(
            messages=[Message(role="user", content="hi")],
            model="groq/llama-3.3-70b-versatile",
        ):
            pass

        kwargs = create.await_args.kwargs
        # API sees the bare name.
        assert kwargs["model"] == "llama-3.3-70b-versatile"

    @pytest.mark.asyncio
    async def test_stream_passes_system_prompt_as_first_message(self, monkeypatch):
        provider = self._make_provider(monkeypatch)
        create = _patch_client(provider, [_make_text_chunk("k"), _make_final_chunk()])

        async for _ in provider.stream(
            messages=[Message(role="user", content="hi")],
            system_prompt="act like a pirate",
        ):
            pass

        api_msgs = create.await_args.kwargs["messages"]
        assert api_msgs[0] == {"role": "system", "content": "act like a pirate"}
        assert api_msgs[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_done_event_includes_usage(self, monkeypatch):
        provider = self._make_provider(monkeypatch)
        _patch_client(provider, [
            _make_text_chunk("hi"),
            _make_final_chunk(prompt_tokens=42, completion_tokens=7),
        ])

        events = [e async for e in provider.stream(messages=[Message(role="user", content="q")])]
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        assert done[0]["usage"]["input_tokens"] == 42
        assert done[0]["usage"]["output_tokens"] == 7

    @pytest.mark.asyncio
    async def test_done_event_surfaces_rate_limit_metadata(self, monkeypatch):
        provider = self._make_provider(monkeypatch)
        headers = {
            "x-ratelimit-remaining-tokens": "4200",
            "x-ratelimit-remaining-requests": "14000",
            "x-groq-region": "us-west-2",
        }
        _patch_client(provider, [_make_text_chunk("k"), _make_final_chunk()], headers=headers)

        events = [e async for e in provider.stream(messages=[Message(role="user", content="q")])]
        done = next(e for e in events if e["type"] == "done")
        rl = done["rate_limit"]
        assert rl["x-ratelimit-remaining-tokens"] == "4200"
        assert rl["x-ratelimit-remaining-requests"] == "14000"
        assert rl["x-groq-region"] == "us-west-2"

    @pytest.mark.asyncio
    async def test_assistant_message_carries_model_and_rate_limit(self, monkeypatch):
        provider = self._make_provider(monkeypatch)
        _patch_client(
            provider,
            [_make_text_chunk("ok"), _make_final_chunk()],
            headers={"x-groq-region": "us-east-1"},
        )

        events = [
            e async for e in provider.stream(
                messages=[Message(role="user", content="q")],
                model="groq/llama-3.1-8b-instant",
            )
        ]
        asst = next(e for e in events if e["type"] == "assistant")
        # Display name keeps the namespaced form (for session tracking).
        assert asst["message"].metadata["model"] == "groq/llama-3.1-8b-instant"
        assert asst["message"].metadata["rate_limit"]["x-groq-region"] == "us-east-1"

    @pytest.mark.asyncio
    async def test_backoff_retries_on_429_rate_limit(self, monkeypatch):
        provider = self._make_provider(monkeypatch)
        call_count = {"n": 0}

        class _RateLimitError(Exception):
            def __init__(self) -> None:
                super().__init__("rate_limit_exceeded: too many requests")
                self.status_code = 429

        async def _flaky_create(**_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise _RateLimitError()
            return _make_raw_response([
                _make_text_chunk("recovered"),
                _make_final_chunk(),
            ])

        provider._client.chat.completions.with_raw_response.create = _flaky_create
        # Keep the retry sleep short for the test.
        monkeypatch.setattr("duh.kernel.backoff._compute_delay", lambda *a, **kw: 0.0)

        events = [e async for e in provider.stream(messages=[Message(role="user", content="q")])]
        assert call_count["n"] >= 2
        # We got a text_delta from the second (successful) attempt.
        assert any(e["type"] == "text_delta" and str(e["text"]) == "recovered" for e in events)

    @pytest.mark.asyncio
    async def test_non_retryable_error_redacts_api_key_in_message(self, monkeypatch):
        provider = self._make_provider(monkeypatch)

        class _AuthError(Exception):
            status_code = 401

        async def _boom(**_kwargs):
            # Include a fake key in the error message to verify redaction.
            raise _AuthError("invalid api key: gsk_leakedthingydata123456 rejected")

        provider._client.chat.completions.with_raw_response.create = _boom

        events = [e async for e in provider.stream(messages=[Message(role="user", content="q")])]
        asst = next(e for e in events if e["type"] == "assistant")
        text = asst["message"].content[0]["text"]
        assert "gsk_leakedthingydata123456" not in text
        assert "REDACTED" in text


# ---------------------------------------------------------------------------
# Content block assembly
# ---------------------------------------------------------------------------

class TestBuildContentBlocks:
    def test_text_block_is_tainted(self):
        blocks = _build_content_blocks(["hi"], {})
        assert blocks[0]["type"] == "text"
        assert isinstance(blocks[0]["text"], UntrustedStr)
        assert blocks[0]["text"].source == TaintSource.MODEL_OUTPUT

    def test_tool_use_block_includes_parsed_input(self):
        tool_calls = {0: {"id": "tc_1", "name": "Read", "arguments": '{"path": "/x"}'}}
        blocks = _build_content_blocks([], tool_calls)
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["name"] == "Read"
        assert blocks[0]["input"] == {"path": "/x"}

    def test_malformed_arguments_yield_empty_dict(self):
        blocks = _build_content_blocks([], {0: {"id": "x", "name": "Y", "arguments": "not-json"}})
        assert blocks[0]["input"] == {}


# ---------------------------------------------------------------------------
# Differential parser (cross-provider equivalence)
# ---------------------------------------------------------------------------

class TestParsedToolUse:
    def test_parse_matches_anthropic_structure(self):
        from duh.adapters.anthropic import AnthropicProvider
        block = {"type": "tool_use", "id": "abc", "name": "Bash", "input": {"command": "ls"}}
        anth = AnthropicProvider._parse_tool_use_block(block)
        groq = GroqProvider._parse_tool_use_block(block)
        assert anth.id == groq.id
        assert anth.name == groq.name
        assert anth.input == groq.input
