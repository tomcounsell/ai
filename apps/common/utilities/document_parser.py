"""Document text extraction utility using kreuzberg.

Provides a single sync function ``parse_document()`` that accepts a file path
and returns extracted plain text.  Supports PDF, DOCX, ODT, and other formats
that kreuzberg can handle.

Kreuzberg auto-detects the file format from the extension / MIME type and
delegates to the appropriate extractor (pdfium for PDFs, python-docx for DOCX,
etc.).  Image OCR requires Tesseract to be installed separately.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Extensions we explicitly support and have tested.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".docx", ".odt", ".txt", ".md"}
)


def parse_document(path: Path | str) -> str:
    """Extract plain text from a document file.

    Uses kreuzberg's synchronous ``extract_file_sync`` API to detect the file
    format and extract text content.

    Args:
        path: Filesystem path to the document.  Accepts :class:`~pathlib.Path`
            or a string.

    Returns:
        Extracted text as a string.  Returns an empty string if the file
        exists but contains no extractable text.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the file extension is not in :data:`SUPPORTED_EXTENSIONS`.
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension '{suffix}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    # For plain-text formats, just read directly — no need for kreuzberg.
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")

    try:
        from kreuzberg import extract_file_sync

        result = extract_file_sync(str(path))
        text = result.content.strip()
        logger.info(
            "Extracted %d chars from %s (%s)",
            len(text),
            path.name,
            suffix,
        )
        return text
    except Exception:
        logger.exception("kreuzberg extraction failed for %s", path)
        return ""
