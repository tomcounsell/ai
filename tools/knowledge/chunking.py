"""Chunking engine for splitting documents into embeddable chunks.

Splits document content into overlapping chunks suitable for individual
embedding. Uses heading-aware splitting when possible, falling back to
token-count splitting.

Constants:
    CHUNK_SIZE_TOKENS: Target size for each chunk (~1500 tokens).
    CHUNK_OVERLAP_TOKENS: Overlap between adjacent chunks (~200 tokens).
"""

import logging
import re

import tiktoken

logger = logging.getLogger(__name__)

# Configurable chunk parameters
CHUNK_SIZE_TOKENS = 1500
CHUNK_OVERLAP_TOKENS = 200

# Cache tiktoken encoding at module level for performance
_encoding = None


def _get_encoding() -> tiktoken.Encoding:
    """Get the cached tiktoken encoding (cl100k_base, same as text-embedding-3-small)."""
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.get_encoding("cl100k_base")
    return _encoding


def _count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken cl100k_base encoding."""
    try:
        return len(_get_encoding().encode(text))
    except Exception as e:
        logger.warning(f"tiktoken encoding failed, estimating: {e}")
        # Fallback: rough estimate of ~4 chars per token
        return len(text) // 4


def _split_by_headings(content: str) -> list[tuple[str, str]]:
    """Split content by top-level (h1/h2) headings.

    Returns list of (heading, section_content) tuples.
    If no headings found, returns single entry with empty heading.
    """
    sections = []
    current_heading = ""
    current_lines = []

    for line in content.split("\n"):
        if re.match(r"^#{1,2}\s+", line):
            if current_lines:
                section_text = "\n".join(current_lines).strip()
                if section_text:
                    sections.append((current_heading, section_text))
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Save last section
    if current_lines:
        section_text = "\n".join(current_lines).strip()
        if section_text:
            sections.append((current_heading, section_text))

    if not sections:
        return [("", content.strip())]

    return sections


def _split_by_tokens(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split text into chunks by token count with overlap.

    Args:
        text: The text to split.
        chunk_size: Maximum tokens per chunk.
        overlap: Number of overlapping tokens between adjacent chunks.

    Returns:
        List of chunk text strings.
    """
    encoding = _get_encoding()
    try:
        tokens = encoding.encode(text)
    except Exception as e:
        logger.warning(f"tiktoken encoding failed in _split_by_tokens: {e}")
        return [text] if text.strip() else []

    if len(tokens) <= chunk_size:
        return [text] if text.strip() else []

    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        try:
            chunk_text = encoding.decode(chunk_tokens)
        except Exception:
            chunk_text = text[start * 4 : end * 4]  # rough fallback

        if chunk_text.strip():
            chunks.append(chunk_text.strip())

        # Advance by (chunk_size - overlap) to create overlap
        step = chunk_size - overlap
        if step <= 0:
            step = chunk_size  # safety: prevent infinite loop
        start += step

    return chunks


def chunk_document(content: str) -> list[dict]:
    """Split document content into chunks for individual embedding.

    Strategy:
    1. If the document is under CHUNK_SIZE_TOKENS, return a single chunk.
    2. Split by h1/h2 headings.
    3. For sections exceeding CHUNK_SIZE_TOKENS, sub-split by token count with overlap.
    4. For documents with no headings, split entirely by token count with overlap.

    Args:
        content: The full document text.

    Returns:
        List of dicts: [{"chunk_index": int, "text": str}, ...]
        Empty list for empty/whitespace-only content.
    """
    if not content or not content.strip():
        return []

    content = content.strip()

    # Short document: single chunk
    total_tokens = _count_tokens(content)
    if total_tokens <= CHUNK_SIZE_TOKENS:
        return [{"chunk_index": 0, "text": content}]

    # Try heading-aware splitting
    sections = _split_by_headings(content)

    # If only one section with no heading, it means no headings were found
    # Fall back to pure token splitting
    if len(sections) == 1 and sections[0][0] == "":
        token_chunks = _split_by_tokens(content, CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS)
        return [{"chunk_index": i, "text": chunk} for i, chunk in enumerate(token_chunks)]

    # Process each heading section
    all_chunks = []
    for heading, section_text in sections:
        # Prepend heading to section content for context
        full_section = f"{heading}\n\n{section_text}" if heading else section_text

        section_tokens = _count_tokens(full_section)
        if section_tokens <= CHUNK_SIZE_TOKENS:
            all_chunks.append(full_section)
        else:
            # Sub-split large sections by token count
            sub_chunks = _split_by_tokens(full_section, CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS)
            all_chunks.extend(sub_chunks)

    return [{"chunk_index": i, "text": chunk} for i, chunk in enumerate(all_chunks)]
