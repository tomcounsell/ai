> Use with [the Codex procedure](SKILL.md). These domain details do not override the user's scope, current tool capabilities, or that procedure. Verify version-sensitive commands before use.

## Step 2: Format selection

When multiple formats are available for the same book, prefer in this order:

1. **EPUB** — preserves chapter structure and semantic markup; cleanest pandoc output
2. **MOBI / AZW3** — Amazon formats; convert via `ebook-convert` first
3. **Plain text** — already clean, but loses structure
4. **PDF (text-based)** — workable; expect cleanup work for headers/footers
5. **PDF (image-only / scanned)** — last resort; needs OCR pass

**Avoid:**
- DRM'd files (`.acsm`, encrypted EPUB) — won't convert without DRM stripping (separate legal question)
- DJVU — poor tooling, convert to PDF first if unavoidable

## Step 3: Verify authorized input

Preserve the input under `library/raw/<author-slug>/` and verify its file type and integrity. Acquire only from authorized sources as described in SKILL.md.

## Step 4: Convert to Markdown

### EPUB → Markdown (preferred)

```bash
pandoc input.epub -o output.md \
  --wrap=none \
  --markdown-headings=atx \
  --extract-media=./media
```

If pandoc output is messy (some EPUBs have non-standard CSS that breaks parsing):

```bash
ebook-convert input.epub output.txt --enable-heuristics
# then wrap in light Markdown via post-processing
```

### MOBI / AZW3 → EPUB → Markdown

```bash
ebook-convert input.mobi intermediate.epub
pandoc intermediate.epub -o output.md --wrap=none
```

### PDF (text-based) → Markdown

```bash
pdftotext -layout input.pdf output.txt
# inspect output.txt; if columns/headers look right, run clean_book.py
```

For PDFs with complex layout, `pymupdf` with block detection works better:

```python
import fitz  # pymupdf
doc = fitz.open("input.pdf")
text = "\n\n".join(page.get_text("text") for page in doc)
```

### PDF (scanned) → searchable PDF → text

```bash
ocrmypdf --deskew --clean --output-type pdf input.pdf ocr_output.pdf
pdftotext -layout ocr_output.pdf output.txt
```

For poor scan quality, bump DPI and language:
```bash
ocrmypdf --image-dpi 300 --language eng --deskew --clean input.pdf out.pdf
```

## Step 5: Clean for AI ingestion

Run the bundled cleaner `scripts/clean_book.py` (in this skill's directory: `scripts/clean_book.py`) over the converted text. It rejoins words hyphenated across line breaks, strips page-number lines and repeated running headers/footers, collapses blank-line runs, and normalizes smart quotes and dashes for tokenizer consistency.

```bash
python scripts/clean_book.py library/processed/<author-slug>/<title-slug>.md
# writes <title-slug>.clean.md alongside the input
```

## Step 6: Add metadata frontmatter

Every processed book file starts with YAML:

```markdown
---
title: "How to Write Short: Word Craft for Fast Times"
author: "Roy Peter Clark"
isbn: "9780316204323"
published: 2013
publisher: "Little, Brown and Company"
source: "personal print copy, ebook acquired for private AI use"
ingested: 2026-05-10
format_origin: epub
word_count: 51000
chapter_count: 35
tags: [writing, craft, nonfiction, journalism]
---

# How to Write Short

## Introduction

...
```

Generate the `word_count` and `chapter_count` programmatically from the cleaned file before writing the frontmatter.

## Step 7: Chunk for RAG (optional)

If the book is going into a vector store rather than served as full text:

```python
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

# First split by chapter
header_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("##", "chapter"), ("###", "section")]
)
chapter_chunks = header_splitter.split_text(book_text)

# Sub-split long chapters into 800-token windows with overlap
char_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=100,
    separators=["\n\n", "\n", ". ", " "],
)
final_chunks = char_splitter.split_documents(chapter_chunks)

# Persist with stable IDs
import json, hashlib
for i, chunk in enumerate(final_chunks):
    chunk_id = hashlib.sha1(chunk.page_content.encode()).hexdigest()[:12]
    out = {
        "id": f"{book_slug}-{i:04d}-{chunk_id}",
        "book": book_slug,
        "chapter": chunk.metadata.get("chapter"),
        "text": chunk.page_content,
        "metadata": chunk.metadata,
    }
    Path(f"library/chunks/{book_slug}/{out['id']}.json").write_text(json.dumps(out))
```

**Chunk sizing guide:**
- 500-800 tokens for narrative/instructional prose
- 1000-1500 tokens for dense technical content where context matters
- 100-token overlap for continuity at boundaries

## Output structure

```
library/
├── raw/
│   └── clark-roy-peter/
│       └── how-to-write-short.epub
├── processed/
│   └── clark-roy-peter/
│       ├── how-to-write-short.md          # cleaned, with frontmatter
│       └── how-to-write-short.meta.json   # structured metadata mirror
└── chunks/
    └── clark-roy-peter-how-to-write-short/
        └── *.json                          # RAG-ready chunks
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `ANNAS_ARCHIVE_ACCOUNT_ID not set` | Export from `~/Desktop/Valor/.env` or check env is loaded |
| API returns "Not a member" / 403 | Key invalid or membership expired; re-check at annas-archive.org/account |
| API returns "daily limit reached" | Wait 24h or fall back to slow download links from search page |
| All mirrors timing out | Check current mirror status; the `.li` and `.se` mirrors are usually most stable |
| Search results empty but book exists | HTML markup changed; inspect page and update CSS selector in `annas_get.py` |
| EPUB → MD has stray HTML tags | Add `pandoc --strip-comments`, or post-process with `bleach` |
| PDF columns interleave in output | Use `pdftotext -layout` (already default), or `pymupdf` with `get_text("blocks")` and sort by x-coordinate |
| Source returns wrong edition | Re-search with ISBN; check copyright page in preview |
| OCR garbles text | Increase DPI to 400+, ensure `--language` flag matches the book |
| Chapter detection fails | Regex fallbacks: `^Chapter \d+`, `^[IVXLC]+\.\s`, `^\d+\s*$\n[A-Z]` on next line |
| Smart quotes survive cleanup | Run text through Unicode NFKC normalization: `unicodedata.normalize('NFKC', text)` |
| Footnotes inline awkwardly | Pandoc has `--reference-links`; or post-process to move to bottom of section |
