---
name: ebook-ingest
description: "Find, download, and prep an ebook as clean Markdown for AI ingestion. Use when digitizing an owned print book, converting EPUB/PDF/MOBI, or chunking for RAG. NOT academic papers or DRM'd ebooks."
model: sonnet
effort: medium
---

# Ebook acquisition and AI ingestion prep

**Objective:** a named book in; clean, structured Markdown out at
`library/processed/<author-slug>/<title-slug>.md`, ready for an AI agent (full text,
RAG, or fine-tuning). **Done when** the file is free of page numbers, running headers, and
broken hyphenation, and starts with accurate YAML frontmatter (below).

**Premise:** the user owns a print copy and wants a private digital backup for AI use.
If that doesn't hold, don't use this skill. Not for academic papers (use Sci-Hub or
Unpaywall), bulk public-domain scraping (hit Gutenberg's API directly), or DRM'd ebooks
the user hasn't bought (`.acsm`, encrypted EPUB won't convert anyway).

## Setup

```bash
brew install calibre pandoc poppler tesseract ocrmypdf        # macOS (apt: poppler-utils, tesseract-ocr)
pip install ebooklib beautifulsoup4 markdownify pymupdf langchain-text-splitters httpx
```

Anna's Archive fast downloads need `ANNAS_ARCHIVE_ACCOUNT_ID` and
`ANNAS_ARCHIVE_SECRET_KEY` (in `~/Desktop/Valor/.env`; values at
annas-archive.org/account). If either is unset, fall back to manual browser download.

## Find and download

Sources in priority order; stop at the first clean match in a good format: Project
Gutenberg (pre-1928 public domain), Standard Ebooks (curated public domain), Internet
Archive, Anna's Archive (meta-search over LibGen, Z-Lib), Library Genesis, Z-Library
(mirrors rotate; verify the URL). Search by ISBN when known to avoid wrong editions; pick
editions by format quality, not recency, unless the edition itself matters (annotated,
revised, translated).

Format preference: EPUB > MOBI/AZW3 (`ebook-convert` to EPUB first) > plain text >
text PDF > scanned PDF (needs OCR). DJVU: convert to PDF first.

With the Anna's Archive keys set, use the bundled helper:

```bash
python .claude/skills/ebook-ingest/scripts/annas_get.py search "How to Write Short Roy Peter Clark" --ext epub
python .claude/skills/ebook-ingest/scripts/annas_get.py download <md5> --output library/raw/<author-slug>/
file library/raw/<author-slug>/*.epub     # expect "EPUB document" or "Zip archive data"
```

- Membership has a daily fast-download cap (a few dozen books); a wrong edition burns it.
  The API reports the limit in its JSON, so check the response before assuming success.
- If `search` returns nothing when results clearly exist, the page markup changed: inspect
  it and update the `soup.select(...)` line in `annas_get.py`. The `fast_download.json`
  contract is more stable than the HTML.

Layout: `library/raw/<author-slug>/` for downloads, `library/processed/<author-slug>/`
for the cleaned `.md` (plus a `.meta.json` mirror of the frontmatter),
`library/chunks/<author-slug>-<title-slug>/` for optional RAG chunks.

## Convert

```bash
pandoc input.epub -o output.md --wrap=none --markdown-headings=atx --extract-media=./media
ebook-convert input.epub output.txt --enable-heuristics   # when pandoc chokes on odd EPUB CSS
ebook-convert input.mobi intermediate.epub                # MOBI/AZW3, then pandoc as above
pdftotext -layout input.pdf output.txt                    # text PDF; pymupdf blocks for complex layouts
ocrmypdf --deskew --clean --output-type pdf input.pdf ocr.pdf && pdftotext -layout ocr.pdf output.txt
```

For poor scans add `--image-dpi 300 --language <lang>` to `ocrmypdf`.

## Clean

```bash
python .claude/skills/ebook-ingest/scripts/clean_book.py library/processed/<author-slug>/<title-slug>.md
# writes <title-slug>.clean.md alongside the input
```

It rejoins line-break hyphenation, strips page-number lines and repeated running
headers/footers, collapses blank-line runs, and normalizes smart quotes and dashes.

## Frontmatter

Compute `word_count` and `chapter_count` from the cleaned file.

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

## More

Chunking for RAG (optional; only when the book goes into a vector store) and a
troubleshooting table: `references/chunking-and-troubleshooting.md`.
