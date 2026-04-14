"""Coverage tests for duh.__main__, duh.cli.main, and duh.cli.doctor.

Covers:
  - duh.__main__ import + main is callable
  - duh.cli.main._setup_signal_handlers
  - duh.cli.main.main (doctor, bridge, stream-json SDK mode, print mode,
    REPL fallback, KeyboardInterrupt handling across every branch)
  - duh.cli.doctor: all checks including Anthropic/OpenAI connectivity,
    Ollama running + not running, MCP server listing branches, provider
    summary paths, SDK import error paths.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli import main as main_mod
from duh.cli.main import _setup_signal_handlers, main


# ============================================================================
# __main__ module
# ============================================================================


class TestDunderMain:
    def test_import_main_module(self):
        """Importing duh.__main__ should call main() via SystemExit."""
        # We can't import it directly (raises SystemExit). Instead verify
        # the module file exists and references duh.cli.main.main.
        import importlib.util
        import duh

        spec = importlib.util.find_spec("duh.__main__")
        assert spec is not None
        # Read the source to confirm it calls main.
        with open(spec.origin, encoding="utf-8") as f:
            src = f.read()
        assert "from duh.cli.main import main" in src

    def test_run_as_module_calls_main(self):
        """Execute duh.__main__ via runpy to actually run line 5."""
        import runpy

        with patch("duh.cli.main.main", return_value=0) as mock_main:
            with pytest.raises(SystemExit) as exc_info:
                runpy.run_module("duh.__main__", run_name="__main__")
        assert exc_info.value.code == 0


# ============================================================================
# _setup_signal_handlers
# ============================================================================


class TestSignalHandlers:
    def test_sets_sigterm_handler(self):
        """_setup_signal_handlers should register a SIGTERM handler."""
        old = signal.getsignal(signal.SIGTERM)
        try:
            _setup_signal_handlers()
            new_handler = signal.getsignal(signal.SIGTERM)
            assert callable(new_handler)
            # The handler should raise KeyboardInterrupt when called
            with pytest.raises(KeyboardInterrupt):
                new_handler(signal.SIGTERM, None)
        finally:
            signal.signal(signal.SIGTERM, old)


# ============================================================================
# main() branches
# ============================================================================


class TestMainBranches:
    def test_doctor_subcommand(self):
        with patch("duh.cli.main.run_doctor", return_value=42) as mock_doc:
            code = main(["doctor"])
        assert code == 42
        mock_doc.assert_called_once()

    def test_bridge_start_subcommand(self):
        """Bridge subcommand should run bridge until CancelledError."""
        mock_server = MagicMock()
        mock_server.start = AsyncMock()
        mock_server.stop = AsyncMock()

        with patch("duh.bridge.server.BridgeServer", return_value=mock_server):
            # Real asyncio.run drives the coroutine. We patch asyncio.Future
            # inside main's `_run_bridge` to return a cancelled future so
            # the `await asyncio.Future()` raises CancelledError and we
            # exercise the except + finally branches.
            real_future = asyncio.Future

            def _cancelled_future():
                loop = asyncio.get_event_loop()
                fut = real_future(loop=loop)
                fut.cancel()
                return fut

            with patch("duh.cli.main.asyncio.Future", side_effect=_cancelled_future):
                code = main(["bridge", "start", "--port", "9999"])

        assert code == 0
        mock_server.start.assert_awaited_once()
        mock_server.stop.assert_awaited_once()

    def test_bridge_subcommand_keyboard_interrupt(self, capsys):
        """KeyboardInterrupt during bridge should print 'stopped' and exit 0."""
        with patch("duh.bridge.server.BridgeServer") as mock_bs:
            mock_bs.return_value.start = AsyncMock()
            mock_bs.return_value.stop = AsyncMock()

            with patch("asyncio.run", side_effect=KeyboardInterrupt):
                code = main(["bridge", "start"])
        assert code == 0
        captured = capsys.readouterr()
        assert "Bridge server stopped" in captured.err

    def test_stream_json_input_format(self):
        """--input-format stream-json should run sdk_runner."""
        with patch("duh.cli.sdk_runner.run_stream_json_mode", new_callable=AsyncMock, return_value=0):
            with patch("asyncio.run", return_value=0) as mock_run:
                code = main(["--input-format", "stream-json", "--output-format", "stream-json"])
        assert code == 0
        assert mock_run.called

    def test_stream_json_keyboard_interrupt(self, capsys):
        with patch("duh.cli.sdk_runner.run_stream_json_mode", new_callable=AsyncMock):
            with patch("asyncio.run", side_effect=KeyboardInterrupt):
                code = main(["--input-format", "stream-json", "--output-format", "stream-json"])
        assert code == 130
        captured = capsys.readouterr()
        assert "Interrupted" in captured.err

    def test_print_mode_with_prompt(self):
        with patch("duh.cli.runner.run_print_mode", new_callable=AsyncMock, return_value=0):
            with patch("asyncio.run", return_value=0) as mock_run:
                code = main(["-p", "hello"])
        assert code == 0
        assert mock_run.called

    def test_print_mode_keyboard_interrupt(self, capsys):
        with patch("duh.cli.runner.run_print_mode", new_callable=AsyncMock):
            with patch("asyncio.run", side_effect=KeyboardInterrupt):
                code = main(["-p", "hello"])
        assert code == 130
        captured = capsys.readouterr()
        assert "Interrupted" in captured.err

    def test_repl_mode_no_args(self):
        with patch("duh.cli.repl.run_repl", new_callable=AsyncMock, return_value=0):
            with patch("asyncio.run", return_value=0) as mock_run:
                code = main([])
        assert code == 0
        assert mock_run.called

    def test_repl_mode_keyboard_interrupt(self, capsys):
        with patch("duh.cli.repl.run_repl", new_callable=AsyncMock):
            with patch("asyncio.run", side_effect=KeyboardInterrupt):
                code = main([])
        assert code == 0
        captured = capsys.readouterr()
        # Newline emitted on interrupt
        assert captured.err == "\n"


# ============================================================================
# Doctor branches
# ============================================================================


class TestDoctorBranches:
    def test_anthropic_connectivity_healthy(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        fake_checker = MagicMock()

        def fake_check(provider):
            if provider == "anthropic":
                return {"healthy": True, "latency_ms": 150, "error": None}
            return {"healthy": False, "latency_ms": 0, "error": "not running"}

        fake_checker.check_provider.side_effect = fake_check

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            from duh.cli.doctor import run_doctor
            code = run_doctor()
        captured = capsys.readouterr()
        assert "Anthropic connectivity" in captured.out
        assert "reachable" in captured.out

    def test_anthropic_connectivity_unhealthy(self, monkeypatch, capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        fake_checker = MagicMock()

        def fake_check(provider):
            if provider == "anthropic":
                return {"healthy": False, "latency_ms": 5000, "error": "timeout"}
            return {"healthy": False, "latency_ms": 0, "error": "not running"}

        fake_checker.check_provider.side_effect = fake_check

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            from duh.cli.doctor import run_doctor
            run_doctor()
        captured = capsys.readouterr()
        assert "unreachable" in captured.out

    def test_openai_connectivity_healthy(self, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        fake_checker = MagicMock()

        def fake_check(provider):
            if provider == "openai":
                return {"healthy": True, "latency_ms": 100, "error": None}
            return {"healthy": False, "latency_ms": 0, "error": "nope"}

        fake_checker.check_provider.side_effect = fake_check

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            from duh.cli.doctor import run_doctor
            run_doctor()
        captured = capsys.readouterr()
        assert "OpenAI connectivity" in captured.out
        assert "reachable" in captured.out

    def test_openai_connectivity_unhealthy(self, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        fake_checker = MagicMock()
        fake_checker.check_provider.return_value = {
            "healthy": False, "latency_ms": 500, "error": "dns"
        }

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            from duh.cli.doctor import run_doctor
            run_doctor()
        captured = capsys.readouterr()
        assert "unreachable" in captured.out

    def test_ollama_running_with_models(self, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        fake_checker = MagicMock()

        def fake_check(provider):
            if provider == "ollama":
                return {"healthy": True, "latency_ms": 50, "error": None}
            return {"healthy": False, "latency_ms": 0, "error": "nope"}

        fake_checker.check_provider.side_effect = fake_check

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "models": [
                {"name": "qwen2.5-coder:1.5b"},
                {"name": "llama3.2:3b"},
            ]
        }

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            with patch("httpx.get", return_value=fake_resp):
                from duh.cli.doctor import run_doctor
                run_doctor()
        captured = capsys.readouterr()
        assert "Ollama" in captured.out
        assert "running" in captured.out
        assert "qwen2.5-coder:1.5b" in captured.out

    def test_ollama_running_no_model_endpoint(self, monkeypatch, capsys):
        """Ollama is healthy but /api/tags fails — still marks as running."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        fake_checker = MagicMock()
        fake_checker.check_provider.return_value = {
            "healthy": True, "latency_ms": 50, "error": None,
        }

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            with patch("httpx.get", side_effect=Exception("boom")):
                from duh.cli.doctor import run_doctor
                run_doctor()
        captured = capsys.readouterr()
        assert "running" in captured.out

    def test_ollama_not_running(self, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        fake_checker = MagicMock()
        fake_checker.check_provider.return_value = {
            "healthy": False, "latency_ms": 1000, "error": "refused",
        }

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            from duh.cli.doctor import run_doctor
            run_doctor()
        captured = capsys.readouterr()
        assert "not running" in captured.out

    def test_format_latency_ms(self):
        from duh.cli.doctor import _format_latency
        assert "500ms" in _format_latency(500)
        assert "ms" in _format_latency(999)

    def test_format_latency_seconds(self):
        from duh.cli.doctor import _format_latency
        out = _format_latency(1500)
        assert "s" in out
        assert "." in out  # e.g., "1.5s"

    def test_mcp_servers_listed_from_config(self, monkeypatch, capsys):
        """When config has MCP servers, doctor lists them."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        fake_config = MagicMock()
        fake_config.mcp_servers = {
            "mcpServers": {
                "server1": {"command": "foo"},
                "server2": {"command": "bar"},
            }
        }

        fake_checker = MagicMock()
        fake_checker.check_provider.return_value = {
            "healthy": False, "latency_ms": 0, "error": "nope",
        }

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            with patch("duh.config.load_config", return_value=fake_config):
                from duh.cli.doctor import run_doctor
                run_doctor()
        captured = capsys.readouterr()
        assert "server1" in captured.out
        assert "server2" in captured.out

    def test_mcp_config_exception_swallowed(self, monkeypatch, capsys):
        """Bad config should be silently skipped."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        fake_checker = MagicMock()
        fake_checker.check_provider.return_value = {
            "healthy": False, "latency_ms": 0, "error": "x",
        }

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            with patch("duh.config.load_config", side_effect=RuntimeError("bad")):
                from duh.cli.doctor import run_doctor
                code = run_doctor()
        # Should still complete (not crash)
        assert isinstance(code, int)

    def test_anthropic_sdk_missing(self, monkeypatch, capsys):
        """ImportError on anthropic SDK should be caught and reported."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        import builtins
        real_import = builtins.__import__

        def blocker(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        fake_checker = MagicMock()
        fake_checker.check_provider.return_value = {
            "healthy": False, "latency_ms": 0, "error": "x",
        }

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            with patch("builtins.__import__", side_effect=blocker):
                from duh.cli.doctor import run_doctor
                run_doctor()
        captured = capsys.readouterr()
        assert "not installed" in captured.out

    def test_openai_sdk_missing(self, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        import builtins
        real_import = builtins.__import__

        def blocker(name, *args, **kwargs):
            if name == "openai":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        fake_checker = MagicMock()
        fake_checker.check_provider.return_value = {
            "healthy": False, "latency_ms": 0, "error": "x",
        }

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            with patch("builtins.__import__", side_effect=blocker):
                from duh.cli.doctor import run_doctor
                run_doctor()
        captured = capsys.readouterr()
        assert "not installed (optional)" in captured.out

    def test_provider_summary_all_providers(self, monkeypatch, capsys):
        """All three provider paths should appear in summary."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        fake_checker = MagicMock()
        fake_checker.check_provider.return_value = {
            "healthy": True, "latency_ms": 10, "error": None,
        }

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"models": []}

        with patch(
            "duh.kernel.health_check.HealthChecker",
            return_value=fake_checker,
        ):
            with patch("httpx.get", return_value=fake_resp):
                from duh.cli.doctor import run_doctor
                run_doctor()
        captured = capsys.readouterr()
        assert "Providers ready" in captured.out
        # All three should appear in the Providers ready line
        assert "Anthropic" in captured.out
        assert "OpenAI" in captured.out
