---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/861
last_comment_id:
plan_link: https://github.com/tomcounsell/ai/blob/main/docs/plans/chunked_document_retrieval.md
---

# Chunked Document Retrieval

## Problem

Long documents (legal contracts, research papers, project briefs) cannot be meaningfully embedded as a single vector. The `KnowledgeDocument` model produces one 1536-dimensional embedding per document, which creates two failure modes:

**Current behavior:**
1. **Content truncation**: Documents exceeding 8,192 tokens are truncated before embedding (#859 stopgap). Everything after the cutoff is invisible to semantic search.
2. **Signal dilution**: Even for documents under the token limit, a single embedding averages the semantic signal across the entire document. Searching for a specific clause returns weak matches because the relevant signal is a tiny fraction of the overall vector.

**Desired outcome:**
- All content in a long document is embedded and searchable -- nothing is silently dropped
- Searching for a specific passage returns the chunk containing that passage, with the parent document file path
- Short documents produce a single chunk -- no unnecessary splitting

## Prior Art

- **[Issue #859](https://github.com/tomcounsell/ai/issues/859)**: "OpenAI embedding input exceeds 8192 token limit" -- Added simple truncation as a stopgap. Its plan explicitly noted "chunking can be a follow-up." This issue is that follow-up.
- **[Issue #528](https://github.com/tomcounsell/ai/issues/528)** / **[PR #605](https://github.com/tomcounsell/ai/pull/605)**: Original KnowledgeDocument integration with single-vector embeddings.
- **Legacy `tools/knowledge_search/`**: SQLite-based system with a `chunks` table, per-chunk embeddings, cosine similarity scoring, and document-level deduplication. Validates the chunk-level search pattern. Slated for removal.

## Data Flow

### Current (single-vector)

1. **Entry point**: File change detected by `KnowledgeWatcher` or `full_scan()`
2. **`index_file()`**: Reads file content, resolves scope
3. **`KnowledgeDocument.safe_upsert()`**: Stores full content, triggers `EmbeddingField.on_save()` which embeds the whole (or truncated) document as one vector
4. **Search**: Queries `KnowledgeDocument` embedding matrix -- returns document-level matches only

### Proposed (chunked)

1. **Entry point**: Same -- file change or scan
2. **`index_file()`**: Reads file content, resolves scope
3. **`KnowledgeDocument.safe_upsert()`**: Stores full content and document-level embedding (unchanged)
4. **NEW -- Chunking**: After parent doc is saved, split content into overlapping chunks (~1,500 tokens each, ~200 token overlap). Use heading boundaries when available (reuse `_split_by_headings()`), fall back to token-count splitting.
5. **NEW -- `DocumentChunk` records**: Create one `DocumentChunk` Popoto model per chunk, each with its own `EmbeddingField`. Delete old chunks first (full regeneration on content change).
6. **Search**: Query `DocumentChunk` embeddings -- return matching chunk text, parent doc path, chunk index, and similarity score. `KnowledgeDocument` embeddings remain available for document-level similarity.

## Architectural Impact

- **New dependencies**: None. `tiktoken` is already available as a transitive dependency of `openai`. Popoto ORM already in use.
- **Interface changes**: New `DocumentChunk` model. New `search_chunks()` function. Existing `KnowledgeDocument` interface unchanged.
- **Coupling**: `DocumentChunk` depends on `KnowledgeDocument` (parent FK). The indexer orchestrates both.
- **Data ownership**: Chunk records are owned by the indexer pipeline, same as `KnowledgeDocument`.
- **Reversibility**: High -- delete `DocumentChunk` model, remove chunk creation from indexer, flush Redis keys. `KnowledgeDocument` is untouched.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on chunk search API)
- Review rounds: 1

The new model is straightforward (follows `KnowledgeDocument` patterns exactly), but the chunking logic, search integration, and test coverage require careful work.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `OPENAI_API_KEY` | `python -c "import os; assert os.environ.get('OPENAI_API_KEY')"` | Embedding generation via OpenAI provider |
| PR #863 merged | `git log --oneline main \| grep -q 'popoto orphan cleanup'` | #863 modifies `models/__init__.py` -- rebase needed before building |
| `tiktoken` available | `python -c "import tiktoken"` | Token counting for chunk boundaries |

## Solution

### Key Elements

- **`DocumentChunk` Popoto model**: Stores individual chunks with per-chunk embeddings. Fields: `chunk_id` (auto key), `document_doc_id` (FK to parent), `chunk_index` (ordering), `content` (chunk text via ContentField), `embedding` (auto-generated), `file_path` (denormalized), `project_key` (denormalized).
- **Chunking engine**: Splits document content into chunks respecting heading boundaries and token limits. Configurable chunk size and overlap via module constants.
- **Chunk lifecycle**: Managed entirely by the indexer. When a document is re-indexed (content hash changed), all old chunks are deleted and regenerated. When a document is deleted, its chunks are deleted.
- **Chunk search**: New `search_chunks()` function queries `DocumentChunk` embeddings, returns matching chunk text with parent doc path and chunk index.

### Flow

**Indexing:** `index_file()` -> `KnowledgeDocument.safe_upsert()` -> **`_sync_chunks(doc, content)`** -> split content -> delete old `DocumentChunk` records -> create new `DocumentChunk` records (each auto-embeds on save)

**Search:** `search_chunks(query, project_key)` -> embed query via `OpenAIProvider` -> `EmbeddingField.load_embeddings(DocumentChunk)` -> cosine similarity -> return top-K chunks with metadata

### Technical Approach

- Chunk size of ~1,500 tokens with ~200 token overlap, configurable via constants `CHUNK_SIZE_TOKENS` and `CHUNK_OVERLAP_TOKENS`
- Use `tiktoken` with `cl100k_base` encoding for accurate token counting (same encoding as `text-embedding-3-small`)
- Heading-aware splitting: prefer splitting at h1/h2 boundaries (reuse/extend `_split_by_headings()`). If a heading section exceeds `CHUNK_SIZE_TOKENS`, split it further by token count with overlap.
- Documents under `CHUNK_SIZE_TOKENS` produce exactly one chunk
- `DocumentChunk.content` uses `ContentField(store="filesystem")` to keep chunk text out of Redis (consistent with `KnowledgeDocument`)
- The `EmbeddingField(source="content")` on `DocumentChunk` auto-generates embeddings on save -- same pattern as `KnowledgeDocument`
- Chunk search returns: `{"chunk_text": str, "file_path": str, "chunk_index": int, "score": float, "project_key": str}`
- Parent doc's own `EmbeddingField` is retained for document-level similarity (e.g., "find documents like this one")

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `DocumentChunk` save failures must not crash the parent `KnowledgeDocument` indexing -- wrap chunk creation in try/except with logging, consistent with `safe_upsert()` pattern
- [ ] If `tiktoken` encoding fails for a chunk, log warning and skip that chunk rather than failing the entire document

### Empty/Invalid Input Handling
- [ ] Empty document content produces zero chunks (no empty `DocumentChunk` records)
- [ ] Document with only whitespace produces zero chunks
- [ ] Very short documents (under chunk size) produce exactly one chunk
- [ ] Documents with no headings fall back to token-count splitting correctly

### Error State Rendering
- [ ] No user-visible output -- chunk search results are consumed programmatically by the agent. Errors are logged.

## Test Impact

- [ ] `tests/unit/test_knowledge_document.py::test_model_has_required_fields` -- UPDATE: add `DocumentChunk` import and field checks in a new test class
- [ ] `tests/unit/test_knowledge_document.py::TestKnowledgeDocumentModel` -- No change needed (existing tests for `KnowledgeDocument` are unaffected since its interface is unchanged)

No other existing tests are affected -- the indexer tests (`tests/unit/test_knowledge_indexer.py`) test companion memory creation but not chunk creation (which does not exist yet). The new chunking logic will have its own test file.

## Rabbit Holes

- **Hierarchical chunk retrieval (parent-child-sibling navigation)**: Advanced RAG patterns add significant complexity. A flat chunk model with overlap is sufficient for the stated use cases.
- **Dynamic chunk sizing based on content type**: Adapting chunk size to code vs. prose vs. tables adds complexity with marginal benefit. Fixed token-count chunks with heading awareness is good enough.
- **Modifying the Popoto `OpenAIProvider`**: Token truncation and chunking should happen in this repo's code, not by patching the library.
- **Replacing `KnowledgeDocument` single embedding**: The document-level embedding remains useful for document-to-document similarity. Chunks are additive, not a replacement.
- **Embedding model upgrades**: Staying with `text-embedding-3-small` (1536-dim). Model changes are a separate concern.

## Risks

### Risk 1: Embedding API cost increase
**Impact:** Each document now generates N embeddings (one per chunk) instead of one. A 10-page document (~5,000 tokens) would produce ~4 chunks instead of 1.
**Mitigation:** `text-embedding-3-small` is extremely cheap ($0.02 per million tokens). Even indexing 1,000 documents with 10 chunks each would cost ~$0.30. Re-indexing only happens when content hash changes.

### Risk 2: Redis/filesystem storage increase
**Impact:** More `DocumentChunk` records in Redis, more `.npy` files and `ContentField` files on disk.
**Mitigation:** Each chunk's Redis footprint is minimal (key fields + dimension count integer). Content and embeddings are stored on the filesystem via `ContentField` and `EmbeddingField`. Storage scales linearly with document count -- not a concern at current vault sizes.

### Risk 3: Chunk boundary artifacts
**Impact:** If a concept spans a chunk boundary, it may not be captured fully in either chunk's embedding.
**Mitigation:** 200-token overlap (~800 characters) ensures concepts at boundaries appear in at least one chunk. Heading-aware splitting reduces arbitrary mid-sentence breaks.

## Race Conditions

No race conditions identified -- the indexer is invoked serially from `KnowledgeWatcher` (debounced file events) or `full_scan()` (sequential file walk). Chunk deletion and recreation happen in the same synchronous call. The `EmbeddingField` cache is invalidated on save/delete within the same process.

## No-Gos (Out of Scope)

- Hierarchical chunk retrieval or parent summary embeddings
- Modifying the Popoto package or `EmbeddingField` internals
- Removing or replacing the legacy `tools/knowledge_search/` SQLite system (separate task)
- Changing the embedding model or dimensions
- Adding chunk-level search to the MCP server (can be a follow-up if the agent needs direct access)
- Re-indexing all existing documents automatically on deploy (manual `full_scan()` is sufficient)

## Update System

No update system changes required -- no new dependencies, no new config files. The `DocumentChunk` model will be created automatically by Popoto when first accessed. Existing installations will start creating chunks on the next `full_scan()` or file change event.

## Agent Integration

No agent integration required for v1 -- chunk search is consumed by the companion memory system and future search tools. The agent currently accesses knowledge documents through subconscious memory (bloom filter -> thought injection -> `read_file`). Chunks improve what gets recalled but do not change the agent-facing interface.

A follow-up could expose `search_chunks()` via an MCP server tool for direct chunk-level search, but that is out of scope for this issue.

## Documentation

- [ ] Update `docs/features/knowledge-document-integration.md` to document the chunking system, `DocumentChunk` model, and chunk search
- [ ] Add entry to `docs/features/README.md` index table if not already present
- [ ] Code comments on chunking algorithm (heading-aware splitting, overlap logic)
- [ ] Docstrings for `DocumentChunk` model, `_sync_chunks()`, `search_chunks()`, and chunking utility functions

## Success Criteria

- [ ] Long documents (>8,192 tokens) are fully embedded with no content loss
- [ ] Semantic search for a specific passage returns the chunk containing that passage
- [ ] Search results include: matching chunk text, parent document file path, chunk index, and similarity score
- [ ] Chunk overlap ensures concepts at chunk boundaries are discoverable
- [ ] Existing `KnowledgeDocument` records continue to work (backward compatible)
- [ ] Re-indexing a changed document regenerates its chunks (no stale chunks left behind)
- [ ] Short documents (under chunk size threshold) produce a single chunk
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (chunk-model)**
  - Name: chunk-model-builder
  - Role: Create DocumentChunk Popoto model and chunking engine
  - Agent Type: builder
  - Resume: true

- **Builder (indexer-integration)**
  - Name: indexer-builder
  - Role: Integrate chunk lifecycle into the indexer pipeline and add search
  - Agent Type: builder
  - Resume: true

- **Validator (chunk-system)**
  - Name: chunk-validator
  - Role: Verify all success criteria and run tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create DocumentChunk model
- **Task ID**: build-chunk-model
- **Depends On**: none
- **Validates**: tests/unit/test_document_chunk.py (create)
- **Assigned To**: chunk-model-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `models/document_chunk.py` with the `DocumentChunk` Popoto model
- Fields: `chunk_id` (AutoKeyField), `document_doc_id` (KeyField, FK to KnowledgeDocument), `chunk_index` (IntField), `content` (ContentField, store="filesystem"), `embedding` (EmbeddingField, source="content"), `file_path` (KeyField, denormalized), `project_key` (KeyField, denormalized)
- Add `DocumentChunk` to `models/__init__.py` and `__all__`
- Add class methods: `delete_by_parent(doc_id: str)` to delete all chunks for a parent document, `search(query_text: str, project_key: str = None, top_k: int = 5) -> list[dict]` for chunk-level semantic search
- Create `tests/unit/test_document_chunk.py` with: model importable, has required fields, `delete_by_parent` handles missing parent gracefully

### 2. Create chunking engine
- **Task ID**: build-chunking-engine
- **Depends On**: none
- **Validates**: tests/unit/test_chunking.py (create)
- **Assigned To**: chunk-model-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/knowledge/chunking.py` with chunking utilities
- Constants: `CHUNK_SIZE_TOKENS = 1500`, `CHUNK_OVERLAP_TOKENS = 200`
- Function `chunk_document(content: str) -> list[dict]` returns `[{"chunk_index": int, "text": str}, ...]`
- Splitting strategy: (1) split by h1/h2 headings using existing `_split_by_headings()` logic, (2) for sections exceeding `CHUNK_SIZE_TOKENS`, sub-split by token count with overlap, (3) for documents without headings, split entirely by token count with overlap
- Use `tiktoken.get_encoding("cl100k_base")` for token counting (cached at module level)
- Documents under `CHUNK_SIZE_TOKENS` return a single chunk
- Create `tests/unit/test_chunking.py` with: short doc produces one chunk, long doc produces multiple chunks, heading-aware splitting works, overlap is present between adjacent chunks, empty content produces zero chunks

### 3. Integrate chunking into indexer pipeline
- **Task ID**: build-indexer-integration
- **Depends On**: build-chunk-model, build-chunking-engine
- **Validates**: tests/unit/test_knowledge_indexer.py (update), tests/integration/test_chunk_indexing.py (create)
- **Assigned To**: indexer-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_sync_chunks(doc: KnowledgeDocument, content: str)` to `tools/knowledge/indexer.py`
- `_sync_chunks` calls `DocumentChunk.delete_by_parent(doc.doc_id)`, then `chunk_document(content)`, then creates a `DocumentChunk` for each chunk
- Call `_sync_chunks()` from `index_file()` after successful `safe_upsert()` (only when content actually changed -- check return value)
- Add chunk cleanup to `delete_file()`: call `DocumentChunk.delete_by_parent(doc.doc_id)` before deleting the parent
- Wrap all chunk operations in try/except to maintain crash isolation (chunk failures must not break document indexing)

### 4. Implement chunk search
- **Task ID**: build-chunk-search
- **Depends On**: build-chunk-model
- **Validates**: tests/unit/test_document_chunk.py (update)
- **Assigned To**: indexer-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `DocumentChunk.search()` class method: embed query text via the configured OpenAI provider, load all chunk embeddings via `EmbeddingField.load_embeddings(DocumentChunk)`, compute cosine similarity, filter by `project_key` if provided, return top-K results as `[{"chunk_text": str, "file_path": str, "chunk_index": int, "score": float, "project_key": str}]`
- Add search tests to `tests/unit/test_document_chunk.py`: search with no chunks returns empty list, search with matching chunks returns results sorted by score

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-indexer-integration, build-chunk-search
- **Assigned To**: chunk-model-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/knowledge-document-integration.md` to document chunking architecture, `DocumentChunk` model, chunk search, and configuration constants
- Add entry to `docs/features/README.md` index table if needed
- Ensure all new public functions have docstrings

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-indexer-integration, build-chunk-search, document-feature
- **Assigned To**: chunk-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_document_chunk.py tests/unit/test_chunking.py -v`
- Run `pytest tests/unit/test_knowledge_document.py tests/unit/test_knowledge_indexer.py -v` (existing tests still pass)
- Run `python -m ruff check models/document_chunk.py tools/knowledge/chunking.py`
- Run `python -m ruff format --check models/document_chunk.py tools/knowledge/chunking.py`
- Verify `DocumentChunk` is importable from `models`
- Verify chunk search returns results (manual or integration test)

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_document_chunk.py tests/unit/test_chunking.py -x -q` | exit code 0 |
| Existing tests pass | `pytest tests/unit/test_knowledge_document.py tests/unit/test_knowledge_indexer.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check models/document_chunk.py tools/knowledge/chunking.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/document_chunk.py tools/knowledge/chunking.py` | exit code 0 |
| Model importable | `python -c "from models.document_chunk import DocumentChunk; print('OK')"` | output contains OK |
| Chunking importable | `python -c "from tools.knowledge.chunking import chunk_document; print('OK')"` | output contains OK |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions -- the issue provides complete recon, solution sketch, and acceptance criteria. The chunking approach (heading-aware + token-count splitting with overlap) is well-established in RAG literature and validated by the existing `tools/knowledge_search/` implementation in this codebase.
