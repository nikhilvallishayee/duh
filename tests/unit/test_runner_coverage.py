"""Extended coverage tests for duh/cli/runner.py.

Targets the uncovered lines: provider auto-detection, unknown provider error,
permission-mode handling, stream-json output, brief mode wiring,
error interpretation, event summarization, and serialization.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli import exit_codes
from duh.cli.runner import (
    BRIEF_INSTRUCTION,
    SYSTEM_PROMPT,
    _interpret_error,
    _make_serializable,
    _summarize_event,
    run_print_mode,
)
from duh.kernel.messages import Message, TextBlock, ToolUseBlock


# ===================================================================
# Helper: build a Namespace that matches what the parser produces
# ===================================================================

def _make_args(**overrides) -> argparse.Namespace:
    """Build an args Namespace matching parser defaults + overrides."""
    defaults = dict(
        prompt="test prompt",
        debug=False,
        verbose=False,
        provider="anthropic",
        model=None,
        fallback_model=None,
        max_turns=10,
        max_cost=None,
        dangerously_skip_permissions=True,
        permission_mode=None,
        output_format="text",
        input_format="text",
        system_prompt=None,
        tool_choice=None,
        continue_session=False,
        resume=None,
        brief=False,
        log_json=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _infra_patches(mock_engine):
    """Return a dict of context managers patching runner's heavy infrastructure.

    runner.py uses local imports inside run_print_mode, so we must patch at source.
    Top-level imports (Engine, AnthropicProvider, etc.) are patched on duh.cli.runner.
    Local imports are patched at their source modules.
    """
    return {
        "engine": patch("duh.cli.runner.Engine", return_value=mock_engine),
        "anthropic": patch("duh.cli.runner.AnthropicProvider"),
        "executor": patch("duh.cli.runner.NativeExecutor"),
        "tools": patch("duh.cli.runner.get_all_tools", return_value=[]),
        # Local imports — patched at source
        "skills": patch("duh.kernel.skill.load_all_skills", return_value=[]),
        "discover_plugins": patch("duh.plugins.discover_plugins", return_value=[]),
        "plugin_registry": patch("duh.plugins.PluginRegistry", return_value=MagicMock(plugin_tools=[])),
        "load_instructions": patch("duh.config.load_instructions", return_value=[]),
        "memory_store": patch("duh.adapters.memory_store.FileMemoryStore", return_value=MagicMock()),
        "build_memory": patch("duh.kernel.memory.build_memory_prompt", return_value=""),
        "git_context": patch("duh.kernel.git_context.get_git_context", return_value=""),
        "git_warnings": patch("duh.kernel.git_context.get_git_warnings", return_value=[]),
        "templates": patch("duh.kernel.templates.load_all_templates", return_value=[]),
        "load_config": patch("duh.config.load_config", return_value=MagicMock(mcp_servers=None, hooks=None)),
        "hook_registry": patch("duh.hooks.HookRegistry", return_value=MagicMock()),
        "execute_hooks": patch("duh.hooks.execute_hooks", new_callable=AsyncMock),
        "file_store": patch("duh.adapters.file_store.FileStore", return_value=MagicMock()),
        "compactor": patch("duh.adapters.simple_compactor.SimpleCompactor", return_value=MagicMock()),
    }


class _InfraContext:
    """Helper to enter/exit all infra patches at once."""

    def __init__(self, mock_engine, extra_patches=None):
        self._patches = _infra_patches(mock_engine)
        self._extra = extra_patches or {}
        self._entered = {}

    def __enter__(self):
        for name, p in {**self._patches, **self._extra}.items():
            self._entered[name] = p.__enter__()
        return self._entered

    def __exit__(self, *args):
        for p in {**self._patches, **self._extra}.values():
            p.__exit__(None, None, None)


def _make_mock_engine(events=None):
    """Build a MagicMock engine that yields the given events."""
    if events is None:
        events = [{"type": "done", "stop_reason": "end_turn"}]

    async def fake_run(prompt, **kwargs):
        for e in events:
            yield e

    mock = MagicMock()
    mock.run = fake_run
    mock.session_id = "test-session"
    mock.turn_count = 1
    mock.total_input_tokens = 0
    mock.total_output_tokens = 0
    mock._messages = []
    return mock


# ===================================================================
# Provider auto-detection
# ===================================================================

class TestProviderAutoDetection:
    @pytest.mark.asyncio
    async def test_anthropic_auto_detected_from_env(self, monkeypatch, capsys):
        """ANTHROPIC_API_KEY in env → provider='anthropic'."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        events = [
            {"type": "text_delta", "text": "hi"},
            {"type": "done", "stop_reason": "end_turn"},
        ]
        mock_engine = _make_mock_engine(events)
        args = _make_args(provider=None)

        with _InfraContext(mock_engine) as mocks:
            code = await run_print_mode(args)

        assert code == 0
        mocks["anthropic"].assert_called_once()

    @pytest.mark.asyncio
    async def test_openai_auto_detected_from_env(self, monkeypatch, capsys):
        """OPENAI_API_KEY in env (no ANTHROPIC) → provider='openai'."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        mock_engine = _make_mock_engine()
        mock_openai = MagicMock()

        args = _make_args(provider=None)

        extra = {"openai_prov": patch("duh.adapters.openai.OpenAIProvider", return_value=mock_openai)}
        with _InfraContext(mock_engine, extra_patches=extra):
            code = await run_print_mode(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_ollama_auto_detected_as_fallback(self, monkeypatch, capsys):
        """No API keys but Ollama responds → provider='ollama'."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        mock_engine = _make_mock_engine()
        mock_httpx_response = MagicMock()
        mock_httpx_response.status_code = 200
        mock_ollama = MagicMock()

        args = _make_args(provider=None)

        extra = {
            "httpx": patch("httpx.get", return_value=mock_httpx_response),
            "ollama_prov": patch("duh.adapters.ollama.OllamaProvider", return_value=mock_ollama),
        }
        with _InfraContext(mock_engine, extra_patches=extra):
            code = await run_print_mode(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_no_provider_available(self, monkeypatch, capsys):
        """No keys, no Ollama → error to stderr, PROVIDER_ERROR."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        args = _make_args(provider=None)

        with patch("httpx.get", side_effect=Exception("no ollama")):
            code = await run_print_mode(args)

        assert code == exit_codes.PROVIDER_ERROR
        captured = capsys.readouterr()
        assert "No provider available" in captured.err

    @pytest.mark.asyncio
    async def test_unknown_provider_error(self, capsys):
        """Explicit unknown provider → error to stderr, PROVIDER_ERROR."""
        args = _make_args(provider="google")

        code = await run_print_mode(args)

        assert code == exit_codes.PROVIDER_ERROR
        captured = capsys.readouterr()
        assert "Unknown provider" in captured.err


# ===================================================================
# Permission mode
# ===================================================================

class TestPermissionMode:
    @pytest.mark.asyncio
    async def test_bypass_permissions_uses_auto_approver(self, monkeypatch):
        """--permission-mode bypassPermissions should use AutoApprover."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _make_mock_engine()

        captured_approver = {}
        from duh.kernel.deps import Deps

        def capture_deps(*a, **kw):
            deps = Deps(*a, **kw)
            captured_approver["approve"] = deps.approve
            return deps

        args = _make_args(
            permission_mode="bypassPermissions",
            dangerously_skip_permissions=False,
        )

        extra = {"deps": patch("duh.cli.runner.Deps", side_effect=capture_deps)}
        with _InfraContext(mock_engine, extra_patches=extra):
            code = await run_print_mode(args)

        assert code == 0
        assert captured_approver.get("approve") is not None

    @pytest.mark.asyncio
    async def test_dontask_permission_uses_auto_approver(self, monkeypatch):
        """--permission-mode dontAsk should also use AutoApprover."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _make_mock_engine()

        captured_approver = {}
        from duh.kernel.deps import Deps

        def capture_deps(*a, **kw):
            deps = Deps(*a, **kw)
            captured_approver["approve"] = deps.approve
            return deps

        args = _make_args(
            permission_mode="dontAsk",
            dangerously_skip_permissions=False,
        )

        extra = {"deps": patch("duh.cli.runner.Deps", side_effect=capture_deps)}
        with _InfraContext(mock_engine, extra_patches=extra):
            code = await run_print_mode(args)

        assert code == 0
        assert captured_approver.get("approve") is not None

    @pytest.mark.asyncio
    async def test_default_permission_uses_interactive_approver(self, monkeypatch):
        """No permission flag → InteractiveApprover."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _make_mock_engine()

        captured_approver = {}
        from duh.kernel.deps import Deps

        def capture_deps(*a, **kw):
            deps = Deps(*a, **kw)
            captured_approver["approve"] = deps.approve
            return deps

        args = _make_args(
            permission_mode=None,
            dangerously_skip_permissions=False,
        )

        extra = {"deps": patch("duh.cli.runner.Deps", side_effect=capture_deps)}
        with _InfraContext(mock_engine, extra_patches=extra):
            code = await run_print_mode(args)

        assert code == 0
        # InteractiveApprover.check is a different function from AutoApprover.check
        approve_fn = captured_approver.get("approve")
        assert approve_fn is not None
        # Check it's from InteractiveApprover (bound method)
        from duh.adapters.approvers import InteractiveApprover
        assert hasattr(approve_fn, "__self__")
        assert isinstance(approve_fn.__self__, InteractiveApprover)


# ===================================================================
# Output format: stream-json
# ===================================================================

class TestStreamJsonOutput:
    @pytest.mark.asyncio
    async def test_stream_json_output_emits_ndjson(self, monkeypatch, capsys):
        """--output-format stream-json should emit events via ndjson_write."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        events = [
            {"type": "text_delta", "text": "hello"},
            {"type": "done", "stop_reason": "end_turn"},
        ]
        mock_engine = _make_mock_engine(events)

        captured_ndjson: list[dict] = []

        def capture_ndjson(obj, file=None):
            captured_ndjson.append(obj)

        args = _make_args(output_format="stream-json")

        extra = {"ndjson": patch("duh.cli.ndjson.ndjson_write", side_effect=capture_ndjson)}
        with _InfraContext(mock_engine, extra_patches=extra):
            code = await run_print_mode(args)

        assert code == 0
        types = [e.get("type") for e in captured_ndjson]
        assert "text_delta" in types

    @pytest.mark.asyncio
    async def test_stream_json_error_event(self, monkeypatch, capsys):
        """stream-json mode should detect error events."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        events = [
            {"type": "error", "error": "api failure"},
            {"type": "done", "stop_reason": "error"},
        ]
        mock_engine = _make_mock_engine(events)

        captured_ndjson: list[dict] = []

        def capture_ndjson(obj, file=None):
            captured_ndjson.append(obj)

        args = _make_args(output_format="stream-json")

        extra = {"ndjson": patch("duh.cli.ndjson.ndjson_write", side_effect=capture_ndjson)}
        with _InfraContext(mock_engine, extra_patches=extra):
            code = await run_print_mode(args)

        assert code == exit_codes.ERROR  # generic "api failure" → ERROR


# ===================================================================
# Brief mode system prompt wiring
# ===================================================================

class TestBriefModeWiring:
    @pytest.mark.asyncio
    async def test_brief_appends_instruction_to_system_prompt(self, monkeypatch):
        """--brief should append BRIEF_INSTRUCTION to the system prompt."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _make_mock_engine()

        from duh.kernel.engine import EngineConfig
        captured_config = {}

        def capture_engine(*a, **kw):
            if "config" in kw:
                captured_config["config"] = kw["config"]
            return mock_engine

        args = _make_args(brief=True)

        extra = {"engine_capture": patch("duh.cli.runner.Engine", side_effect=capture_engine)}
        # We need to NOT use the default engine patch, so build infra manually
        patches = _infra_patches(mock_engine)
        # Remove the engine patch, use our capture version instead
        del patches["engine"]
        ctx = {}

        entered = []
        try:
            for name, p in patches.items():
                entered.append(p)
                p.__enter__()
            extra_p = patch("duh.cli.runner.Engine", side_effect=capture_engine)
            entered.append(extra_p)
            extra_p.__enter__()

            code = await run_print_mode(args)
        finally:
            for p in reversed(entered):
                p.__exit__(None, None, None)

        assert code == 0
        config = captured_config.get("config")
        assert config is not None
        assert BRIEF_INSTRUCTION in config.system_prompt


# ===================================================================
# _interpret_error
# ===================================================================

class TestInterpretError:
    def test_credit_balance_error(self):
        hint = _interpret_error("Your credit balance is too low to proceed")
        assert "credits" in hint.lower()

    def test_invalid_api_key_error(self):
        hint = _interpret_error("invalid x-api-key provided")
        assert "API key" in hint

    def test_authentication_error(self):
        hint = _interpret_error("authentication_error: bad key")
        assert "ANTHROPIC_API_KEY" in hint

    def test_rate_limit_error(self):
        hint = _interpret_error("rate_limit exceeded for this key")
        assert "Rate limited" in hint

    def test_overloaded_error(self):
        hint = _interpret_error("API is overloaded, try again")
        assert "overloaded" in hint.lower()

    def test_prompt_too_long_error(self):
        hint = _interpret_error("prompt is too long for context window")
        assert "context window" in hint

    def test_no_authentication_error(self):
        hint = _interpret_error("Could not resolve authentication method")
        assert "ANTHROPIC_API_KEY" in hint

    def test_unknown_error_passthrough(self):
        hint = _interpret_error("totally unknown error xyz")
        assert hint == "totally unknown error xyz"

    def test_case_insensitive_matching(self):
        hint = _interpret_error("CREDIT BALANCE IS TOO LOW")
        assert "credits" in hint.lower()


# ===================================================================
# _summarize_event
# ===================================================================

class TestSummarizeEvent:
    def test_text_delta(self):
        s = _summarize_event({"type": "text_delta", "text": "hello world"})
        assert "text_delta" in s
        assert "hello world" in s

    def test_tool_use(self):
        s = _summarize_event({"type": "tool_use", "name": "Read", "input": {"path": "/x"}})
        assert "tool_use" in s
        assert "Read" in s

    def test_tool_result(self):
        s = _summarize_event({"type": "tool_result", "is_error": True, "output": "fail"})
        assert "tool_result" in s
        assert "True" in s

    def test_assistant_with_message(self):
        msg = Message(role="assistant", content="hello there!")
        s = _summarize_event({"type": "assistant", "message": msg})
        assert "assistant" in s
        assert "hello" in s

    def test_assistant_without_message(self):
        s = _summarize_event({"type": "assistant", "message": "not a Message"})
        assert "assistant" in s
        assert "?" in s

    def test_error(self):
        s = _summarize_event({"type": "error", "error": "bad things happened"})
        assert "error" in s
        assert "bad things" in s

    def test_unknown_type(self):
        s = _summarize_event({"type": "custom_thing", "data": "abc"})
        assert "custom_thing" in s


# ===================================================================
# _make_serializable
# ===================================================================

class TestMakeSerializable:
    def test_plain_values_pass_through(self):
        event = {"type": "text", "count": 5, "flag": True, "items": [1, 2]}
        result = _make_serializable(event)
        assert result == event

    def test_dataclass_converted_to_dict(self):
        msg = Message(role="assistant", content="hi", id="m1")
        event = {"type": "assistant", "message": msg}
        result = _make_serializable(event)
        assert isinstance(result["message"], dict)
        assert result["message"]["role"] == "assistant"
        assert result["message"]["content"] == "hi"

    def test_non_serializable_object_stringified(self):
        event = {"type": "thing", "obj": object()}
        result = _make_serializable(event)
        assert isinstance(result["obj"], str)

    def test_none_passes_through(self):
        event = {"type": "x", "val": None}
        result = _make_serializable(event)
        assert result["val"] is None


# ===================================================================
# Provider-specific model defaults and error paths
# ===================================================================

class TestProviderModelDefaults:
    @pytest.mark.asyncio
    async def test_anthropic_default_model(self, monkeypatch):
        """Anthropic with no --model should default to claude-sonnet-4-6."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        mock_engine = _make_mock_engine()
        captured_model = {}

        def capture_provider(**kw):
            captured_model["model"] = kw.get("model")
            return MagicMock()

        args = _make_args(provider="anthropic", model=None)

        patches = _infra_patches(mock_engine)
        del patches["anthropic"]
        entered = []
        try:
            for name, p in patches.items():
                entered.append(p)
                p.__enter__()
            p_prov = patch("duh.cli.runner.AnthropicProvider", side_effect=capture_provider)
            entered.append(p_prov)
            p_prov.__enter__()

            await run_print_mode(args)
        finally:
            for p in reversed(entered):
                p.__exit__(None, None, None)

        assert captured_model["model"] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_openai_no_key_error(self, monkeypatch, capsys):
        """Explicit openai provider but no key → PROVIDER_ERROR."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        args = _make_args(provider="openai")
        code = await run_print_mode(args)

        assert code == exit_codes.PROVIDER_ERROR
        captured = capsys.readouterr()
        assert "OPENAI_API_KEY" in captured.err

    @pytest.mark.asyncio
    async def test_anthropic_no_key_error(self, monkeypatch, capsys):
        """Explicit anthropic provider but no key → PROVIDER_ERROR."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        args = _make_args(provider="anthropic")
        code = await run_print_mode(args)

        assert code == exit_codes.PROVIDER_ERROR
        captured = capsys.readouterr()
        assert "ANTHROPIC_API_KEY" in captured.err


# ===================================================================
# Text-mode event handling branches
# ===================================================================

class TestTextModeEvents:
    """Cover the text-mode (default output_format) event handling branches."""

    @pytest.mark.asyncio
    async def test_tool_use_event_prints_to_stderr(self, monkeypatch, capsys):
        """tool_use events should print tool name to stderr."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        events = [
            {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/x"}},
            {"type": "done", "stop_reason": "end_turn"},
        ]
        mock_engine = _make_mock_engine(events)
        args = _make_args()

        with _InfraContext(mock_engine):
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        assert "Read" in captured.err

    @pytest.mark.asyncio
    async def test_tool_result_error_prints_to_stderr(self, monkeypatch, capsys):
        """tool_result with is_error=True should print to stderr."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        events = [
            {"type": "tool_result", "is_error": True, "output": "file not found"},
            {"type": "done", "stop_reason": "end_turn"},
        ]
        mock_engine = _make_mock_engine(events)
        args = _make_args()

        with _InfraContext(mock_engine):
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        assert "file not found" in captured.err

    @pytest.mark.asyncio
    async def test_tool_result_success_no_output_in_normal_mode(self, monkeypatch, capsys):
        """Successful tool_result should not print in non-debug mode."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        events = [
            {"type": "tool_result", "is_error": False, "output": "file data"},
            {"type": "done", "stop_reason": "end_turn"},
        ]
        mock_engine = _make_mock_engine(events)
        args = _make_args(debug=False)

        with _InfraContext(mock_engine):
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        # Success tool results not shown in non-debug mode
        assert "file data" not in captured.err

    @pytest.mark.asyncio
    async def test_assistant_error_message_prints_hint(self, monkeypatch, capsys):
        """An assistant message with is_error in metadata → error hint."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        error_msg = Message(
            role="assistant",
            content="credit balance is too low",
            metadata={"is_error": True},
        )
        events = [
            {"type": "assistant", "message": error_msg},
            {"type": "done", "stop_reason": "error"},
        ]
        mock_engine = _make_mock_engine(events)
        args = _make_args()

        with _InfraContext(mock_engine):
            code = await run_print_mode(args)

        assert code == exit_codes.PROVIDER_ERROR  # "credit balance is too low"
        captured = capsys.readouterr()
        assert "credits" in captured.err.lower()

    @pytest.mark.asyncio
    async def test_thinking_delta_in_debug_mode(self, monkeypatch, capsys):
        """thinking_delta events should print to stderr in debug mode."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        events = [
            {"type": "thinking_delta", "text": "Let me think about this..."},
            {"type": "done", "stop_reason": "end_turn"},
        ]
        mock_engine = _make_mock_engine(events)
        args = _make_args(debug=True)

        with _InfraContext(mock_engine):
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        assert "Let me think" in captured.err

    @pytest.mark.asyncio
    async def test_done_event_debug_logging(self, monkeypatch, capsys):
        """done event should log turns/reason in debug mode."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        events = [
            {"type": "done", "turns": 3, "stop_reason": "end_turn"},
        ]
        mock_engine = _make_mock_engine(events)
        args = _make_args(debug=True)

        with _InfraContext(mock_engine):
            code = await run_print_mode(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_stream_json_assistant_is_error(self, monkeypatch, capsys):
        """stream-json mode: assistant message with is_error sets had_error."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        error_msg = Message(
            role="assistant",
            content="rate_limit exceeded",
            metadata={"is_error": True},
        )
        events = [
            {"type": "assistant", "message": error_msg},
            {"type": "done", "stop_reason": "error"},
        ]
        mock_engine = _make_mock_engine(events)

        captured_ndjson: list[dict] = []

        def capture_ndjson(obj, file=None):
            captured_ndjson.append(obj)

        args = _make_args(output_format="stream-json")

        extra = {"ndjson": patch("duh.cli.ndjson.ndjson_write", side_effect=capture_ndjson)}
        with _InfraContext(mock_engine, extra_patches=extra):
            code = await run_print_mode(args)

        assert code == exit_codes.PROVIDER_ERROR  # "rate_limit exceeded"


# ===================================================================
# DUH_MAX_COST env var
# ===================================================================

class TestMaxCostEnvVar:
    @pytest.mark.asyncio
    async def test_max_cost_from_env(self, monkeypatch):
        """DUH_MAX_COST env var should set max_cost on EngineConfig."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("DUH_MAX_COST", "2.50")

        mock_engine = _make_mock_engine()
        captured_config = {}

        def capture_engine(*a, **kw):
            if "config" in kw:
                captured_config["config"] = kw["config"]
            return mock_engine

        args = _make_args(max_cost=None)

        patches = _infra_patches(mock_engine)
        del patches["engine"]

        entered = []
        try:
            for name, p in patches.items():
                entered.append(p)
                p.__enter__()
            extra_p = patch("duh.cli.runner.Engine", side_effect=capture_engine)
            entered.append(extra_p)
            extra_p.__enter__()

            code = await run_print_mode(args)
        finally:
            for p in reversed(entered):
                p.__exit__(None, None, None)

        assert code == 0
        config = captured_config.get("config")
        assert config is not None
        assert config.max_cost == 2.50

    @pytest.mark.asyncio
    async def test_max_cost_invalid_env_ignored(self, monkeypatch):
        """DUH_MAX_COST with non-numeric value should be silently ignored."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("DUH_MAX_COST", "not-a-number")

        mock_engine = _make_mock_engine()
        captured_config = {}

        def capture_engine(*a, **kw):
            if "config" in kw:
                captured_config["config"] = kw["config"]
            return mock_engine

        args = _make_args(max_cost=None)

        patches = _infra_patches(mock_engine)
        del patches["engine"]

        entered = []
        try:
            for name, p in patches.items():
                entered.append(p)
                p.__enter__()
            extra_p = patch("duh.cli.runner.Engine", side_effect=capture_engine)
            entered.append(extra_p)
            extra_p.__enter__()

            code = await run_print_mode(args)
        finally:
            for p in reversed(entered):
                p.__exit__(None, None, None)

        assert code == 0
        config = captured_config.get("config")
        assert config.max_cost is None


# ===================================================================
# JSON output format
# ===================================================================

class TestJsonOutputFormat:
    @pytest.mark.asyncio
    async def test_json_output_collects_events(self, monkeypatch, capsys):
        """--output-format json should collect events and write JSON array."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        events = [
            {"type": "text_delta", "text": "hi"},
            {"type": "done", "stop_reason": "end_turn"},
        ]
        mock_engine = _make_mock_engine(events)
        args = _make_args(output_format="json")

        with _InfraContext(mock_engine):
            code = await run_print_mode(args)

        assert code == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, list)
        types = [e["type"] for e in parsed]
        assert "text_delta" in types
