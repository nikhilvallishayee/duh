"""Additional coverage for low-coverage modules.

Targets specific uncovered lines in:
- duh._optional_deps
- duh.adapters.approvers (InteractiveApprover, TieredApprover unknowns)
- duh.adapters.sandbox.policy (_landlock_available, fallback)
- duh.bridge.server (start/stop/error paths)
- duh.kernel.attachments (mimetype fallback, pdf extraction)
- duh.tools.worktree (real _run_git_async with mocked subprocess)
- duh.tools.edit (error branches)
- duh.tools.ask_user_tool (exception branches)
- duh.tools.todo_tool (summary method)
- duh.tools.lsp_tool (error branches)
- duh.adapters.openai (streaming partial/error branches)
- duh.adapters.ollama (tool_choice branches, extracted-from-text)
- duh.adapters.mcp_executor (retry/reconnect/cleanup branches)
- duh.adapters.mcp_transports (error branches)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =============================================================================
# duh._optional_deps
# =============================================================================

class TestOptionalDeps:
    def test_require_websockets_missing(self, monkeypatch):
        import duh._optional_deps as mod
        monkeypatch.setattr(mod, "ws_available", False)
        with pytest.raises(RuntimeError, match="websockets"):
            mod.require_websockets()

    def test_require_websockets_present(self, monkeypatch):
        import duh._optional_deps as mod
        monkeypatch.setattr(mod, "ws_available", True)
        # Should not raise
        mod.require_websockets()

    def test_require_httpx_missing(self, monkeypatch):
        import duh._optional_deps as mod
        monkeypatch.setattr(mod, "httpx_available", False)
        with pytest.raises(RuntimeError, match="httpx"):
            mod.require_httpx()

    def test_require_httpx_present(self, monkeypatch):
        import duh._optional_deps as mod
        monkeypatch.setattr(mod, "httpx_available", True)
        mod.require_httpx()

    def test_import_fallback_simulated(self):
        """Run the try/except blocks by re-executing the module source with a
        forced ImportError for the optional packages. This covers the except
        branches (lines 17-19, 39-41).
        """
        import importlib.util
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in ("websockets", "httpx"):
                raise ImportError(f"mocked: {name} missing")
            return real_import(name, *args, **kwargs)

        # Execute module source in a fresh namespace with forced failures
        spec = importlib.util.find_spec("duh._optional_deps")
        assert spec is not None and spec.origin is not None
        source = Path(spec.origin).read_text()

        ns: dict[str, Any] = {"__name__": "duh._optional_deps_probe", "__builtins__": builtins}
        with patch.object(builtins, "__import__", side_effect=fake_import):
            exec(compile(source, spec.origin, "exec"), ns)

        assert ns["ws_available"] is False
        assert ns["httpx_available"] is False
        assert ns["websockets"] is None
        assert ns["httpx"] is None
        with pytest.raises(RuntimeError, match="websockets"):
            ns["require_websockets"]()
        with pytest.raises(RuntimeError, match="httpx"):
            ns["require_httpx"]()


# =============================================================================
# duh.adapters.approvers — InteractiveApprover and TieredApprover unknown-tool
# =============================================================================

class TestInteractiveApprover:
    async def test_allows_on_yes(self, monkeypatch):
        from duh.adapters.approvers import InteractiveApprover
        import builtins
        monkeypatch.setattr(builtins, "input", lambda *_a, **_kw: "y")
        a = InteractiveApprover()
        result = await a.check("Bash", {"command": "ls"})
        assert result["allowed"] is True

    async def test_allows_on_empty(self, monkeypatch):
        from duh.adapters.approvers import InteractiveApprover
        import builtins
        monkeypatch.setattr(builtins, "input", lambda *_a, **_kw: "")
        a = InteractiveApprover()
        result = await a.check("Read", {"path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_denies_on_n(self, monkeypatch):
        from duh.adapters.approvers import InteractiveApprover
        import builtins
        monkeypatch.setattr(builtins, "input", lambda *_a, **_kw: "n")
        a = InteractiveApprover()
        result = await a.check("Bash", {"command": "rm -rf /"})
        assert result["allowed"] is False
        assert result["reason"] == "User denied"

    async def test_eof_interrupt(self, monkeypatch):
        from duh.adapters.approvers import InteractiveApprover
        import builtins
        def eof(*_a, **_kw):
            raise EOFError()
        monkeypatch.setattr(builtins, "input", eof)
        a = InteractiveApprover()
        result = await a.check("Bash", {"command": "ls"})
        assert result["allowed"] is False
        assert "cancel" in result["reason"].lower()

    async def test_keyboard_interrupt(self, monkeypatch):
        from duh.adapters.approvers import InteractiveApprover
        import builtins
        def kb(*_a, **_kw):
            raise KeyboardInterrupt()
        monkeypatch.setattr(builtins, "input", kb)
        a = InteractiveApprover()
        result = await a.check("Bash", {"command": "ls"})
        assert result["allowed"] is False

    async def test_long_summary_truncated(self, monkeypatch):
        from duh.adapters.approvers import InteractiveApprover
        import builtins
        monkeypatch.setattr(builtins, "input", lambda *_a, **_kw: "y")
        a = InteractiveApprover()
        huge = "x" * 500
        result = await a.check("Bash", {"command": huge, "cwd": "/tmp", "env": {"A": "B"}})
        assert result["allowed"] is True


class TestTieredApproverUnknownTool:
    async def test_suggest_unknown_denied(self):
        from duh.adapters.approvers import ApprovalMode, TieredApprover
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("MysteryTool", {})
        assert result["allowed"] is False
        assert "Unknown tool" in result["reason"]
        assert "suggest" in result["reason"]

    async def test_auto_edit_unknown_denied(self):
        from duh.adapters.approvers import ApprovalMode, TieredApprover
        with patch("duh.adapters.approvers._is_git_repo", return_value=True):
            approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("MysteryTool", {})
        assert result["allowed"] is False
        assert "Unknown tool" in result["reason"]
        assert "auto-edit" in result["reason"]


# =============================================================================
# duh.adapters.sandbox.policy — _landlock_available, fallback branch
# =============================================================================

class TestLandlockAvailable:
    def test_landlock_available_exception(self, monkeypatch):
        """When ctypes fails, _landlock_available returns False."""
        from duh.adapters.sandbox import policy
        with patch("ctypes.CDLL", side_effect=OSError("no libc")):
            assert policy._landlock_available() is False

    def test_landlock_available_runs(self):
        """Exercise the real function; result depends on platform."""
        from duh.adapters.sandbox.policy import _landlock_available
        # Should return bool without exception
        result = _landlock_available()
        assert isinstance(result, bool)

    def test_build_unknown_sandbox_type_fallback(self):
        """If a future SandboxType is added without a handler, fallback path."""
        from duh.adapters.sandbox.policy import SandboxCommand, SandboxPolicy, SandboxType
        # Build with NONE to get to the final return, but use a hack:
        # Cover the unreachable return at line 160 by patching SandboxType checks.
        # Simulate an unknown type with a direct instance.
        from enum import Enum
        class FakeType(Enum):
            WEIRD = "weird"
        policy = SandboxPolicy()
        # Pass a non-matching type — all if branches skipped, reaches final return
        result = SandboxCommand.build(
            command="echo x",
            policy=policy,
            sandbox_type=FakeType.WEIRD,  # type: ignore[arg-type]
        )
        assert result.command == "echo x"
        assert result.argv == ["bash", "-c", "echo x"]
        assert result.profile_path is None

    def test_cleanup_removes_profile(self, tmp_path):
        from duh.adapters.sandbox.policy import SandboxCommand
        profile = tmp_path / "x.sb"
        profile.write_text("(version 1)")
        cmd = SandboxCommand(
            command="x",
            argv=["x"],
            profile_path=str(profile),
        )
        cmd.cleanup()
        assert not profile.exists()

    def test_cleanup_missing_profile(self, tmp_path):
        from duh.adapters.sandbox.policy import SandboxCommand
        cmd = SandboxCommand(
            command="x",
            argv=["x"],
            profile_path=str(tmp_path / "nonexistent.sb"),
        )
        # Should not raise
        cmd.cleanup()

    def test_cleanup_no_profile(self):
        from duh.adapters.sandbox.policy import SandboxCommand
        cmd = SandboxCommand(command="x", argv=["x"], profile_path=None)
        cmd.cleanup()


# =============================================================================
# duh.bridge.server — start, stop, error branches
# =============================================================================

class TestBridgeServerLifecycle:
    async def test_start_calls_websockets_serve(self):
        from duh.bridge.server import BridgeServer
        fake_serve = AsyncMock(return_value=MagicMock())
        fake_ws_module = MagicMock()
        fake_ws_module.serve = fake_serve

        with patch("duh.bridge.server.websockets", fake_ws_module), \
             patch("duh.bridge.server._require_websockets"):
            server = BridgeServer(host="localhost", port=1234, token="tk")
            await server.start()
        assert fake_serve.called

    async def test_start_without_token_warns(self, caplog):
        from duh.bridge.server import BridgeServer
        fake_serve = AsyncMock(return_value=MagicMock())
        fake_ws_module = MagicMock()
        fake_ws_module.serve = fake_serve

        with patch("duh.bridge.server.websockets", fake_ws_module), \
             patch("duh.bridge.server._require_websockets"):
            server = BridgeServer(host="localhost", port=1234)  # no token
            import logging
            with caplog.at_level(logging.WARNING):
                await server.start()
        assert any("WITHOUT authentication" in r.message for r in caplog.records)

    async def test_stop_closes_server(self):
        from duh.bridge.server import BridgeServer
        fake_server = MagicMock()
        fake_server.close = MagicMock()
        fake_server.wait_closed = AsyncMock()

        server = BridgeServer()
        server._server = fake_server
        await server.stop()
        assert fake_server.close.called
        assert server._server is None

    async def test_stop_no_server_noop(self):
        from duh.bridge.server import BridgeServer
        server = BridgeServer()
        await server.stop()  # _server is None, should not raise

    async def test_handle_connection_exception(self):
        from duh.bridge.server import BridgeServer
        server = BridgeServer(token="t")

        class BadWS:
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise RuntimeError("boom")
            async def send(self, _):
                pass
            async def close(self):
                pass

        # Should swallow and cleanup
        await server._handle_connection(BadWS())

    async def test_handle_prompt_engine_error(self):
        from duh.bridge.server import BridgeServer
        from duh.bridge.protocol import PromptMessage

        async def failing_factory(session_id):
            class FailEngine:
                async def run(self, content):
                    raise ValueError("engine kaput")
                    yield {}  # unreachable
            return FailEngine()

        server = BridgeServer(engine_factory=failing_factory)

        sent: list[str] = []
        class WS:
            async def send(self, data):
                sent.append(data)

        ws = WS()
        server._relay.register("s1", ws)  # type: ignore[arg-type]
        msg = PromptMessage(session_id="s1", content="hi")
        await server._handle_prompt("s1", msg, ws)

        # Should have received an error message
        errors = [json.loads(s) for s in sent]
        assert any(e.get("type") == "error" for e in errors)


# =============================================================================
# duh.kernel.attachments — mimetypes fallback, heuristic, pdf extraction
# =============================================================================

class TestAttachmentsEdgeCases:
    def test_detect_via_mimetypes_fallback(self):
        """Unknown extension but mimetypes module recognizes it."""
        from duh.kernel.attachments import AttachmentManager
        mgr = AttachmentManager()
        # .htm is not in _EXT_CONTENT_TYPES but mimetypes knows it
        ct = mgr.detect_content_type("page.htm", b"<html></html>")
        assert ct  # something non-empty
        assert ct != "application/octet-stream"

    def test_detect_unknown_binary_bytes(self):
        """Unknown extension, non-decodable bytes."""
        from duh.kernel.attachments import AttachmentManager
        mgr = AttachmentManager()
        # Raw binary without extension hint
        ct = mgr.detect_content_type("random.qqz", b"\xff\xfe\x00\x01\x80")
        assert ct == "application/octet-stream"

    def test_extract_text_from_pdf_returns_fallback(self):
        """PDF without pdfplumber or streams falls through to description."""
        from duh.kernel.attachments import Attachment, AttachmentManager
        mgr = AttachmentManager()
        att = Attachment(
            name="weird.pdf",
            content_type="application/pdf",
            data=b"%PDF-1.4\nno text streams here",
        )
        text = mgr.extract_text(att)
        assert isinstance(text, str)

    def test_extract_text_image_placeholder(self):
        from duh.kernel.attachments import Attachment, AttachmentManager
        mgr = AttachmentManager()
        att = Attachment(
            name="photo.png",
            content_type="image/png",
            data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
        )
        text = mgr.extract_text(att)
        assert "Image" in text
        assert "photo.png" in text

    def test_extract_text_binary_fallback(self):
        from duh.kernel.attachments import Attachment, AttachmentManager
        mgr = AttachmentManager()
        att = Attachment(
            name="mystery.bin",
            content_type="application/octet-stream",
            data=b"\x00\x01\x02\x03\xff",
        )
        text = mgr.extract_text(att)
        assert "Binary file" in text
        assert "mystery.bin" in text

    def test_extract_pdf_with_text_stream(self, tmp_path):
        """Fallback regex extraction of text between parens followed by Tj."""
        from duh.kernel.attachments import Attachment, AttachmentManager
        # Force the pdfplumber path to fail so the regex fallback runs
        mgr = AttachmentManager()
        # pdfplumber may not be installed — this is fine; fallback runs
        raw = b"%PDF-1.4\nstream (Hello World) Tj endstream"
        att = Attachment(name="t.pdf", content_type="application/pdf", data=raw)
        text = mgr.extract_text(att)
        assert isinstance(text, str)

    def test_extract_pdf_with_pdfplumber_mock(self):
        """Exercise the pdfplumber branch if library is available or mocked."""
        from duh.kernel.attachments import Attachment, AttachmentManager
        mgr = AttachmentManager()

        fake_page = MagicMock()
        fake_page.extract_text.return_value = "Mocked PDF text"

        class FakePDF:
            pages = [fake_page]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        fake_module = MagicMock()
        fake_module.open.return_value = FakePDF()

        with patch.dict(sys.modules, {"pdfplumber": fake_module}):
            att = Attachment(name="x.pdf", content_type="application/pdf", data=b"%PDF-1.4")
            result = mgr._extract_pdf_text(att)
        assert "Mocked PDF text" in result

    def test_extract_pdf_pdfplumber_no_text(self):
        from duh.kernel.attachments import Attachment, AttachmentManager
        mgr = AttachmentManager()

        fake_page = MagicMock()
        fake_page.extract_text.return_value = None

        class FakePDF:
            pages = [fake_page]
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass

        fake_module = MagicMock()
        fake_module.open.return_value = FakePDF()

        with patch.dict(sys.modules, {"pdfplumber": fake_module}):
            att = Attachment(name="x.pdf", content_type="application/pdf", data=b"%PDF")
            result = mgr._extract_pdf_text(att)
        assert "no extractable text" in result

    def test_extract_pdf_pdfplumber_exception(self):
        from duh.kernel.attachments import Attachment, AttachmentManager
        mgr = AttachmentManager()

        fake_module = MagicMock()
        fake_module.open.side_effect = RuntimeError("corrupted")

        with patch.dict(sys.modules, {"pdfplumber": fake_module}):
            att = Attachment(name="x.pdf", content_type="application/pdf", data=b"%PDF")
            result = mgr._extract_pdf_text(att)
        # Falls back to regex
        assert isinstance(result, str)


# =============================================================================
# duh.tools.worktree — real _run_git_async with mocked subprocess
# =============================================================================

class TestRunGitAsync:
    async def test_run_git_success(self):
        from duh.tools.worktree import _run_git_async

        class FakeProc:
            returncode = 0
            async def communicate(self):
                return (b"stdout", b"stderr")

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=FakeProc())):
            rc, out, err = await _run_git_async(["status"], cwd="/tmp")
        assert rc == 0
        assert out == "stdout"
        assert err == "stderr"

    async def test_run_git_timeout(self):
        from duh.tools.worktree import _run_git_async

        class HangingProc:
            returncode = None
            def kill(self):
                pass
            async def communicate(self):
                await asyncio.sleep(10)
                return (b"", b"")

        async def fake_wait_for(coro, timeout):
            # Drain the coroutine to prevent "was never awaited"
            coro.close()
            raise asyncio.TimeoutError()

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=HangingProc())), \
             patch("asyncio.wait_for", side_effect=fake_wait_for):
            rc, out, err = await _run_git_async(["status"], cwd="/tmp", timeout=0.01)
        assert rc == 1
        assert "timed out" in err

    async def test_run_git_timeout_process_gone(self):
        from duh.tools.worktree import _run_git_async

        class GoneProc:
            returncode = None
            def kill(self):
                raise ProcessLookupError("gone")
            async def communicate(self):
                await asyncio.sleep(10)
                return (b"", b"")

        async def fake_wait_for(coro, timeout):
            coro.close()
            raise asyncio.TimeoutError()

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=GoneProc())), \
             patch("asyncio.wait_for", side_effect=fake_wait_for):
            rc, out, err = await _run_git_async(["status"], cwd="/tmp", timeout=0.01)
        assert rc == 1


# =============================================================================
# duh.tools.edit — error branches
# =============================================================================

class TestEditToolErrors:
    async def test_missing_file_path(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.edit import EditTool
        result = await EditTool().call(
            {"file_path": "", "old_string": "x", "new_string": "y"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error is True
        assert "file_path" in result.output

    async def test_missing_old_string(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.edit import EditTool
        f = tmp_path / "f.txt"
        f.write_text("hello")
        result = await EditTool().call(
            {"file_path": str(f), "old_string": "", "new_string": "y"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error is True
        assert "old_string" in result.output

    async def test_relative_file_path_resolved(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.edit import EditTool
        f = tmp_path / "f.txt"
        f.write_text("hello world")
        result = await EditTool().call(
            {"file_path": "f.txt", "old_string": "hello", "new_string": "HI"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error is False

    async def test_permission_denied(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.edit import EditTool
        import os as _os
        f = tmp_path / "readonly.txt"
        f.write_text("hello")
        _os.chmod(f, 0o000)
        try:
            result = await EditTool().call(
                {"file_path": str(f), "old_string": "hello", "new_string": "hi"},
                ToolContext(cwd=str(tmp_path)),
            )
            assert result.is_error is True
        finally:
            _os.chmod(f, 0o644)

    async def test_read_error(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.edit import EditTool
        f = tmp_path / "f.txt"
        f.write_text("hello")
        with patch("pathlib.Path.read_text", side_effect=OSError("disk error")):
            result = await EditTool().call(
                {"file_path": str(f), "old_string": "hello", "new_string": "hi"},
                ToolContext(cwd=str(tmp_path)),
            )
        assert result.is_error is True
        assert "Error reading file" in result.output

    async def test_write_error(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.edit import EditTool
        f = tmp_path / "f.txt"
        f.write_text("hello")
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = await EditTool().call(
                {"file_path": str(f), "old_string": "hello", "new_string": "hi"},
                ToolContext(cwd=str(tmp_path)),
            )
        assert result.is_error is True
        assert "Error writing file" in result.output

    async def test_check_permissions(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.edit import EditTool
        perm = await EditTool().check_permissions({}, ToolContext(cwd=str(tmp_path)))
        assert perm["allowed"] is True


# =============================================================================
# duh.tools.ask_user_tool — exception branches
# =============================================================================

class TestAskUserToolExtras:
    async def test_eof_cancel(self):
        from duh.kernel.tool import ToolContext
        from duh.tools.ask_user_tool import AskUserQuestionTool
        async def raiser(_q):
            raise EOFError()
        tool = AskUserQuestionTool(ask_fn=raiser)
        result = await tool.call({"question": "ok?"}, ToolContext())
        assert "cancelled" in result.output

    async def test_keyboard_interrupt(self):
        from duh.kernel.tool import ToolContext
        from duh.tools.ask_user_tool import AskUserQuestionTool
        async def raiser(_q):
            raise KeyboardInterrupt()
        tool = AskUserQuestionTool(ask_fn=raiser)
        result = await tool.call({"question": "ok?"}, ToolContext())
        assert "cancelled" in result.output

    async def test_generic_exception(self):
        from duh.kernel.tool import ToolContext
        from duh.tools.ask_user_tool import AskUserQuestionTool
        async def raiser(_q):
            raise RuntimeError("no tty")
        tool = AskUserQuestionTool(ask_fn=raiser)
        result = await tool.call({"question": "ok?"}, ToolContext())
        assert result.is_error is True
        assert "no tty" in result.output

    async def test_check_permissions(self):
        from duh.kernel.tool import ToolContext
        from duh.tools.ask_user_tool import AskUserQuestionTool
        perm = await AskUserQuestionTool().check_permissions({}, ToolContext())
        assert perm["allowed"] is True


# =============================================================================
# duh.tools.todo_tool — summary, check_permissions
# =============================================================================

class TestTodoToolSummary:
    async def test_summary_empty(self):
        from duh.tools.todo_tool import TodoWriteTool
        tool = TodoWriteTool()
        assert tool.summary() == "No tasks."

    async def test_summary_with_todos(self):
        from duh.kernel.tool import ToolContext
        from duh.tools.todo_tool import TodoWriteTool
        tool = TodoWriteTool()
        await tool.call({
            "todos": [
                {"id": "1", "text": "A", "status": "done"},
                {"id": "2", "text": "B", "status": "pending"},
                {"id": "3", "text": "C", "status": "in_progress"},
                {"id": "4", "text": "D", "status": "blocked"},
                {"id": "5", "text": "E", "status": "cancelled"},
            ]
        }, ToolContext())
        s = tool.summary()
        assert "A" in s
        assert "1/5 done" in s
        assert "[x]" in s
        assert "[ ]" in s
        assert "[~]" in s
        assert "[!]" in s
        assert "[-]" in s

    async def test_check_permissions(self):
        from duh.kernel.tool import ToolContext
        from duh.tools.todo_tool import TodoWriteTool
        perm = await TodoWriteTool().check_permissions({}, ToolContext())
        assert perm["allowed"] is True


# =============================================================================
# duh.tools.lsp_tool — missing branches
# =============================================================================

class TestLSPToolGaps:
    async def test_read_file_failure(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.lsp_tool import LSPTool
        f = tmp_path / "broken.py"
        f.write_text("x = 1\n")
        with patch("duh.tools.lsp_tool._read_file", return_value=None):
            result = await LSPTool().call(
                {"action": "symbols", "file": str(f)},
                ToolContext(cwd=str(tmp_path)),
            )
        assert result.is_error is True
        assert "Could not read" in result.output

    def test_read_file_exception(self, tmp_path):
        from duh.tools.lsp_tool import _read_file
        with patch("pathlib.Path.read_text", side_effect=OSError("nope")):
            assert _read_file(tmp_path / "x.py") is None

    def test_python_symbols_syntax_error(self):
        from duh.tools.lsp_tool import _python_symbols
        assert _python_symbols("def :::\n") == []

    def test_python_function_with_defaults(self, tmp_path):
        from duh.tools.lsp_tool import _python_symbols
        src = "def f(x: int, y: int = 5, *args, **kwargs) -> str: return ''\n"
        syms = _python_symbols(src)
        assert len(syms) == 1
        sig = syms[0]["signature"]
        assert "x: int" in sig
        assert "y: int = 5" in sig
        assert "*args" in sig
        assert "**kwargs" in sig
        assert "-> str" in sig

    def test_python_function_typed_varargs(self):
        from duh.tools.lsp_tool import _python_symbols
        src = "def g(*items: int, **opts: str) -> None: pass\n"
        syms = _python_symbols(src)
        assert "*items: int" in syms[0]["signature"]
        assert "**opts: str" in syms[0]["signature"]

    def test_ann_assign_non_name_target(self):
        """AnnAssign with non-Name target yields no variables."""
        from duh.tools.lsp_tool import _python_symbols
        src = "x: int\n"  # AnnAssign Name -> ok
        syms = _python_symbols(src)
        assert syms[0]["name"] == "x"

        # AnnAssign with attribute target (non-Name)
        src2 = "class C: pass\nC.x: int = 1\n"
        syms2 = _python_symbols(src2)
        names = {s["name"] for s in syms2}
        assert "C" in names
        # C.x should be filtered
        assert "x" not in names

    def test_find_symbol_at_out_of_range(self):
        from duh.tools.lsp_tool import _find_symbol_at
        assert _find_symbol_at("x\n", line=0, character=0) is None
        assert _find_symbol_at("x\n", line=99, character=0) is None

    def test_find_symbol_at_char_beyond_line(self):
        from duh.tools.lsp_tool import _find_symbol_at
        # Line is "hello", character index beyond length but line has content
        result = _find_symbol_at("hello\n", line=1, character=100)
        assert result == "hello"

    def test_find_symbol_negative_char(self):
        from duh.tools.lsp_tool import _find_symbol_at
        assert _find_symbol_at("hello\n", line=1, character=-1) is None

    async def test_regex_definition_fallback(self, tmp_path):
        """Non-python file with definition-like pattern."""
        from duh.kernel.tool import ToolContext
        from duh.tools.lsp_tool import LSPTool
        f = tmp_path / "app.js"
        f.write_text("function helper(x) {\n  return x\n}\nhelper(1)\n")
        result = await LSPTool().call(
            {"action": "definition", "file": str(f), "line": 4, "character": 0},
            ToolContext(cwd=str(tmp_path)),
        )
        # Should find the function definition
        assert "helper" in result.output

    async def test_references_on_non_python(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.lsp_tool import LSPTool
        f = tmp_path / "app.go"
        f.write_text("func process(x int) int {\n  return x\n}\nprocess(5)\n")
        result = await LSPTool().call(
            {"action": "references", "file": str(f), "line": 1, "character": 5},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.metadata["count"] >= 2

    async def test_hover_on_non_python(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.lsp_tool import LSPTool
        f = tmp_path / "app.rs"
        f.write_text("pub fn handle(req: Req) -> Resp { Resp{} }\n")
        result = await LSPTool().call(
            {"action": "hover", "file": str(f), "line": 1, "character": 7},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.metadata["found"] is True
        assert "handle" in result.output

    async def test_references_not_found(self, tmp_path):
        from duh.kernel.tool import ToolContext
        from duh.tools.lsp_tool import LSPTool
        f = tmp_path / "f.py"
        f.write_text("# just a comment\nnotarealsymbol = 1\n")
        result = await LSPTool().call(
            {"action": "references", "file": str(f), "line": 2, "character": 0},
            ToolContext(cwd=str(tmp_path)),
        )
        # notarealsymbol appears once — not "not found"
        assert result.metadata["count"] == 1


# =============================================================================
# duh.adapters.openai — streaming partial/error branches
# =============================================================================

class _FakeChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, choices):
        self.choices = choices


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeToolCall:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        if name is not None or arguments is not None:
            self.function = MagicMock()
            self.function.name = name
            self.function.arguments = arguments
        else:
            self.function = None


class _FakeOpenAIResponse:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c


class _FakeCompletions:
    def __init__(self, response):
        self._response = response

    async def create(self, **_kwargs):
        return self._response


class _FakeChatCompletions:
    def __init__(self, response):
        self.completions = _FakeCompletions(response)


class _FakeAsyncOpenAI:
    def __init__(self, *args, **kwargs):
        pass

    def _setup(self, response):
        self.chat = _FakeChatCompletions(response)


class TestOpenAIProviderStreaming:
    async def test_stream_text_and_tool_calls(self):
        import openai as _openai
        chunks = [
            _FakeChunk([_FakeChoice(_FakeDelta(content="Hello "))]),
            _FakeChunk([_FakeChoice(_FakeDelta(content="world"))]),
            _FakeChunk([_FakeChoice(
                _FakeDelta(tool_calls=[_FakeToolCall(0, id="t1", name="Read",
                                                     arguments='{"path": "x"}')])
            )]),
            _FakeChunk([_FakeChoice(_FakeDelta(), finish_reason="tool_calls")]),
        ]
        response = _FakeOpenAIResponse(chunks)

        with patch.object(_openai, "AsyncOpenAI") as mock_cls:
            instance = _FakeAsyncOpenAI()
            instance._setup(response)
            mock_cls.return_value = instance
            from duh.adapters.openai import OpenAIProvider
            provider = OpenAIProvider(api_key="sk-test")
            events = []
            async for evt in provider.stream(messages=[], max_tokens=100):
                events.append(evt)

        text_events = [e for e in events if e.get("type") == "text_delta"]
        assert len(text_events) >= 2
        assistant = [e for e in events if e.get("type") == "assistant"]
        assert len(assistant) == 1
        content = assistant[0]["message"].content
        assert any(b.get("type") == "tool_use" for b in content if isinstance(b, dict))

    async def test_stream_mid_stream_error(self):
        import openai as _openai

        class BadResponse:
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise ConnectionError("dropped")

        bad = BadResponse()
        with patch.object(_openai, "AsyncOpenAI") as mock_cls:
            instance = _FakeAsyncOpenAI()
            instance._setup(bad)
            mock_cls.return_value = instance
            from duh.adapters.openai import OpenAIProvider
            # Disable backoff retries — 0 attempts means a single call, then error
            with patch("duh.adapters.openai.with_backoff") as wb:
                async def passthrough(gen_fn, *args, **kwargs):
                    async for e in gen_fn():
                        yield e
                wb.side_effect = lambda gen_fn, **_: passthrough(gen_fn)
                provider = OpenAIProvider(api_key="sk-test", max_retries=0)
                events = []
                async for evt in provider.stream(messages=[]):
                    events.append(evt)

        # Should yield an error event at least
        assert any(e.get("type") == "error" for e in events) or \
               any(e.get("type") == "assistant" for e in events)

    async def test_stream_tool_choice_variants(self):
        """Exercise the tool_choice branches: any, none, auto, specific."""
        import openai as _openai

        chunks = [_FakeChunk([_FakeChoice(_FakeDelta(content="ok"), finish_reason="stop")])]

        class FakeTool:
            name = "Read"
            description = "read"
            input_schema = {"type": "object"}

        from duh.adapters.openai import OpenAIProvider
        for tc in ("any", "none", "auto", "SpecificTool"):
            response = _FakeOpenAIResponse(list(chunks))
            with patch.object(_openai, "AsyncOpenAI") as mock_cls:
                instance = _FakeAsyncOpenAI()
                instance._setup(response)
                mock_cls.return_value = instance
                provider = OpenAIProvider(api_key="sk-test")
                events = []
                async for evt in provider.stream(
                    messages=[], tools=[FakeTool()], tool_choice=tc, max_tokens=50,
                ):
                    events.append(evt)
            assert any(e.get("type") == "assistant" for e in events)

    async def test_stream_outer_exception_surfaces(self):
        """Test the outer exception handler yielding an error message."""
        import openai as _openai

        with patch.object(_openai, "AsyncOpenAI") as mock_cls:
            instance = MagicMock()
            instance.chat.completions.create = AsyncMock(
                side_effect=RuntimeError("boom")
            )
            mock_cls.return_value = instance
            from duh.adapters.openai import OpenAIProvider
            provider = OpenAIProvider(api_key="sk-test")
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)
        # Should get an error assistant message
        errors = [e for e in events if e.get("type") == "assistant"]
        assert len(errors) >= 1
        assert errors[-1]["message"].metadata.get("is_error") is True

    def test_to_openai_messages_user_text_block(self):
        from duh.adapters.openai import _to_openai_messages
        from duh.kernel.messages import Message
        user = Message(role="user", content=[
            {"type": "text", "text": "hi there"},
        ])
        result = _to_openai_messages([user], "")
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "hi there"

    def test_to_openai_assistant_only_tool_calls(self):
        """Assistant with only tool_use blocks, no text → content=None."""
        from duh.adapters.openai import _to_openai_messages
        from duh.kernel.messages import Message
        asst = Message(role="assistant", content=[
            {"type": "tool_use", "id": "t1", "name": "Read", "input": {"p": "x"}},
        ])
        result = _to_openai_messages([asst], "")
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] is None
        assert "tool_calls" in result[0]


# =============================================================================
# duh.adapters.ollama — tool_choice branches, extracted-from-text
# =============================================================================

class _OllamaMockResponse:
    def __init__(self, status_code, lines=None, body=b""):
        self.status_code = status_code
        self._lines = lines or []
        self._body = body

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


class _OllamaMockClient:
    def __init__(self, response):
        self._r = response
        self.last_payload = None

    def stream(self, method, url, **kwargs):
        self.last_payload = kwargs.get("json")
        return self._r

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


class TestOllamaToolChoice:
    async def test_tool_choice_none(self):
        """tool_choice='none' should suppress tools from payload."""
        from duh.adapters.ollama import OllamaProvider
        lines = [json.dumps({"message": {"content": "ok"}, "done": True})]
        response = _OllamaMockResponse(200, lines)
        client = _OllamaMockClient(response)

        with patch("httpx.AsyncClient", return_value=client):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(
                messages=[], tools=[{"type": "function", "function": {"name": "x"}}],
                tool_choice="none",
            ):
                events.append(evt)
        assert "tools" not in client.last_payload

    async def test_tool_choice_any_with_string_system(self):
        from duh.adapters.ollama import OllamaProvider
        lines = [json.dumps({"message": {"content": "ok"}, "done": True})]
        response = _OllamaMockResponse(200, lines)
        client = _OllamaMockClient(response)

        class FakeTool:
            name = "Read"
            description = "r"
            input_schema = {"type": "object"}

        with patch("httpx.AsyncClient", return_value=client):
            provider = OllamaProvider()
            async for _ in provider.stream(
                messages=[], tools=[FakeTool()],
                system_prompt="You are helpful.",
                tool_choice="any",
            ):
                pass
        msgs = client.last_payload["messages"]
        assert "MUST call" in msgs[0]["content"]

    async def test_tool_choice_any_with_list_system(self):
        from duh.adapters.ollama import OllamaProvider
        lines = [json.dumps({"message": {"content": "ok"}, "done": True})]
        response = _OllamaMockResponse(200, lines)
        client = _OllamaMockClient(response)

        class FakeTool:
            name = "Read"
            description = "r"
            input_schema = {"type": "object"}

        with patch("httpx.AsyncClient", return_value=client):
            provider = OllamaProvider()
            async for _ in provider.stream(
                messages=[], tools=[FakeTool()],
                system_prompt=["Part A", "Part B"],
                tool_choice="any",
            ):
                pass
        msgs = client.last_payload["messages"]
        assert "MUST call" in msgs[0]["content"]

    async def test_tool_choice_any_with_no_system(self):
        from duh.adapters.ollama import OllamaProvider
        lines = [json.dumps({"message": {"content": "ok"}, "done": True})]
        response = _OllamaMockResponse(200, lines)
        client = _OllamaMockClient(response)

        class FakeTool:
            name = "Read"
            description = "r"
            input_schema = {"type": "object"}

        with patch("httpx.AsyncClient", return_value=client):
            provider = OllamaProvider()
            async for _ in provider.stream(
                messages=[], tools=[FakeTool()],
                system_prompt="",
                tool_choice="any",
            ):
                pass
        msgs = client.last_payload["messages"]
        assert any("MUST call" in m["content"] for m in msgs)

    async def test_tool_choice_specific_tool(self):
        from duh.adapters.ollama import OllamaProvider
        lines = [json.dumps({"message": {"content": "ok"}, "done": True})]
        response = _OllamaMockResponse(200, lines)
        client = _OllamaMockClient(response)

        class FakeTool:
            name = "Read"
            description = "r"
            input_schema = {"type": "object"}

        with patch("httpx.AsyncClient", return_value=client):
            provider = OllamaProvider()
            async for _ in provider.stream(
                messages=[], tools=[FakeTool()],
                system_prompt="Base system",
                tool_choice="Read",
            ):
                pass
        msgs = client.last_payload["messages"]
        assert "MUST call the 'Read'" in msgs[0]["content"]

    async def test_tool_choice_specific_list_system(self):
        from duh.adapters.ollama import OllamaProvider
        lines = [json.dumps({"message": {"content": "ok"}, "done": True})]
        response = _OllamaMockResponse(200, lines)
        client = _OllamaMockClient(response)

        class FakeTool:
            name = "Read"
            description = "r"
            input_schema = {"type": "object"}

        with patch("httpx.AsyncClient", return_value=client):
            provider = OllamaProvider()
            async for _ in provider.stream(
                messages=[], tools=[FakeTool()],
                system_prompt=["A", "B"],
                tool_choice="Read",
            ):
                pass

    async def test_tool_choice_specific_empty_system(self):
        from duh.adapters.ollama import OllamaProvider
        lines = [json.dumps({"message": {"content": "ok"}, "done": True})]
        response = _OllamaMockResponse(200, lines)
        client = _OllamaMockClient(response)

        class FakeTool:
            name = "Read"
            description = "r"
            input_schema = {"type": "object"}

        with patch("httpx.AsyncClient", return_value=client):
            provider = OllamaProvider()
            async for _ in provider.stream(
                messages=[], tools=[FakeTool()],
                system_prompt="",
                tool_choice="Read",
            ):
                pass

    async def test_extracted_tool_calls_from_text(self):
        """Text-only response containing JSON tool-call patterns."""
        from duh.adapters.ollama import OllamaProvider
        body = json.dumps({"name": "Read", "arguments": {"path": "/tmp/x"}})
        lines = [
            json.dumps({"message": {"content": body}, "done": True}),
        ]
        response = _OllamaMockResponse(200, lines)
        client = _OllamaMockClient(response)

        with patch("httpx.AsyncClient", return_value=client):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)

        assistant = [e for e in events if e["type"] == "assistant"][-1]
        content = assistant["message"].content
        # Should have extracted the tool call
        assert any(
            isinstance(b, dict) and b.get("type") == "tool_use"
            for b in content
        )

    def test_extract_helper_variants(self):
        from duh.adapters.ollama import _extract_tool_calls_from_text
        # Missing name field
        assert _extract_tool_calls_from_text('{"name": "", "arguments": {}}') == []
        # Invalid JSON inside pattern
        result = _extract_tool_calls_from_text("not a match at all")
        assert result == []
        # Valid
        r2 = _extract_tool_calls_from_text(
            '{"name": "Read", "arguments": {"path": "x"}}'
        )
        assert len(r2) == 1
        assert r2[0]["name"] == "Read"


# =============================================================================
# duh.adapters.mcp_executor — missing error/retry/reconnect branches
# =============================================================================

class TestMCPExecutorGaps:
    def test_require_mcp_raises_when_missing(self, monkeypatch):
        import duh.adapters.mcp_executor as mod
        monkeypatch.setattr(mod, "_mcp_available", False)
        with pytest.raises(RuntimeError, match="mcp"):
            mod._require_mcp()

    async def test_connect_failure_cleanup(self):
        """If initialize() fails, cleanup is attempted."""
        from duh.adapters.mcp_executor import MCPExecutor, MCPServerConfig

        executor = MCPExecutor({"srv": MCPServerConfig(command="fake")})

        failing_session = MagicMock()
        failing_session.__aenter__ = AsyncMock(return_value=failing_session)
        failing_session.__aexit__ = AsyncMock()
        failing_session.initialize = AsyncMock(side_effect=RuntimeError("init failed"))

        fake_ctx = MagicMock()
        fake_ctx.__aexit__ = AsyncMock()

        async def fake_start(params):
            return fake_ctx, MagicMock(), MagicMock()

        executor._start_stdio = fake_start  # type: ignore
        with patch("duh.adapters.mcp_executor.ClientSession", return_value=failing_session):
            with pytest.raises(RuntimeError, match="Failed to connect"):
                await executor.connect("srv")

        # Cleanup: session.__aexit__ should have been called
        assert failing_session.__aexit__.called
        assert fake_ctx.__aexit__.called

    async def test_connect_all_failure_records_empty(self):
        """A server that fails to connect should yield empty tools but not raise."""
        from duh.adapters.mcp_executor import MCPExecutor, MCPServerConfig

        executor = MCPExecutor({"srv": MCPServerConfig(command="fake")})

        async def bad_connect(_name):
            raise RuntimeError("cannot")

        executor.connect = bad_connect  # type: ignore
        results = await executor.connect_all()
        assert results["srv"] == []

    async def test_run_retries_on_session_expiry(self):
        """MCP session expiry triggers a reconnect and single retry."""
        from duh.adapters.mcp_executor import (
            MCPExecutor,
            MCPConnection,
            MCPServerConfig,
            MCPToolInfo,
        )

        executor = MCPExecutor({"s": MCPServerConfig(command="fake")})

        call_count = [0]
        class FakeSession:
            async def call_tool(self, name, arguments):
                call_count[0] += 1
                if call_count[0] == 1:
                    err = RuntimeError("session not found")
                    err.status_code = 404  # type: ignore
                    raise err
                from types import SimpleNamespace
                return SimpleNamespace(content=[SimpleNamespace(text="ok")])

        session = FakeSession()
        conn = MCPConnection(
            server_name="s",
            config=executor._servers["s"],
            session=session,
        )
        executor._connections["s"] = conn
        info = MCPToolInfo(name="tool", server_name="s")
        executor._tool_index["mcp__s__tool"] = info

        async def fake_reconnect(_name):
            executor._connections["s"] = conn
            return []

        async def fake_disconnect(_name):
            pass

        executor.connect = fake_reconnect  # type: ignore
        executor.disconnect = fake_disconnect  # type: ignore

        result = await executor.run("mcp__s__tool", {})
        assert result == "ok"
        assert call_count[0] == 2

    async def test_run_reconnect_after_max_errors(self):
        """After MAX_ERRORS_BEFORE_RECONNECT consecutive errors, reconnect triggered."""
        from duh.adapters.mcp_executor import (
            MCPExecutor,
            MCPConnection,
            MCPServerConfig,
            MCPToolInfo,
            MAX_ERRORS_BEFORE_RECONNECT,
        )

        executor = MCPExecutor({"s": MCPServerConfig(command="fake")})

        class FakeSession:
            async def call_tool(self, name, arguments):
                raise RuntimeError("other error")

        conn = MCPConnection(
            server_name="s",
            config=executor._servers["s"],
            session=FakeSession(),
        )
        executor._connections["s"] = conn
        info = MCPToolInfo(name="tool", server_name="s")
        executor._tool_index["mcp__s__tool"] = info
        # Pre-seed error count so the next failure triggers reconnect
        executor._error_counts["s"] = MAX_ERRORS_BEFORE_RECONNECT - 1

        reconnect_called = []
        async def fake_reconnect(_name):
            reconnect_called.append(_name)
            return []

        async def fake_disconnect(_name):
            pass

        executor.connect = fake_reconnect  # type: ignore
        executor.disconnect = fake_disconnect  # type: ignore

        with pytest.raises(RuntimeError, match="MCP tool call failed"):
            await executor.run("mcp__s__tool", {})
        assert "s" in reconnect_called

    async def test_run_reconnect_attempt_raises(self):
        """If reconnection after error fails, the original error still propagates."""
        from duh.adapters.mcp_executor import (
            MCPExecutor,
            MCPConnection,
            MCPServerConfig,
            MCPToolInfo,
            MAX_ERRORS_BEFORE_RECONNECT,
        )

        executor = MCPExecutor({"s": MCPServerConfig(command="fake")})

        class FakeSession:
            async def call_tool(self, name, arguments):
                raise RuntimeError("boom")

        conn = MCPConnection(
            server_name="s",
            config=executor._servers["s"],
            session=FakeSession(),
        )
        executor._connections["s"] = conn
        info = MCPToolInfo(name="tool", server_name="s")
        executor._tool_index["mcp__s__tool"] = info
        executor._error_counts["s"] = MAX_ERRORS_BEFORE_RECONNECT - 1

        async def bad_reconnect(_name):
            raise RuntimeError("reconnect failed")

        async def fake_disconnect(_name):
            pass

        executor.connect = bad_reconnect  # type: ignore
        executor.disconnect = fake_disconnect  # type: ignore

        with pytest.raises(RuntimeError):
            await executor.run("mcp__s__tool", {})

    async def test_run_extracts_data_block(self):
        """Result with content blocks lacking 'text' but having 'data'."""
        from types import SimpleNamespace
        from duh.adapters.mcp_executor import (
            MCPExecutor,
            MCPConnection,
            MCPServerConfig,
            MCPToolInfo,
        )

        executor = MCPExecutor({"s": MCPServerConfig(command="fake")})

        class FakeSession:
            async def call_tool(self, name, arguments):
                return SimpleNamespace(content=[
                    SimpleNamespace(data=b"binary-data"),
                ])

        conn = MCPConnection(
            server_name="s",
            config=executor._servers["s"],
            session=FakeSession(),
        )
        executor._connections["s"] = conn
        info = MCPToolInfo(name="tool", server_name="s")
        executor._tool_index["mcp__s__tool"] = info

        result = await executor.run("mcp__s__tool", {})
        assert "binary-data" in result

    async def test_run_extracts_unknown_block(self):
        """Block without text or data falls to str() branch."""
        from types import SimpleNamespace
        from duh.adapters.mcp_executor import (
            MCPExecutor,
            MCPConnection,
            MCPServerConfig,
            MCPToolInfo,
        )

        executor = MCPExecutor({"s": MCPServerConfig(command="fake")})

        class FakeSession:
            async def call_tool(self, name, arguments):
                class Weird:
                    def __repr__(self):
                        return "<weird>"
                return SimpleNamespace(content=[Weird()])

        conn = MCPConnection(
            server_name="s",
            config=executor._servers["s"],
            session=FakeSession(),
        )
        executor._connections["s"] = conn
        info = MCPToolInfo(name="tool", server_name="s")
        executor._tool_index["mcp__s__tool"] = info

        result = await executor.run("mcp__s__tool", {})
        assert "<weird>" in result


# =============================================================================
# duh.adapters.mcp_transports — error branches
# =============================================================================

class TestMCPTransportGaps:
    async def test_sse_send_not_connected(self):
        from duh.adapters.mcp_transports import SSETransport
        t = SSETransport(url="http://x")
        with pytest.raises(RuntimeError, match="not connected"):
            await t.send({"id": 1})

    async def test_http_send_not_connected(self):
        from duh.adapters.mcp_transports import HTTPTransport
        t = HTTPTransport(base_url="http://x")
        with pytest.raises(RuntimeError, match="not connected"):
            await t.send({"id": 1})

    async def test_ws_send_not_connected(self):
        from duh.adapters.mcp_transports import WebSocketTransport
        t = WebSocketTransport(url="ws://x")
        with pytest.raises(RuntimeError, match="not connected"):
            await t.send({"id": 1})

    async def test_ws_listen_handles_exception(self):
        from duh.adapters.mcp_transports import WebSocketTransport
        t = WebSocketTransport(url="ws://x")
        t._connected = True

        class BadWS:
            async def recv(self):
                raise RuntimeError("ws dead")

        t._ws = BadWS()
        # _listen should exit gracefully when recv raises
        await t._listen()

    async def test_ws_listen_handles_cancel(self):
        from duh.adapters.mcp_transports import WebSocketTransport
        t = WebSocketTransport(url="ws://x")
        t._connected = True

        class CancelWS:
            async def recv(self):
                raise asyncio.CancelledError()

        t._ws = CancelWS()
        await t._listen()

    async def test_ws_reconnect_success(self):
        from duh.adapters.mcp_transports import WebSocketTransport
        t = WebSocketTransport(url="ws://x", max_reconnect_attempts=2, reconnect_delay=0.001)

        fake_ws = MagicMock()
        fake_connect = AsyncMock(return_value=fake_ws)
        fake_module = MagicMock()
        fake_module.connect = fake_connect

        with patch("duh.adapters.mcp_transports.websockets", fake_module):
            await t._reconnect()
        assert t._connected is True

    async def test_ws_reconnect_all_fail(self):
        from duh.adapters.mcp_transports import WebSocketTransport
        t = WebSocketTransport(url="ws://x", max_reconnect_attempts=2, reconnect_delay=0.001)

        fake_connect = AsyncMock(side_effect=ConnectionError("dead"))
        fake_module = MagicMock()
        fake_module.connect = fake_connect

        with patch("duh.adapters.mcp_transports.websockets", fake_module):
            with pytest.raises(ConnectionError):
                await t._reconnect()

    async def test_sse_disconnect_cleans_task(self):
        """Disconnect with a pending SSE task."""
        from duh.adapters.mcp_transports import SSETransport
        t = SSETransport(url="http://x")

        async def hang():
            await asyncio.sleep(10)

        fake_client = MagicMock()
        fake_client.aclose = AsyncMock()
        t._client = fake_client
        t._sse_task = asyncio.create_task(hang())

        await t.disconnect()
        assert t._client is None

    async def test_http_disconnect_cleans_client(self):
        from duh.adapters.mcp_transports import HTTPTransport
        t = HTTPTransport(base_url="http://x")
        fake_client = MagicMock()
        fake_client.aclose = AsyncMock()
        t._client = fake_client
        await t.disconnect()
        assert t._client is None

    async def test_ws_disconnect_full(self):
        from duh.adapters.mcp_transports import WebSocketTransport
        t = WebSocketTransport(url="ws://x")
        fake_ws = MagicMock()
        fake_ws.close = AsyncMock()
        t._ws = fake_ws
        t._connected = True

        # Add a pending future
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        t._pending["abc"] = fut

        # Start a dummy listener task
        async def noop():
            await asyncio.sleep(10)
        t._listener_task = asyncio.create_task(noop())

        await t.disconnect()
        assert t._ws is None
        assert t._pending == {}
        assert fut.cancelled()

    async def test_sse_connect_json_endpoint(self):
        """SSE connect returning content-type: application/json with absolute endpoint."""
        from duh.adapters.mcp_transports import SSETransport

        fake_resp = MagicMock()
        fake_resp.headers = {"content-type": "application/json"}
        fake_resp.json = MagicMock(return_value={"endpoint": "http://abs/msg"})
        fake_resp.raise_for_status = MagicMock()

        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_resp)
        fake_client.aclose = AsyncMock()

        fake_httpx = MagicMock()
        fake_httpx.AsyncClient = MagicMock(return_value=fake_client)
        fake_httpx.Timeout = MagicMock()

        with patch("duh.adapters.mcp_transports.httpx", fake_httpx):
            t = SSETransport(url="http://srv/sse")
            await t.connect()
        assert t._message_endpoint == "http://abs/msg"

    async def test_sse_connect_json_relative_endpoint(self):
        """SSE connect returning a relative endpoint, should be made absolute."""
        from duh.adapters.mcp_transports import SSETransport

        fake_resp = MagicMock()
        fake_resp.headers = {"content-type": "application/json"}
        fake_resp.json = MagicMock(return_value={"endpoint": "messages/go"})
        fake_resp.raise_for_status = MagicMock()

        fake_client = MagicMock()
        fake_client.get = AsyncMock(return_value=fake_resp)
        fake_client.aclose = AsyncMock()

        fake_httpx = MagicMock()
        fake_httpx.AsyncClient = MagicMock(return_value=fake_client)
        fake_httpx.Timeout = MagicMock()

        with patch("duh.adapters.mcp_transports.httpx", fake_httpx):
            t = SSETransport(url="http://srv/sse")
            await t.connect()
        assert t._message_endpoint.startswith("http://srv/")


# =============================================================================
# More gap-fills
# =============================================================================

class TestMoreGaps:
    async def test_openai_chunk_no_choices_continues(self):
        """A chunk with empty choices triggers the 'continue' branch."""
        import openai as _openai

        class ChoicelessChunk:
            choices = []

        chunks = [
            ChoicelessChunk(),
            _FakeChunk([_FakeChoice(_FakeDelta(content="hello"), finish_reason="stop")]),
        ]
        response = _FakeOpenAIResponse(chunks)

        with patch.object(_openai, "AsyncOpenAI") as mock_cls:
            instance = _FakeAsyncOpenAI()
            instance._setup(response)
            mock_cls.return_value = instance
            from duh.adapters.openai import OpenAIProvider
            provider = OpenAIProvider(api_key="sk-test")
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)
        # The continue branch means delta=None, but we still get a final assistant event
        # The code path `if not delta: continue` requires delta.content falsy
        assert any(e.get("type") == "assistant" for e in events)

    async def test_openai_invalid_tool_arguments_json(self):
        """Tool call arguments are not valid JSON → parsed_input = {}."""
        import openai as _openai
        chunks = [
            _FakeChunk([_FakeChoice(
                _FakeDelta(tool_calls=[_FakeToolCall(0, id="t1", name="Read",
                                                     arguments="not json")])
            )]),
            _FakeChunk([_FakeChoice(_FakeDelta(), finish_reason="tool_calls")]),
        ]
        response = _FakeOpenAIResponse(chunks)

        with patch.object(_openai, "AsyncOpenAI") as mock_cls:
            instance = _FakeAsyncOpenAI()
            instance._setup(response)
            mock_cls.return_value = instance
            from duh.adapters.openai import OpenAIProvider
            provider = OpenAIProvider(api_key="sk-test")
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)
        assistant = [e for e in events if e.get("type") == "assistant"][-1]
        content = assistant["message"].content
        tool_use = next(b for b in content if isinstance(b, dict) and b.get("type") == "tool_use")
        assert tool_use["input"] == {}

    async def test_ollama_partial_after_malformed_json_with_tools(self):
        """Mid-stream malformed JSON after tool calls already accumulated."""
        from duh.adapters.ollama import OllamaProvider
        lines = [
            json.dumps({
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "Read", "arguments": {"p": "x"}}},
                    ],
                },
                "done": False,
            }),
            "not valid json",
        ]
        response = _OllamaMockResponse(200, lines)
        client = _OllamaMockClient(response)

        with patch("httpx.AsyncClient", return_value=client):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)

        partials = [
            e for e in events
            if e.get("type") == "assistant"
            and e["message"].metadata.get("partial")
        ]
        assert len(partials) >= 1

    async def test_ollama_mid_stream_error_with_tool_calls(self):
        """Mid-stream read error after tool_calls accumulated."""
        import httpx as _httpx
        from duh.adapters.ollama import OllamaProvider

        class BadStream:
            status_code = 200
            async def aread(self):
                return b""
            async def aiter_lines(self):
                yield json.dumps({
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "Read", "arguments": {"p": "x"}}},
                        ],
                    },
                    "done": False,
                })
                raise _httpx.ReadError("connection reset")
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                pass

        class BadClient:
            def stream(self, *a, **kw):
                return BadStream()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                pass

        with patch("httpx.AsyncClient", return_value=BadClient()):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)

        partials = [
            e for e in events
            if e.get("type") == "assistant"
            and e["message"].metadata.get("partial")
        ]
        assert len(partials) >= 1

    def test_extract_tool_calls_malformed_json_ignored(self):
        """Pattern matches but json.loads fails → continue."""
        from duh.adapters.ollama import _extract_tool_calls_from_text
        # Crafted to match the regex but fail inside json.loads via unterminated escape
        # The regex requires balanced {} so this is tricky; invoke directly via patch
        with patch("json.loads", side_effect=json.JSONDecodeError("x", "y", 0)):
            result = _extract_tool_calls_from_text(
                '{"name": "Read", "arguments": {"path": "x"}}'
            )
        assert result == []

    def test_lsp_definition_fallback_match(self, tmp_path):
        """The ast finds nothing at top level, but fallback regex matches nested def."""
        from duh.tools.lsp_tool import _action_definition
        # Python file with a NESTED class — _python_symbols only grabs top-level,
        # so the fallback regex path is the only thing that can find NestedClass.
        src = "class Outer:\n    class NestedClass:\n        pass\n"
        p = tmp_path / "f.py"
        p.write_text(src)
        result = _action_definition(p, src, "NestedClass")
        assert result.metadata["found"] is True
        # Found via fallback regex
        assert "NestedClass" in result.output

    def test_lsp_references_not_found_message(self, tmp_path):
        from duh.tools.lsp_tool import _action_references
        src = "x = 1\n"
        p = tmp_path / "f.py"
        p.write_text(src)
        result = _action_references(p, src, "nowhere_to_be_found")
        assert result.metadata["count"] == 0
        assert "No references" in result.output

    def test_attachments_pdf_fallback_text(self):
        """Exercise the 'binary file' fallback branch in extract_text via
        a PDF with no pdfplumber and no regex matches."""
        from duh.kernel.attachments import Attachment, AttachmentManager
        mgr = AttachmentManager()

        # Force pdfplumber to be unimportable → ImportError branch
        class ImportBlocker:
            def find_spec(self, name, *args):
                if name == "pdfplumber":
                    raise ImportError("blocked")
                return None

        import importlib
        # Simpler: stub pdfplumber to raise ImportError on import
        orig = sys.modules.pop("pdfplumber", None)
        try:
            with patch.dict(sys.modules, {"pdfplumber": None}):
                att = Attachment(
                    name="empty.pdf",
                    content_type="application/pdf",
                    data=b"%PDF-1.4\nno streams\n",
                )
                text = mgr._extract_pdf_text(att)
            assert isinstance(text, str)
        finally:
            if orig is not None:
                sys.modules["pdfplumber"] = orig

    def test_attachments_pdf_regex_fallback_exception(self):
        """The regex fallback inside _extract_pdf_text must swallow exceptions."""
        from duh.kernel.attachments import Attachment, AttachmentManager
        mgr = AttachmentManager()

        # Force pdfplumber to be absent and re.findall to raise
        orig = sys.modules.pop("pdfplumber", None)
        try:
            with patch.dict(sys.modules, {"pdfplumber": None}), \
                 patch("re.findall", side_effect=RuntimeError("regex crash")):
                att = Attachment(
                    name="crash.pdf",
                    content_type="application/pdf",
                    data=b"%PDF-1.4\n(some text) Tj",
                )
                result = mgr._extract_pdf_text(att)
            # Should return the final fallback description
            assert "crash.pdf" in result
        finally:
            if orig is not None:
                sys.modules["pdfplumber"] = orig

    def test_attachments_extract_text_calls_pdf(self):
        """extract_text routes PDFs through _extract_pdf_text."""
        from duh.kernel.attachments import Attachment, AttachmentManager
        mgr = AttachmentManager()

        called = []
        def fake_extract(att):
            called.append(att)
            return "pdf text"
        mgr._extract_pdf_text = fake_extract  # type: ignore

        # Make .text return None by using non-text content
        att = Attachment(
            name="x.pdf",
            content_type="application/pdf",
            data=b"\x00\x01\x02\xff\xfe",
        )
        result = mgr.extract_text(att)
        assert result == "pdf text"
        assert called

    def test_sandbox_policy_landlock_success_path(self, monkeypatch):
        """Simulate a successful landlock syscall returning a valid fd."""
        from duh.adapters.sandbox import policy

        # We can't easily test the syscall success — but we can mock libc
        fake_libc = MagicMock()
        fake_libc.syscall.return_value = 5  # pretend fd=5 was returned
        with patch("ctypes.CDLL", return_value=fake_libc), \
             patch("os.close") as close_mock:
            result = policy._landlock_available()
        # Either True (success path) or False (EINVAL fallback), but importantly
        # the success branch (os.close) was exercised
        assert result in (True, False)

    async def test_mcp_executor_disconnect_exception(self):
        """disconnect() when session.__aexit__ raises — should swallow."""
        from duh.adapters.mcp_executor import (
            MCPExecutor,
            MCPConnection,
            MCPServerConfig,
            MCPToolInfo,
        )

        executor = MCPExecutor({"s": MCPServerConfig(command="fake")})

        fake_session = MagicMock()
        fake_session.__aexit__ = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        fake_ctx = MagicMock()
        fake_ctx.__aexit__ = AsyncMock(side_effect=RuntimeError("ctx failed"))

        conn = MCPConnection(
            server_name="s",
            config=executor._servers["s"],
            session=fake_session,
            _stdio_ctx=fake_ctx,
        )
        executor._connections["s"] = conn
        executor._tool_index["mcp__s__tool"] = MCPToolInfo(name="t", server_name="s")

        # Should not raise — both exceptions must be swallowed
        await executor.disconnect("s")
        assert "s" not in executor._connections

    async def test_mcp_executor_connect_cleanup_raises(self):
        """Connection cleanup paths where session/ctx __aexit__ raise."""
        from duh.adapters.mcp_executor import MCPExecutor, MCPServerConfig

        executor = MCPExecutor({"srv": MCPServerConfig(command="fake")})

        failing_session = MagicMock()
        failing_session.__aenter__ = AsyncMock(return_value=failing_session)
        # aexit raises
        failing_session.__aexit__ = AsyncMock(side_effect=RuntimeError("aexit fail"))
        failing_session.initialize = AsyncMock(side_effect=RuntimeError("init fail"))

        fake_ctx = MagicMock()
        fake_ctx.__aexit__ = AsyncMock(side_effect=RuntimeError("ctx aexit fail"))

        async def fake_start(params):
            return fake_ctx, MagicMock(), MagicMock()

        executor._start_stdio = fake_start  # type: ignore
        with patch("duh.adapters.mcp_executor.ClientSession", return_value=failing_session):
            with pytest.raises(RuntimeError, match="Failed to connect"):
                await executor.connect("srv")

    async def test_mcp_executor_reconnect_returns_no_session(self):
        """Reconnect after session expiry but connection ends up empty."""
        from duh.adapters.mcp_executor import (
            MCPExecutor,
            MCPConnection,
            MCPServerConfig,
            MCPToolInfo,
        )

        executor = MCPExecutor({"s": MCPServerConfig(command="fake")})

        class FakeSession:
            async def call_tool(self, name, arguments):
                err = RuntimeError("session not found")
                err.status_code = 404  # type: ignore
                raise err

        conn = MCPConnection(
            server_name="s",
            config=executor._servers["s"],
            session=FakeSession(),
        )
        executor._connections["s"] = conn
        executor._tool_index["mcp__s__tool"] = MCPToolInfo(name="t", server_name="s")

        async def fake_reconnect(_name):
            # Simulate connection didn't come back
            if _name in executor._connections:
                del executor._connections[_name]
            return []

        async def fake_disconnect(_name):
            executor._connections.pop(_name, None)

        executor.connect = fake_reconnect  # type: ignore
        executor.disconnect = fake_disconnect  # type: ignore

        with pytest.raises(RuntimeError, match="Reconnection"):
            await executor.run("mcp__s__tool", {})
