"""Tests for duh.kernel.attachments -- file, image, and PDF attachment handling."""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from duh.kernel.attachments import (
    Attachment,
    AttachmentManager,
    MAX_ATTACHMENT_SIZE,
)
from duh.kernel.messages import ImageBlock


# ---------------------------------------------------------------------------
# Tests: Attachment dataclass
# ---------------------------------------------------------------------------

class TestAttachment:
    def test_create_text_attachment(self):
        a = Attachment(
            name="readme.txt",
            content_type="text/plain",
            data=b"Hello, world!",
        )
        assert a.name == "readme.txt"
        assert a.content_type == "text/plain"
        assert a.data == b"Hello, world!"
        assert a.metadata == {}

    def test_create_with_metadata(self):
        a = Attachment(
            name="config.json",
            content_type="application/json",
            data=b'{"key": "value"}',
            metadata={"source": "clipboard"},
        )
        assert a.metadata["source"] == "clipboard"

    def test_size_property(self):
        data = b"x" * 1024
        a = Attachment(name="f.bin", content_type="application/octet-stream", data=data)
        assert a.size == 1024

    def test_is_image(self):
        a = Attachment(name="photo.png", content_type="image/png", data=b"\x89PNG")
        assert a.is_image is True

    def test_is_not_image(self):
        a = Attachment(name="doc.txt", content_type="text/plain", data=b"text")
        assert a.is_image is False

    def test_text_property_for_text_file(self):
        a = Attachment(name="f.txt", content_type="text/plain", data=b"hello")
        assert a.text == "hello"

    def test_text_property_for_binary_returns_none(self):
        a = Attachment(name="f.bin", content_type="application/octet-stream", data=b"\x00\x01")
        assert a.text is None

    def test_base64_property(self):
        data = b"test data"
        a = Attachment(name="f.bin", content_type="application/octet-stream", data=data)
        assert a.base64 == base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# Tests: ImageBlock content type
# ---------------------------------------------------------------------------

class TestImageBlock:
    def test_create_image_block(self):
        block = ImageBlock(
            media_type="image/png",
            data=base64.b64encode(b"\x89PNG").decode("ascii"),
        )
        assert block.type == "image"
        assert block.media_type == "image/png"
        assert block.data == base64.b64encode(b"\x89PNG").decode("ascii")

    def test_image_block_is_frozen(self):
        block = ImageBlock(media_type="image/jpeg", data="abc")
        with pytest.raises(AttributeError):
            block.data = "xyz"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests: AttachmentManager -- file reading
# ---------------------------------------------------------------------------

class TestAttachmentManagerFiles:
    def test_read_text_file(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("Hello from file", encoding="utf-8")
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        assert att.name == "test.txt"
        assert att.content_type == "text/plain"
        assert att.text == "Hello from file"

    def test_read_json_file(self, tmp_path: Path):
        f = tmp_path / "data.json"
        f.write_text('{"key": 1}', encoding="utf-8")
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        assert att.content_type == "application/json"

    def test_read_python_file(self, tmp_path: Path):
        f = tmp_path / "script.py"
        f.write_text("print('hi')", encoding="utf-8")
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        # Python files should be detected as text
        assert att.text == "print('hi')"

    def test_read_image_file(self, tmp_path: Path):
        f = tmp_path / "photo.png"
        # Minimal PNG header
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        f.write_bytes(png_header)
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        assert att.is_image
        assert att.content_type == "image/png"

    def test_read_nonexistent_file_raises(self):
        mgr = AttachmentManager()
        with pytest.raises(FileNotFoundError):
            mgr.read_file("/nonexistent/file.txt")

    def test_read_file_too_large_raises(self, tmp_path: Path):
        f = tmp_path / "huge.bin"
        # Write just over the limit
        f.write_bytes(b"\x00" * (MAX_ATTACHMENT_SIZE + 1))
        mgr = AttachmentManager()
        with pytest.raises(ValueError, match="exceeds.*limit"):
            mgr.read_file(str(f))


# ---------------------------------------------------------------------------
# Tests: AttachmentManager -- image handling
# ---------------------------------------------------------------------------

class TestAttachmentManagerImages:
    def test_to_image_block(self, tmp_path: Path):
        f = tmp_path / "img.png"
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        f.write_bytes(data)
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        block = mgr.to_image_block(att)
        assert isinstance(block, ImageBlock)
        assert block.media_type == "image/png"
        assert block.data == base64.b64encode(data).decode("ascii")

    def test_to_image_block_rejects_non_image(self, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_text("not an image", encoding="utf-8")
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        with pytest.raises(ValueError, match="not an image"):
            mgr.to_image_block(att)


# ---------------------------------------------------------------------------
# Tests: AttachmentManager -- content type detection
# ---------------------------------------------------------------------------

class TestContentTypeDetection:
    def test_detect_png(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("photo.png", b"\x89PNG") == "image/png"

    def test_detect_jpeg(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("photo.jpg", b"\xff\xd8\xff") == "image/jpeg"

    def test_detect_gif(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("anim.gif", b"GIF89a") == "image/gif"

    def test_detect_webp(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("img.webp", b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"

    def test_detect_pdf(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("doc.pdf", b"%PDF-1.4") == "application/pdf"

    def test_detect_json_by_extension(self):
        mgr = AttachmentManager()
        assert mgr.detect_content_type("data.json", b'{"key": 1}') == "application/json"

    def test_detect_python_by_extension(self):
        mgr = AttachmentManager()
        ct = mgr.detect_content_type("script.py", b"print('hi')")
        assert "text" in ct  # text/x-python or text/plain

    def test_detect_unknown_binary(self):
        mgr = AttachmentManager()
        ct = mgr.detect_content_type("mystery.zzq", b"\x00\x01\x02\x03")
        assert ct == "application/octet-stream"

    def test_detect_unknown_text(self):
        mgr = AttachmentManager()
        ct = mgr.detect_content_type("mystery.zzq", b"looks like text content here")
        # Should detect as text when content is printable
        assert "text" in ct


# ---------------------------------------------------------------------------
# Tests: AttachmentManager -- PDF handling
# ---------------------------------------------------------------------------

class TestAttachmentManagerPDF:
    def test_extract_pdf_text_basic(self, tmp_path: Path):
        """Test basic PDF text extraction (without pdfplumber, uses fallback)."""
        mgr = AttachmentManager()
        # Create a minimal PDF-like file
        f = tmp_path / "doc.pdf"
        # Real PDF parsing needs pdfplumber; test the fallback path
        f.write_bytes(b"%PDF-1.4 some content stream (Hello World) Tj")
        att = mgr.read_file(str(f))
        assert att.content_type == "application/pdf"
        # The text extraction should at least not crash
        text = mgr.extract_text(att)
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# Tests: ADR-041 gap fixes
# ---------------------------------------------------------------------------

class TestADR041MaxAttachmentSize:
    """ADR-041 specifies MAX_IMAGE_SIZE = 20 MB (not 10 MB)."""

    def test_max_attachment_size_is_20mb(self):
        """MAX_ATTACHMENT_SIZE must be 20 MB as specified in ADR-041."""
        assert MAX_ATTACHMENT_SIZE == 20 * 1024 * 1024, (
            f"Expected 20 MB ({20 * 1024 * 1024:,} bytes) but got "
            f"{MAX_ATTACHMENT_SIZE:,} bytes. ADR-041 specifies 20 MB limit."
        )

    def test_file_at_20mb_is_accepted(self, tmp_path: Path):
        """A file of exactly 20 MB should be accepted."""
        f = tmp_path / "exactly_20mb.bin"
        twenty_mb = 20 * 1024 * 1024
        # Write exactly 20 MB (within limit)
        f.write_bytes(b"x" * twenty_mb)
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        assert att.size == twenty_mb

    def test_file_just_over_20mb_is_rejected(self, tmp_path: Path):
        """A file just over 20 MB should be rejected."""
        f = tmp_path / "over_20mb.bin"
        f.write_bytes(b"x" * (20 * 1024 * 1024 + 1))
        mgr = AttachmentManager()
        with pytest.raises(ValueError, match="exceeds.*limit"):
            mgr.read_file(str(f))

    def test_file_between_10mb_and_20mb_is_accepted(self, tmp_path: Path):
        """A 15 MB file should be accepted (was rejected under old 10 MB limit)."""
        f = tmp_path / "fifteen_mb.bin"
        fifteen_mb = 15 * 1024 * 1024
        f.write_bytes(b"x" * fifteen_mb)
        mgr = AttachmentManager()
        att = mgr.read_file(str(f))
        assert att.size == fifteen_mb


class TestADR041SlashAttachCommand:
    """ADR-041 requires a /attach slash command in the REPL."""

    def test_attach_command_in_slash_commands(self):
        """'/attach' must appear in SLASH_COMMANDS dict."""
        from duh.cli.repl import SLASH_COMMANDS
        assert "/attach" in SLASH_COMMANDS, (
            "/attach command not registered in SLASH_COMMANDS. "
            "ADR-041 requires a /attach path/to/file.png command."
        )

    def test_attach_no_arg_prints_usage(self, tmp_path: Path, capsys):
        """'/attach' with no argument should print usage, not crash."""
        from duh.cli.repl import _handle_slash
        from unittest.mock import MagicMock

        engine = MagicMock()
        engine.session_id = "test-session"
        engine.turn_count = 0
        engine.messages = []

        deps = MagicMock()

        keep_going, new_model = _handle_slash(
            "/attach",
            engine, "claude-3-5-sonnet", deps,
        )
        assert keep_going is True
        captured = capsys.readouterr()
        assert "Usage" in captured.out or "usage" in captured.out.lower()

    def test_attach_nonexistent_file_prints_error(self, tmp_path: Path, capsys):
        """'/attach /nonexistent/file.png' should print an error, not crash."""
        from duh.cli.repl import _handle_slash
        from unittest.mock import MagicMock

        engine = MagicMock()
        engine.session_id = "test-session"
        engine.turn_count = 0
        engine.messages = []

        deps = MagicMock()

        keep_going, new_model = _handle_slash(
            "/attach /nonexistent/file.png",
            engine, "claude-3-5-sonnet", deps,
        )
        assert keep_going is True
        captured = capsys.readouterr()
        # Should print an error about file not found
        assert "not found" in captured.out.lower() or "error" in captured.out.lower()

    def test_attach_valid_file_queues_attachment(self, tmp_path: Path, capsys):
        """'/attach file.txt' with a valid file should queue it for the next message."""
        from duh.cli.repl import _handle_slash
        from unittest.mock import MagicMock

        f = tmp_path / "note.txt"
        f.write_text("hello attachment", encoding="utf-8")

        engine = MagicMock()
        engine.session_id = "test-session"
        engine.turn_count = 0
        engine.messages = []
        engine._pending_attachments = []

        deps = MagicMock()

        keep_going, new_model = _handle_slash(
            f"/attach {f}",
            engine, "claude-3-5-sonnet", deps,
        )
        assert keep_going is True
        captured = capsys.readouterr()
        # Should confirm the attachment was added
        assert "attach" in captured.out.lower() or "queued" in captured.out.lower() or "added" in captured.out.lower()
