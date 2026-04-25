"""Tests for the drift-risk consolidation refactor of ``duh.providers.registry``.

Covers the new single-source-of-truth tables added to consolidate facts
that were previously duplicated across 3-5 files:

* ``_PROVIDER_PREFIX_MAP`` — provider lookup by model-name prefix
* ``ModelAliases`` — canonical default model names
* ``DEFAULT_MODELS`` + ``get_default_model`` — per-provider defaults
* ``PROVIDER_ENV_VARS`` + ``get_api_key`` — env var registry
* ``strip_provider_prefix`` — shared namespace strip helper

Plus the slash-command parity assertion that the canonical ``SLASH_COMMANDS``
and the dispatcher ``_HANDLERS`` table stay in sync (no orphans either way).
"""

from __future__ import annotations

import pytest

from duh.providers.registry import (
    DEFAULT_MODELS,
    ModelAliases,
    OPENAI_CODEX_MODEL_HINTS,
    PROVIDER_ENV_VARS,
    _PROVIDER_PREFIX_MAP,
    get_api_key,
    get_default_model,
    infer_provider_from_model,
    is_gemini_model,
    strip_provider_prefix,
)


# ---------------------------------------------------------------------------
# _PROVIDER_PREFIX_MAP
# ---------------------------------------------------------------------------


class TestProviderPrefixMap:
    def test_map_contains_gemini_entries(self) -> None:
        providers = {p for _, p in _PROVIDER_PREFIX_MAP}
        assert "gemini" in providers

    def test_map_contains_native_provider_entries(self) -> None:
        providers = {p for _, p in _PROVIDER_PREFIX_MAP}
        for native in ("deepseek", "mistral", "qwen", "together"):
            assert native in providers

    def test_is_gemini_model_handles_both_prefixes(self) -> None:
        assert is_gemini_model("gemini/gemini-2.5-flash")
        assert is_gemini_model("gemini-2.5-pro")
        assert not is_gemini_model("claude-sonnet-4-6")
        assert not is_gemini_model(None)
        assert not is_gemini_model("")

    def test_infer_provider_uses_prefix_map_first(self) -> None:
        # Gemini should win over "/" LiteLLM fallback when SDK is present
        # (test environment has google.genai installed).
        pytest.importorskip("google.genai")
        assert infer_provider_from_model("gemini/gemini-2.5-flash") == "gemini"


class TestStripProviderPrefix:
    def test_strips_gemini_namespace(self) -> None:
        assert strip_provider_prefix("gemini/gemini-2.5-pro") == "gemini-2.5-pro"


    def test_preserves_bare_gemini_name(self) -> None:
        # ``gemini-`` alone is NOT a namespace — the Google API accepts
        # ``gemini-2.5-pro`` as a canonical model id.
        assert strip_provider_prefix("gemini-2.5-pro") == "gemini-2.5-pro"

    def test_noop_on_unprefixed_names(self) -> None:
        assert strip_provider_prefix("claude-sonnet-4-6") == "claude-sonnet-4-6"
        assert strip_provider_prefix("gpt-4o") == "gpt-4o"

    def test_empty_string_returns_empty(self) -> None:
        assert strip_provider_prefix("") == ""


# ---------------------------------------------------------------------------
# ModelAliases / DEFAULT_MODELS / get_default_model
# ---------------------------------------------------------------------------


class TestModelAliases:
    def test_chatgpt_codex_model_matches_auth_module(self) -> None:
        from duh.auth.openai_chatgpt import OPENAI_CHATGPT_MODELS
        # The head of the auth-module Codex list must equal the
        # canonical alias — both sides rely on this invariant.
        assert OPENAI_CHATGPT_MODELS[0] == ModelAliases.CHATGPT_CODEX_MODEL

    def test_codex_hints_alias_points_to_same_tuple(self) -> None:
        assert OPENAI_CODEX_MODEL_HINTS == ModelAliases.CHATGPT_CODEX_HINTS

    def test_default_models_keys_cover_expected_providers(self) -> None:
        expected = {
            "anthropic", "openai", "gemini", "deepseek", "ollama",
            "deepseek", "mistral", "qwen", "together",
        }
        assert expected.issubset(set(DEFAULT_MODELS))

    def test_get_default_model_returns_canonical_names(self) -> None:
        assert get_default_model("anthropic") == ModelAliases.ANTHROPIC_DEFAULT
        assert get_default_model("openai") == ModelAliases.OPENAI_DEFAULT
        assert get_default_model("gemini") == ModelAliases.GEMINI_DEFAULT
        assert get_default_model("deepseek") == "deepseek-chat"
        assert get_default_model("ollama") == ModelAliases.OLLAMA_DEFAULT

    def test_get_default_model_returns_empty_for_unknown(self) -> None:
        assert get_default_model("nonsense-provider") == ""

    def test_native_provider_defaults(self) -> None:
        # Each native provider declares a default model in DEFAULT_MODELS.
        # ADR-027: D.U.H. uses native adapters per provider.
        assert get_default_model("deepseek") == "deepseek-chat"
        assert get_default_model("mistral").startswith("mistral-")
        assert get_default_model("qwen").startswith("qwen3")
        assert get_default_model("together").startswith("meta-llama/")


# ---------------------------------------------------------------------------
# PROVIDER_ENV_VARS / get_api_key
# ---------------------------------------------------------------------------


class TestProviderEnvVars:
    def test_map_lists_every_expected_provider(self) -> None:
        for provider in ("anthropic", "openai", "gemini", "deepseek", "cerebras"):
            assert provider in PROVIDER_ENV_VARS

    def test_gemini_supports_google_api_key_fallback(self) -> None:
        # Order matters: GEMINI_API_KEY is checked before GOOGLE_API_KEY.
        assert PROVIDER_ENV_VARS["gemini"] == ("GEMINI_API_KEY", "GOOGLE_API_KEY")

    def test_get_api_key_first_set_wins(self, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "primary")
        monkeypatch.setenv("GOOGLE_API_KEY", "secondary")
        assert get_api_key("gemini") == "primary"

    def test_get_api_key_falls_back_to_next(self, monkeypatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "fallback-val")
        assert get_api_key("gemini") == "fallback-val"

    def test_get_api_key_empty_when_none_set(self, monkeypatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        assert get_api_key("gemini") == ""

    def test_get_api_key_unknown_provider(self) -> None:
        assert get_api_key("no-such-provider") == ""


# ---------------------------------------------------------------------------
# Slash-command parity
# ---------------------------------------------------------------------------


class TestSlashCommandParity:
    """Every handler in ``SlashDispatcher._HANDLERS`` has a ``SLASH_COMMANDS``
    help entry and vice versa — no orphans in either direction.
    """

    def test_no_orphan_commands(self) -> None:
        from duh.cli.slash_commands import SLASH_COMMANDS, SlashDispatcher
        handler_keys = set(SlashDispatcher._HANDLERS)
        help_keys = set(SLASH_COMMANDS)
        # Handlers without help entries -> missing docs
        missing_help = handler_keys - help_keys
        # Help entries without handlers -> dead commands
        missing_handlers = help_keys - handler_keys
        assert not missing_help, (
            f"Handlers without SLASH_COMMANDS entries: {missing_help}"
        )
        assert not missing_handlers, (
            f"SLASH_COMMANDS entries without handlers: {missing_handlers}"
        )

    def test_repl_reexports_slash_commands(self) -> None:
        from duh.cli.repl import SLASH_COMMANDS as repl_sc
        from duh.cli.slash_commands import SLASH_COMMANDS as sc_sc
        # They must be the SAME dict, not just equal — no copying.
        assert repl_sc is sc_sc

    def test_slash_help_exposed(self) -> None:
        from duh.cli.slash_commands import SLASH_COMMANDS, __slash_help__
        # The generated help map mirrors the canonical dict.
        assert __slash_help__ == SLASH_COMMANDS
