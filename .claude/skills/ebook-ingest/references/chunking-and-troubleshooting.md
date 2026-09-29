# ebook-ingest reference

## Chunking for RAG

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
