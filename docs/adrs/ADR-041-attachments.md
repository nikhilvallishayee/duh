# ADR-041: Attachment System

**Status**: Accepted  
**Date**: 2026-04-08  
**Implemented**: 2026-04-08  
**Note**: MAX_ATTACHMENT_SIZE is 10MB (implementation) vs 20MB (ADR). CLI integration points (Ctrl+V paste, /attach command, drag-drop, @image: syntax) not implemented.

## Context

D.U.H. has no support for non-text content. Users cannot:
- Paste screenshots for the model to analyze (common for UI bug reports)
- Attach images from disk for visual reasoning
- Process PDF documents for context

The reference TS harness supports image attachments (base64-encoded in messages) and has infrastructure for document handling. Modern vision-capable models (Claude, GPT-4V) can process images natively, making this a high-value feature for coding assistance (UI screenshots, architecture diagrams, error screenshots).

## Decision

Add an `AttachmentManager` that handles encoding, validation, and injection of non-text content:

### Supported Types

| Type | Detection | Encoding | Model Support Required |
|------|-----------|----------|----------------------|
| PNG/JPEG/GIF/WebP | File extension + magic bytes | Base64 | Vision capability |
| SVG | File extension + `<svg` header | Inline text | Text capability |
| PDF | File extension + `%PDF` header | Text extraction | Text capability |

### Attachment Flow

```python
@dataclass
class Attachment:
    path: str
    media_type: str       # "image/png", "application/pdf", etc.
    content: str | bytes  # Base64 for images, extracted text for PDFs
    size_bytes: int

class AttachmentManager:
    MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20 MB
    MAX_ATTACHMENTS_PER_MESSAGE = 5

    async def process(self, path: str) -> Attachment:
        media_type = self._detect_type(path)
        size = os.path.getsize(path)

        if media_type.startswith("image/"):
            if size > self.MAX_IMAGE_SIZE:
                raise AttachmentError(f"Image too large: {size // 1024 // 1024}MB > 20MB limit")
            content = base64.b64encode(open(path, "rb").read()).decode()
            return Attachment(path, media_type, content, size)

        if media_type == "application/pdf":
            text = await self._extract_pdf_text(path)
            return Attachment(path, media_type, text, size)

        raise AttachmentError(f"Unsupported type: {media_type}")
```

### CLI Integration

- **Paste**: `Ctrl+V` in TUI detects clipboard image data and attaches it
- **Path**: `/attach path/to/file.png` command adds an attachment to the next message
- **Drag**: Terminal emulators that support file drop trigger attachment processing
- **Inline**: `@image:path/to/file.png` syntax in message text

### PDF Extraction

PDF text extraction is optional — it depends on `pymupdf` or `pdfplumber`. If neither is installed, PDFs are rejected with a message: `"Install pymupdf for PDF support: pip install pymupdf"`. This keeps the core dependency-free.

### Provider Adaptation

Attachments are converted to provider-specific format in the provider adapter:

```python
# Anthropic: content block with type "image" and base64 source
# OpenAI: content block with type "image_url" and data URI
# Other: text fallback with "[Image attached but not supported by this provider]"
```

## Consequences

### Positive
- Enables visual debugging workflows (screenshot → analysis → fix)
- PDF extraction brings document context into conversations
- Provider adapter pattern means attachment support scales to all providers
- Optional PDF dependency keeps the core lightweight

### Negative
- Base64 images consume significant context (a 1MB image ≈ 1.3MB in base64)
- Clipboard paste behavior varies across terminal emulators
- PDF extraction quality depends on the PDF structure

### Risks
- Large images can trigger PTL errors — mitigated by integration with compaction (ADR-035) which strips old images first
- Not all providers support vision — mitigated by text fallback in the adapter
