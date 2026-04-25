"""Adapter routing tests (ADR-075).

These tests lock in the config + install migration that demotes LiteLLM
to an opt-in fallback and promotes native Gemini / Groq adapters.

They intentionally avoid importing the real ``GeminiProvider`` /
``GroqProvider`` classes (those live in ``duh/adapters/gemini.py`` and
``duh/adapters/groq.py`` and are implemented by a sibling work-stream).
Where the registry would construct them, we inject a factory through the
``provider_factories`` kwarg — the production code already supports this
escape hatch for every provider.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from unittest.mock import MagicMock

import pytest

from duh.providers import registry as reg


@pytest.fixture(autouse=True)
def _reset_registry_state(monkeypatch):
    """Clear one-shot session flags + env vars before every test."""
    reg._reset_session_state_for_tests()
    reg._MODEL_CACHE.clear()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
    yield
    reg._reset_session_state_for_tests()
    reg._MODEL_CACHE.clear()


def _fake_provider_factory() -> MagicMock:
    provider = MagicMock()
    provider.stream = MagicMock()
    return provider


# ---------------------------------------------------------------------------
# infer_provider_from_model: native-first routing
# ---------------------------------------------------------------------------


class TestInferenceRouting:
    def test_gemini_prefixed_routes_native_when_sdk_present(self, monkeypatch):
        monkeypatch.setattr(reg, "_google_genai_available", lambda: True)
        assert reg.infer_provider_from_model("gemini/gemini-2.5-pro") == "gemini"

    def test_gemini_bare_name_routes_native_when_sdk_present(self, monkeypatch):
        monkeypatch.setattr(reg, "_google_genai_available", lambda: True)
        assert reg.infer_provider_from_model("gemini-2.0-flash") == "gemini"

    def test_gemini_returns_none_when_sdk_missing(self, monkeypatch):
        monkeypatch.setattr(reg, "_google_genai_available", lambda: False)
        # "gemini/..." still has a slash so litellm is the fallback target.
        assert reg.infer_provider_from_model("gemini/gemini-2.5-pro") is None

    def test_groq_prefixed_routes_native_when_sdk_present(self, monkeypatch):
        monkeypatch.setattr(reg, "_groq_sdk_available", lambda: True)
        assert (
            reg.infer_provider_from_model("groq/llama-3.3-70b-versatile") == "groq"
        )

    def test_groq_returns_none_when_sdk_missing(self, monkeypatch):
        monkeypatch.setattr(reg, "_groq_sdk_available", lambda: False)
        assert reg.infer_provider_from_model("groq/llama-3.3-70b-versatile") is None


# ---------------------------------------------------------------------------
# build_model_backend: dispatch + startup log + LiteLLM opt-in guard
# ---------------------------------------------------------------------------


class TestBuildModelBackendDispatch:
    def test_gemini_native_uses_injected_factory(self, monkeypatch):
        monkeypatch.setattr(reg, "_google_genai_available", lambda: True)
        factory = MagicMock(return_value=_fake_provider_factory())
        backend = reg.build_model_backend(
            "gemini",
            "gemini-2.5-pro",
            provider_factories={"gemini": factory},
        )
        assert backend.ok, backend.error
        assert backend.provider == "gemini"
        assert backend.model == "gemini-2.5-pro"
        factory.assert_called_once_with("gemini-2.5-pro")

    def test_gemini_without_sdk_returns_clear_error(self, monkeypatch):
        """No SDK, no explicit factory → actionable install message."""
        monkeypatch.setattr(reg, "_google_genai_available", lambda: False)
        backend = reg.build_model_backend("gemini", "gemini-2.5-pro")
        assert not backend.ok
        assert "google-genai" in backend.error

    def test_groq_native_uses_injected_factory(self, monkeypatch):
        monkeypatch.setattr(reg, "_groq_sdk_available", lambda: True)
        factory = MagicMock(return_value=_fake_provider_factory())
        backend = reg.build_model_backend(
            "groq",
            "groq/llama-3.3-70b-versatile",
            provider_factories={"groq": factory},
        )
        assert backend.ok, backend.error
        assert backend.provider == "groq"
        factory.assert_called_once()

    def test_groq_without_sdk_returns_clear_error(self, monkeypatch):
        monkeypatch.setattr(reg, "_groq_sdk_available", lambda: False)
        backend = reg.build_model_backend("groq", "groq/llama-3.3-70b-versatile")
        assert not backend.ok
        assert "groq" in backend.error.lower()




class TestStartupLog:
    def test_gemini_startup_log_names_native_adapter(self, monkeypatch, caplog):
        monkeypatch.setattr(reg, "_google_genai_available", lambda: True)
        factory = lambda m: _fake_provider_factory()  # noqa: E731
        with caplog.at_level("INFO", logger="duh.providers"):
            reg.build_model_backend(
                "gemini",
                "gemini-2.5-pro",
                provider_factories={"gemini": factory},
            )
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "GeminiProvider (native)" in joined
        assert "gemini-2.5-pro" in joined


    def test_startup_log_emitted_once_per_model(self, monkeypatch, caplog):
        monkeypatch.setattr(reg, "_google_genai_available", lambda: True)
        factory = lambda m: _fake_provider_factory()  # noqa: E731
        with caplog.at_level("INFO", logger="duh.providers"):
            reg.build_model_backend(
                "gemini", "gemini-2.5-pro",
                provider_factories={"gemini": factory},
            )
            reg.build_model_backend(
                "gemini", "gemini-2.5-pro",
                provider_factories={"gemini": factory},
            )
        hits = [
            r for r in caplog.records
            if "GeminiProvider (native)" in r.getMessage()
            and "gemini-2.5-pro" in r.getMessage()
        ]
        assert len(hits) == 1


# ---------------------------------------------------------------------------
# LiteLLM deprecation warning (single-shot, stderr)
# ---------------------------------------------------------------------------



class TestDoctorAdapterSection:
    def test_doctor_renders_adapter_table(self, monkeypatch):
        """The doctor output must list every provider + adapter status."""
        from duh.cli import doctor as doctor_mod

        monkeypatch.setattr(reg, "_google_genai_available", lambda: True)
        monkeypatch.setattr(reg, "_groq_sdk_available", lambda: True)

        text = doctor_mod._render_adapter_section()
        assert "Providers:" in text
        for name in ("anthropic", "openai", "ollama", "gemini", "groq",
                     "deepseek", "mistral", "qwen", "together"):
            assert name in text

    def test_doctor_section_reflects_missing_gemini_sdk(self, monkeypatch):
        from duh.cli import doctor as doctor_mod

        monkeypatch.setattr(reg, "_google_genai_available", lambda: False)
        monkeypatch.setattr(reg, "_groq_sdk_available", lambda: True)
        text = doctor_mod._render_adapter_section()
        # Gemini should report not-installed hint.
        assert "pip install google-genai" in text
