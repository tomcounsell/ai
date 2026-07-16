# Length-safe content store (#2085)

## The bug

`KnowledgeDocument.content` and `DocumentChunk.content` are popoto
`ContentField`s: the actual text lives on the filesystem (not in Redis), and
Redis stores a `$CF:{hash}:{relative_path}` reference string pointing at it.

Popoto's default `FilesystemStore` derives the on-disk filename from the
model's sorted key fields joined by `:`, sanitized into a single filename
component. For `DocumentChunk` that key is
`chunk_id:document_doc_id:file_path:project_key`; for `KnowledgeDocument` it's
`doc_id:file_path:project_key`. Both embed `file_path` — an absolute vault
path that can be arbitrarily long (deeply nested directories, long
filenames).

A long enough `file_path` pushes the sanitized filename past the 255-byte
POSIX/HFS+/APFS `NAME_MAX`, and `chunk.save()` raises
`OSError: [Errno 63] File name too long`. Because `DocumentChunk.save()` is
called inside a per-chunk `try/except` in
`tools/knowledge/indexer.py::_sync_chunks`, that failure is swallowed — the
chunk is silently dropped and the parent `KnowledgeDocument` row still saves
and re-indexes normally. The result: documents with long vault paths quietly
lose all fine-grained chunk search coverage while looking fully indexed.

## The fix: `LengthSafeFilesystemStore`

`models/length_safe_content_store.py` defines `LengthSafeFilesystemStore`, a
`FilesystemStore` subclass that overrides exactly one method:
`_sanitize_filename`. That method is the single seam both `save()` (via
`_live_path`) and `_live_path()` itself use to turn a model's key into an
on-disk filename, so overriding it is sufficient to cap every write path.

Behavior:
- Names at or under the configured byte budget sanitize identically to the
  parent (byte-for-byte) — no churn for already-short keys.
- Names over budget are truncated to a readable prefix plus a stable
  16-hex-char `sha256` digest suffix, computed over the **full, unsanitized**
  key. Because that key always includes the model's unique auto-key field
  (`chunk_id` / `doc_id`), two distinct records can never collide even when
  their long path prefixes are identical. The function is a pure,
  deterministic function of the key, so re-saving the same record overwrites
  its own live file rather than orphaning a new one.

`models/knowledge_document.py` and `models/document_chunk.py` both pass the
same module-level singleton (`length_safe_content_store`) to their
`ContentField(store=...)`, so both models share one content directory.

## Why reads stay backward-compatible

`FilesystemStore.load()` / `_parse_reference()` read the relative path
embedded in the `$CF:{hash}:{relative_path}` reference string stored in
Redis — they never re-derive the filename from the model's key fields. So
old (short-key, non-truncated) references keep resolving unchanged, and new
(long-key, truncated) references resolve through the exact same mechanism.
No migration or backfill is needed for existing content.

## The tunable: `max_content_filename_bytes`

The byte budget is `config/settings.py::PerformanceSettings.max_content_filename_bytes`
(default `200`, provisional/tunable — leaves headroom under the 255-byte
`NAME_MAX` for extensions and tempfile suffixes appended during atomic
writes). Override with the flat `POPOTO_MAX_CONTENT_FILENAME_BYTES` env var;
it's applied in `PerformanceSettings.model_post_init` because
pydantic-settings' nested env-var explosion only discovers
`PERFORMANCE__`-prefixed keys, not a bare `validation_alias` on a nested
field.

`models/length_safe_content_store.py::_load_default_budget()` re-checks the
env var on every `_sanitize_filename` call (not cached from the settings
singleton at import time) so tests can monkeypatch the override after
settings has already been constructed elsewhere in the process. It falls
back to the settings singleton, and finally to a bare literal `200` if
settings import/initialization fails for any reason — this module must never
make popoto model saves depend on the full settings stack being importable.

## The doctor guard

`tools/doctor.py::_check_knowledge_zero_chunk_documents` (category
`Services`, runs unconditionally, not gated behind `--quality`) samples up
to the first 500 `KnowledgeDocument` rows via the ORM and flags any with
non-empty content but zero `DocumentChunk` rows — the exact symptom this bug
produces. A failing check's `fix` message points at the repair helper below.

## The repair helper: `rechunk_zero_chunk_documents()`

`tools/knowledge/indexer.py::rechunk_zero_chunk_documents(project_key=None)`
walks `KnowledgeDocument` records (optionally filtered by `project_key`),
finds ones with non-empty content but zero chunks, and re-runs the same
`_sync_chunks` used at index time. It's idempotent — a document that already
has chunks is skipped — and per-document failures are logged without
aborting the scan.

**Query-load decode subtlety:** a `KnowledgeDocument` instance returned by
`.all()` / `.filter()` / `.get()` surfaces `doc.content` as the raw
`$CF:{hash}:{relative_path}` **reference string**, not the decoded text —
popoto's `Model.__getattribute__` lazy-field path bypasses
`ContentField.__get__` for query-loaded rows (that descriptor only runs for
in-memory instances that were never round-tripped through Redis). Passing
that reference straight into `_sync_chunks` would chunk the literal
`"$CF:..."` string into one garbage chunk — and because the reference is a
non-empty string, a naive `.strip()` truthiness guard doesn't catch it either.

`rechunk_zero_chunk_documents` detects a `$CF:` prefix and decodes through
the field's store before chunking:

```python
field = KnowledgeDocument._meta.fields.get("content")
raw = field.store.load(doc.content).decode("utf-8")
```

skipping with a warning if the content file is missing (`FileNotFoundError`).
The non-empty guard runs *after* decoding, not before.

## Related tests

`tests/integration/test_document_chunk_long_path.py` covers:
- Both models' `content` field routes through `LengthSafeFilesystemStore`
  (and share the singleton instance).
- A `DocumentChunk` / `KnowledgeDocument` with an intentionally long
  `file_path` saves without raising `OSError: [Errno 63] File name too long`,
  and the stored reference round-trips back to the original text via the
  store.
- `rechunk_zero_chunk_documents` decodes a query-loaded `$CF:` reference
  before chunking, rather than chunking the literal reference string.
