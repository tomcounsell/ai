# Semantic Doc Impact Finder

Identifies documentation affected by code changes using semantic similarity rather than keyword matching. Catches conceptual coupling that `grep` and `rg` miss entirely.

## How It Works

Two-stage pipeline:

### Stage 1: Embedding Recall

1. **Chunking**: Splits every Markdown doc on `## ` headings. Each chunk gets a SHA-256 content hash for cache invalidation.
2. **Indexing**: Embeds all chunks using the best available provider (OpenAI `text-embedding-3-small` preferred, Voyage `voyage-3-lite` as fallback).
3. **Query**: Embeds the change summary and computes cosine similarity against all indexed chunks. Takes top-N candidates (default 15).

### Stage 2: LLM Reranking

Each candidate chunk is scored by Claude Haiku (0-10) for whether the doc section actually needs updating given the specific change. Only chunks scoring >= 5 pass through. Results are grouped by file path and sorted by relevance.

## What It Catches That Grep Doesn't

| Change | Grep finds | Semantic finder also finds |
|--------|-----------|--------------------------|
| Refactored session scoping logic | Files mentioning `session` | `session-isolation.md` even if it uses "task list isolation" terminology |
| Changed bridge restart behavior | Files referencing `restart` | `bridge-self-healing.md` which describes recovery escalation |
| New config key for sampling | Files containing the key name | `deployment.md` which covers environment configuration conceptually |

The key insight: documentation often describes the *concept* using different vocabulary than the code uses. Embedding similarity bridges this vocabulary gap.

## Integration

The finder runs as **Agent C** in the `/do-docs` cascade skill (`.claude/skills/do-docs/SKILL.md`), alongside:

- **Agent A** (Change Explorer): Analyzes the code diff, traces data flow, identifies retired terms
- **Agent B** (Documentation Inventory): Scans all doc locations for references
- **Agent C** (Semantic Impact Finder): Finds conceptually related docs via embeddings

All three agents run in parallel. If Agent C fails (no API key, no index), the cascade degrades gracefully to Agents A and B only.

## Usage

```python
from tools.doc_impact_finder import index_docs, find_affected_docs

# Build/rebuild the embedding index (skips unchanged chunks via content hashing)
index = index_docs(repo_root=Path("."))

# Find docs affected by a code change
results = find_affected_docs("Refactored thread ID derivation in session manager")
for doc in results:
    print(f"{doc.relevance:.2f} | {doc.path} | {doc.sections}")
    print(f"  Reason: {doc.reason}")
```

### Key Functions

| Function | Purpose |
|----------|---------|
| `index_docs(repo_root)` | Walk doc locations, chunk on `##` headings, embed, save index |
| `find_affected_docs(change_summary, top_n, repo_root)` | Two-stage pipeline returning `list[AffectedDoc]` |
| `chunk_markdown(content, file_path)` | Split markdown on `##` headings into chunks with content hashes |
| `cosine_similarity(a, b)` | Compute cosine similarity between two embedding vectors |

### AffectedDoc Schema

```python
class AffectedDoc(BaseModel):
    path: str          # e.g. "docs/features/session-isolation.md"
    relevance: float   # 0.0 - 1.0
    sections: list[str]  # e.g. ["## Tier 1 (thread-scoped)", "## Git worktrees"]
    reason: str        # Human-readable explanation from Haiku
```

## Configuration

### Embedding Provider Priority

1. `OPENAI_API_KEY` -- Uses `text-embedding-3-small` (recommended, lowest cost)
2. `VOYAGE_API_KEY` -- Uses `voyage-3-lite` (requires `voyageai` package)
3. `ANTHROPIC_API_KEY` -- Falls through to Voyage or OpenAI if available

### Graceful Degradation

- **No API key**: `find_affected_docs()` returns empty list, logs a warning. The `/do-docs` cascade continues with Agents A and B.
- **No index file**: `find_affected_docs()` warns "Doc index is empty" and returns empty list.
- **API call failure**: Logs the exception and returns empty list (or falls back to embedding-only results if only Haiku reranking fails).

### Doc Locations Indexed

The finder indexes these patterns (defined in `DOC_PATTERNS`):

- `docs/**/*.md` -- All feature docs, plans, guides
- `CLAUDE.md` -- Primary project guidance
- `.claude/skills/*/SKILL.md` -- Workflow skill definitions
- `.claude/commands/*.md` -- Slash command docs
- `config/SOUL.md` -- Agent identity

## Storage

Embeddings are cached in `data/doc_embeddings.json` (gitignored, machine-local). The index includes:

- Version number for format migration
- Model name for invalidation when switching providers
- Per-chunk content hashes for incremental re-embedding

Re-indexing only embeds chunks whose content hash changed, keeping API costs low on repeated runs.

## Dependencies

- `numpy` -- Cosine similarity computation
- `pydantic` -- AffectedDoc data model
- `openai` or `voyageai` -- Embedding API client (optional, needed for indexing)
- `anthropic` -- Haiku reranking (optional, falls back to embedding-only results)
