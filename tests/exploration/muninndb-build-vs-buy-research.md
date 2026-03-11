# Research Task: MuninnDB Internals vs Building on Popoto/Redis

## Objective

Determine whether we should wait for MuninnDB to mature, or build the cognitive memory features we need directly on top of our existing Redis + Popoto ORM stack. This requires understanding the implementation complexity of each MuninnDB feature and assessing what Popoto/Redis can and cannot do natively.

## Context

We tested MuninnDB v0.3.12-alpha and found that the features that actually work (BM25 full-text search, score transparency, vault isolation) are things Redis can already do. The features that would justify a new dependency (ACT-R frequency scoring, Hebbian co-retrieval learning, semantic triggers) are broken or missing in the alpha. See `tests/exploration/muninndb-findings.md` for full test results.

Our stack already has:
- **Redis** as the primary data store
- **Popoto ORM** for all models (`models/` directory) — gives us queryable fields, TTL, and simple persistence
- **LessonLearned** model with category, pattern, summary, prevention, validation_count
- **AgentSession** with history, stage tracking, link inventory
- Embeddings available via OpenAI API (used elsewhere in the codebase)

## Research Questions

### Part 1: MuninnDB Implementation Analysis

Study the MuninnDB source at `github.com/scrypster/muninndb` (Go codebase). For each cognitive feature, document:

1. **ACT-R Base-Level Activation**
   - Where is `B(M) = ln(n+1) − d × ln(ageDays / (n+1))` computed? Find the source file and function.
   - How is `access_count` supposed to increment? Is it on read, on activate, or on a separate endpoint?
   - Is the frequency tracking actually implemented in the current code, or is it stubbed/TODO?
   - How does decay work — is it computed at query time or via a background job?

2. **Hebbian Association Learning**
   - Where are associations stored? What's the data structure (adjacency list, edge table, embedded in engram)?
   - Is co-retrieval strengthening implemented? Find the code path from Activate response → association update.
   - How does bidirectional decay work? Background goroutine? Lazy on read?
   - What's the initial association seeding — is it based on tag overlap, embedding similarity, or something else?

3. **Scoring Pipeline**
   - What is the full scoring formula? Map each `score_components` field to its computation.
   - What is `transition_boost` (undocumented, showed value 0.778 in our tests)?
   - Why does the final score clamp at 1.0? Is this intentional or a normalization bug?
   - How are the component weights configured? Are they hardcoded or per-vault configurable?

4. **Subscription/Trigger System**
   - Is the SSE subscription implemented in the REST handler? (The 500 error on GET /subscribe suggests partial implementation.)
   - How do threshold-based triggers work internally — polling, event-driven, or computed on write?
   - Is the trigger/callback system functional via MCP or gRPC even if REST is broken?

5. **Embedding Integration**
   - How are embeddings stored alongside engrams? (The `embed_dim: 2` field suggests a dimension indicator.)
   - Is retroactive embedding a one-time migration or does it run continuously?
   - How is similarity computed at query time — brute force, HNSW, or something else?

### Part 2: Popoto/Redis Implementation Feasibility

For each MuninnDB feature, assess what it would take to build on our stack:

1. **ACT-R Scoring on Popoto Models**
   - Add `access_count` (IntField) and `last_accessed` (DateTimeField) to LessonLearned
   - Compute `B(M) = ln(n+1) − d × ln(ageDays / (n+1))` at query time in Python
   - Increment access_count on every retrieval
   - **Complexity estimate**: How many lines? Any Popoto limitations (e.g., atomic increment, sort-by-computed-field)?
   - **Key question**: Can Popoto sort by a computed field, or do we need to pre-compute and store the score?

2. **Hebbian Associations**
   - Option A: Separate `Association` Popoto model with `source_id`, `target_id`, `weight`, `last_co_retrieved`
   - Option B: Store associations as a DictField on the engram itself (like `{"other_id": 0.75, ...}`)
   - On each retrieval that returns multiple results, increment weights for all co-retrieved pairs
   - Decay: background job (reflections pipeline) or lazy decay on read
   - **Complexity estimate**: Data model, co-retrieval update logic, decay mechanism
   - **Key question**: What's the performance profile of updating N*(N-1)/2 associations for N co-retrieved results?

3. **Semantic Search**
   - Store embeddings as a field on the model (Popoto stores arbitrary Python objects in Redis)
   - Compute cosine similarity in Python at query time
   - Or use Redis Vector Search (RediSearch module) if available
   - **Key question**: Does our Redis instance have the RediSearch module? If not, is brute-force cosine similarity over <10K engrams fast enough?

4. **BM25 Full-Text Search**
   - Redis has RediSearch with full-text indexing
   - Alternative: compute TF-IDF in Python at query time over the corpus
   - Or simply use substring/keyword matching (what LessonLearned already does with `pattern` field)
   - **Key question**: Is BM25 overkill for our corpus size (<1000 lessons)? Would simpler keyword matching + embeddings be sufficient?

5. **Subscriptions/Triggers**
   - Redis Pub/Sub is native and battle-tested
   - Redis Keyspace Notifications can trigger on write events
   - We could implement threshold triggers: on each write, compute similarity to registered subscription contexts, fire callback if above threshold
   - **Key question**: How expensive is computing similarity against all active subscriptions on every write?

6. **Vault Isolation**
   - Popoto already uses key prefixes for model isolation
   - Vaults = key prefix namespacing (e.g., `memory:lessons:`, `memory:steering:`)
   - Per-vault access control: not needed internally (we trust our own code)
   - **Complexity estimate**: Trivial — just a field on the model or a key prefix convention

### Part 3: Build vs Buy Decision Matrix

For each feature, fill in:

| Feature | MuninnDB Status | Build Complexity | Build Time | Redis Native Support | Recommendation |
|---------|----------------|-----------------|------------|---------------------|----------------|
| ACT-R scoring | Broken | ? | ? | No | ? |
| Hebbian learning | Broken | ? | ? | No | ? |
| Semantic search | Works (with embedder) | ? | ? | RediSearch (if available) | ? |
| BM25 full-text | Works | ? | ? | RediSearch (if available) | ? |
| Subscriptions | Broken (REST) | ? | ? | Pub/Sub native | ? |
| Score transparency | Works | ? | ? | Custom (easy) | ? |
| Vault isolation | Works | ? | ? | Key prefix (trivial) | ? |
| Extractive brief | Works | ? | ? | LLM call (easy) | ? |

### Part 4: Recommended Architecture

Based on the analysis, propose one of:

**Option A: Wait for MuninnDB** — Monitor the alpha, revisit when ACT-R and Hebbian are functional. Use REST API when ready.

**Option B: Build on Popoto** — Implement a `CognitiveMemory` model (or extend `LessonLearned`) with ACT-R scoring, Hebbian associations, and embedding search. Use Redis Pub/Sub for triggers.

**Option C: Hybrid** — Use MuninnDB for what works now (BM25 + semantic + briefs) via REST, build ACT-R and Hebbian on Popoto since MuninnDB's implementations are broken anyway.

**Option D: Minimal viable memory** — Skip the cognitive features entirely. Add `access_count` and embeddings to LessonLearned, use simple cosine similarity for retrieval, and revisit cognitive scoring when we have enough data to prove it matters.

For the recommended option, sketch:
- The data model (Popoto fields or MuninnDB engram schema)
- The query/retrieval flow
- The learning/update flow
- Integration points with Observer (#309) and Reflections pipeline

## Deliverables

1. Annotated source references for MuninnDB's cognitive features (file paths, function names, key data structures)
2. Completed decision matrix with complexity estimates
3. Architecture recommendation with rationale
4. If recommending build: a sketch of the Popoto model and key functions (pseudocode, not production code)

## Source Material

- MuninnDB source: `github.com/scrypster/muninndb` (Go)
- MuninnDB Python SDK source: `github.com/scrypster/muninndb/sdk/python/`
- Our test results: `tests/exploration/muninndb-findings.md`
- Our test scripts: `tests/exploration/test_muninndb.py`, `tests/exploration/test_muninndb_deep.py`
- Popoto ORM: `models/` directory, especially `models/daydream.py` (LessonLearned)
- Observer design: GitHub issue #309
- MuninnDB integration issue: GitHub issue #323
- Redis docs for RediSearch: https://redis.io/docs/latest/develop/interact/search-and-query/
