from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from duh.cli.provider_utils import (
    _MODEL_CACHE,
    available_models_for_provider,
    infer_provider_from_model,
    resolve_openai_auth_mode,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _MODEL_CACHE.clear()
    yield
    _MODEL_CACHE.clear()


def test_infer_provider_from_codex_model():
    assert infer_provider_from_model("gpt-5.2-codex") == "openai"


def test_available_models_openai_includes_codex():
    with patch("duh.cli.provider_utils.resolve_openai_auth_mode", return_value="api_key"), \
         patch("duh.cli.provider_utils._discover_openai_models_api_key", return_value=["gpt-4o", "gpt-5.2-codex"]):
        models = available_models_for_provider("openai")
        assert "gpt-5.2-codex" in models
        assert "gpt-4o" in models


def test_resolve_openai_auth_mode_prefers_chatgpt_for_codex():
    with patch("duh.cli.provider_utils.get_valid_openai_chatgpt_oauth", return_value={"access_token": "x"}), \
         patch("duh.cli.provider_utils.get_saved_openai_api_key", return_value="sk-test"), \
         patch("os.environ.get", side_effect=lambda k, d=None: d):
        assert resolve_openai_auth_mode("gpt-5.2-codex") == "chatgpt"


def test_resolve_openai_auth_mode_prefers_api_key_for_non_codex():
    with patch("duh.cli.provider_utils.get_valid_openai_chatgpt_oauth", return_value={"access_token": "x"}), \
         patch("duh.cli.provider_utils.get_saved_openai_api_key", return_value="sk-test"), \
         patch("os.environ.get", side_effect=lambda k, d=None: "sk-env" if k == "OPENAI_API_KEY" else d):
        assert resolve_openai_auth_mode("gpt-4o") == "api_key"


def test_openai_api_key_model_discovery_from_v1_models():
    fake_response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "data": [
                {"id": "gpt-4o"},
                {"id": "gpt-5.2-codex"},
                {"id": "text-embedding-3-large"},
            ]
        },
    )

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            return fake_response

    with patch("duh.cli.provider_utils.resolve_openai_auth_mode", return_value="api_key"), \
         patch("duh.cli.provider_utils.httpx.Client", _FakeClient), \
         patch("duh.cli.provider_utils.get_saved_openai_api_key", return_value="sk-test"), \
         patch("os.environ.get", side_effect=lambda k, d=None: d):
        models = available_models_for_provider("openai")

    assert "gpt-4o" in models
    assert "gpt-5.2-codex" in models
    assert "text-embedding-3-large" not in models


def test_openai_chatgpt_discovery_falls_back_to_codex_catalog():
    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, *args, **kwargs):
            raise RuntimeError("no model endpoint")

    oauth = {"access_token": "tok", "account_id": "acct"}
    with patch("duh.cli.provider_utils.resolve_openai_auth_mode", return_value="chatgpt"), \
         patch("duh.cli.provider_utils.httpx.Client", _FakeClient), \
         patch("duh.cli.provider_utils.get_valid_openai_chatgpt_oauth", return_value=oauth):
        models = available_models_for_provider("openai")

    assert "gpt-5.2-codex" in models
