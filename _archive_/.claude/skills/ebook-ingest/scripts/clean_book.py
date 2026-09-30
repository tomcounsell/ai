"""Normalize raw book text for AI ingestion.

Usage:
  python clean_book.py <path-to-raw.md-or-txt>

Writes the cleaned text next to the input with a `.clean.md` suffix.
Fixes hyphenation across line breaks, strips page numbers and repeated
running headers/footers, collapses blank-line runs, and normalizes
smart quotes and dashes for tokenizer consistency.
"""

import re
from collections import Counter
from pathlib import Path


def clean_book_text(raw: str) -> str:
    text = raw

    # Rejoin words hyphenated across line breaks: "exam-\nple" -> "example"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    # Strip lines that are only page numbers
    text = re.sub(r"^\s*\d+\s*$\n", "", text, flags=re.MULTILINE)

    # Strip running headers/footers: detect lines that repeat >5 times
    lines = text.split("\n")
    counts = Counter(line.strip() for line in lines if line.strip())
    boilerplate = {line for line, c in counts.items() if c > 5 and len(line) < 80}
    lines = [line for line in lines if line.strip() not in boilerplate]
    text = "\n".join(lines)

    # Collapse runs of blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Normalize smart quotes (helps tokenizer consistency)
    text = (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
        .replace("–", "-")
        .replace("—", "--")
    )

    return text.strip()


if __name__ == "__main__":
    import sys

    path = Path(sys.argv[1])
    cleaned = clean_book_text(path.read_text())
    path.with_suffix(".clean.md").write_text(cleaned)
