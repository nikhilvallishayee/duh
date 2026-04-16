"""First-run UX (QX-3, QX-4, QX-5) regression tests.

Covers four user-visible polish items:

1. ``duh security init`` -- interactive wizard writes ``.duh/security.json``
   with a per-scanner enable map, ``fail_on`` threshold, and ``allowed_paths``
   list (QX-3).
2. The ``No provider available`` error in the print-mode runner mentions
   ``duh doctor`` and lists per-provider env vars (QX-4).
3. The ``/model`` slash command warns when the destination model is at
   least 10x more expensive than the current one (QX bonus).
4. ``connect_openai_chatgpt_subscription`` surfaces the HTTP status code
   and a ``duh doctor`` remediation hint when the token exchange fails
   (QX-5).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.auth import openai_chatgpt as oauth_mod
from duh.cli import exit_codes
from duh.cli.runner import _no_provider_message, run_print_mode
from duh.cli.slash_commands import (
    SlashContext,
    SlashDispatcher,
    _format_cost_delta_warning,
    _short_name,
)
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.security import cli as sec_cli
from duh.security.wizard import (
    Answers,
    Detection,
    render_plan,
    run_interactive,
    write_plan,
)


# =====================================================================
# QX-4: No-provider error includes doctor suggestion + env vars
# =====================================================================


class TestNoProviderMessage:
    def test_message_mentions_duh_doctor(self) -> None:
        msg = _no_provider_message()
        assert "duh doctor" in msg

    def test_message_lists_provider_env_vars(self) -> None:
        msg = _no_provider_message()
        # All four documented providers should be advertised so users know
        # which env var to set first.
        assert "ANTHROPIC_API_KEY" in msg
        assert "OPENAI_API_KEY" in msg
        assert "GEMINI_API_KEY" in msg
        # Ollama doesn't use a key but should be mentioned for parity.
        assert "ollama" in msg.lower()

    def test_message_includes_docs_link(self) -> None:
        msg = _no_provider_message()
        assert "getting-started" in msg

    def test_message_starts_with_canonical_error(self) -> None:
        # Existing tests grep for "No provider available" -- preserve it.
        assert _no_provider_message().startswith("Error: No provider available.")

    @pytest.mark.asyncio
    async def test_runner_emits_enhanced_error(self, monkeypatch, capsys) -> None:
        """End-to-end: when no provider is available, the runner writes the
        enhanced first-run guidance to stderr."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        args = argparse.Namespace(
            prompt="hello",
            debug=False,
            verbose=False,
            provider=None,
            model=None,
            fallback_model=None,
            max_turns=1,
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

        with patch("httpx.get", side_effect=Exception("no ollama")):
            code = await run_print_mode(args)

        captured = capsys.readouterr()
        assert code == exit_codes.PROVIDER_ERROR
        assert "duh doctor" in captured.err
        assert "ANTHROPIC_API_KEY" in captured.err


# =====================================================================
# Bonus: /model cost-delta warning
# =====================================================================


class TestCostDeltaWarning:
    def test_short_name_classifies_anthropic(self) -> None:
        assert _short_name("claude-haiku-3-5") == "haiku"
        assert _short_name("claude-sonnet-4-6") == "sonnet"
        assert _short_name("claude-opus-4-6") == "opus"

    def test_short_name_classifies_openai(self) -> None:
        assert _short_name("gpt-4o-mini") == "gpt-4o-mini"
        assert _short_name("gpt-4o") == "gpt-4o"

    def test_short_name_classifies_local(self) -> None:
        assert _short_name("ollama:qwen2.5") == "local"
        assert _short_name("llama3") == "local"

    def test_no_warning_for_same_model(self) -> None:
        assert _format_cost_delta_warning(
            "claude-sonnet-4-6", "claude-sonnet-4-6"
        ) == ""

    def test_no_warning_for_cheaper_model(self) -> None:
        # Opus -> Haiku: cheaper, no warning.
        assert _format_cost_delta_warning("claude-opus-4-6", "claude-haiku-3-5") == ""

    def test_warning_haiku_to_opus(self) -> None:
        # Haiku $0.25/M in, Opus $15/M in -> 60x.
        warning = _format_cost_delta_warning("claude-haiku-3-5", "claude-opus-4-6")
        assert warning != ""
        assert "haiku" in warning
        assert "opus" in warning
        assert "60x" in warning
        # Must include input AND output cost per 1M tokens.
        assert "0.25/M" in warning
        assert "15/M" in warning

    def test_warning_local_to_paid(self) -> None:
        warning = _format_cost_delta_warning("ollama:qwen2.5", "claude-opus-4-6")
        assert "free" in warning
        assert "opus" in warning

    def test_no_warning_under_10x_threshold(self) -> None:
        # Sonnet ($3/M) -> Opus ($15/M) is only 5x -> no warning.
        assert _format_cost_delta_warning("claude-sonnet-4-6", "claude-opus-4-6") == ""

    def test_slash_model_emits_warning(self, capsys) -> None:
        """The /model handler prints the warning before changing models."""
        cfg = EngineConfig(
            model="claude-haiku-3-5",
            system_prompt="x",
            tools=[],
        )
        engine = Engine(cfg)
        deps = Deps(
            call_model=MagicMock(),
            run_tool=MagicMock(),
            approve=MagicMock(),
        )
        ctx = SlashContext(
            engine=engine,
            model="claude-haiku-3-5",
            deps=deps,
            provider_name="anthropic",
        )
        dispatcher = SlashDispatcher(ctx)

        # Stub out the backend swap so we don't touch real provider plumbing.
        with patch.object(
            SlashDispatcher,
            "_switch_backend_for_model",
            return_value=(True, "anthropic"),
        ), patch(
            "duh.kernel.model_caps.rebuild_system_prompt",
            side_effect=lambda sp, *a, **k: sp,
        ):
            keep, new_model = dispatcher.dispatch("/model", "claude-opus-4-6")

        assert keep is True
        assert new_model == "claude-opus-4-6"
        captured = capsys.readouterr()
        assert "60x" in captured.out
        assert "haiku" in captured.out
        assert "opus" in captured.out


# =====================================================================
# QX-3: security init writes config file
# =====================================================================


class TestSecurityInitWizard:
    def test_render_plan_includes_fail_on_and_allowed_paths(
        self, tmp_path: Path
    ) -> None:
        det = Detection(
            is_python=True, is_git_repo=False, has_github=False,
            has_docker=False, has_go=False,
            available_scanners=("ruff-sec", "pip-audit"),
        )
        answers = Answers(
            mode="strict",
            enable_runtime=True,
            extended_scanners=(),
            generate_ci=False,
            ci_template="standard",
            install_git_hook=False,
            generate_security_md=False,
            import_legacy=False,
            pin_scanner_versions=True,
            enabled_scanners=("ruff-sec",),
            fail_on="high",
            allowed_paths=("src/", "tests/"),
        )
        plan = render_plan(detection=det, answers=answers, project_root=tmp_path)
        write_plan(plan, dry_run=False)

        cfg_path = tmp_path / ".duh" / "security.json"
        assert cfg_path.exists()
        data = json.loads(cfg_path.read_text())
        assert data["mode"] == "strict"
        assert data["fail_on"] == "high"
        assert data["allowed_paths"] == ["src/", "tests/"]
        # ruff-sec was opted in; pip-audit was not, so must be present but disabled.
        assert data["scanners"]["ruff-sec"]["enabled"] is True
        assert data["scanners"]["pip-audit"]["enabled"] is False

    def test_run_interactive_collects_answers(self, tmp_path: Path) -> None:
        # Simulate a user accepting some scanners and providing fail_on/paths.
        det = Detection(
            is_python=True, is_git_repo=True, has_github=False,
            has_docker=False, has_go=False,
            available_scanners=("ruff-sec", "pip-audit"),
        )
        # Replies in order:
        #   enable ruff-sec? (default Y) -> blank = Y
        #   enable pip-audit? (default Y) -> "n"
        #   fail-on threshold -> blank = "high"
        #   allowed paths -> "src/, tests/"
        replies = iter(["", "n", "", "src/, tests/"])
        prints: list[str] = []

        def fake_input(_prompt: str) -> str:
            return next(replies)

        def fake_print(*args: Any, **kwargs: Any) -> None:
            prints.append(" ".join(str(a) for a in args))

        answers = run_interactive(
            project_root=tmp_path,
            detection=det,
            input_fn=fake_input,
            output_fn=fake_print,
        )
        assert "ruff-sec" in answers.enabled_scanners
        assert "pip-audit" not in answers.enabled_scanners
        assert answers.fail_on == "high"
        assert answers.allowed_paths == ("src/", "tests/")

    def test_run_interactive_invalid_severity_falls_back_to_high(
        self, tmp_path: Path
    ) -> None:
        det = Detection(
            is_python=False, is_git_repo=False, has_github=False,
            has_docker=False, has_go=False,
            available_scanners=(),
        )
        # Garbage severity -> must fall back to "high" with a warning.
        replies = iter(["bogus-severity", ""])
        warnings: list[str] = []

        def fake_input(_prompt: str) -> str:
            return next(replies)

        def fake_print(*args: Any, **kwargs: Any) -> None:
            warnings.append(" ".join(str(a) for a in args))

        answers = run_interactive(
            project_root=tmp_path,
            detection=det,
            input_fn=fake_input,
            output_fn=fake_print,
        )
        assert answers.fail_on == "high"
        assert any("warning" in w.lower() for w in warnings)

    def test_dispatch_init_non_interactive_writes_file(self, tmp_path: Path) -> None:
        # The non-interactive code path must continue to work.
        rc = sec_cli.main([
            "init",
            "--non-interactive",
            "--project-root", str(tmp_path),
            "--mode", "advisory",
        ])
        assert rc == 0
        cfg_path = tmp_path / ".duh" / "security.json"
        assert cfg_path.exists()
        data = json.loads(cfg_path.read_text())
        assert data["mode"] == "advisory"

    def test_dispatch_init_interactive_writes_file(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """End-to-end: ``duh security init`` (no --non-interactive) drives the
        prompts and writes ``.duh/security.json``."""
        # Force the wizard to see no installed scanners so we only need to
        # answer the trailing fail-on / allowed-paths prompts.  In dev envs
        # the registry may have entry points; we elide them here for
        # determinism.
        from duh.security import wizard as wiz_mod

        original_detect = wiz_mod.detect

        def _empty_detect(*, project_root):
            det = original_detect(project_root=project_root)
            from dataclasses import replace as _replace
            return _replace(det, available_scanners=())

        monkeypatch.setattr(wiz_mod, "detect", _empty_detect)

        replies = iter(["medium", "src/"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(replies))

        rc = sec_cli.main([
            "init",
            "--project-root", str(tmp_path),
        ])
        assert rc == 0
        cfg_path = tmp_path / ".duh" / "security.json"
        assert cfg_path.exists()
        data = json.loads(cfg_path.read_text())
        assert data["fail_on"] == "medium"
        assert data["allowed_paths"] == ["src/"]


# =====================================================================
# QX-5: OAuth error includes HTTP status + remediation
# =====================================================================


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    """Context-manager stub for httpx.Client."""

    def __init__(self, response: _FakeResponse | None = None,
                 exc: Exception | None = None):
        self._response = response
        self._exc = exc

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def post(self, url: str, data: dict[str, Any], headers: dict[str, str]
             ) -> _FakeResponse:
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


@pytest.fixture
def fake_provider_store(monkeypatch):
    store: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(
        oauth_mod, "load_provider_auth", lambda p: dict(store.get(p, {}))
    )
    monkeypatch.setattr(
        oauth_mod, "save_provider_auth",
        lambda p, v: store.update({p: dict(v)}),
    )
    return store


class TestOAuthErrorMessages:
    def test_token_exchange_detailed_returns_status_on_4xx(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            oauth_mod.httpx, "Client",
            lambda *a, **k: _FakeClient(_FakeResponse(401, {})),
        )
        result = oauth_mod._exchange_code_for_tokens_detailed("c", "v")
        assert result.ok is False
        assert result.status == 401
        assert "401" in result.error

    def test_token_exchange_detailed_returns_no_status_on_network_error(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            oauth_mod.httpx, "Client",
            lambda *a, **k: _FakeClient(exc=RuntimeError("boom")),
        )
        result = oauth_mod._exchange_code_for_tokens_detailed("c", "v")
        assert result.ok is False
        assert result.status is None
        assert "network error" in result.error

    def test_connect_includes_http_status_in_error(
        self, monkeypatch, fake_provider_store
    ) -> None:
        # Force HTTPServer to fail so we go straight to the paste branch,
        # then short-circuit on the token exchange returning 503.
        class _BoomServer:
            def __init__(self, *a, **k):
                raise OSError("port in use")

        monkeypatch.setattr(oauth_mod, "HTTPServer", _BoomServer)
        monkeypatch.setattr(oauth_mod.webbrowser, "open", lambda url: None)
        monkeypatch.setattr(
            oauth_mod.httpx, "Client",
            lambda *a, **k: _FakeClient(_FakeResponse(503, {})),
        )

        ok, msg = oauth_mod.connect_openai_chatgpt_subscription(
            input_fn=lambda _prompt: "somecode",
            output_fn=lambda *a, **k: None,
        )
        assert ok is False
        # Canonical prefix preserved for backwards-greppability.
        assert msg.startswith("OAuth token exchange failed")
        # The new bits: HTTP status + remediation.
        assert "HTTP 503" in msg
        assert "duh doctor" in msg

    def test_connect_includes_remediation_on_network_error(
        self, monkeypatch, fake_provider_store
    ) -> None:
        class _BoomServer:
            def __init__(self, *a, **k):
                raise OSError("port in use")

        monkeypatch.setattr(oauth_mod, "HTTPServer", _BoomServer)
        monkeypatch.setattr(oauth_mod.webbrowser, "open", lambda url: None)
        monkeypatch.setattr(
            oauth_mod.httpx, "Client",
            lambda *a, **k: _FakeClient(exc=RuntimeError("dns")),
        )

        ok, msg = oauth_mod.connect_openai_chatgpt_subscription(
            input_fn=lambda _prompt: "somecode",
            output_fn=lambda *a, **k: None,
        )
        assert ok is False
        assert "duh doctor" in msg
        # No HTTP status because the request never reached the server.
        assert "HTTP" not in msg
