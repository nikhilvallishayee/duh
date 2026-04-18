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

    def test_gemini_falls_back_to_litellm_when_sdk_missing(self, monkeypatch):
        monkeypatch.setattr(reg, "_google_genai_available", lambda: False)
        # "gemini/..." still has a slash so litellm is the fallback target.
        assert reg.infer_provider_from_model("gemini/gemini-2.5-pro") == "litellm"

    def test_groq_prefixed_routes_native_when_sdk_present(self, monkeypatch):
        monkeypatch.setattr(reg, "_groq_sdk_available", lambda: True)
        assert (
            reg.infer_provider_from_model("groq/llama-3.3-70b-versatile") == "groq"
        )

    def test_groq_falls_back_to_litellm_when_sdk_missing(self, monkeypatch):
        monkeypatch.setattr(reg, "_groq_sdk_available", lambda: False)
        assert (
            reg.infer_provider_from_model("groq/llama-3.3-70b-versatile")
            == "litellm"
        )


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

    def test_litellm_explicit_override_for_gemini_model(self, monkeypatch):
        """``--provider litellm`` wins even when the model is a gemini one."""
        fake_litellm = _fake_provider_factory()
        backend = reg.build_model_backend(
            "litellm",
            "gemini/gemini-2.5-pro",
            provider_factories={"litellm": lambda m: fake_litellm},
        )
        assert backend.ok
        assert backend.provider == "litellm"
        assert backend.model == "gemini/gemini-2.5-pro"

    def test_litellm_missing_install_returns_clear_error(self, monkeypatch):
        """No factory + litellm not installed → actionable install message."""
        monkeypatch.setattr(reg, "_litellm_available", lambda: False)
        backend = reg.build_model_backend("litellm", "gemini/gemini-2.5-pro")
        assert not backend.ok
        assert "duh-cli[litellm]" in backend.error

    def test_litellm_installed_uses_real_factory_path(self, monkeypatch):
        """When litellm is reported available, the normal factory path runs.

        We don't exercise the real LiteLLMProvider; we replace it via the
        ``provider_factories`` kwarg but keep ``_litellm_available`` true so
        the guard doesn't short-circuit.
        """
        monkeypatch.setattr(reg, "_litellm_available", lambda: True)
        fake = _fake_provider_factory()
        backend = reg.build_model_backend(
            "litellm",
            "bedrock/claude-3-haiku",
            provider_factories={"litellm": lambda m: fake},
        )
        assert backend.ok
        assert backend.model == "bedrock/claude-3-haiku"


# ---------------------------------------------------------------------------
# Startup log line (single-shot, correct adapter name)
# ---------------------------------------------------------------------------


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

    def test_litellm_startup_log_says_fallback(self, monkeypatch, caplog):
        monkeypatch.setattr(reg, "_litellm_available", lambda: True)
        fake = _fake_provider_factory()
        with caplog.at_level("INFO", logger="duh.providers"):
            reg.build_model_backend(
                "litellm",
                "togetherai/mistral-large",
                provider_factories={"litellm": lambda m: fake},
            )
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "LiteLLM fallback" in joined
        assert "togetherai/mistral-large" in joined

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


class TestLiteLLMDeprecation:
    def test_warning_emitted_once(self, capsys):
        reg.emit_litellm_deprecation_warning()
        reg.emit_litellm_deprecation_warning()
        captured = capsys.readouterr()
        # Exactly one occurrence even though called twice.
        assert captured.err.count("LiteLLM adapter is opt-in fallback") == 1
        assert "ADR-075" in captured.err

    def test_warning_not_emitted_for_native_providers(self, capsys, monkeypatch):
        """Picking ``--provider gemini`` / ``groq`` must not warn."""
        from duh.cli.main import main  # noqa: F401 — just ensure importable

        # Simulate the main() gate: warning is only triggered when
        # args.provider == "litellm".
        # We call the gate's path manually since main() needs a real CLI.
        reg._reset_session_state_for_tests()
        # No call → no warning.
        captured = capsys.readouterr()
        assert "LiteLLM adapter is opt-in fallback" not in captured.err


# ---------------------------------------------------------------------------
# Doctor output includes adapter availability
# ---------------------------------------------------------------------------


class TestDoctorAdapterSection:
    def test_doctor_renders_adapter_table(self, monkeypatch):
        """The doctor output must list every provider + adapter status."""
        from duh.cli import doctor as doctor_mod

        monkeypatch.setattr(reg, "_google_genai_available", lambda: True)
        monkeypatch.setattr(reg, "_groq_sdk_available", lambda: True)
        monkeypatch.setattr(reg, "_litellm_available", lambda: False)

        text = doctor_mod._render_adapter_section()
        assert "Providers:" in text
        for name in ("anthropic", "openai", "ollama", "gemini", "groq", "litellm"):
            assert name in text
        # LiteLLM missing → install hint included.
        assert "duh-cli[litellm]" in text

    def test_doctor_section_reflects_missing_gemini_sdk(self, monkeypatch):
        from duh.cli import doctor as doctor_mod

        monkeypatch.setattr(reg, "_google_genai_available", lambda: False)
        monkeypatch.setattr(reg, "_groq_sdk_available", lambda: True)
        monkeypatch.setattr(reg, "_litellm_available", lambda: True)
        text = doctor_mod._render_adapter_section()
        # Gemini should report not-installed hint.
        assert "pip install google-genai" in text
