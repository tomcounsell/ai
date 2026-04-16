---
status: In Progress
type: feature
appetite: Medium
owner: Valor
created: 2026-04-16
tracking: https://github.com/tomcounsell/ai/issues/965
last_comment_id:
---

# Add Vector-Similarity (Semantic) Recall as Fourth RRF Signal on Memory

## Problem

Memory recall is keyword-only. The three RRF signals (BM25 keyword, temporal decay, confidence) are strong for structured/outcome signals but blind to semantic overlap. A memory saved as "the user prefers terse Slack replies" is not retrieved when the agent is deciding tone for an email, because the lexical overlap is near zero. Paraphrase queries, concept-neighbor recall, and cross-domain transfer all fail.

## Solution

Add an `EmbeddingField` to the Memory model and wire a fourth RRF signal into `retrieve_memories()` that ranks memories by vector cosine similarity to the query. Use an `OllamaProvider` adapter (calling the local Ollama instance at `localhost:11434` with `nomic-embed-text`) so embeddings remain consistent over time regardless of vendor model rotations.

Graceful degradation: if Ollama is unreachable, log a warning and fall back to the existing 3-signal RRF. The embedding field's `auto_embed` is set True, so new memories embed on save when the provider is available; saves still succeed when it is not (the on_save in EmbeddingField catches provider errors).

## Data Flow

```
retrieve_memories(query, project_key)
    |
    +-- Signal 1: BM25Field.search()           (keyword match)
    +-- Signal 2: DecayingSortedField           (temporal relevance)
    +-- Signal 3: ConfidenceField               (learned trust)
    +-- Signal 4: EmbeddingField.load_embeddings() + cosine similarity  (NEW)
    |
    v
  rrf_fuse(bm25, relevance, confidence, embedding)
    |
    v
  Hydrate Memory instances, filter superseded, apply category weights
```

## Architectural Impact

- **New field**: `Memory.embedding = EmbeddingField(source="content")` — adds .npy files under `data/embeddings/Memory/`
- **New module**: `agent/embedding_provider.py` — lightweight `OllamaEmbeddingProvider` class implementing the provider interface (`embed()` + `dimensions`)
- **Modified**: `agent/memory_retrieval.py` — new `get_embedding_ranked()` function + fourth signal in `retrieve_memories()`
- **Modified**: `config/memory_defaults.py` — provider configuration function
- **No new external dependencies**: Uses existing `requests` and `numpy` packages
- **Reversibility**: High — removing the field and provider reverts cleanly; .npy files are orphaned but harmless

## Appetite

**Size:** Medium
**Time budget:** 1 day
**Risk:** Low — additive change with graceful degradation; existing 3-signal path unchanged

## No-Gos

- Do not embed `DocumentChunk` — that's a separate subsystem
- Do not add reranker models (cross-encoder second-stage) — layer on later
- Do not add ANN indexes — brute-force cosine similarity is fine at our memory-record scale (~10k records max)
- Do not make embedding a hard dependency — worker must start and recall must work without Ollama

## Failure Path Test Strategy

- Provider unreachable: `get_embedding_ranked()` returns `[]`, RRF fuses remaining 3 signals
- Provider returns wrong dimensions: `EmbeddingField.on_save()` raises, caught by `safe_save` wrapper
- No embeddings on disk: `load_embeddings()` returns `(None, [])`, similarity search returns `[]`
- Corrupt .npy file: `load_embeddings()` skips it with warning

## Test Impact

- [ ] `tests/unit/test_memory_retrieval.py::TestRetrieveMemories::test_fuses_three_signals` — UPDATE: now fuses four signals; add embedding mock
- [ ] `tests/unit/test_memory_retrieval.py::TestRetrieveMemories::test_bm25_failure_degrades_gracefully` — UPDATE: embedding signal also present
- [ ] `tests/unit/test_memory_retrieval.py::TestRetrieveMemories::test_returns_empty_on_exception` — UPDATE: add embedding mock
- [ ] New test class `TestGetEmbeddingRanked` — tests for the new similarity search function
- [ ] New test class `TestOllamaEmbeddingProvider` — tests for the provider adapter
- [ ] New test `test_paraphrase_recall` — acceptance criterion: semantic recall works

## Update System

No update system changes required — `nomic-embed-text` model pull is a one-time manual step per machine (`ollama pull nomic-embed-text`). The provider gracefully degrades when the model is absent.

## Agent Integration

No agent integration required — this is an internal change to the memory retrieval pipeline. The agent already uses `retrieve_memories()` via the memory hook; the fourth signal is transparent.

## Documentation

- [ ] Update `docs/features/subconscious-memory.md` — add fourth signal to architecture diagram and describe Ollama dependency
- [ ] Update docstrings in `agent/memory_retrieval.py` and `models/memory.py`

## Rabbit Holes

- **Embedding model selection**: Use `nomic-embed-text` (768 dimensions, good quality/speed tradeoff). Do not research alternatives.
- **Backfill script**: Defer to a follow-up. New memories embed on save; old memories participate in 3-signal RRF only until re-saved.
- **Cache invalidation**: `EmbeddingField.load_embeddings()` caches in memory. The existing `invalidate_cache()` call in `on_save` handles this.

## Tasks

### Step 1: OllamaEmbeddingProvider adapter

Create `agent/embedding_provider.py` with:
- `OllamaEmbeddingProvider` class with `embed(texts, input_type)` and `dimensions` property
- HTTP call to `POST http://localhost:11434/api/embed` with model name
- Timeout of 5 seconds per call
- `configure_embedding_provider()` function that sets the global default, with graceful fallback

### Step 2: Add EmbeddingField to Memory model

- Add `from popoto.fields.embedding_field import EmbeddingField` to imports
- Add `embedding = EmbeddingField(source="content")` field
- EmbeddingField's `on_save` handles embedding generation; `safe_save` catches any errors

### Step 3: Wire fourth signal into retrieve_memories()

- Add `get_embedding_ranked(query_text, project_key, limit)` function in `agent/memory_retrieval.py`
- Embed the query via the provider, compute cosine similarity against `EmbeddingField.load_embeddings()`
- Filter by project_key, return `(redis_key, similarity)` tuples
- Add as fourth argument to `rrf_fuse()` in `retrieve_memories()`

### Step 4: Configure provider at startup

- Call `configure_embedding_provider()` in `worker/__main__.py` during startup
- Call it in `config/memory_defaults.py::apply_defaults()` as well for non-worker paths (hooks, CLI)

### Step 5: Tests

- Unit tests for `OllamaEmbeddingProvider` (mocked HTTP)
- Unit tests for `get_embedding_ranked()` (mocked embeddings)
- Update existing `retrieve_memories` tests to include fourth signal mock
- Paraphrase recall test (mocked provider returning similar vectors)

### Step 6: Documentation

- Update `docs/features/subconscious-memory.md`
- Update module docstrings
