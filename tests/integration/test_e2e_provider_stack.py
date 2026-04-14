"""End-to-end tests: PROVIDERS + MCP TRANSPORTS + BRIDGE + CLI subprocess flows.

This file exercises the provider stack (resolve_provider_name,
build_model_backend, OpenAI ChatGPT adapter streaming), the MCP transport
factory and executor, the WebSocket bridge protocol + server, and real
subprocess invocation of ``python -m duh``.

External HTTP/WS endpoints are mocked, but real classes and code paths are
used everywhere else.

Classes:
    TestProviderResolutionE2E
    TestOpenAIChatGPTAdapterE2E
    TestMCPTransportFactoryE2E
    TestBridgeProtocolE2E
    TestBridgeServerE2E
    TestCLISubprocessE2E
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import pathlib
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.providers.registry import (
    build_model_backend,
    infer_provider_from_model,
    resolve_openai_auth_mode,
    resolve_provider_name,
)

from duh.adapters.openai_chatgpt import OpenAIChatGPTProvider
from duh.adapters.mcp_executor import (
    MAX_ERRORS_BEFORE_RECONNECT,
    MAX_SESSION_RETRIES,
    MCPExecutor,
    MCPServerConfig,
    MCPToolInfo,
    _create_transport,
    _is_session_expired,
)
from duh.adapters.mcp_transports import (
    HTTPTransport,
    SSETransport,
    WebSocketTransport,
)

from duh.bridge.protocol import (
    ConnectMessage,
    DisconnectMessage,
    ErrorMessage,
    EventMessage,
    PromptMessage,
    decode_message,
    encode_message,
    validate_token,
)
from duh.bridge.session_relay import SessionRelay
from duh.bridge.server import BridgeServer

from duh.kernel.messages import Message


PYTHON = sys.executable
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ===========================================================================
# Provider resolution + build_model_backend
# ===========================================================================


@pytest.fixture
def _clean_provider_env(monkeypatch):
    """Neutral env for provider-resolution tests (stub disabled by default)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
    # Make saved-keystore lookups return empty so only the env drives result.
    monkeypatch.setattr(
        "duh.providers.registry.get_saved_anthropic_api_key", lambda: ""
    )
    monkeypatch.setattr(
        "duh.providers.registry.get_saved_openai_api_key", lambda: ""
    )
    monkeypatch.setattr(
        "duh.providers.registry.get_valid_openai_chatgpt_oauth", lambda: None
    )
    yield


class TestProviderResolutionE2E:
    """End-to-end resolve_provider_name + build_model_backend."""

    def test_env_only_anthropic(self, monkeypatch, _clean_provider_env):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
        provider = resolve_provider_name(
            explicit_provider=None,
            model=None,
            check_ollama=lambda: False,
        )
        assert provider == "anthropic"

    def test_env_only_openai(self, monkeypatch, _clean_provider_env):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa-xxx")
        provider = resolve_provider_name(
            explicit_provider=None,
            model=None,
            check_ollama=lambda: False,
        )
        assert provider == "openai"

    def test_explicit_provider_flag_overrides_env(
        self, monkeypatch, _clean_provider_env
    ):
        # Anthropic key is set, but explicit provider should win.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
        provider = resolve_provider_name(
            explicit_provider="openai",
            model=None,
            check_ollama=lambda: False,
        )
        assert provider == "openai"

    def test_model_hint_infers_anthropic(self, _clean_provider_env):
        assert infer_provider_from_model("claude-sonnet-4-6") == "anthropic"
        provider = resolve_provider_name(
            explicit_provider=None,
            model="claude-sonnet-4-6",
            check_ollama=lambda: False,
        )
        assert provider == "anthropic"

    def test_model_hint_infers_openai(self, _clean_provider_env):
        assert infer_provider_from_model("gpt-5.2-codex") == "openai"
        provider = resolve_provider_name(
            explicit_provider=None,
            model="gpt-5.2-codex",
            check_ollama=lambda: False,
        )
        assert provider == "openai"

    def test_ollama_fallback(self, _clean_provider_env):
        # No env, no model, but ollama is available.
        provider = resolve_provider_name(
            explicit_provider=None,
            model=None,
            check_ollama=lambda: True,
        )
        assert provider == "ollama"

    def test_no_provider_at_all(self, _clean_provider_env):
        provider = resolve_provider_name(
            explicit_provider=None,
            model=None,
            check_ollama=lambda: False,
        )
        assert provider is None

    def test_stub_provider_shortcircuits_everything(
        self, monkeypatch, _clean_provider_env
    ):
        monkeypatch.setenv("DUH_STUB_PROVIDER", "1")
        # Every other input points elsewhere, but stub wins.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa-xxx")
        provider = resolve_provider_name(
            explicit_provider="anthropic",
            model="claude-sonnet-4-6",
            check_ollama=lambda: True,
        )
        assert provider == "stub"

    # -- resolve_openai_auth_mode ---------------------------------------

    def test_resolve_openai_auth_mode_codex_with_oauth(
        self, monkeypatch, _clean_provider_env
    ):
        monkeypatch.setattr(
            "duh.providers.registry.get_valid_openai_chatgpt_oauth",
            lambda: {"access_token": "tok", "account_id": "acct"},
        )
        mode = resolve_openai_auth_mode("gpt-5.2-codex")
        assert mode == "chatgpt"

    def test_resolve_openai_auth_mode_noncodex_with_api_key(
        self, monkeypatch, _clean_provider_env
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        mode = resolve_openai_auth_mode("gpt-4o")
        assert mode == "api_key"

    def test_resolve_openai_auth_mode_nothing(self, _clean_provider_env):
        mode = resolve_openai_auth_mode(None)
        assert mode == "none"

    # -- build_model_backend per provider --------------------------------

    def test_build_anthropic_backend_default_model(
        self, monkeypatch, _clean_provider_env
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
        fake = MagicMock()
        fake.stream = lambda **kw: None
        backend = build_model_backend(
            "anthropic",
            None,
            provider_factories={"anthropic": lambda m: fake},
        )
        assert backend.ok
        assert backend.provider == "anthropic"
        assert backend.model == "claude-sonnet-4-6"
        assert backend.auth_mode == "api_key"

    def test_build_openai_api_key_backend_default_model(
        self, monkeypatch, _clean_provider_env
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
        monkeypatch.setattr(
            "duh.providers.registry.resolve_openai_auth_mode",
            lambda m: "api_key",
        )
        fake = MagicMock()
        fake.stream = lambda **kw: None
        backend = build_model_backend(
            "openai",
            None,
            provider_factories={"openai_api": lambda m: fake},
        )
        assert backend.ok
        assert backend.provider == "openai"
        assert backend.model == "gpt-4o"
        assert backend.auth_mode == "api_key"

    def test_build_openai_chatgpt_backend(
        self, monkeypatch, _clean_provider_env
    ):
        monkeypatch.setattr(
            "duh.providers.registry.resolve_openai_auth_mode",
            lambda m: "chatgpt",
        )
        fake = MagicMock()
        fake.stream = lambda **kw: None
        backend = build_model_backend(
            "openai",
            "gpt-5.2-codex",
            provider_factories={"openai_chatgpt": lambda m: fake},
        )
        assert backend.ok
        assert backend.auth_mode == "chatgpt"
        assert backend.model == "gpt-5.2-codex"

    def test_build_ollama_backend(self, _clean_provider_env):
        fake = MagicMock()
        fake.stream = lambda **kw: None
        backend = build_model_backend(
            "ollama",
            None,
            provider_factories={"ollama": lambda m: fake},
        )
        assert backend.ok
        assert backend.provider == "ollama"
        assert backend.auth_mode == "local"
        assert backend.model == "qwen2.5-coder:1.5b"

    def test_build_stub_backend(self, monkeypatch, _clean_provider_env):
        monkeypatch.setenv("DUH_STUB_PROVIDER", "1")
        backend = build_model_backend("stub", "custom-stub")
        assert backend.ok
        assert backend.provider == "stub"
        assert backend.auth_mode == "stub"
        assert backend.model == "custom-stub"

    def test_build_unknown_provider_backend(self, _clean_provider_env):
        backend = build_model_backend("martian-llm", "anything")
        assert not backend.ok
        assert "Unknown provider" in backend.error


# ===========================================================================
# OpenAI ChatGPT adapter streaming
# ===========================================================================


def _make_fake_httpx(
    *,
    lines: list[str] | None = None,
    status_code: int = 200,
    read_body: bytes = b"",
    get_status: int = 404,
    get_body: Any | None = None,
    capture: dict | None = None,
):
    """Minimal fake of httpx.AsyncClient exercising .stream() + .get()."""

    class _FakeResp:
        def __init__(self) -> None:
            self.status_code = status_code
            self.headers = {"content-type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aread(self):
            return read_body

        async def aiter_lines(self):
            for ln in lines or []:
                yield ln

    class _FakeGetResp:
        status_code = get_status

        @staticmethod
        def json():
            return get_body if get_body is not None else {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, headers=None, json=None):
            if capture is not None:
                capture["method"] = method
                capture["url"] = url
                capture["headers"] = headers
                capture["json"] = json
            return _FakeResp()

        async def get(self, url, headers=None):
            if capture is not None:
                capture["get_url"] = url
            return _FakeGetResp()

    return _FakeClient


def _patch_oauth_ok(monkeypatch):
    monkeypatch.setattr(
        "duh.adapters.openai_chatgpt.get_valid_openai_chatgpt_oauth",
        lambda: {"access_token": "tok", "account_id": "acct-1"},
    )


class _FakeTool:
    def __init__(self, name: str):
        self.name = name
        self.description = "desc"
        self.input_schema = {"type": "object"}


class TestOpenAIChatGPTAdapterE2E:
    """End-to-end OpenAIChatGPTProvider.stream with mocked httpx."""

    @pytest.mark.asyncio
    async def test_happy_path_text_delta_events(self, monkeypatch):
        _patch_oauth_ok(monkeypatch)
        client_cls = _make_fake_httpx(
            lines=[
                'data: {"type":"response.output_text.delta","delta":"Hello"}',
                'data: {"type":"response.output_text.delta","delta":", world"}',
                'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"Hello, world"}]}]}}',
                "data: [DONE]",
            ]
        )
        monkeypatch.setattr(
            "duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls
        )
        provider = OpenAIChatGPTProvider(model="gpt-5.2-codex")
        events = []
        async for ev in provider.stream(
            messages=[Message(role="user", content="hi")],
            system_prompt="sys",
        ):
            events.append(ev)
        deltas = [e for e in events if e.get("type") == "text_delta"]
        assert len(deltas) >= 2
        assert any("Hello" in d.get("text", "") for d in deltas)
        assistants = [e for e in events if e.get("type") == "assistant"]
        assert len(assistants) == 1
        assert "Hello, world" in assistants[-1]["message"].text

    @pytest.mark.asyncio
    async def test_function_call_accumulation(self, monkeypatch):
        _patch_oauth_ok(monkeypatch)
        client_cls = _make_fake_httpx(
            lines=[
                'data: {"type":"response.output_item.added","item":{"type":"function_call","id":"itm_1","call_id":"call_42","name":"Read","arguments":""}}',
                'data: {"type":"response.function_call_arguments.delta","call_id":"call_42","item_id":"itm_1","delta":"{\\"path\\":"}',
                'data: {"type":"response.function_call_arguments.delta","call_id":"call_42","item_id":"itm_1","delta":"\\"/tmp/x\\"}"}',
                'data: {"type":"response.completed","response":{"status":"completed","output":[]}}',
                "data: [DONE]",
            ]
        )
        monkeypatch.setattr(
            "duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls
        )
        provider = OpenAIChatGPTProvider()
        events = []
        async for ev in provider.stream(
            messages=[Message(role="user", content="read a file")],
            tools=[_FakeTool("Read")],
        ):
            events.append(ev)
        assistants = [e for e in events if e.get("type") == "assistant"]
        assert assistants, events
        tool_uses = [
            b for b in assistants[-1]["message"].content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        assert tool_uses, assistants[-1]["message"].content
        tu = tool_uses[0]
        assert tu["name"] == "Read"
        # Arguments were accumulated across two deltas into a valid path.
        assert tu["input"].get("path") == "/tmp/x"

    @pytest.mark.asyncio
    async def test_fetch_by_id_fallback(self, monkeypatch):
        """Stream ends with empty completed response → GET /responses/{id}."""
        _patch_oauth_ok(monkeypatch)
        capture: dict = {}
        client_cls = _make_fake_httpx(
            lines=[
                'data: {"type":"response.created","response":{"id":"resp_xyz"}}',
                'data: {"type":"response.completed","response":{"id":"resp_xyz","status":"completed"}}',
                "data: [DONE]",
            ],
            get_status=200,
            get_body={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "fetched content"}
                        ],
                    }
                ]
            },
            capture=capture,
        )
        monkeypatch.setattr(
            "duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls
        )
        provider = OpenAIChatGPTProvider()
        events = []
        async for ev in provider.stream(
            messages=[Message(role="user", content="hi")]
        ):
            events.append(ev)
        assistants = [e for e in events if e.get("type") == "assistant"]
        assert assistants
        assert "fetched content" in assistants[-1]["message"].text
        assert capture.get("get_url", "").endswith("/responses/resp_xyz")

    @pytest.mark.asyncio
    async def test_error_event_emits_is_error(self, monkeypatch):
        _patch_oauth_ok(monkeypatch)
        client_cls = _make_fake_httpx(
            lines=[
                'data: {"type":"response.error","error":{"message":"model offline"}}',
                "data: [DONE]",
            ]
        )
        monkeypatch.setattr(
            "duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls
        )
        provider = OpenAIChatGPTProvider()
        events = []
        async for ev in provider.stream(
            messages=[Message(role="user", content="hi")]
        ):
            events.append(ev)
        last = events[-1]
        assert last["type"] == "assistant"
        assert last["message"].metadata.get("is_error") is True
        assert "model offline" in last["message"].text

    @pytest.mark.asyncio
    async def test_http_4xx_yields_error(self, monkeypatch):
        _patch_oauth_ok(monkeypatch)
        client_cls = _make_fake_httpx(
            lines=[],
            status_code=401,
            read_body=b"unauthorized",
        )
        monkeypatch.setattr(
            "duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls
        )
        provider = OpenAIChatGPTProvider()
        events = []
        async for ev in provider.stream(
            messages=[Message(role="user", content="hi")]
        ):
            events.append(ev)
        assert events
        last = events[-1]
        assert last["type"] == "assistant"
        assert last["message"].metadata.get("is_error") is True
        assert "401" in last["message"].text

    @pytest.mark.asyncio
    async def test_tool_choice_any_maps_to_required(self, monkeypatch):
        _patch_oauth_ok(monkeypatch)
        capture: dict = {}
        client_cls = _make_fake_httpx(
            lines=[
                'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}]}}',
                "data: [DONE]",
            ],
            capture=capture,
        )
        monkeypatch.setattr(
            "duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls
        )
        provider = OpenAIChatGPTProvider()
        async for _ in provider.stream(
            messages=[Message(role="user", content="hi")],
            tools=[_FakeTool("T")],
            tool_choice="any",
        ):
            pass
        assert capture["json"]["tool_choice"] == "required"

    @pytest.mark.asyncio
    async def test_tool_choice_none_and_auto_passthrough(self, monkeypatch):
        _patch_oauth_ok(monkeypatch)
        for choice in ("none", "auto"):
            capture: dict = {}
            client_cls = _make_fake_httpx(
                lines=[
                    'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}]}}',
                    "data: [DONE]",
                ],
                capture=capture,
            )
            monkeypatch.setattr(
                "duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls
            )
            provider = OpenAIChatGPTProvider()
            async for _ in provider.stream(
                messages=[Message(role="user", content="hi")],
                tools=[_FakeTool("T")],
                tool_choice=choice,
            ):
                pass
            assert capture["json"]["tool_choice"] == choice

    @pytest.mark.asyncio
    async def test_tool_choice_named_string_maps_to_function(
        self, monkeypatch
    ):
        _patch_oauth_ok(monkeypatch)
        capture: dict = {}
        client_cls = _make_fake_httpx(
            lines=[
                'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"ok"}]}]}}',
                "data: [DONE]",
            ],
            capture=capture,
        )
        monkeypatch.setattr(
            "duh.adapters.openai_chatgpt.httpx.AsyncClient", client_cls
        )
        provider = OpenAIChatGPTProvider()
        async for _ in provider.stream(
            messages=[Message(role="user", content="hi")],
            tools=[_FakeTool("MyTool")],
            tool_choice="MyTool",
        ):
            pass
        assert capture["json"]["tool_choice"] == {
            "type": "function",
            "name": "MyTool",
        }


# ===========================================================================
# MCP transport factory + executor
# ===========================================================================


class TestMCPTransportFactoryE2E:
    """End-to-end _create_transport + from_config + connect_all."""

    def test_stdio_config_has_command_args(self):
        cfg = MCPServerConfig(command="npx", args=["-y", "mcp-fs"])
        assert cfg.transport == "stdio"
        assert cfg.command == "npx"
        assert cfg.args == ["-y", "mcp-fs"]
        # stdio handled inline → factory returns None
        assert _create_transport(cfg) is None

    def test_sse_config_creates_sse_transport(self):
        cfg = MCPServerConfig(
            command="",
            transport="sse",
            url="https://remote/sse",
            headers={"X-Auth": "x"},
        )
        t = _create_transport(cfg)
        assert isinstance(t, SSETransport)

    def test_http_config_creates_http_transport(self):
        cfg = MCPServerConfig(
            command="", transport="http", url="https://remote/rpc"
        )
        t = _create_transport(cfg)
        assert isinstance(t, HTTPTransport)

    def test_ws_config_creates_ws_transport(self):
        cfg = MCPServerConfig(command="", transport="ws", url="ws://remote/ws")
        t = _create_transport(cfg)
        assert isinstance(t, WebSocketTransport)

    def test_unknown_transport_raises_value_error(self):
        cfg = MCPServerConfig(command="", transport="grpc", url="x")
        with pytest.raises(ValueError, match="Unsupported.*grpc"):
            _create_transport(cfg)

    def test_from_config_mixed_local_remote(self):
        cfg = {
            "mcpServers": {
                "local_fs": {"command": "npx", "args": ["-y", "mcp-fs"]},
                "remote_sse": {
                    "command": "",
                    "transport": "sse",
                    "url": "https://remote/sse",
                },
                "remote_http": {
                    "command": "",
                    "transport": "http",
                    "url": "https://remote/rpc",
                },
                "remote_ws": {
                    "command": "",
                    "transport": "ws",
                    "url": "ws://remote/ws",
                },
            }
        }
        executor = MCPExecutor.from_config(cfg)
        assert executor._servers["local_fs"].transport == "stdio"
        assert executor._servers["remote_sse"].transport == "sse"
        assert executor._servers["remote_http"].transport == "http"
        assert executor._servers["remote_ws"].transport == "ws"

    @pytest.mark.asyncio
    async def test_connect_all_batches_local_first_then_remote(self):
        """Local stdio servers must finish connecting before remote servers."""
        order: list[str] = []
        cfg = {
            "mcpServers": {
                "local_a": {"command": "echo", "args": ["a"]},
                "local_b": {"command": "echo", "args": ["b"]},
                "remote_1": {
                    "command": "",
                    "transport": "sse",
                    "url": "https://r/sse",
                },
                "remote_2": {
                    "command": "",
                    "transport": "http",
                    "url": "https://r/rpc",
                },
            }
        }
        executor = MCPExecutor.from_config(cfg)

        async def fake_connect(name: str) -> list[MCPToolInfo]:
            order.append(name)
            return []

        executor.connect = fake_connect  # type: ignore[assignment]
        await executor.connect_all()

        # All local names appear before any remote name.
        local_indices = [
            order.index(n) for n in ("local_a", "local_b")
        ]
        remote_indices = [
            order.index(n) for n in ("remote_1", "remote_2")
        ]
        for li in local_indices:
            for ri in remote_indices:
                assert li < ri, order

    @pytest.mark.asyncio
    async def test_session_expiry_triggers_reconnect_and_retry(self):
        """404 + "Session not found" triggers one reconnect-and-retry."""
        # Build an executor with one configured server and a dummy connection.
        cfg = MCPServerConfig(command="echo", args=[])
        executor = MCPExecutor({"srv": cfg})

        info = MCPToolInfo(name="ping", server_name="srv")
        executor._tool_index["mcp__srv__ping"] = info

        call_count = {"calls": 0}

        class ExpiredError(Exception):
            status_code = 404

            def __str__(self) -> str:
                return "Session not found"

        class _Session:
            async def call_tool(self, name, arguments=None):
                call_count["calls"] += 1
                if call_count["calls"] == 1:
                    raise ExpiredError()
                # After reconnect, return a successful result.
                return SimpleNamespace(
                    content=[SimpleNamespace(text="pong")]
                )

        # Initial live connection
        executor._connections["srv"] = SimpleNamespace(
            server_name="srv",
            config=cfg,
            session=_Session(),
            tools=[info],
            _stdio_ctx=None,
            _cleanup=None,
        )

        reconnect_calls = {"n": 0}

        async def fake_disconnect(name: str) -> None:
            # Pretend clean disconnect (leave tool_index intact so retry works)
            pass

        async def fake_connect(name: str) -> list[MCPToolInfo]:
            reconnect_calls["n"] += 1
            # Replace connection with a fresh session object
            executor._connections["srv"] = SimpleNamespace(
                server_name="srv",
                config=cfg,
                session=_Session(),
                tools=[info],
                _stdio_ctx=None,
                _cleanup=None,
            )
            # Reset call_count so the retried call_tool succeeds.
            call_count["calls"] = 99
            return [info]

        executor.disconnect = fake_disconnect  # type: ignore[assignment]
        executor.connect = fake_connect  # type: ignore[assignment]

        result = await executor.run("mcp__srv__ping", {})
        assert reconnect_calls["n"] == 1
        assert result == "pong"

    @pytest.mark.asyncio
    async def test_max_errors_triggers_reconnect(self):
        """MAX_ERRORS_BEFORE_RECONNECT consecutive errors → circuit breaker fires.

        ADR-032: on the Nth consecutive failure the server is marked *degraded*
        and its tools are removed from the active schema.  The old reconnect
        logic has been replaced by the circuit-breaker pattern.
        """
        cfg = MCPServerConfig(command="echo", args=[])
        executor = MCPExecutor({"srv": cfg})
        info = MCPToolInfo(name="ping", server_name="srv")
        executor._tool_index["mcp__srv__ping"] = info

        class _FailSession:
            async def call_tool(self, name, arguments=None):
                raise RuntimeError("transient failure")

        executor._connections["srv"] = SimpleNamespace(
            server_name="srv",
            config=cfg,
            session=_FailSession(),
            tools=[info],
            _stdio_ctx=None,
            _cleanup=None,
        )

        disconnect_calls: list[str] = []

        async def fake_disconnect(name: str) -> None:
            disconnect_calls.append(name)

        executor.disconnect = fake_disconnect  # type: ignore[assignment]

        # Trip the breaker: MAX_ERRORS_BEFORE_RECONNECT consecutive failures.
        for _ in range(MAX_ERRORS_BEFORE_RECONNECT):
            with pytest.raises(RuntimeError):
                await executor.run("mcp__srv__ping", {})

        # Circuit breaker must have fired: server is degraded, tool is gone.
        assert executor.is_degraded("srv")
        assert "mcp__srv__ping" not in executor.tool_names
        # Disconnect was called to clean up the connection
        assert "srv" in disconnect_calls


# ===========================================================================
# Bridge protocol + session relay
# ===========================================================================


class _FakeWebSocket:
    """Minimal server-side WebSocket stand-in."""

    def __init__(self):
        self.sent: list[str] = []
        self._recv: asyncio.Queue[str] = asyncio.Queue()
        self._closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self._closed = True

    def feed(self, msg: str) -> None:
        self._recv.put_nowait(msg)

    def done(self) -> None:
        """Signal no more messages — __aiter__ will stop."""
        self._closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await asyncio.wait_for(self._recv.get(), timeout=0.2)
        except asyncio.TimeoutError:
            raise StopAsyncIteration


class TestBridgeProtocolE2E:
    """End-to-end bridge/protocol.py + session_relay."""

    def test_roundtrip_connect(self):
        m = ConnectMessage(session_id="s1", token="tok")
        decoded = decode_message(encode_message(m))
        assert isinstance(decoded, ConnectMessage)
        assert decoded.session_id == "s1"
        assert decoded.token == "tok"

    def test_roundtrip_prompt(self):
        m = PromptMessage(session_id="s1", content="hello world")
        decoded = decode_message(encode_message(m))
        assert isinstance(decoded, PromptMessage)
        assert decoded.content == "hello world"

    def test_roundtrip_event(self):
        m = EventMessage(
            session_id="s1",
            event_type="text_delta",
            data={"delta": "hi"},
        )
        decoded = decode_message(encode_message(m))
        assert isinstance(decoded, EventMessage)
        assert decoded.event_type == "text_delta"
        assert decoded.data == {"delta": "hi"}

    def test_roundtrip_disconnect(self):
        m = DisconnectMessage(session_id="s1")
        decoded = decode_message(encode_message(m))
        assert isinstance(decoded, DisconnectMessage)

    def test_roundtrip_error(self):
        m = ErrorMessage(session_id="s1", error="boom", code=500)
        decoded = decode_message(encode_message(m))
        assert isinstance(decoded, ErrorMessage)
        assert decoded.error == "boom"
        assert decoded.code == 500

    def test_encode_preserves_field_values(self):
        m = PromptMessage(session_id="abc", content="x", timestamp=1234.5)
        raw = encode_message(m)
        data = json.loads(raw)
        assert data["type"] == "prompt"
        assert data["session_id"] == "abc"
        assert data["content"] == "x"
        assert data["timestamp"] == 1234.5

    def test_decode_unknown_type_raises(self):
        raw = json.dumps({"type": "mystery"})
        with pytest.raises(ValueError, match="Unknown bridge message type"):
            decode_message(raw)

    def test_decode_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid bridge JSON"):
            decode_message("{not json")

    def test_validate_token_empty_expected_open_mode(self):
        # Any provided token is accepted when expected is empty.
        assert validate_token("", "") is True
        assert validate_token("anything", "") is True

    def test_validate_token_match(self):
        assert validate_token("s3cret", "s3cret") is True

    def test_validate_token_mismatch(self):
        assert validate_token("wrong", "s3cret") is False

    def test_validate_token_uses_compare_digest(self):
        real = hmac.compare_digest
        calls = {"n": 0}

        def _spy(a, b):
            calls["n"] += 1
            return real(a, b)

        with patch("hmac.compare_digest", side_effect=_spy) as m:
            validate_token("tok", "tok")
            assert m.called
            assert calls["n"] >= 1

    def test_session_relay_register_unregister_lookup(self):
        relay = SessionRelay()
        ws = _FakeWebSocket()
        assert relay.get_websocket("s1") is None
        relay.register("s1", ws)
        assert relay.session_count == 1
        assert relay.has_session("s1")
        # "lookup" roundtrip
        assert relay.get_websocket("s1") is ws
        relay.unregister("s1")
        assert relay.session_count == 0
        assert relay.get_websocket("s1") is None

    @pytest.mark.asyncio
    async def test_session_relay_send_event_resilient_to_broken_ws(self):
        """Broken websocket.send must not propagate — relay just logs."""
        relay = SessionRelay()
        broken = MagicMock()
        broken.send = AsyncMock(side_effect=RuntimeError("peer gone"))
        relay.register("s1", broken)

        event = EventMessage(
            session_id="s1",
            event_type="text_delta",
            data={"delta": "x"},
        )
        # Must not raise
        await relay.send_event("s1", event)
        broken.send.assert_awaited()


# ===========================================================================
# BridgeServer
# ===========================================================================


class TestBridgeServerE2E:
    """End-to-end BridgeServer with mocked websockets."""

    @pytest.mark.asyncio
    async def test_start_calls_websockets_serve_with_max_size(self):
        server = BridgeServer(host="localhost", port=9999, token="tok")
        fake_serve = AsyncMock(return_value=MagicMock())
        with patch(
            "duh.bridge.server.websockets.serve", fake_serve
        ) as mocked:
            await server.start()
        mocked.assert_called_once()
        _, kwargs = mocked.call_args
        assert kwargs.get("max_size") == 1_048_576

    @pytest.mark.asyncio
    async def test_start_auto_generates_token_when_empty(self, capsys):
        server = BridgeServer(host="localhost", port=9998, token="")
        fake_serve = AsyncMock(return_value=MagicMock())
        with patch("duh.bridge.server.websockets.serve", fake_serve):
            await server.start()
        # ADR-042: empty token triggers auto-generation, prints to stdout
        assert server._token != ""
        assert len(server._token) >= 20
        captured = capsys.readouterr()
        assert "Auth token:" in captured.out

    @pytest.mark.asyncio
    async def test_valid_connect_message_acknowledged(self):
        engine_factory = AsyncMock(return_value=MagicMock())
        server = BridgeServer(token="secret", engine_factory=engine_factory)
        ws = _FakeWebSocket()
        ws.feed(encode_message(ConnectMessage(token="secret", session_id="s1")))
        ws.done()
        await server._handle_connection(ws)
        # Server should have sent a "connected" ack event.
        assert ws.sent, "expected an ack from the server"
        parsed = json.loads(ws.sent[0])
        assert parsed["type"] == "event"
        assert parsed["event_type"] == "connected"
        assert parsed["data"]["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_invalid_token_returns_error_and_closes(self):
        server = BridgeServer(token="secret")
        ws = _FakeWebSocket()
        ws.feed(encode_message(ConnectMessage(token="wrong", session_id="s1")))
        ws.done()
        await server._handle_connection(ws)
        assert ws.sent, "expected error response"
        parsed = json.loads(ws.sent[0])
        assert parsed["type"] == "error"
        assert parsed["code"] == 401
        assert ws._closed is True

    @pytest.mark.asyncio
    async def test_prompt_without_connect_returns_error(self):
        server = BridgeServer(token="")
        ws = _FakeWebSocket()
        ws.feed(encode_message(PromptMessage(session_id="s1", content="hi")))
        ws.done()
        await server._handle_connection(ws)
        assert ws.sent
        parsed = json.loads(ws.sent[0])
        assert parsed["type"] == "error"
        assert parsed["code"] == 403
        assert "Not connected" in parsed["error"]

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self):
        server = BridgeServer(token="")
        ws = _FakeWebSocket()
        ws.feed("{not json}")
        ws.done()
        await server._handle_connection(ws)
        assert ws.sent
        parsed = json.loads(ws.sent[0])
        assert parsed["type"] == "error"
        assert parsed["code"] == 400

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up_session(self):
        server = BridgeServer(token="")
        ws = _FakeWebSocket()
        ws.feed(encode_message(ConnectMessage(token="", session_id="s9")))
        ws.feed(encode_message(DisconnectMessage(session_id="s9")))
        ws.done()
        await server._handle_connection(ws)
        # After disconnect, the session must be gone from the relay.
        assert not server.relay.has_session("s9")


# ===========================================================================
# Real CLI subprocess invocations
# ===========================================================================


def _run_duh(
    *args: str,
    timeout: int = 20,
    input: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DUH_STUB_PROVIDER"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [PYTHON, "-m", "duh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
        env=env,
        input=input,
    )


class TestCLISubprocessE2E:
    """Real ``python -m duh`` invocations (subprocess)."""

    def test_version(self):
        result = _run_duh("--version")
        assert result.returncode == 0
        assert "duh" in result.stdout.lower()
        # Version string contains a dotted version.
        assert any(ch.isdigit() for ch in result.stdout)

    def test_help(self):
        result = _run_duh("--help")
        assert result.returncode == 0
        assert "--prompt" in result.stdout

    def test_print_mode_stub_response(self):
        result = _run_duh(
            "-p", "hi", "--max-turns", "1",
            extra_env={"DUH_STUB_RESPONSE": "stub-say-hi"},
        )
        assert result.returncode == 0, result.stderr
        # Stub response must appear in stdout.
        assert "stub-say-hi" in result.stdout

    def test_stream_json_control_and_user(self):
        """NDJSON in/out: control_request + user → control_response + assistant + result."""
        ndjson_in = (
            '{"type":"control_request","request_id":"r1",'
            '"request":{"subtype":"initialize"}}\n'
            '{"type":"user","session_id":"",'
            '"message":{"role":"user","content":"hello"},'
            '"parent_tool_use_id":null}\n'
        )
        result = _run_duh(
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
            "--max-turns", "1",
            input=ndjson_in,
            extra_env={"DUH_STUB_RESPONSE": "ndjson-stub"},
        )
        assert result.returncode == 0, (
            f"stderr={result.stderr}\nstdout={result.stdout}"
        )
        # Parse NDJSON output.
        out_msgs = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.strip()
        ]
        types = [m.get("type") for m in out_msgs]
        assert "control_response" in types, out_msgs
        assert "assistant" in types, out_msgs
        assert "result" in types, out_msgs
        # Result is last and has success subtype.
        result_msg = out_msgs[-1]
        assert result_msg["type"] == "result"
        assert result_msg["subtype"] == "success"
        # Assistant message contains the stub response.
        assistant_msgs = [m for m in out_msgs if m.get("type") == "assistant"]
        concat = json.dumps(assistant_msgs)
        assert "ndjson-stub" in concat, concat

    def test_doctor_lists_provider_status_and_tool_count(self):
        result = _run_duh("doctor", timeout=20)
        # doctor exits 0 if all checks pass, 1 otherwise — either is acceptable.
        assert result.returncode in (0, 1)
        assert "Python version" in result.stdout
        assert "ANTHROPIC_API_KEY" in result.stdout
        assert "OPENAI_API_KEY" in result.stdout
        assert "Tools available" in result.stdout
        # Providers summary line
        assert "Providers" in result.stdout

    def test_three_sequential_invocations_succeed(self):
        """Stateless: three sequential runs must not leave stale state."""
        for i in range(3):
            result = _run_duh(
                "-p", f"call-{i}",
                "--max-turns", "1",
                extra_env={"DUH_STUB_RESPONSE": f"resp-{i}"},
            )
            assert result.returncode == 0, (
                f"iteration {i} failed: {result.stderr}"
            )
            assert f"resp-{i}" in result.stdout
