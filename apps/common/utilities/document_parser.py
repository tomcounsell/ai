"""Document text extraction utility.

Provides a single sync function ``parse_document()`` that accepts a file path
and returns extracted plain text.  Supports PDF, DOCX, and plain text formats.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Extensions we explicitly support and have tested.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".txt", ".md"})


def parse_document(path: Path | str) -> str:
    """Extract plain text from a document file.

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

    # Plain-text formats — read directly.
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages).strip()
            logger.info("Extracted %d chars from %s (pdf)", len(text), path.name)
            return text
        except Exception:
            logger.exception("PDF extraction failed for %s", path)
            return ""

    if suffix == ".docx":
        try:
            import docx

            doc = docx.Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs).strip()
            logger.info("Extracted %d chars from %s (docx)", len(text), path.name)
            return text
        except ImportError:
            logger.warning("python-docx not installed; cannot parse %s", path)
            return ""
        except Exception:
            logger.exception("DOCX extraction failed for %s", path)
            return ""

    return ""
