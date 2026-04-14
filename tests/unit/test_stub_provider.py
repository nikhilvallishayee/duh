"""Tests for the stub provider — the offline/test fake provider."""

from __future__ import annotations

import os

import pytest

from duh.adapters.stub_provider import (
    DEFAULT_STUB_RESPONSE,
    STUB_PROVIDER_ENV,
    STUB_RESPONSE_ENV,
    StubProvider,
    stub_provider_enabled,
    stub_response_text,
)
from duh.kernel.messages import Message


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(STUB_PROVIDER_ENV, raising=False)
    assert stub_provider_enabled() is False


def test_enabled_via_env(monkeypatch):
    monkeypatch.setenv(STUB_PROVIDER_ENV, "1")
    assert stub_provider_enabled() is True


def test_enabled_only_for_one_value(monkeypatch):
    """Only the literal "1" enables stub mode — anything else is disabled."""
    monkeypatch.setenv(STUB_PROVIDER_ENV, "true")
    assert stub_provider_enabled() is False
    monkeypatch.setenv(STUB_PROVIDER_ENV, "yes")
    assert stub_provider_enabled() is False


def test_default_response_text(monkeypatch):
    monkeypatch.delenv(STUB_RESPONSE_ENV, raising=False)
    assert stub_response_text() == DEFAULT_STUB_RESPONSE


def test_response_text_overridable(monkeypatch):
    monkeypatch.setenv(STUB_RESPONSE_ENV, "hello world")
    assert stub_response_text() == "hello world"


@pytest.mark.asyncio
async def test_stream_yields_text_assistant_done(monkeypatch):
    monkeypatch.setenv(STUB_RESPONSE_ENV, "test-response")
    p = StubProvider()
    events = []
    async for ev in p.stream(messages=[]):
        events.append(ev)
    types = [e.get("type") for e in events]
    assert types == ["text_delta", "assistant", "done"]
    assert events[0]["text"] == "test-response"
    assert isinstance(events[1]["message"], Message)
    assert events[1]["message"].text == "test-response"
    assert events[2]["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_stream_uses_constructor_model_when_no_override(monkeypatch):
    monkeypatch.delenv(STUB_RESPONSE_ENV, raising=False)
    p = StubProvider(model="my-model")
    events = []
    async for ev in p.stream(messages=[]):
        events.append(ev)
    msg = events[1]["message"]
    assert msg.metadata.get("model") == "my-model"


@pytest.mark.asyncio
async def test_stream_prefers_call_time_model_kwarg():
    p = StubProvider(model="ctor-model")
    events = []
    async for ev in p.stream(messages=[], model="call-model"):
        events.append(ev)
    assert events[1]["message"].metadata.get("model") == "call-model"


@pytest.mark.asyncio
async def test_stream_ignores_unknown_kwargs():
    """The stub must accept the same keyword surface as real providers."""
    p = StubProvider()
    out = []
    async for ev in p.stream(
        messages=[],
        system_prompt="ignored",
        tools=[{"name": "x"}],
        max_tokens=999,
        tool_choice="auto",
        thinking={"enabled": True},  # arbitrary extra kwarg
    ):
        out.append(ev)
    assert any(e.get("type") == "done" for e in out)


def test_registry_returns_stub_when_env_set(monkeypatch):
    monkeypatch.setenv(STUB_PROVIDER_ENV, "1")
    from duh.providers.registry import resolve_provider_name, build_model_backend

    name = resolve_provider_name(
        explicit_provider="anthropic",  # explicit ignored when stub on
        model="claude-anything",
        check_ollama=lambda: True,
    )
    assert name == "stub"

    backend = build_model_backend("stub", "claude-anything")
    assert backend.provider == "stub"
    assert backend.ok
    assert backend.auth_mode == "stub"


def test_registry_stub_overrides_other_providers(monkeypatch):
    monkeypatch.setenv(STUB_PROVIDER_ENV, "1")
    from duh.providers.registry import build_model_backend

    backend = build_model_backend("anthropic", "claude-sonnet-4-6")
    # Even though we asked for anthropic, the stub flag overrides.
    assert backend.provider == "stub"
