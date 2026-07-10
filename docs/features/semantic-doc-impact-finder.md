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
- **Agent D** (Issue Impact Scanner): Reviews open GitHub issues for downstream effects and posts comments on affected issues

All four agents run in parallel. If Agent C or D fails (no API key, no matching issues), the cascade degrades gracefully to the remaining agents' output.

## Usage

```python
from tools.doc_impact_finder import index_docs, find_affected_docs

# Build/rebuild the embedding index (skips unchanged chunks via content hashing)
index = index_docs(repo_root=Path("."))

# Find docs affected by a code change
results, meta = find_affected_docs("Refactored thread ID derivation in session manager")
if meta.degraded:
    print(f"finder degraded: {meta.reason}")
for doc in results:
    print(f"{doc.relevance:.2f} | {doc.path} | {doc.sections}")
    print(f"  Reason: {doc.reason}")
```

### Key Functions

| Function | Purpose |
|----------|---------|
| `index_docs(repo_root)` | Walk doc locations, chunk on `##` headings, embed, save index |
| `find_affected_docs(change_summary, top_n, repo_root)` | Two-stage pipeline returning `(list[AffectedDoc], ImpactFinderMeta)` (issue #2004 T1.4 — see Degraded-Result Contract below) |
| `chunk_markdown(content, file_path)` | Split markdown on `##` headings into chunks with content hashes |
| `cosine_similarity(a, b)` | Compute cosine similarity between two embedding vectors |

### Degraded-Result Contract (`ImpactFinderMeta`, issue #2004)

`find_affected_docs` returns a `(results, meta)` tuple, not a bare list. Before
#2004, every degraded/fallback branch returned `[]` indistinguishably from a
clean run that legitimately found nothing (issue #1950) — callers had no way
to tell "no docs affected" from "the finder is broken." `meta` is an
`ImpactFinderMeta` (`tools/impact_finder_core.py`), shared with
`find_affected_code` (see [Code Impact Finder](code-impact-finder.md)):

```python
@dataclass(frozen=True)
class ImpactFinderMeta:
    degraded: bool
    reason: str | None
    rerank_failures: int
    candidates: int
```

`([], meta.degraded=False)` means "clean run, nothing scored high enough to
report." `([], meta.degraded=True)` (or a non-empty `results` with
`degraded=True` on the embedding-only fallback path) means the finder itself
did not complete its normal two-stage pipeline; `meta.reason` names which
branch fired:

| `reason` | Fires when |
|----------|------------|
| `no_embedding_provider` | No `OPENAI_API_KEY`/`VOYAGE_API_KEY`/`ANTHROPIC_API_KEY` configured |
| `empty_index` | `data/doc_embeddings.json` has no chunks yet |
| `query_embedding_failed` | Embedding the change summary itself raised |
| `no_scorable_candidates` | Stage 1 found no candidate above `MIN_SIMILARITY_THRESHOLD` |
| `rerank_client_init_failed` | The Haiku client could not be constructed |
| `rerank_all_failed` | Every Stage 2 rerank request hard-failed (results fall back to embedding-only) |
| `rerank_partial_failure` | Some (not all) Stage 2 requests failed (`rerank_failures` > 0); reranked results are kept as-is, but `degraded=True` flags the gap |

`rerank_failures` counts hard-failed Stage 2 requests (0 when Stage 2 never
ran); `candidates` is the Stage 1 candidate count selected for reranking (0
when Stage 1 produced none). All in-repo callers — both the `doc_impact_finder`
and `code_impact_finder` CLIs, and the `/do-docs` skill's Agent C invocation
(`.claude/skill-context/do-docs.md`) — were migrated to the tuple return in
the same PR; there is no list-subclass compatibility shim.

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

Every branch below is now paired with an `ImpactFinderMeta` naming the
cause, instead of surfacing as an unlabeled `[]` — see Degraded-Result
Contract above.

- **No API key**: `find_affected_docs()` returns `([], meta(degraded=True, reason="no_embedding_provider"))`, logs a warning. The `/do-docs` cascade continues with Agents A and B.
- **No index file**: `find_affected_docs()` warns "Doc index is empty" and returns `([], meta(degraded=True, reason="empty_index"))`.
- **Anthropic client cannot be constructed**: falls back to embedding-only results (Stage 1 candidates above `MIN_SIMILARITY_THRESHOLD`) with `meta.reason="rerank_client_init_failed"`.
- **Every Haiku rerank request fails**: when a transport/API error hits *every* Stage 2 candidate (e.g. a misconfigured `ANTHROPIC_BASE_URL` that 404s on the Haiku model id), `find_affected()` falls back to embedding-only results (`meta.reason="rerank_all_failed"`) and logs `All N Haiku rerank requests failed (check ANTHROPIC_BASE_URL / model id); falling back to embedding-only candidates.` This is an all-or-nothing gate by design: a partial failure where at least one candidate still reranks keeps the reranked results as-is but is still flagged `degraded=True, reason="rerank_partial_failure"` so the gap stays visible, and a clean run where nothing scores >= 5 legitimately returns `([], degraded=False)` (never a false-positive fallback dump). Grep for that warning, or check `meta.reason`, when a run returns fewer docs than expected. (issue #1950, contract added #2004)

### Doc Locations Indexed

The finder indexes these patterns (defined in `DOC_PATTERNS`):

- `docs/**/*.md` -- All feature docs, plans, guides
- `CLAUDE.md` -- Primary project guidance
- `.claude/skills/*/SKILL.md` -- Workflow skill definitions
- `.claude/commands/*.md` -- Slash command docs
- `config/personas/segments/identity.md` -- Agent identity

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
