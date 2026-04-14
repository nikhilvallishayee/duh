"""Every provider adapter must tag streamed text as MODEL_OUTPUT."""

from __future__ import annotations

import pytest

from duh.kernel.untrusted import TaintSource, UntrustedStr


def _make_wrap_fn(module_path: str):
    """Import the _wrap_model_output helper from a provider module."""
    import importlib
    mod = importlib.import_module(module_path)
    return mod._wrap_model_output


@pytest.mark.parametrize("module_path", [
    "duh.adapters.anthropic",
    "duh.adapters.openai",
    "duh.adapters.openai_chatgpt",
    "duh.adapters.ollama",
    "duh.adapters.stub_provider",
])
def test_provider_wrap_model_output_returns_untrusted(module_path: str) -> None:
    wrap = _make_wrap_fn(module_path)
    result = wrap("hello from the model")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.MODEL_OUTPUT


@pytest.mark.parametrize("module_path", [
    "duh.adapters.anthropic",
    "duh.adapters.openai",
    "duh.adapters.openai_chatgpt",
    "duh.adapters.ollama",
    "duh.adapters.stub_provider",
])
def test_provider_wrap_idempotent(module_path: str) -> None:
    wrap = _make_wrap_fn(module_path)
    pre = UntrustedStr("already tagged", TaintSource.MODEL_OUTPUT)
    result = wrap(pre)
    assert result.source == TaintSource.MODEL_OUTPUT
