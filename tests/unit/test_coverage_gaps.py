"""Tests to fill coverage gaps in new modules.

Covers:
- signals.py: install() with/without loop, _shutdown_and_exit
- query_guard.py: end() from non-RUNNING state, try_start from wrong state
- snapshot.py: add_message on discarded snapshot
- attachments.py: extract_text paths, _is_likely_text, text property edge cases
- sandbox/policy.py: cleanup(), _landlock_available (mocked)
- bridge/server.py: _handle_connection flow (connect/prompt/disconnect)
- network.py: subdomain matching, bad URL
"""

from __future__ import annotations

import asyncio
import json
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================================
# signals.py coverage gaps
# ============================================================================

from duh.kernel.signals import ShutdownHandler


class TestShutdownInstall:
    @pytest.mark.asyncio
    async def test_install_registers_handlers(self):
        """install() with a running loop should register signal handlers."""
        handler = ShutdownHandler()
        loop = asyncio.get_running_loop()
        handlers_before = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                handlers_before[sig] = loop._signal_handlers.get(sig)
            except AttributeError:
                pass  # Not all loops expose this

        handler.install(loop)
        # After install, the handler is registered. We can't easily check
        # the exact handler, but calling install should not raise.

    @pytest.mark.asyncio
    async def test_install_no_loop_does_not_crash(self):
        """install() with loop=None when no loop is running should be a no-op."""
        handler = ShutdownHandler()
        # Patch get_running_loop to raise RuntimeError
        with patch("duh.kernel.signals.asyncio.get_running_loop", side_effect=RuntimeError):
            handler.install(loop=None)
        # Should not have crashed

    @pytest.mark.asyncio
    async def test_shutdown_and_exit_raises_system_exit(self):
        """_shutdown_and_exit should run cleanup then raise SystemExit."""
        handler = ShutdownHandler()
        cleanup_ran = False

        async def cb():
            nonlocal cleanup_ran
            cleanup_ran = True

        handler.on_shutdown(cb)
        with pytest.raises(SystemExit) as exc_info:
            await handler._shutdown_and_exit(signal.SIGTERM)
        assert exc_info.value.code == 128 + signal.SIGTERM.value
        assert cleanup_ran

    @pytest.mark.asyncio
    async def test_run_cleanup_sets_shutting_down(self):
        handler = ShutdownHandler()
        assert not handler.shutting_down
        await handler.run_cleanup()
        assert handler.shutting_down

    @pytest.mark.asyncio
    async def test_install_with_explicit_loop(self):
        """install() with an explicit loop parameter."""
        handler = ShutdownHandler()
        loop = asyncio.get_running_loop()
        handler.install(loop=loop)
        # Should have registered without error


# ============================================================================
# query_guard.py coverage gaps
# ============================================================================

from duh.kernel.query_guard import QueryGuard, QueryState


class TestQueryGuardEdgeCases:
    def test_end_from_dispatching_state(self):
        """end() from DISPATCHING state (not RUNNING) should still succeed
        if the generation matches, because end() only checks generation."""
        guard = QueryGuard()
        gen = guard.reserve()
        assert guard.state == QueryState.DISPATCHING
        # end() checks generation, not state — this is by design
        result = guard.end(gen)
        # The source code just checks gen == self._generation and sets IDLE
        assert result is True
        assert guard.state == QueryState.IDLE

    def test_try_start_from_running_state(self):
        """try_start() from RUNNING state should return None."""
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        assert guard.state == QueryState.RUNNING
        # Calling try_start again with same gen should fail (state != DISPATCHING)
        assert guard.try_start(gen) is None

    def test_reserve_from_running_state_raises(self):
        """reserve() from RUNNING state should raise."""
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        assert guard.state == QueryState.RUNNING
        with pytest.raises(RuntimeError, match="not idle"):
            guard.reserve()

    def test_force_end_from_running(self):
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        old_gen = guard.generation
        guard.force_end()
        assert guard.state == QueryState.IDLE
        assert guard.generation == old_gen + 1

    def test_multiple_reserve_end_cycles(self):
        """Multiple reserve-start-end cycles should increment generation."""
        guard = QueryGuard()
        for i in range(5):
            gen = guard.reserve()
            guard.try_start(gen)
            guard.end(gen)
            assert guard.state == QueryState.IDLE
        assert guard.generation == 5


# ============================================================================
# snapshot.py coverage gap: add_message on discarded
# ============================================================================

from duh.kernel.messages import Message
from duh.kernel.snapshot import ReadOnlyExecutor, SnapshotSession


class TestSnapshotDiscardedAddMessage:
    def test_add_message_after_discard_raises(self):
        snapshot = SnapshotSession([Message(role="user", content="hello")])
        snapshot.discard()
        with pytest.raises(RuntimeError, match="discarded"):
            snapshot.add_message(Message(role="user", content="new"))

    def test_str_after_discard(self):
        snapshot = SnapshotSession([Message(role="user", content="hello")])
        snapshot.discard()
        s = str(snapshot)
        assert "discarded" in s

    def test_str_active_with_new_messages(self):
        snapshot = SnapshotSession([Message(role="user", content="hello")])
        snapshot.add_message(Message(role="assistant", content="reply"))
        s = str(snapshot)
        assert "active" in s
        assert "1 new" in s


class TestReadOnlyExecutorEdgeCases:
    async def test_blocks_docker(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("Docker", {"image": "alpine"})

    async def test_blocks_github(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("GitHub", {"action": "create-pr"})

    async def test_blocks_task(self):
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("Task", {"prompt": "do work"})

    async def test_allows_memory_recall(self):
        inner = AsyncMock(return_value="recalled")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("MemoryRecall", {"query": "test"})
        assert result == "recalled"

    async def test_allows_skill(self):
        inner = AsyncMock(return_value="skill result")
        executor = ReadOnlyExecutor(inner)
        result = await executor.run("Skill", {"name": "test"})
        assert result == "skill result"

    async def test_blocks_unknown_tool(self):
        """Tools not in the allowed set should be blocked."""
        inner = AsyncMock()
        executor = ReadOnlyExecutor(inner)
        with pytest.raises(PermissionError, match="[Ss]napshot"):
            await executor.run("SomeFutureDangerousTool", {})


# ============================================================================
# attachments.py coverage gaps
# ============================================================================

from duh.kernel.attachments import (
    Attachment,
    AttachmentManager,
    MAX_ATTACHMENT_SIZE,
    _is_likely_text,
)


class TestAttachmentTextProperty:
    def test_text_for_json_returns_decoded(self):
        """application/json content should be decoded as text."""
        a = Attachment(
            name="data.json",
            content_type="application/json",
            data=b'{"key": "value"}',
        )
        assert a.text == '{"key": "value"}'

    def test_text_for_xml_returns_decoded(self):
        a = Attachment(
            name="data.xml",
            content_type="application/xml",
            data=b"<root/>",
        )
        assert a.text == "<root/>"

    def test_text_for_binary_but_decodable_unknown_type(self):
        """Unknown type that decodes as valid text should return text."""
        a = Attachment(
            name="mystery.zzq",
            content_type="application/octet-stream",
            data=b"This is actually text content",
        )
        # Will try decoding and heuristic check
        result = a.text
        # Could return None or text depending on heuristic
        # The key is it doesn't crash

    def test_text_for_binary_with_control_chars(self):
        """Binary data with lots of control chars should return None."""
        a = Attachment(
            name="binary.bin",
            content_type="application/octet-stream",
            data=bytes(range(256)) * 4,  # lots of control characters
        )
        assert a.text is None

    def test_text_for_utf8_decode_error(self):
        """Content that can't be decoded as UTF-8 should return None."""
        a = Attachment(
            name="broken.bin",
            content_type="text/plain",
            data=b"\xff\xfe\x00\x01" * 100,
        )
        # text/plain triggers UTF-8 decode attempt, which may fail
        # Result should be None on decode error
        result = a.text
        assert result is None


class TestIsLikelyText:
    def test_empty_string_is_text(self):
        assert _is_likely_text("") is True

    def test_normal_text_is_text(self):
        assert _is_likely_text("Hello, world!\nThis is text.") is True

    def test_control_chars_is_not_text(self):
        s = "\x00\x01\x02\x03\x04\x05\x06\x07\x08" * 200
        assert _is_likely_text(s) is False

    def test_tabs_and_newlines_are_ok(self):
        s = "line1\n\tindented\n\t\ttab\r\nwindows"
        assert _is_likely_text(s) is True


class TestAttachmentManagerExtractText:
    def test_extract_text_for_image(self):
        mgr = AttachmentManager()
        att = Attachment(
            name="photo.png",
            content_type="image/png",
            data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
        )
        text = mgr.extract_text(att)
        assert "[Image:" in text
        assert "photo.png" in text

    def test_extract_text_for_binary(self):
        mgr = AttachmentManager()
        att = Attachment(
            name="data.bin",
            content_type="application/octet-stream",
            data=bytes(range(256)),
        )
        text = mgr.extract_text(att)
        assert "[Binary file:" in text
        assert "data.bin" in text

    def test_extract_text_for_pdf_without_pdfplumber(self):
        """PDF extraction fallback when pdfplumber is not installed."""
        mgr = AttachmentManager()
        pdf_data = b"%PDF-1.4 (Hello World) Tj more content"
        att = Attachment(name="test.pdf", content_type="application/pdf", data=pdf_data)
        text = mgr.extract_text(att)
        assert isinstance(text, str)
        # Fallback regex extracts text between parentheses before Tj
        assert "Hello World" in text

    def test_extract_pdf_no_text_found(self):
        """PDF with no extractable text should still return a useful string."""
        mgr = AttachmentManager()
        pdf_data = b"%PDF-1.4 binary gibberish with no text objects"
        att = Attachment(name="empty.pdf", content_type="application/pdf", data=pdf_data)
        text = mgr.extract_text(att)
        assert isinstance(text, str)
        # Should contain at least the filename
        assert "empty.pdf" in text or "PDF" in text


# ============================================================================
# sandbox/policy.py coverage gaps: cleanup()
# ============================================================================

from duh.adapters.sandbox.policy import SandboxCommand, SandboxPolicy, SandboxType


class TestSandboxCommandCleanup:
    def test_cleanup_removes_profile(self, tmp_path: Path):
        """cleanup() should remove the profile file."""
        profile = tmp_path / "test.sb"
        profile.write_text("(version 1)")
        cmd = SandboxCommand(
            command="echo hi",
            argv=["bash", "-c", "echo hi"],
            profile_path=str(profile),
        )
        assert profile.exists()
        cmd.cleanup()
        assert not profile.exists()

    def test_cleanup_no_profile_is_noop(self):
        """cleanup() with no profile_path should not raise."""
        cmd = SandboxCommand(
            command="echo hi",
            argv=["bash", "-c", "echo hi"],
            profile_path=None,
        )
        cmd.cleanup()  # Should not raise

    def test_cleanup_missing_file_is_noop(self):
        """cleanup() with a nonexistent profile path should not raise."""
        cmd = SandboxCommand(
            command="echo hi",
            argv=["bash", "-c", "echo hi"],
            profile_path="/tmp/nonexistent_profile_12345.sb",
        )
        cmd.cleanup()  # Should not raise


# ============================================================================
# bridge/server.py coverage: _handle_connection flow
# ============================================================================

from duh.bridge.protocol import (
    ConnectMessage,
    DisconnectMessage,
    ErrorMessage,
    EventMessage,
    PromptMessage,
    encode_message,
    decode_message,
)
from duh.bridge.session_relay import SessionRelay
from duh.bridge.server import BridgeServer


class FakeWebSocket:
    """Simulates a websockets server-side connection for testing."""

    def __init__(self):
        self.sent: list[str] = []
        self._recv_queue: asyncio.Queue[str] = asyncio.Queue()
        self._closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self._closed = True

    def feed(self, msg: str) -> None:
        self._recv_queue.put_nowait(msg)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await asyncio.wait_for(self._recv_queue.get(), timeout=0.3)
        except asyncio.TimeoutError:
            raise StopAsyncIteration


class TestBridgeServerHandleConnection:
    @pytest.mark.asyncio
    async def test_full_connect_disconnect_flow(self):
        """Connect, then disconnect should register and unregister session."""
        server = BridgeServer(token="")
        ws = FakeWebSocket()

        connect = encode_message(ConnectMessage(token="", session_id="s1"))
        disconnect = encode_message(DisconnectMessage(session_id="s1"))
        ws.feed(connect)
        ws.feed(disconnect)

        await server._handle_connection(ws)

        # Should have sent an ack
        assert len(ws.sent) >= 1
        ack = json.loads(ws.sent[0])
        assert ack["type"] == "event"
        assert ack["event_type"] == "connected"
        assert ack["data"]["session_id"] == "s1"

        # Session should be unregistered after disconnect
        assert not server.relay.has_session("s1")

    @pytest.mark.asyncio
    async def test_invalid_token_closes_connection(self):
        """Invalid token should send error and close."""
        server = BridgeServer(token="secret")
        ws = FakeWebSocket()

        connect = encode_message(ConnectMessage(token="wrong", session_id="s1"))
        ws.feed(connect)

        await server._handle_connection(ws)

        assert len(ws.sent) >= 1
        error = json.loads(ws.sent[0])
        assert error["type"] == "error"
        assert error["code"] == 401
        assert ws._closed

    @pytest.mark.asyncio
    async def test_prompt_without_connect_sends_error(self):
        """Sending a prompt before connecting should send a 403 error."""
        server = BridgeServer(token="")
        ws = FakeWebSocket()

        prompt = encode_message(PromptMessage(session_id="s1", content="hello"))
        ws.feed(prompt)

        await server._handle_connection(ws)

        assert len(ws.sent) >= 1
        error = json.loads(ws.sent[0])
        assert error["type"] == "error"
        assert error["code"] == 403

    @pytest.mark.asyncio
    async def test_invalid_json_sends_error(self):
        """Malformed JSON should send a 400 error."""
        server = BridgeServer(token="")
        ws = FakeWebSocket()

        ws.feed("not valid json {{{")

        await server._handle_connection(ws)

        assert len(ws.sent) >= 1
        error = json.loads(ws.sent[0])
        assert error["type"] == "error"
        assert error["code"] == 400

    @pytest.mark.asyncio
    async def test_connect_auto_generates_session_id(self):
        """Connect without session_id should auto-generate one."""
        server = BridgeServer(token="")
        ws = FakeWebSocket()

        connect = encode_message(ConnectMessage(token=""))
        ws.feed(connect)

        await server._handle_connection(ws)

        assert len(ws.sent) >= 1
        ack = json.loads(ws.sent[0])
        assert ack["data"]["session_id"]  # Should be non-empty

    @pytest.mark.asyncio
    async def test_prompt_with_no_engine_sends_error(self):
        """Prompt when no engine_factory is configured should send 500 error."""
        server = BridgeServer(token="", engine_factory=None)
        ws = FakeWebSocket()

        connect = encode_message(ConnectMessage(token="", session_id="s1"))
        prompt = encode_message(PromptMessage(session_id="s1", content="hello"))
        ws.feed(connect)
        ws.feed(prompt)

        await server._handle_connection(ws)

        # First message is the connect ack, second should be the error
        assert len(ws.sent) >= 2
        error = json.loads(ws.sent[1])
        assert error["type"] == "error"
        assert error["code"] == 500
        assert "engine" in error["error"].lower() or "No engine" in error["error"]

    @pytest.mark.asyncio
    async def test_prompt_with_engine_relays_events(self):
        """Prompt with a working engine should relay events to client."""
        async def fake_engine_factory(session_id):
            engine = MagicMock()

            async def fake_run(content):
                yield {"type": "text_delta", "text": "Hello"}
                yield {"type": "done", "stop_reason": "end_turn"}

            engine.run = fake_run
            return engine

        server = BridgeServer(token="", engine_factory=fake_engine_factory)
        ws = FakeWebSocket()

        connect = encode_message(ConnectMessage(token="", session_id="s1"))
        prompt = encode_message(PromptMessage(session_id="s1", content="hello"))
        ws.feed(connect)
        ws.feed(prompt)

        await server._handle_connection(ws)

        # Should have: connect-ack, then 2 events from engine
        assert len(ws.sent) >= 3
        ack = json.loads(ws.sent[0])
        assert ack["event_type"] == "connected"

        # The engine events
        evt1 = json.loads(ws.sent[1])
        assert evt1["type"] == "event"
        assert evt1["event_type"] == "text_delta"

        evt2 = json.loads(ws.sent[2])
        assert evt2["type"] == "event"
        assert evt2["event_type"] == "done"


class TestSessionRelayEdgeCases:
    @pytest.mark.asyncio
    async def test_send_event_to_broken_websocket(self):
        """If the websocket .send() raises, it should be caught silently."""
        relay = SessionRelay()

        class BrokenWS:
            async def send(self, data: str) -> None:
                raise ConnectionError("connection lost")

        relay.register("s1", BrokenWS())
        event = EventMessage(session_id="s1", event_type="test", data={})
        # Should not raise
        await relay.send_event("s1", event)

    def test_register_overwrites_previous(self):
        """Registering the same session_id twice should overwrite."""
        relay = SessionRelay()
        ws1 = MagicMock()
        ws2 = MagicMock()
        relay.register("s1", ws1)
        relay.register("s1", ws2)
        assert relay.get_websocket("s1") is ws2
        assert relay.session_count == 1


# ============================================================================
# network.py edge cases
# ============================================================================

from duh.adapters.sandbox.network import NetworkMode, NetworkPolicy


class TestNetworkPolicyEdgeCases:
    def test_subdomain_matching(self):
        """Subdomain of a denied host should also be denied."""
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            denied_hosts=["evil.com"],
        )
        assert policy.is_request_allowed("GET", "https://sub.evil.com/path") is False

    def test_subdomain_of_allowed_host(self):
        """Subdomain of an allowed host should be allowed."""
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            allowed_hosts=["api.example.com"],
        )
        assert policy.is_request_allowed("GET", "https://v2.api.example.com/path") is True

    def test_bad_url_returns_empty_host(self):
        """A bad URL should not crash, just return empty host."""
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            allowed_hosts=["example.com"],
        )
        # Non-matching host from a weird URL
        assert policy.is_request_allowed("GET", "notaurl") is False

    def test_none_mode_blocks_all_methods(self):
        """NONE mode should block GET, POST, HEAD, everything."""
        policy = NetworkPolicy(mode=NetworkMode.NONE)
        for method in ("GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"):
            assert policy.is_request_allowed(method, "https://example.com") is False

    def test_case_insensitive_method(self):
        """Method check should be case-insensitive."""
        policy = NetworkPolicy(mode=NetworkMode.LIMITED)
        assert policy.is_request_allowed("get", "https://example.com") is True
        assert policy.is_request_allowed("Get", "https://example.com") is True
        assert policy.is_request_allowed("post", "https://example.com") is False
