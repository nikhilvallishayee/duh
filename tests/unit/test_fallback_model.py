"""Tests for fallback model support in duh.kernel.engine.

When the primary model fails with overload/rate-limit errors and a
fallback_model is configured, the engine should retry the query loop
exactly once with the fallback model.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

import pytest

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig, _is_fallback_error
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_model(text: str = "Hello!"):
    """Return a model function that yields a successful assistant response."""
    async def model_fn(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": text}],
        )}
    return model_fn


def _error_model(error_text: str):
    """Return a model function that raises an exception (caught by the loop)."""
    async def model_fn(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        raise Exception(error_text)
        # unreachable, but makes the type checker happy
        yield {}  # noqa: E501  # pragma: no cover
    return model_fn


def _tracking_model(primary_error: str, fallback_response: str = "Fallback OK"):
    """Return a model function that errors on the primary model, succeeds on fallback.

    Tracks which models were called via a shared list.
    """
    calls: list[str] = []

    async def model_fn(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
        model_name = kwargs.get("model", "")
        calls.append(model_name)
        if model_name != "fallback-model":
            raise Exception(primary_error)
        yield {"type": "assistant", "message": Message(
            role="assistant",
            content=[{"type": "text", "text": fallback_response}],
        )}

    return model_fn, calls


# ---------------------------------------------------------------------------
# Tests for _is_fallback_error
# ---------------------------------------------------------------------------

class TestIsFallbackError:
    def test_overloaded(self):
        assert _is_fallback_error("API is overloaded") is True

    def test_rate_limit(self):
        assert _is_fallback_error("rate_limit exceeded") is True

    def test_rate_limit_mixed_case(self):
        assert _is_fallback_error("Rate_Limit error from server") is True

    def test_overloaded_mixed_case(self):
        assert _is_fallback_error("Server is Overloaded right now") is True

    def test_auth_error(self):
        assert _is_fallback_error("authentication_error: invalid key") is False

    def test_generic_error(self):
        assert _is_fallback_error("connection refused") is False


# ---------------------------------------------------------------------------
# Tests for Engine fallback behavior
# ---------------------------------------------------------------------------

class TestFallbackModel:
    async def test_fallback_triggers_on_overload(self):
        """When primary yields overload error and fallback is set, retry succeeds."""
        model_fn, calls = _tracking_model("API is overloaded")
        deps = Deps(call_model=model_fn)
        config = EngineConfig(
            model="primary-model",
            fallback_model="fallback-model",
        )
        engine = Engine(deps=deps, config=config)

        events = []
        async for e in engine.run("hello"):
            events.append(e)

        # Should have called primary then fallback
        assert "primary-model" in calls
        assert "fallback-model" in calls

        # Should have assistant event from fallback
        assistant_events = [e for e in events if e.get("type") == "assistant"]
        assert len(assistant_events) >= 1
        msg = assistant_events[-1]["message"]
        assert msg.text == "Fallback OK"

    async def test_fallback_triggers_on_rate_limit(self):
        """rate_limit errors also trigger fallback."""
        model_fn, calls = _tracking_model("rate_limit: too many requests")
        deps = Deps(call_model=model_fn)
        config = EngineConfig(
            model="primary-model",
            fallback_model="fallback-model",
        )
        engine = Engine(deps=deps, config=config)

        events = []
        async for e in engine.run("hello"):
            events.append(e)

        assert "primary-model" in calls
        assert "fallback-model" in calls

        # Should complete without yielding the original error
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 0

    async def test_no_fallback_on_auth_error(self):
        """Auth errors should NOT trigger fallback — they bubble up normally."""
        model_fn, calls = _tracking_model("authentication_error: invalid key")
        deps = Deps(call_model=model_fn)
        config = EngineConfig(
            model="primary-model",
            fallback_model="fallback-model",
        )
        engine = Engine(deps=deps, config=config)

        events = []
        async for e in engine.run("hello"):
            events.append(e)

        # Only primary was called, fallback was NOT used
        assert calls == ["primary-model"]

        # The auth error should have been yielded
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert "authentication_error" in error_events[0]["error"]

    async def test_no_fallback_when_fallback_model_is_none(self):
        """When fallback_model is None, errors propagate normally."""
        async def error_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            raise Exception("API is overloaded")
            yield {}  # pragma: no cover

        deps = Deps(call_model=error_model)
        config = EngineConfig(
            model="primary-model",
            fallback_model=None,
        )
        engine = Engine(deps=deps, config=config)

        events = []
        async for e in engine.run("hello"):
            events.append(e)

        # Error should be yielded, no fallback retry
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert "overloaded" in error_events[0]["error"].lower()

    async def test_fallback_retries_only_once(self):
        """If fallback also fails with overload, don't retry again."""
        calls: list[str] = []

        async def always_overloaded(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            model_name = kwargs.get("model", "")
            calls.append(model_name)
            raise Exception("API is overloaded")
            yield {}  # pragma: no cover

        deps = Deps(call_model=always_overloaded)
        config = EngineConfig(
            model="primary-model",
            fallback_model="fallback-model",
        )
        engine = Engine(deps=deps, config=config)

        events = []
        async for e in engine.run("hello"):
            events.append(e)

        # Should call primary once, then fallback once — no infinite loop
        assert calls == ["primary-model", "fallback-model"]

        # The fallback's error IS yielded (since we don't retry again)
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1

    async def test_successful_primary_does_not_trigger_fallback(self):
        """When primary succeeds, fallback is never consulted."""
        calls: list[str] = []

        async def ok_model(**kwargs) -> AsyncGenerator[dict[str, Any], None]:
            calls.append(kwargs.get("model", ""))
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content=[{"type": "text", "text": "Primary OK"}],
            )}

        deps = Deps(call_model=ok_model)
        config = EngineConfig(
            model="primary-model",
            fallback_model="fallback-model",
        )
        engine = Engine(deps=deps, config=config)

        events = []
        async for e in engine.run("hello"):
            events.append(e)

        # Only primary was called
        assert calls == ["primary-model"]

        # No errors
        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 0

        # Got the response from primary
        assistant_events = [e for e in events if e.get("type") == "assistant"]
        assert assistant_events[-1]["message"].text == "Primary OK"

    async def test_fallback_default_none(self):
        """EngineConfig.fallback_model defaults to None."""
        config = EngineConfig(model="test")
        assert config.fallback_model is None

    async def test_fallback_preserves_messages(self):
        """After fallback, engine.messages includes user msg + fallback assistant msg."""
        model_fn, calls = _tracking_model("overloaded error")
        deps = Deps(call_model=model_fn)
        config = EngineConfig(
            model="primary-model",
            fallback_model="fallback-model",
        )
        engine = Engine(deps=deps, config=config)

        async for _ in engine.run("hello"):
            pass

        # Engine should have at least the user message and the fallback response
        assert len(engine.messages) >= 2
        roles = [m.role for m in engine.messages]
        assert "user" in roles
        assert "assistant" in roles
