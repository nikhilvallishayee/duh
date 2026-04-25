"""Tests for duh.kernel.model_caps — model capability detection and prompt rebuild."""

from __future__ import annotations

import pytest

from duh.kernel.model_caps import (
    ModelCapabilities,
    get_capabilities,
    model_context_block,
    rebuild_system_prompt,
)


# ── get_capabilities ────────────────────────────────────────────────


class TestGetCapabilities:
    """Prefix and substring matching for model capability lookup."""

    def test_claude_opus_4_6(self) -> None:
        caps = get_capabilities("claude-opus-4-6")
        assert caps.context_window == 1_000_000
        assert caps.supports_thinking is True
        assert caps.supports_vision is True
        assert caps.supports_cache_control is True
        assert caps.max_output_tokens == 16384

    def test_claude_sonnet_4_6(self) -> None:
        caps = get_capabilities("claude-sonnet-4-6")
        assert caps.context_window == 1_000_000
        assert caps.supports_thinking is True

    def test_claude_opus_4(self) -> None:
        caps = get_capabilities("claude-opus-4-20250514")
        assert caps.context_window == 200_000
        assert caps.supports_thinking is True
        assert caps.supports_cache_control is True

    def test_claude_sonnet_4(self) -> None:
        caps = get_capabilities("claude-sonnet-4-20250514")
        assert caps.context_window == 200_000
        assert caps.supports_thinking is True

    def test_claude_haiku_4(self) -> None:
        caps = get_capabilities("claude-haiku-4-5-20251001")
        assert caps.context_window == 200_000
        assert caps.supports_thinking is False
        assert caps.supports_vision is True
        assert caps.max_output_tokens == 8192

    def test_claude_haiku_3_5(self) -> None:
        """claude-haiku-3-5 is Haiku 3.5 — same gen as claude-3-5-haiku."""
        caps = get_capabilities("claude-haiku-3-5")
        assert caps.context_window == 200_000
        assert caps.supports_thinking is False
        assert caps.max_output_tokens == 8192

    def test_claude_haiku_3(self) -> None:
        caps = get_capabilities("claude-3-haiku")
        assert caps.context_window == 200_000
        assert caps.max_output_tokens == 4096

    def test_claude_3_5_haiku_legacy_name(self) -> None:
        caps = get_capabilities("claude-3-5-haiku-20241022")
        assert caps.context_window == 200_000

    def test_claude_3_5_sonnet_legacy_name(self) -> None:
        caps = get_capabilities("claude-3-5-sonnet-20241022")
        assert caps.context_window == 200_000
        assert caps.supports_thinking is True

    def test_claude_3_opus_legacy(self) -> None:
        caps = get_capabilities("claude-3-opus")
        assert caps.context_window == 200_000
        assert caps.supports_thinking is True

    def test_gpt_4o(self) -> None:
        caps = get_capabilities("gpt-4o")
        assert caps.context_window == 128_000
        assert caps.supports_tools is True
        assert caps.supports_thinking is False
        assert caps.supports_vision is True
        assert caps.supports_cache_control is False

    def test_gpt_4o_dated(self) -> None:
        caps = get_capabilities("gpt-4o-2024-08-06")
        assert caps.context_window == 128_000

    def test_gpt_4o_mini(self) -> None:
        caps = get_capabilities("gpt-4o-mini")
        assert caps.context_window == 128_000
        assert caps.supports_tools is True

    def test_gemini_15_pro(self) -> None:
        caps = get_capabilities("gemini-1.5-pro")
        assert caps.context_window == 2_000_000
        assert caps.supports_tools is True
        assert caps.supports_vision is True

    def test_gemini_15_flash(self) -> None:
        caps = get_capabilities("gemini-1.5-flash")
        assert caps.context_window == 1_048_576
        assert caps.supports_tools is True

    def test_gemini_20_flash(self) -> None:
        caps = get_capabilities("gemini/gemini-2.0-flash-exp")
        assert caps.context_window == 1_048_576
        assert caps.supports_tools is True

    def test_gemini_25_pro(self) -> None:
        caps = get_capabilities("gemini/gemini-2.5-pro")
        assert caps.context_window == 2_000_000
        assert caps.supports_thinking is True
        assert caps.supports_cache_control is True

    def test_gemini_25_flash(self) -> None:
        caps = get_capabilities("gemini/gemini-2.5-flash")
        assert caps.context_window == 1_048_576
        assert caps.supports_thinking is True




    def test_qwen25_coder_7b(self) -> None:
        caps = get_capabilities("qwen2.5-coder:7b")
        assert caps.context_window == 128_000

    def test_qwen25_coder_1_5b_small(self) -> None:
        """1.5B variant correctly gets the 32K small-context entry."""
        caps = get_capabilities("qwen2.5-coder:1.5b")
        assert caps.context_window == 32_000

    def test_deepseek_coder_v2(self) -> None:
        caps = get_capabilities("deepseek-coder-v2:lite")
        assert caps.context_window == 128_000

    def test_llama32_local(self) -> None:
        caps = get_capabilities("llama3.2:3b")
        assert caps.context_window == 128_000

    def test_ollama_model(self) -> None:
        caps = get_capabilities("ollama/llama3:8b")
        assert caps.context_window == 32_000
        assert caps.supports_tools is False
        assert caps.supports_thinking is False
        assert caps.supports_vision is False
        assert caps.max_output_tokens == 4096

    def test_llama_substring(self) -> None:
        caps = get_capabilities("llama3.1:70b")
        assert caps.context_window == 32_000
        assert caps.supports_tools is False

    def test_qwen_substring(self) -> None:
        caps = get_capabilities("qwen2.5-coder:1.5b")
        assert caps.context_window == 32_000

    def test_mistral_substring(self) -> None:
        caps = get_capabilities("mistral-7b")
        assert caps.context_window == 32_000

    def test_deepseek_coder_v2_uses_128k_not_default(self) -> None:
        """deepseek-coder-v2 now has a dedicated 128K entry (was 32K default)."""
        caps = get_capabilities("deepseek-coder-v2")
        assert caps.context_window == 128_000

    def test_unknown_model_returns_default(self) -> None:
        caps = get_capabilities("some-unknown-model-v42")
        assert caps == ModelCapabilities()
        assert caps.context_window == 200_000
        assert caps.supports_tools is True

    def test_case_insensitive(self) -> None:
        caps = get_capabilities("Claude-Opus-4-6")
        assert caps.context_window == 1_000_000

    def test_empty_string(self) -> None:
        caps = get_capabilities("")
        assert caps == ModelCapabilities()


# ── model_context_block ─────────────────────────────────────────────


class TestModelContextBlock:
    """The <model-context> block embedded in the system prompt."""

    def test_contains_model_name(self) -> None:
        block = model_context_block("claude-opus-4-6")
        assert "model: claude-opus-4-6" in block

    def test_contains_context_window(self) -> None:
        block = model_context_block("claude-opus-4-6")
        assert "context_window: 1,000,000" in block

    def test_contains_xml_tags(self) -> None:
        block = model_context_block("gpt-4o")
        assert block.startswith("<model-context>")
        assert block.endswith("</model-context>")

    def test_different_models_produce_different_blocks(self) -> None:
        opus = model_context_block("claude-opus-4-6")
        haiku = model_context_block("claude-haiku-4-5-20251001")
        assert opus != haiku
        assert "1,000,000" in opus
        assert "200,000" in haiku

    def test_supports_thinking_true_for_opus(self) -> None:
        block = model_context_block("claude-opus-4-6")
        assert "supports_thinking: true" in block

    def test_supports_thinking_false_for_haiku(self) -> None:
        block = model_context_block("claude-haiku-4-5-20251001")
        assert "supports_thinking: false" in block


# ── rebuild_system_prompt ───────────────────────────────────────────


class TestRebuildSystemPrompt:
    """System prompt rebuilding on /model switch."""

    def test_replaces_existing_block(self) -> None:
        old_block = model_context_block("claude-opus-4-6")
        prompt = f"You are helpful.\n\n{old_block}\n\nDo things."

        result = rebuild_system_prompt(prompt, "claude-opus-4-6", "claude-haiku-4-5-20251001")

        assert "model: claude-haiku-4-5-20251001" in result
        assert "model: claude-opus-4-6" not in result
        assert "You are helpful." in result
        assert "Do things." in result

    def test_appends_block_when_missing(self) -> None:
        prompt = "You are helpful.\n\nDo things."

        result = rebuild_system_prompt(prompt, "claude-opus-4-6", "claude-haiku-4-5-20251001")

        assert "model: claude-haiku-4-5-20251001" in result
        assert "<model-context>" in result
        assert "You are helpful." in result

    def test_handles_list_input(self) -> None:
        parts = ["You are helpful.", "Do things."]

        result = rebuild_system_prompt(parts, "claude-opus-4-6", "gpt-4o")

        assert "model: gpt-4o" in result
        assert "context_window: 128,000" in result
        assert "You are helpful." in result

    def test_handles_corrupted_block(self) -> None:
        """If the block exists but was edited manually, regex replacement kicks in."""
        prompt = "You are helpful.\n\n<model-context>\ngarbage\n</model-context>\n\nDo things."

        result = rebuild_system_prompt(prompt, "claude-opus-4-6", "gpt-4o-mini")

        assert "model: gpt-4o-mini" in result
        assert "garbage" not in result
        assert "context_window: 128,000" in result

    def test_idempotent_same_model(self) -> None:
        old_block = model_context_block("claude-sonnet-4-6")
        prompt = f"Base.\n\n{old_block}"

        result = rebuild_system_prompt(prompt, "claude-sonnet-4-6", "claude-sonnet-4-6")

        assert result == prompt

    def test_context_window_updates_on_switch(self) -> None:
        """The core ADR-070 bug: switching from 1M to 200K model must update context."""
        old_block = model_context_block("claude-opus-4-6")
        prompt = f"System prompt.\n\n{old_block}"

        result = rebuild_system_prompt(prompt, "claude-opus-4-6", "claude-haiku-4-5-20251001")

        assert "context_window: 200,000" in result
        assert "context_window: 1,000,000" not in result

    def test_preserves_surrounding_content(self) -> None:
        old_block = model_context_block("gpt-4o")
        prompt = f"BEFORE\n\n{old_block}\n\nAFTER"

        result = rebuild_system_prompt(prompt, "gpt-4o", "claude-opus-4-6")

        assert result.startswith("BEFORE")
        assert result.endswith("AFTER")


# ── ModelCapabilities frozen ────────────────────────────────────────


class TestModelCapabilitiesFrozen:
    """ModelCapabilities is a frozen dataclass — mutation should raise."""

    def test_immutable(self) -> None:
        caps = get_capabilities("gpt-4o")
        with pytest.raises(AttributeError):
            caps.context_window = 999  # type: ignore[misc]
