"""Attachment system -- read files, detect types, handle images and PDFs.

Provides the Attachment dataclass and AttachmentManager for converting
files into content blocks that can be sent to AI models.

Image files are base64-encoded into ImageBlock content blocks.
PDF files get text extracted (via pdfplumber if available, fallback otherwise).
Text files are read directly.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from duh.kernel.messages import ImageBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB

# Magic bytes for common image formats
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"\x89PNG", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF", "application/pdf"),
]

# WebP has a more complex signature: RIFF....WEBP
_WEBP_MAGIC = b"RIFF"
_WEBP_MARKER = b"WEBP"

# Extension-based fallbacks for common dev files
_EXT_CONTENT_TYPES: dict[str, str] = {
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".json": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "text/x-toml",
    ".md": "text/markdown",
    ".rst": "text/x-rst",
    ".html": "text/html",
    ".css": "text/css",
    ".xml": "text/xml",
    ".csv": "text/csv",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".zsh": "text/x-shellscript",
    ".rb": "text/x-ruby",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".java": "text/x-java",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c",
    ".hpp": "text/x-c++",
    ".sql": "text/x-sql",
    ".r": "text/x-r",
    ".lua": "text/x-lua",
    ".swift": "text/x-swift",
    ".kt": "text/x-kotlin",
    ".tex": "text/x-tex",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".cfg": "text/plain",
    ".ini": "text/plain",
    ".env": "text/plain",
}

# Image content types
_IMAGE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "image/svg+xml", "image/bmp", "image/tiff",
})


# ---------------------------------------------------------------------------
# Attachment dataclass
# ---------------------------------------------------------------------------

@dataclass
class Attachment:
    """A file attachment with content type detection."""

    name: str
    content_type: str
    data: bytes
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        """Size in bytes."""
        return len(self.data)

    @property
    def is_image(self) -> bool:
        """True if this attachment is an image."""
        return self.content_type in _IMAGE_TYPES

    @property
    def text(self) -> str | None:
        """Decode as text, or None if binary."""
        if self.content_type.startswith("text/") or self.content_type in (
            "application/json", "application/xml",
        ):
            try:
                return self.data.decode("utf-8")
            except UnicodeDecodeError:
                return None
        # Try decoding anyway for unknown types
        try:
            decoded = self.data.decode("utf-8")
            # If it decoded cleanly and looks like text, return it
            if _is_likely_text(decoded):
                return decoded
        except (UnicodeDecodeError, ValueError):
            pass
        return None

    @property
    def base64(self) -> str:
        """Base64-encoded data as ASCII string."""
        return base64.b64encode(self.data).decode("ascii")


def _is_likely_text(s: str) -> bool:
    """Heuristic: is this string likely text (not binary gibberish)?"""
    if not s:
        return True
    # Count control characters (excluding common whitespace)
    control = sum(1 for c in s[:1024] if ord(c) < 32 and c not in "\n\r\t")
    return control / min(len(s), 1024) < 0.1


# ---------------------------------------------------------------------------
# AttachmentManager
# ---------------------------------------------------------------------------

class AttachmentManager:
    """Reads files, detects content types, and converts to content blocks."""

    def read_file(self, path: str) -> Attachment:
        """Read a file and return an Attachment.

        Raises FileNotFoundError if the file does not exist.
        Raises ValueError if the file exceeds MAX_ATTACHMENT_SIZE.
        """
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        size = p.stat().st_size
        if size > MAX_ATTACHMENT_SIZE:
            raise ValueError(
                f"File '{p.name}' ({size:,} bytes) exceeds the "
                f"{MAX_ATTACHMENT_SIZE:,} byte limit"
            )

        data = p.read_bytes()
        content_type = self.detect_content_type(p.name, data)

        return Attachment(
            name=p.name,
            content_type=content_type,
            data=data,
            metadata={"path": str(p.resolve()), "size": size},
        )

    def detect_content_type(self, filename: str, data: bytes) -> str:
        """Detect content type from filename and magic bytes.

        Priority: magic bytes > extension > heuristic.
        """
        # Check magic bytes first
        for magic, ct in _MAGIC_SIGNATURES:
            if data[:len(magic)] == magic:
                return ct

        # WebP special case: RIFF....WEBP
        if data[:4] == _WEBP_MAGIC and len(data) >= 12 and data[8:12] == _WEBP_MARKER:
            return "image/webp"

        # Check by extension
        ext = Path(filename).suffix.lower()
        if ext in _EXT_CONTENT_TYPES:
            return _EXT_CONTENT_TYPES[ext]

        # Try mimetypes module
        guessed, _ = mimetypes.guess_type(filename)
        if guessed:
            return guessed

        # Heuristic: try decoding as text
        try:
            decoded = data[:4096].decode("utf-8")
            if _is_likely_text(decoded):
                return "text/plain"
        except (UnicodeDecodeError, ValueError):
            pass

        return "application/octet-stream"

    def to_image_block(self, attachment: Attachment) -> ImageBlock:
        """Convert an image attachment to an ImageBlock content block.

        Raises ValueError if the attachment is not an image.
        """
        if not attachment.is_image:
            raise ValueError(
                f"'{attachment.name}' is not an image "
                f"(content_type={attachment.content_type})"
            )
        return ImageBlock(
            media_type=attachment.content_type,
            data=attachment.base64,
        )

    def extract_text(self, attachment: Attachment) -> str:
        """Extract text from an attachment.

        For text files: returns the text content directly.
        For PDFs: uses pdfplumber if available, otherwise basic extraction.
        For images: returns a description placeholder.
        For other types: returns base64 summary.
        """
        # Text files
        if attachment.text is not None:
            return attachment.text

        # PDF
        if attachment.content_type == "application/pdf":
            return self._extract_pdf_text(attachment)

        # Image
        if attachment.is_image:
            return f"[Image: {attachment.name} ({attachment.content_type}, {attachment.size:,} bytes)]"

        # Binary fallback
        return f"[Binary file: {attachment.name} ({attachment.content_type}, {attachment.size:,} bytes)]"

    def _extract_pdf_text(self, attachment: Attachment) -> str:
        """Extract text from a PDF attachment.

        Uses pdfplumber if installed, falls back to basic regex extraction.
        """
        # Try pdfplumber first
        try:
            import pdfplumber
            import io

            with pdfplumber.open(io.BytesIO(attachment.data)) as pdf:
                pages = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n\n".join(pages) if pages else "[PDF: no extractable text]"
        except ImportError:
            logger.debug("pdfplumber not installed, using basic PDF extraction")
        except Exception:
            logger.debug("pdfplumber extraction failed", exc_info=True)

        # Basic fallback: extract text between parentheses in PDF streams
        # This is crude but handles simple PDFs without dependencies
        try:
            text = attachment.data.decode("latin-1")
            # Find text in PDF text objects: (text) Tj or (text) TJ
            matches = re.findall(r"\(([^)]+)\)\s*T[jJ]", text)
            if matches:
                return " ".join(matches)
        except Exception:
            pass

        return f"[PDF: {attachment.name} ({attachment.size:,} bytes, install pdfplumber for text extraction)]"
