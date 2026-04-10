# Knowledge Document Integration

> **Business context:** See [Cognitive Memory Design](~/work-vault/AI Valor Engels System/Harness/Cognitive Memory Design.md) in the work vault for the broader memory architecture vision that motivated this feature.

Indexes markdown and text files from the work-vault (`~/work-vault/`) into the memory system, giving agents subconscious awareness of business context, project notes, and decisions that exist outside of conversations. Documents are scoped by project for NDA isolation.

## Architecture

```
~/work-vault/
    ProjectA/           <- client scope (via projects.json knowledge_base)
        notes.md
    ProjectB/
        decisions.md
    company-handbook.md  <- company-wide scope (root-level files)
        |
        v
KnowledgeWatcher (watchdog Observer thread in bridge)
        |
   [2s debounce]
        |
        v
Indexer Pipeline
   |           |
   v           v
KnowledgeDocument        Companion Memory
(Redis + filesystem)     (source="knowledge", importance=3.0)
(content + embedding)    (summary + reference pointer)
                              |
                              v
                         ExistenceFilter (bloom)
                              |
                              v
                         Agent tool call -> bloom fires ->
                         <thought> injected with summary +
                         file path -> agent reads file on demand
```

## How It Works

### 1. Filesystem Watching

The `KnowledgeWatcher` runs as a daemon thread inside the bridge process. It uses the `watchdog` library to monitor `~/work-vault/` for file changes (create, modify, delete, move).

- **Debouncing**: Rapid saves are collapsed via a 2-second `threading.Timer`. Only unique file paths are processed after the debounce window.
- **File filtering**: Only `.md`, `.txt`, `.markdown`, `.text` files are indexed. Hidden files/directories (starting with `.`) and archive directories (wrapped in `_underscores_`) are skipped.
- **Startup scan**: On start, the watcher launches a background thread that walks the entire vault and indexes any files changed since the last run (compared by mtime).
- **Health check**: The bridge heartbeat loop calls `is_healthy()` every 60 seconds and auto-restarts the watcher if the Observer thread has died.

### 2. Scope Resolution

The scope resolver maps file paths to `(project_key, scope)` tuples using `projects.json` as the single source of truth.

| Path location | project_key | scope |
|---------------|-------------|-------|
| Under a project's `knowledge_base` directory | That project's key | `"client"` |
| Under `~/work-vault/` root, not under any project | `"company"` | `"company-wide"` |
| Outside known paths | Skipped | -- |

The resolver loads `~/Desktop/Valor/projects.json`, extracts `knowledge_base` paths per project, and matches by longest prefix first. Mappings are cached in memory and can be reloaded via `reload_mappings()`.

### 3. Indexing Pipeline

`index_file(file_path)` processes a single file:

1. Validate file type and check it is not hidden/archived
2. Resolve scope via `resolve_scope()`
3. Upsert `KnowledgeDocument` via `safe_upsert()` -- skips re-indexing if content hash (SHA-256) is unchanged
4. Create companion Memory records with Haiku-generated summaries

`full_scan(vault_path)` walks the vault directory and calls `index_file()` for any file whose mtime exceeds the stored `last_modified` timestamp.

`delete_file(file_path)` removes both the `KnowledgeDocument` and its companion memories.

### 4. KnowledgeDocument Model

A Popoto Redis model storing indexed documents:

| Field | Type | Description |
|-------|------|-------------|
| `doc_id` | AutoKeyField | Unique identifier |
| `file_path` | KeyField | Absolute path to source file |
| `project_key` | KeyField | Project partition key |
| `scope` | StringField | `"client"` or `"company-wide"` |
| `content` | ContentField | Document text (stored on filesystem, not in Redis) |
| `embedding` | EmbeddingField | Auto-generated via OpenAI `text-embedding-3-small` (1536-dim) |
| `content_hash` | StringField | SHA-256 hash for skip-if-unchanged optimization |
| `last_modified` | FloatField | File mtime at time of indexing |

Embeddings are generated automatically by Popoto's `EmbeddingField` using the globally configured `OpenAIProvider`. The provider is set via `popoto.configure(embedding_provider=OpenAIProvider())` at bridge startup.

### 5. Document Chunking

Long documents are split into overlapping chunks, each with its own embedding, enabling fine-grained semantic search. This eliminates the two failure modes of single-vector document embedding: content truncation (documents over 8,192 tokens had content silently dropped) and signal dilution (a single embedding averages the semantic signal across the entire document).

#### DocumentChunk Model

A Popoto Redis model storing individual chunks:

| Field | Type | Description |
|-------|------|-------------|
| `chunk_id` | AutoKeyField | Unique identifier |
| `document_doc_id` | KeyField | FK to parent KnowledgeDocument |
| `chunk_index` | IntField | Ordering index within parent (0-based) |
| `content` | ContentField | Chunk text (stored on filesystem) |
| `embedding` | EmbeddingField | Auto-generated via OpenAI `text-embedding-3-small` |
| `file_path` | KeyField | Denormalized parent document path |
| `project_key` | KeyField | Denormalized project key for filtering |

#### Chunking Strategy

The chunking engine (`tools/knowledge/chunking.py`) splits documents using a heading-aware strategy:

1. **Short documents** (under `CHUNK_SIZE_TOKENS`): Produce a single chunk -- no unnecessary splitting.
2. **Documents with headings**: Split at h1/h2 boundaries. If a heading section exceeds `CHUNK_SIZE_TOKENS`, it is sub-split by token count with overlap.
3. **Documents without headings**: Split entirely by token count with overlap.

Token counting uses `tiktoken` with the `cl100k_base` encoding (same encoding as `text-embedding-3-small`). Default constants:

| Constant | Value | Description |
|----------|-------|-------------|
| `CHUNK_SIZE_TOKENS` | 1500 | Target tokens per chunk |
| `CHUNK_OVERLAP_TOKENS` | 200 | Overlap between adjacent chunks (~800 characters) |

The 200-token overlap ensures concepts at chunk boundaries appear in at least one chunk's embedding.

#### Chunk Lifecycle

Chunks are managed entirely by the indexer pipeline:

- **On index**: After `KnowledgeDocument.safe_upsert()`, the indexer computes the content hash and compares it to the existing document's hash. If content changed, `_sync_chunks()` deletes all old chunks and creates new ones.
- **On delete**: `delete_file()` deletes all chunks for the document before deleting the parent.
- **Orphan cleanup**: `_cleanup_orphan_chunks()` runs at the end of `full_scan()`, deleting any chunks whose parent `KnowledgeDocument` no longer exists.

#### Chunk Search

`DocumentChunk.search(query_text, project_key=None, top_k=5)` provides chunk-level semantic search:

1. Embeds the query via `OpenAIProvider`
2. Loads all chunk embeddings via `EmbeddingField.load_embeddings(DocumentChunk)`
3. Computes cosine similarity
4. Filters by `project_key` if provided
5. Returns top-K results as:

```python
[{
    "chunk_text": str,      # The matching chunk content
    "file_path": str,       # Parent document file path
    "chunk_index": int,     # Position within parent document
    "score": float,         # Cosine similarity score
    "project_key": str      # Project key for isolation
}]
```

The parent document's own `EmbeddingField` is retained for document-level similarity (e.g., "find documents like this one").

### 6. Companion Memories

Each indexed document gets one or more companion Memory records that integrate with the existing subconscious memory system:

- **Source**: `source="knowledge"` (new constant `SOURCE_KNOWLEDGE`)
- **Importance**: 3.0 -- between agent observations (1.0) and human messages (6.0)
- **Reference pointer**: JSON string pointing to the source file, e.g. `{"tool": "read_file", "params": {"file_path": "/path/to/doc.md"}}`
- **Summarization**: Haiku generates a 1-2 sentence summary. Falls back to first 500 chars if the API call fails.
- **Large documents**: Files over 2000 words are split by top-level headings (h1/h2), producing one companion memory per section.

Companion memories enter the bloom filter like any other memory. When the agent works on a related project, the bloom fires, the thought is injected, and the agent can read the full file on demand.

### 7. Memory `reference` Field

The Memory model has a new `reference` StringField (default empty string). This is a generic JSON pointer for actionable next steps -- not limited to knowledge documents. The field is backwards-compatible; existing memories are unaffected.

Reference pointer format for knowledge documents:
```json
{"tool": "read_file", "params": {"file_path": "/Users/valor/work-vault/Project/doc.md"}}
```

Future uses could include email thread references, entity pointers, or URLs.

## Bridge Integration

The watcher integrates with the bridge lifecycle:

1. **Startup**: After configuring the embedding provider, `KnowledgeWatcher()` is created and `start()` is called. A mutable ref (`_knowledge_watcher_ref`) stores the instance for shutdown and health checks.
2. **Heartbeat**: Every 60 seconds, the bridge checks `is_healthy()`. If the watcher is unhealthy, it is stopped and restarted automatically.
3. **Shutdown**: Signal handlers call `watcher.stop()` to cleanly shut down the Observer thread.

## Crash Isolation

The watcher is designed to never take down the bridge:

- All watchdog event handler methods catch exceptions internally
- The indexer pipeline catches all exceptions and logs warnings
- Haiku summarization failures fall back to content truncation
- KnowledgeDocument upsert failures return None (non-fatal)
- The watcher thread is a daemon -- it does not prevent bridge shutdown

## Relationship to Existing `tools/knowledge_search/`

The existing `tools/knowledge_search/` is a standalone SQLite-based knowledge search tool. The new KnowledgeDocument system supersedes it by using Popoto (consistent with the rest of the codebase), providing project-scoped isolation, and integrating with the subconscious memory system. The old tool is preserved during v1; a follow-up task will migrate any indexed data and remove it.

## Configuration

| Setting | Value | Location |
|---------|-------|----------|
| Embedding model | `text-embedding-3-small` (1536-dim) | `popoto.embeddings.openai.OpenAIProvider` |
| Debounce delay | 2 seconds | `bridge/knowledge_watcher.py` |
| Large doc threshold | 2000 words | `tools/knowledge/indexer.py` |
| Knowledge importance | 3.0 | `tools/knowledge/indexer.py` |
| Summary fallback length | 500 chars | `tools/knowledge/indexer.py` |
| Chunk size | 1500 tokens | `tools/knowledge/chunking.py` |
| Chunk overlap | 200 tokens | `tools/knowledge/chunking.py` |
| Supported extensions | `.md`, `.txt`, `.markdown`, `.text` | `tools/knowledge/indexer.py` |
| Health check interval | 60 seconds | `bridge/telegram_bridge.py` heartbeat |

## Key Files

| File | Purpose |
|------|---------|
| `models/knowledge_document.py` | KnowledgeDocument Popoto model with `safe_upsert()` and `delete_by_path()` |
| `models/document_chunk.py` | DocumentChunk Popoto model with `delete_by_parent()` and `search()` |
| `models/memory.py` | Memory model with `reference` field and `SOURCE_KNOWLEDGE` constant |
| `tools/knowledge/indexer.py` | Indexer pipeline: `index_file()`, `delete_file()`, `full_scan()`, chunk sync, companion memory creation |
| `tools/knowledge/chunking.py` | Chunking engine: heading-aware + token-count splitting with overlap |
| `tools/knowledge/scope_resolver.py` | Scope resolution from file paths to `(project_key, scope)` via projects.json |
| `bridge/knowledge_watcher.py` | `KnowledgeWatcher` class wrapping watchdog Observer with debouncing and health checks |
| `bridge/telegram_bridge.py` | Bridge integration: watcher startup, shutdown, and 60s health check |

## Reversibility

High reversibility -- to remove this feature:

1. Remove watcher startup/shutdown/health-check code from `bridge/telegram_bridge.py`
2. Delete `bridge/knowledge_watcher.py`
3. Delete `tools/knowledge/` directory
4. Delete `models/knowledge_document.py` and `models/document_chunk.py`
5. Remove `reference` field and `SOURCE_KNOWLEDGE` from `models/memory.py`
6. Remove `watchdog` and `tiktoken` from `pyproject.toml`
7. Flush Redis keys: `redis-cli KEYS "*KnowledgeDocument*" | xargs redis-cli DEL` and `redis-cli KEYS "*DocumentChunk*" | xargs redis-cli DEL`

No existing behavior changes. No schema migrations involved.

## Tracking

- Issue: [#528](https://github.com/tomcounsell/ai/issues/528)
- PR: [#605](https://github.com/tomcounsell/ai/pull/605)
- Depends on: Subconscious memory system ([docs](subconscious-memory.md))
