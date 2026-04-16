"""Tests for Code Quality + UX cleanup from QE Analysis #8.

Covers four independent concerns; one class per concern so failures are
easy to localise:

* ``TestChatGPTStreamRefactor`` — CQ-P4: verifies the SSE dispatch split
  (stream / _dispatch_sse_event / _build_final_response) preserves every
  observable behaviour of the old monolithic ``stream()``.
* ``TestOAuthExpiryInHealth`` — QX: ``_format_expiry_delta`` +
  ``_chatgpt_oauth_status_line`` helpers powering ``/health``.
* ``TestSecurityScanProgress`` — QX: Runner progress callback fires once
  per scanner with ``(name, current, total)``, and the CLI stderr hook
  renders a live line.
* ``TestErrorsSlashCommand`` — QX: ``/errors`` surfaces last N entries
  from the in-session error buffer populated by ``_slog_event``.
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock

import pytest

from duh.adapters.openai_chatgpt import (
    OpenAIChatGPTProvider,
    _StreamState,
    _build_request_body,
    _build_request_headers,
    _error_assistant_event,
    _parse_sse_line,
)
from duh.cli.slash_commands import (
    SlashContext,
    SlashDispatcher,
    _chatgpt_oauth_status_line,
    _format_expiry_delta,
)
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_engine() -> Engine:
    cfg = EngineConfig(model="test-model", system_prompt="sp", tools=[])
    return Engine(cfg)


def _make_ctx(engine: Engine | None = None) -> SlashContext:
    return SlashContext(
        engine=engine or _make_engine(),
        model="test-model",
        deps=Deps(
            call_model=MagicMock(),
            run_tool=MagicMock(),
            approve=MagicMock(),
            compact=None,
        ),
    )


# ===========================================================================
# CQ-P4: ChatGPT stream refactor
# ===========================================================================


class TestChatGPTStreamRefactor:
    """Verify the split of stream() into dispatch + finalise helpers."""

    def test_dispatch_text_delta_appends_chunk_and_queues_event(self) -> None:
        provider = OpenAIChatGPTProvider()
        state = _StreamState(model="gpt-5.2-codex")
        provider._dispatch_sse_event(
            {"type": "response.output_text.delta", "delta": "Hello"}, state,
        )
        assert state.text_chunks == ["Hello"]
        assert "Hello" in state.text_chunks_seen
        assert state.events == [{"type": "text_delta", "text": "Hello"}]
        assert state.done is False

    def test_dispatch_text_done_dedups_against_prior_delta(self) -> None:
        """output_text.done echoes the full text — must not double-yield."""
        provider = OpenAIChatGPTProvider()
        state = _StreamState(model="gpt-5.2-codex")
        provider._dispatch_sse_event(
            {"type": "response.output_text.delta", "delta": "Hi"}, state,
        )
        state.events.clear()
        provider._dispatch_sse_event(
            {"type": "response.output_text.done", "text": "Hi"}, state,
        )
        assert state.events == []  # dedup
        assert state.text_chunks.count("Hi") == 1

    def test_dispatch_response_completed_captures_final_response(self) -> None:
        provider = OpenAIChatGPTProvider()
        state = _StreamState(model="gpt-5.2-codex")
        payload = {"output": [{"type": "message", "content": []}]}
        provider._dispatch_sse_event(
            {"type": "response.completed", "response": payload}, state,
        )
        assert state.final_response == payload
        assert state.events == []

    def test_dispatch_error_event_queues_error_and_marks_done(self) -> None:
        provider = OpenAIChatGPTProvider()
        state = _StreamState(model="gpt-5.2-codex")
        provider._dispatch_sse_event(
            {"type": "response.error", "error": {"message": "boom"}}, state,
        )
        assert state.done is True
        assert len(state.events) == 1
        msg = state.events[0]["message"]
        assert msg.metadata.get("is_error") is True
        assert "boom" in msg.text

    def test_dispatch_function_call_arguments_delta_is_skipped_for_text(self) -> None:
        """function_call_arguments.delta must NOT yield text — only accumulate."""
        provider = OpenAIChatGPTProvider()
        state = _StreamState(model="gpt-5.2-codex")
        provider._dispatch_sse_event(
            {
                "type": "response.function_call_arguments.delta",
                "call_id": "c1",
                "item_id": "i1",
                "delta": '{"path":"x"}',
            },
            state,
        )
        assert state.events == []
        # But the call accumulator should have picked it up.
        assert any("c1" in k or "i1" in k for k in state.streamed_calls)

    def test_dispatch_unknown_event_type_is_noop(self) -> None:
        provider = OpenAIChatGPTProvider()
        state = _StreamState(model="gpt-5.2-codex")
        provider._dispatch_sse_event({"type": "response.something.new"}, state)
        assert state.events == []
        assert state.done is False

    @pytest.mark.asyncio
    async def test_build_final_response_synthesises_from_chunks(self) -> None:
        """When no response.completed arrived, build one from text_chunks."""
        provider = OpenAIChatGPTProvider()
        state = _StreamState(model="gpt-5.2-codex")
        state.text_chunks = ["Hel", "lo"]
        state.text_chunks_seen = {"Hel", "lo"}
        out = await provider._build_final_response(state, headers={})
        assert out["type"] == "assistant"
        assert "Hello" in out["message"].text

    @pytest.mark.asyncio
    async def test_build_final_response_empty_yields_error_event(self) -> None:
        provider = OpenAIChatGPTProvider()
        state = _StreamState(model="gpt-5.2-codex")
        out = await provider._build_final_response(state, headers={})
        assert out["message"].metadata.get("is_error") is True

    def test_parse_sse_line_variants(self) -> None:
        assert _parse_sse_line("ping") is None
        assert _parse_sse_line("data: [DONE]") is None
        assert _parse_sse_line("data: {bad json") is None
        assert _parse_sse_line("data: ") is None
        assert _parse_sse_line('data: {"type":"x"}') == {"type": "x"}

    def test_build_request_body_includes_tools_when_given(self) -> None:
        class _FakeTool:
            name = "Read"
            description = "Read a file"
            input_schema = {"type": "object"}
        body = _build_request_body(
            messages=[Message(role="user", content="hi")],
            system_prompt="sys",
            resolved_model="gpt-5.2-codex",
            tools=[_FakeTool()],
            max_tokens=1000,
            tool_choice="any",
        )
        assert body["model"] == "gpt-5.2-codex"
        assert body["max_output_tokens"] == 1000
        assert body["tool_choice"] == "required"
        assert body["tools"][0]["name"] == "Read"

    def test_build_request_headers_sets_account_id(self) -> None:
        headers = _build_request_headers("tok", "acct-123")
        assert headers["Authorization"] == "Bearer tok"
        assert headers["chatgpt-account-id"] == "acct-123"
        assert headers["accept"] == "text/event-stream"

    def test_error_assistant_event_shape(self) -> None:
        event = _error_assistant_event("nope")
        assert event["type"] == "assistant"
        assert event["message"].metadata.get("is_error") is True
        assert event["message"].text == "nope"

    @pytest.mark.asyncio
    async def test_stream_end_to_end_still_emits_text_and_assistant(
        self, monkeypatch: Any,
    ) -> None:
        """Integration-level smoke: the refactored stream preserves order."""
        class _FakeResp:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def aread(self):
                return b""

            async def aiter_lines(self):
                yield 'data: {"type":"response.output_text.delta","delta":"Hi"}'
                yield (
                    'data: {"type":"response.completed","response":'
                    '{"output":[{"type":"message","content":'
                    '[{"type":"output_text","text":"Hi"}]}]}}'
                )
                yield "data: [DONE]"

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            def stream(self, method, url, headers=None, json=None):
                return _FakeResp()

        monkeypatch.setattr(
            "duh.adapters.openai_chatgpt.get_valid_openai_chatgpt_oauth",
            lambda: {"access_token": "tok", "account_id": "acct"},
        )
        monkeypatch.setattr(
            "duh.adapters.openai_chatgpt.httpx.AsyncClient", _FakeClient,
        )

        provider = OpenAIChatGPTProvider()
        events = [
            ev
            async for ev in provider.stream(
                messages=[Message(role="user", content="go")],
                system_prompt="sys",
            )
        ]
        types = [e["type"] for e in events]
        assert "text_delta" in types
        assert types[-1] == "assistant"


# ===========================================================================
# QX: /health OAuth expiry display
# ===========================================================================


class TestOAuthExpiryInHealth:
    """Verify token-expiry rendering + /health integration."""

    def test_format_expiry_delta_hours_and_minutes(self) -> None:
        # 2h 15m = 2*3600 + 15*60 = 8100 seconds = 8_100_000 ms
        now = 1_000_000
        result = _format_expiry_delta(now + 8_100_000, now)
        assert result == "2h 15m"

    def test_format_expiry_delta_minutes_only(self) -> None:
        now = 1_000_000
        assert _format_expiry_delta(now + 45 * 60 * 1000, now) == "45m"

    def test_format_expiry_delta_expired(self) -> None:
        now = 1_000_000
        assert _format_expiry_delta(now - 5000, now) == "expired"

    def test_format_expiry_delta_multi_day(self) -> None:
        now = 0
        # 3 days, 4 hours.
        ms = (3 * 24 + 4) * 3600 * 1000
        assert _format_expiry_delta(ms, now) == "3d 4h"

    def test_chatgpt_oauth_status_line_absent_when_no_oauth(
        self, monkeypatch: Any,
    ) -> None:
        monkeypatch.setattr(
            "duh.auth.openai_chatgpt._load_oauth", lambda: None,
        )
        assert _chatgpt_oauth_status_line() == ""

    def test_chatgpt_oauth_status_line_valid_shows_remaining(
        self, monkeypatch: Any,
    ) -> None:
        import time as _time

        future_ms = int(_time.time() * 1000) + 2 * 3600 * 1000 + 15 * 60 * 1000
        monkeypatch.setattr(
            "duh.auth.openai_chatgpt._load_oauth",
            lambda: {"access_token": "tok", "expires_at_ms": future_ms},
        )
        line = _chatgpt_oauth_status_line()
        assert "ChatGPT OAuth" in line
        # Token valid; should show hours.
        assert "valid for" in line
        assert "h" in line

    def test_chatgpt_oauth_status_line_expired(self, monkeypatch: Any) -> None:
        import time as _time

        past_ms = int(_time.time() * 1000) - 60_000
        monkeypatch.setattr(
            "duh.auth.openai_chatgpt._load_oauth",
            lambda: {"access_token": "tok", "expires_at_ms": past_ms},
        )
        line = _chatgpt_oauth_status_line()
        assert "expired" in line.lower()

    def test_health_handler_includes_oauth_line_when_present(
        self, monkeypatch: Any, capsys: Any,
    ) -> None:
        import time as _time

        future_ms = int(_time.time() * 1000) + 3600 * 1000  # 1h
        monkeypatch.setattr(
            "duh.auth.openai_chatgpt._load_oauth",
            lambda: {"access_token": "tok", "expires_at_ms": future_ms},
        )
        # Stub HealthChecker to avoid real HTTP.
        monkeypatch.setattr(
            "duh.kernel.health_check.HealthChecker.check_provider",
            lambda self, name: {"healthy": True, "latency_ms": 10, "error": None},
        )
        dispatcher = SlashDispatcher(_make_ctx())
        dispatcher.dispatch("/health", "")
        captured = capsys.readouterr()
        assert "ChatGPT OAuth" in captured.out


# ===========================================================================
# QX: Security scan progress callback
# ===========================================================================


class TestSecurityScanProgress:
    """Verify Runner invokes the progress callback once per scanner."""

    @pytest.mark.asyncio
    async def test_progress_callback_fires_for_each_scanner(self) -> None:
        from duh.security.config import SecurityPolicy
        from duh.security.engine import Runner, ScannerRegistry
        from duh.security.finding import Severity

        class _NoopScanner:
            def __init__(self, name: str) -> None:
                self.name = name
                self.tier = "minimal"
                self.default_severity = (Severity.LOW,)

            def available(self) -> bool:
                return True

            async def scan(self, target, cfg, *, changed_files=None):
                return []

        registry = ScannerRegistry()
        registry.register(_NoopScanner("a"))
        registry.register(_NoopScanner("b"))
        registry.register(_NoopScanner("c"))
        runner = Runner(registry=registry, policy=SecurityPolicy())

        calls: list[tuple[str, int, int]] = []

        def cb(name: str, current: int, total: int) -> None:
            calls.append((name, current, total))

        from pathlib import Path

        await runner.run(Path("."), scanners=["a", "b", "c"], progress=cb)
        assert len(calls) == 3
        assert {c[0] for c in calls} == {"a", "b", "c"}
        assert [c[2] for c in calls] == [3, 3, 3]
        # current counter must be monotonic 1..3
        assert sorted(c[1] for c in calls) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_broken_progress_callback_does_not_abort_scan(self) -> None:
        from pathlib import Path

        from duh.security.config import SecurityPolicy
        from duh.security.engine import Runner, ScannerRegistry
        from duh.security.finding import Severity

        class _NoopScanner:
            name = "s"
            tier = "minimal"
            default_severity = (Severity.LOW,)

            def available(self) -> bool:
                return True

            async def scan(self, target, cfg, *, changed_files=None):
                return []

        registry = ScannerRegistry()
        registry.register(_NoopScanner())

        def bad_cb(name: str, current: int, total: int) -> None:
            raise RuntimeError("surprise!")

        runner = Runner(registry=registry, policy=SecurityPolicy())
        results = await runner.run(
            Path("."), scanners=["s"], progress=bad_cb,
        )
        assert len(results) == 1
        assert results[0].status == "ok"

    def test_cli_progress_callback_writes_to_stderr(
        self, monkeypatch: Any,
    ) -> None:
        from duh.security.cli import _make_stderr_progress_callback

        buf = io.StringIO()
        monkeypatch.setattr("sys.stderr", buf)
        cb = _make_stderr_progress_callback()
        cb("bash_ast", 1, 2)
        cb("pip_audit", 2, 2)
        out = buf.getvalue()
        assert "[1/2]" in out
        assert "bash_ast" in out
        assert "[2/2]" in out
        assert out.endswith("\n")  # final newline flushes the line

    def test_quiet_mode_skips_progress_callback(
        self, monkeypatch: Any, tmp_path: Any,
    ) -> None:
        """--quiet must suppress progress output even on a TTY."""
        from duh.security import cli as sec_cli

        monkeypatch.setattr(
            sec_cli, "_run_scan", _async_noop,
        )
        # Force stderr to a TTY-like object (but --quiet should still silence).
        monkeypatch.setattr("sys.stderr.isatty", lambda: True, raising=False)

        # The invocation path we care about is that _make_stderr_progress_callback
        # is NOT called when args.quiet is True.  We detect this by monkeypatching
        # it to raise.
        called = {"n": 0}

        def _no_progress(*a, **kw):
            called["n"] += 1
            raise AssertionError("progress cb should not be built when --quiet")

        monkeypatch.setattr(sec_cli, "_make_stderr_progress_callback", _no_progress)
        sec_cli.main([
            "scan",
            "--project-root", str(tmp_path),
            "--quiet",
        ])
        assert called["n"] == 0


async def _async_noop(*args, **kwargs):
    """Stub for security cli._run_scan that returns no findings."""
    return []


# ===========================================================================
# QX: /errors slash command
# ===========================================================================


class TestErrorsSlashCommand:
    """Verify /errors surfaces in-session error history."""

    def test_errors_with_empty_buffer_prints_none_message(
        self, capsys: Any,
    ) -> None:
        dispatcher = SlashDispatcher(_make_ctx())
        dispatcher.dispatch("/errors", "")
        out = capsys.readouterr().out
        assert "No errors" in out

    def test_errors_shows_recorded_entries(self, capsys: Any) -> None:
        engine = _make_engine()
        # Simulate the turn loop emitting a few error events.
        engine._slog_event({"type": "error", "error": "boom 1"})
        engine._slog_event({"type": "error", "error": "boom 2"})
        engine._slog_event(
            {"type": "tool_result", "name": "Bash", "output": "bad", "is_error": True},
        )
        dispatcher = SlashDispatcher(_make_ctx(engine))
        dispatcher.dispatch("/errors", "")
        out = capsys.readouterr().out
        assert "boom 1" in out
        assert "boom 2" in out
        assert "tool:Bash" in out
        assert "bad" in out

    def test_errors_respects_limit_argument(self, capsys: Any) -> None:
        engine = _make_engine()
        for i in range(15):
            engine._slog_event({"type": "error", "error": f"err-{i}"})
        dispatcher = SlashDispatcher(_make_ctx(engine))
        dispatcher.dispatch("/errors", "3")
        out = capsys.readouterr().out
        # Should show last 3 only: err-12, err-13, err-14
        assert "err-14" in out
        assert "err-13" in out
        assert "err-12" in out
        assert "err-0" not in out
        assert "err-11" not in out

    def test_errors_rejects_non_numeric_limit(self, capsys: Any) -> None:
        dispatcher = SlashDispatcher(_make_ctx())
        dispatcher.dispatch("/errors", "notanumber")
        out = capsys.readouterr().out
        assert "Usage" in out

    def test_errors_non_error_tool_results_are_not_recorded(self) -> None:
        engine = _make_engine()
        # Non-error tool_result should NOT show up.
        engine._slog_event(
            {"type": "tool_result", "name": "Read", "output": "ok", "is_error": False},
        )
        assert engine._session_errors == []

    def test_errors_buffer_is_bounded(self) -> None:
        """Buffer caps at 100 entries to prevent unbounded growth."""
        engine = _make_engine()
        for i in range(150):
            engine._slog_event({"type": "error", "error": f"err-{i}"})
        assert len(engine._session_errors) == 100
        # Should keep the most-recent entries (err-50 .. err-149).
        assert engine._session_errors[0]["message"] == "err-50"
        assert engine._session_errors[-1]["message"] == "err-149"

    def test_errors_is_registered_in_dispatch_table(self) -> None:
        assert "/errors" in SlashDispatcher._HANDLERS

    def test_errors_entry_includes_timestamp_and_context(self) -> None:
        engine = _make_engine()
        engine._slog_event({"type": "error", "error": "boom"})
        entry = engine._session_errors[-1]
        assert "timestamp" in entry
        assert "T" in entry["timestamp"]  # ISO-8601
        assert entry["context"] == "error"
        assert entry["message"] == "boom"
