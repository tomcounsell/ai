---
name: ebook-ingest
description: "Use this skill when the user wants to find, download, and prepare an ebook for AI agent ingestion (RAG, fine-tuning, or long-context reference). Triggers include: requests to acquire a digital copy of a book the user owns in print, building a personal book corpus for an AI agent, converting EPUB/PDF/MOBI to clean Markdown for LLM consumption, or chunking books for vector stores. Handles search across multiple sources (Gutenberg, Standard Ebooks, Anna's Archive, LibGen, Z-Library, archive.org), format conversion via calibre/pandoc, OCR for scanned PDFs, cleanup, metadata, and chunking. Do NOT use for academic papers (use Sci-Hub/unpaywall), bulk public-domain scraping (hit Gutenberg's API directly), or DRM'd commercial ebooks the user has not purchased."
---

# Ebook acquisition and AI ingestion prep

## Overview

End-to-end pipeline for turning a named book into clean, structured Markdown ready for an AI agent to consume. Covers search → download → convert → clean → chunk.

Assumes the user owns a print copy and is creating a personal digital backup for private AI use. Skip this skill if that premise doesn't hold.

## Quick reference

| Step | Tool | Output |
|------|------|--------|
| Search | Anna's Archive (meta), Gutenberg, Standard Ebooks | candidate file URLs |
| Download | `curl` / `wget` | `library/raw/<slug>.<ext>` |
| Convert | `pandoc` (EPUB), `pdftotext -layout` (PDF), `ocrmypdf` (scanned) | raw `.md` or `.txt` |
| Clean | `clean_book.py` | normalized Markdown |
| Metadata | YAML frontmatter | `<slug>.md` |
| Chunk (optional) | `langchain` text splitters | `chunks/*.json` |

## Prerequisites

```bash
# macOS
brew install calibre pandoc poppler tesseract ocrmypdf

# Ubuntu/Debian
apt-get install calibre pandoc poppler-utils tesseract-ocr ocrmypdf

# Python
pip install ebooklib beautifulsoup4 markdownify pymupdf langchain-text-splitters httpx
```

## Configuration

Anna's Archive supports authenticated programmatic downloads for paid members. Two env vars are required:

```bash
# In ~/Desktop/Valor/.env (already set on this machine)
ANNAS_ARCHIVE_ACCOUNT_ID="<account-id>"
ANNAS_ARCHIVE_SECRET_KEY="<secret-key>"
```

Both values are available at `https://annas-archive.org/account` after donating. They are passed as query parameters on the fast download endpoint.

If either var is unset, fall back to manual browser download from search result pages.

## Step 1: Search

Try sources in priority order. Stop at the first clean match in a good format.

| Priority | Source | URL | Best for |
|---|---|---|---|
| 1 | Project Gutenberg | gutenberg.org | Pre-1928 public domain |
| 2 | Standard Ebooks | standardebooks.org | Public domain, hand-curated formatting |
| 3 | Internet Archive | archive.org | Borrowable lending; some open texts |
| 4 | Anna's Archive | annas-archive.org | Meta-search across LibGen + Z-Lib + sci-hub |
| 5 | Library Genesis | libgen.rs / libgen.is | Direct, large general catalog |
| 6 | Z-Library | z-lib.io (mirrors rotate) | Active mirrors change; verify URL each session |

**Search strategy:**
- Prefer ISBN over title+author when known (avoids wrong editions)
- Multiple editions exist — pick by format quality, not recency, unless the edition matters (annotated, revised, translated)

### Anna's Archive: programmatic search + download

When `ANNAS_ARCHIVE_ACCOUNT_ID` and `ANNAS_ARCHIVE_SECRET_KEY` are set, use the API workflow. Save as `scripts/annas_get.py`:

```python
"""Search Anna's Archive and download via the fast_download API.

Usage:
  python annas_get.py search "How to Write Short Roy Peter Clark" --ext epub
  python annas_get.py download <md5> --output ./library/raw/
"""
import os
import re
import sys
import json
import time
import argparse
from pathlib import Path
import httpx
from bs4 import BeautifulSoup

MIRRORS = [
    "https://annas-archive.org",
    "https://annas-archive.li",
    "https://annas-archive.se",
    "https://annas-archive.in",
    "https://annas-archive.pm",
]

UA = "Mozilla/5.0 (compatible; personal-ebook-ingest/1.0)"


def pick_mirror() -> str:
    """Return the first mirror that responds 200 to /."""
    for m in MIRRORS:
        try:
            r = httpx.get(m, timeout=5.0, headers={"User-Agent": UA})
            if r.status_code == 200:
                return m
        except httpx.HTTPError:
            continue
    raise RuntimeError("No Anna's Archive mirror reachable")


def search(query: str, ext: str | None = None, limit: int = 10) -> list[dict]:
    """Scrape search results. Returns list of {md5, title, author, ext, size, lang}."""
    base = pick_mirror()
    params = {"q": query}
    if ext:
        params["ext"] = ext
    r = httpx.get(f"{base}/search", params=params, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    # Result cards link to /md5/<hash>; metadata is in adjacent text
    for a in soup.select("a[href^='/md5/']")[:limit]:
        md5 = a["href"].split("/md5/")[1].split("?")[0]
        title = a.get_text(strip=True)[:200]
        # Sibling text often has "epub, EN, 1.2MB" etc.
        meta = a.find_next("div")
        meta_text = meta.get_text(" ", strip=True) if meta else ""
        results.append({
            "md5": md5,
            "title": title,
            "meta": meta_text,
        })
    return results


def fast_download(md5: str, output_dir: Path) -> Path:
    """Use the fast_download JSON API to retrieve a direct URL, then fetch the file."""
    account_id = os.environ.get("ANNAS_ARCHIVE_ACCOUNT_ID")
    secret_key = os.environ.get("ANNAS_ARCHIVE_SECRET_KEY")
    if not account_id or not secret_key:
        raise RuntimeError("ANNAS_ARCHIVE_ACCOUNT_ID and ANNAS_ARCHIVE_SECRET_KEY must both be set")

    base = pick_mirror()
    api_url = f"{base}/dyn/api/fast_download.json"
    r = httpx.get(api_url,
                  params={"md5": md5, "account_id": account_id, "secret_key": secret_key},
                  timeout=30, headers={"User-Agent": UA})
    r.raise_for_status()
    payload = r.json()

    if "download_url" not in payload:
        raise RuntimeError(f"API returned no download_url: {payload}")

    direct_url = payload["download_url"]
    # File extension is in the URL path or in the API response
    ext = payload.get("ext") or direct_url.rsplit(".", 1)[-1].split("?")[0]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{md5}.{ext}"

    with httpx.stream("GET", direct_url, timeout=300,
                      headers={"User-Agent": UA}, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                f.write(chunk)

    print(f"Downloaded: {out_path} ({out_path.stat().st_size:,} bytes)")
    return out_path


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--ext", choices=["epub", "pdf", "mobi", "azw3", "djvu"])
    s.add_argument("--limit", type=int, default=10)

    d = sub.add_parser("download")
    d.add_argument("md5")
    d.add_argument("--output", type=Path, default=Path("./library/raw"))

    args = p.parse_args()
    if args.cmd == "search":
        for hit in search(args.query, ext=args.ext, limit=args.limit):
            print(f"{hit['md5']}  {hit['title']}\n    {hit['meta']}")
    elif args.cmd == "download":
        fast_download(args.md5, args.output)
```

Workflow:
```bash
# Find the book
python scripts/annas_get.py search "How to Write Short Roy Peter Clark" --ext epub

# Inspect results, copy the md5 of the cleanest match, download
python scripts/annas_get.py download <md5> --output ./library/raw/clark-roy-peter/
```

**Daily limit**: paid membership has a per-day fast-download cap (typically a few dozen books). The API returns an error message in the JSON when exceeded; check the response before assuming success.

**HTML scraping note**: the search page markup changes occasionally. If `search()` returns empty when results clearly exist, inspect the page in a browser, find the new selector pattern, and update the `soup.select(...)` line. The fast_download.json contract is more stable than the HTML.

**Existing tools** (alternatives to writing your own):
- `iosifache/annas-mcp` — Go binary, both CLI and MCP server modes
- `ratacat/claude-skills-annas-archive-ebooks` — Claude skill bundle that mirrors this approach

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

## Step 3: Acquire and verify

```bash
# Establish library structure once
mkdir -p library/{raw,processed,chunks}

# Download via the API helper (preferred when key is set)
python scripts/annas_get.py download <md5> --output library/raw/<author-slug>/

# Or manual download via browser into library/raw/<author-slug>/<title-slug>.<ext>

# Verify file integrity
file library/raw/clark-roy-peter/*.epub
# Expected: "EPUB document" or "Zip archive data" (EPUBs are ZIPs)
```

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

Save as `scripts/clean_book.py`:

```python
import re
from pathlib import Path

def clean_book_text(raw: str) -> str:
    text = raw

    # Rejoin words hyphenated across line breaks: "exam-\nple" -> "example"
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)

    # Strip lines that are only page numbers
    text = re.sub(r'^\s*\d+\s*$\n', '', text, flags=re.MULTILINE)

    # Strip running headers/footers: detect lines that repeat >5 times
    lines = text.split('\n')
    from collections import Counter
    counts = Counter(l.strip() for l in lines if l.strip())
    boilerplate = {l for l, c in counts.items() if c > 5 and len(l) < 80}
    lines = [l for l in lines if l.strip() not in boilerplate]
    text = '\n'.join(lines)

    # Collapse runs of blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Normalize smart quotes (helps tokenizer consistency)
    text = (text
        .replace('“', '"').replace('”', '"')
        .replace('‘', "'").replace('’', "'")
        .replace('–', '-').replace('—', '--'))

    return text.strip()

if __name__ == '__main__':
    import sys
    path = Path(sys.argv[1])
    cleaned = clean_book_text(path.read_text())
    path.with_suffix('.clean.md').write_text(cleaned)
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

## Notes on legality

US fair use for format-shifting print books you own is genuinely murky — there's no clean DMCA exemption for personal e-text creation, but enforcement against private personal use is essentially nonexistent. The cleanest provenance story comes from scanning print copies you own (services like 1DollarScan, or DIY with a CZUR scanner). This skill is agnostic to source — the conversion and cleanup steps work identically regardless of how the source file was acquired.
