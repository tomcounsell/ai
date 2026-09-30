---
name: ebook-ingest
description: "Convert and clean legally available ebooks or supplied EPUB, PDF, or MOBI files into Markdown and optional retrieval chunks."
---

# Ebook Ingest

Resolve the book/file and output location. For acquisition, prefer the publisher, library, public-domain, or otherwise authorized source; owning a print copy alone does not establish permission to download a third-party digital copy. Do not bypass DRM. Academic-paper research is outside this workflow.
Read [the conversion procedure](CONVERSION.md) for file verification, EPUB/MOBI/PDF conversion, OCR, cleanup, metadata, and optional chunking. Prefer EPUB when available; inspect extraction before cleanup and compare samples against the original, especially tables and footnotes.
Use [clean_book.py](scripts/clean_book.py) for deterministic cleanup. [annas_get.py](scripts/annas_get.py) is an optional existing archive helper only for content the user is authorized to acquire; it requires its documented credentials and network access. Do not print secrets. Preserve raw input, write clean Markdown with provenance metadata, and chunk only when requested. Verify chapter continuity, text fidelity, and stable chunk identifiers before delivery.
