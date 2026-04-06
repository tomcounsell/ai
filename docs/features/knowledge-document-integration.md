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

### 5. Companion Memories

Each indexed document gets one or more companion Memory records that integrate with the existing subconscious memory system:

- **Source**: `source="knowledge"` (new constant `SOURCE_KNOWLEDGE`)
- **Importance**: 3.0 -- between agent observations (1.0) and human messages (6.0)
- **Reference pointer**: JSON string pointing to the source file, e.g. `{"tool": "read_file", "params": {"file_path": "/path/to/doc.md"}}`
- **Summarization**: Haiku generates a 1-2 sentence summary. Falls back to first 500 chars if the API call fails.
- **Large documents**: Files over 2000 words are split by top-level headings (h1/h2), producing one companion memory per section.

Companion memories enter the bloom filter like any other memory. When the agent works on a related project, the bloom fires, the thought is injected, and the agent can read the full file on demand.

### 6. Memory `reference` Field

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
| Supported extensions | `.md`, `.txt`, `.markdown`, `.text` | `tools/knowledge/indexer.py` |
| Health check interval | 60 seconds | `bridge/telegram_bridge.py` heartbeat |

## Key Files

| File | Purpose |
|------|---------|
| `models/knowledge_document.py` | KnowledgeDocument Popoto model with `safe_upsert()` and `delete_by_path()` |
| `models/memory.py` | Memory model with `reference` field and `SOURCE_KNOWLEDGE` constant |
| `tools/knowledge/indexer.py` | Indexer pipeline: `index_file()`, `delete_file()`, `full_scan()`, companion memory creation |
| `tools/knowledge/scope_resolver.py` | Scope resolution from file paths to `(project_key, scope)` via projects.json |
| `bridge/knowledge_watcher.py` | `KnowledgeWatcher` class wrapping watchdog Observer with debouncing and health checks |
| `bridge/telegram_bridge.py` | Bridge integration: watcher startup, shutdown, and 60s health check |

## Reversibility

High reversibility -- to remove this feature:

1. Remove watcher startup/shutdown/health-check code from `bridge/telegram_bridge.py`
2. Delete `bridge/knowledge_watcher.py`
3. Delete `tools/knowledge/` directory
4. Delete `models/knowledge_document.py`
5. Remove `reference` field and `SOURCE_KNOWLEDGE` from `models/memory.py`
6. Remove `watchdog` from `pyproject.toml`
7. Flush Redis keys: `redis-cli KEYS "*KnowledgeDocument*" | xargs redis-cli DEL`

No existing behavior changes. No schema migrations involved.

## Tracking

- Issue: [#528](https://github.com/tomcounsell/ai/issues/528)
- PR: [#605](https://github.com/tomcounsell/ai/pull/605)
- Depends on: Subconscious memory system ([docs](subconscious-memory.md))
