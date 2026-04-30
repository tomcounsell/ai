---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-04-30
tracking: https://github.com/tomcounsell/ai/issues/1213
last_comment_id:
revision_applied: true
---

# Memory Recall Relevance Threshold

## Problem

The subconscious memory recall pipeline (`retrieve_memories()` in `agent/memory_retrieval.py`) returns the top-N RRF-fused results regardless of how relevant they actually are. For ANY query — including queries with zero semantic or keyword overlap with the corpus — RRF returns N records and they get injected into the agent's prompt as `<thought>` blocks via the PostToolUse hook.

**Current behavior** (live reproduction with 173-record corpus):

```bash
$ python -m tools.memory_search search "PHRASE_THAT_DEFINITELY_DOES_NOT_APPEAR_ANYWHERE_QQQQ" --limit 5
Found 5 memories matching '...QQQQ': [5 unrelated records returned]
```

The query string appears in zero records. The CLI returns 5 unrelated records anyway, ranked by recency rather than relevance. When the same path runs from the PostToolUse hook (`recall()` in `.claude/hooks/hook_utils/memory_bridge.py`) or the SDK-side multi-query decomposition (`check_and_inject()` in `agent/memory_hook.py`), those records become `<thought>` blocks injected into the next agent turn — diluting context and burning tokens on noise.

**Desired outcome:** Recall returns 0 results for queries with no real overlap. A relevance gate at the fusion layer drops records below a calibrated RRF score floor. Both recall paths (PostToolUse hook + SDK multi-query) opt into the gate by default; the CLI search path keeps it OFF for back-compat but exposes a flag.

## Freshness Check

**Baseline commit:** `e6aa12a5c7094dae919978fdb9d9e77ad5d6b5a1`
**Issue filed at:** 2026-04-29T16:23:56Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/memory_retrieval.py:235` — `retrieve_memories()` definition — still at line 235.
- `agent/memory_retrieval.py:288` — claimed by issue as `rrf_fuse` location — DRIFTED. `rrf_fuse` is at lines 47-79; line 288 is the `fused = rrf_fuse(...)` call site inside `retrieve_memories`. The recon's correction (`agent/memory_retrieval.py:47-79`) is accurate.
- `agent/memory_retrieval.py:227` — embedding `s > 0` filter — still at line 227.
- `tools/memory_search/__init__.py:89-108` — bloom pre-check loop — still accurate.
- `tools/memory_search/__init__.py:113` — `retrieve_memories(...)` call site in CLI search — still accurate.
- `agent/memory_hook.py:202` — multi-query `retrieve_memories(...)` call inside `check_and_inject` — still accurate.
- `.claude/hooks/hook_utils/memory_bridge.py:352` — recall path. NOTE: the hook bridge has been refactored since the issue was filed. The actual `retrieve_memories()` call now lives in `_recall_with_query()` at line 352, which is invoked from BOTH `recall()` (line 489) and `prefetch()` (line 608). Both paths must opt into the threshold.

**Cited sibling issues/PRs re-checked:**
- issue 1212 (corpus pollution from extraction parser bugs) — still OPEN, not blocking. The two fixes are independent and complementary as the issue states.
- PR #604 (original BM25+RRF fusion) — merged 2026-03-31. PR body explicitly states the design intent: "where a precisely relevant older memory can now outrank a tangentially related but recently accessed one." This resolves the Open Question — design intent is **only-relevant**, not "always surface something."
- PR #1013 (added embedding as 4th signal) — merged 2026-04-16. Already drops negative embedding similarities (`s > 0` at line 227), but this only helps when embedding signal is present and only filters that one signal's negatives — not the fused result.

**Commits on main since issue was filed (touching referenced files):**
- None. `agent/memory_retrieval.py`, `tools/memory_search/__init__.py`, `agent/memory_hook.py`, `.claude/hooks/hook_utils/memory_bridge.py` — all unchanged since 2026-04-29T16:23:56Z.

**Active plans in `docs/plans/` overlapping this area:**
- `memory_embedding_orphan_cleanup.md` — touches embedding orphans, NOT recall ranking. No overlap.
- `memory-extraction-shrapnel-fix.md` — corpus cleanup (issue 1212), distinct from threshold gating. No overlap.

**Notes:** The Recon Summary's correction stands: there are THREE paths into `retrieve_memories` (CLI, SDK multi-query, hook bridge). The hook bridge's `_recall_with_query` is shared by `recall()` and `prefetch()`, so threading the threshold through `_recall_with_query` covers both call sites in one change.

**Revision-pass correction (2026-04-30):** The original Solution/Technical Approach assumed ONLY two bloom-check sites needed tightening. Re-reading the code shows there are actually **FOUR** bloom-check sites — the SDK and hook caller paths both have their own multi-keyword bloom gate that runs BEFORE invoking `_recall_with_query` with `bloom_check=False` (line 493 of `memory_bridge.py`). All four sites must be tightened atomically to avoid leaving a leak:

1. `tools/memory_search/__init__.py:89-108` — CLI search, single query.
2. `_recall_with_query` in `memory_bridge.py:317-346` — used by `prefetch()` (and historically by `recall()` when `bloom_check=True`, but `recall()` now passes `bloom_check=False`).
3. `recall()` in `memory_bridge.py:449-467` — multi-keyword bloom gate that runs BEFORE `_recall_with_query` is called (with `bloom_check=False`). This was MISSED in the original plan.
4. `check_and_inject()` in `agent/memory_hook.py:168-188` — multi-keyword bloom gate. Also MISSED in the original plan.

This correction is folded into the Solution, Technical Approach, Step 2, and Test Impact sections below.

## Prior Art

- **PR #604** (merged 2026-03-31): "Add BM25+RRF fusion retrieval, replace ContextAssembler" — the original recall pipeline. PR body confirms relevance-driven design intent. Did NOT add a min-score gate — that gap is what this plan addresses.
- **PR #1013** (merged 2026-04-16): "feat(#965): add vector-similarity as fourth RRF signal on Memory" — added the embedding signal with `s > 0` filter at retrieval time. Sets precedent for filtering low-quality results inside the retrieval pipeline.
- **Issue #586** (closed 2026-03-30): "Update memory agent integration: metadata-aware recall, retrieval recipes" — added category-weighted re-ranking. Established the pattern of post-fusion adjustments to RRF results.
- **Issue issue 1212** (open): "Memory extraction stores JSON shrapnel and refusal prose as observations" — corpus pollution. Complementary fix; this plan is independent.

No prior issues or PRs attempted to add a relevance threshold to RRF. This is greenfield work on top of an existing pipeline.

## Research

**Queries used:**
- "Reciprocal Rank Fusion RRF minimum score threshold relevance gate 2026"
- "RAG retrieval relevance threshold filtering irrelevant results best practices 2026"

**Key findings:**
- **Major RRF implementations don't ship a min-score gate.** Azure AI Search, Elasticsearch, OpenSearch, and ParadeDB all document RRF without an opinionated threshold — applications are expected to layer their own. Source: [Azure AI Search RRF docs](https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking), [Elasticsearch RRF reference](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion). This confirms we must add the gate ourselves.
- **Weaviate's "autocut" technique** detects a significant drop in similarity scores between consecutive results to identify a cutoff. Useful pattern but harder to calibrate than a fixed score floor; defer to a future iteration. Source: [Weaviate Advanced RAG](https://weaviate.io/blog/advanced-rag).
- **Threshold targets**: production RAG systems target context precision ≥ 0.8. For RRF, a conservative gate that requires presence in ≥ 2 signals (or a single very-high-rank position) approximates this. Source: [Mastering RAG Evaluation 2026](https://orq.ai/blog/rag-evaluation).
- **Even small irrelevance fractions are costly**: irrelevant documents in retrieval reduce final answer accuracy by up to 25% in QA tasks. Justifies adding the gate. Source: [orq.ai RAG Evaluation 2026](https://orq.ai/blog/rag-evaluation).

These findings informed two technical decisions: (1) layer the threshold post-fusion rather than per-signal; (2) prefer a deterministic score floor over autocut for the first iteration to keep calibration straightforward.

## Spike Results

### spike-1: Calibrate the RRF score threshold against the current corpus

- **Assumption**: "A fixed RRF-score floor of approximately `2 / (k + N/2)` filters records that have no genuine overlap with the query while preserving records with at least one strong signal hit."
- **Method**: code-read + math. The existing `rrf_fuse` formula is `score = sum_i(1 / (k + rank_i))`. With `k=60`:
  - A record at rank 1 in 1 signal scores `1/61 ≈ 0.01639`.
  - A record at rank 1 in 2 signals scores `2/61 ≈ 0.03279`.
  - A record at rank 50 (last) in 1 signal scores `1/110 ≈ 0.00909`.
  - For a 173-record corpus, the issue's suggested `1/(k + N/2) = 1/(60+86.5) ≈ 0.00683` requires "above corpus median in any one signal."
- **Finding**: A floor of `1 / (k + N/2)` (issue's suggestion) drops records that rank in the bottom half of all four signals. We adopt this with one enhancement: the threshold MUST also be at least `1 / (k + 1) ≈ 0.01639` so we never accept a record whose only signal contribution is being at the very last rank. The effective floor is `max(1/(k+N/2), MIN_FLOOR)` where `MIN_FLOOR = 1/(k+50)` (rank-50 in one signal). For corpora smaller than ~100 records, the MIN_FLOOR dominates and behaves identically to "must be in top-50 of at least one signal."
- **Confidence**: high
- **Impact on plan**: Adds `RRF_MIN_SCORE` config constant calculated as `1 / (RRF_K + 50)` (a fixed floor of approximately `0.00909`). For empirical calibration, the post-fusion filter's drop-rate on a nonsense query MUST go from 0% (current) to 100% (expected). A unit test enforces this against a real Memory store.

### spike-2: Verify that bloom tightening (≥ 2 hits) doesn't regress true positives

- **Assumption**: "Requiring ≥ 2 unique-token bloom hits before proceeding (vs. the current ≥ 1) catches noise queries without filtering legitimate single-keyword queries."
- **Method**: code-read of `tools/memory_search/__init__.py:89-108` and `_recall_with_query` in `memory_bridge.py:317-346`. The bloom check tokenizes the query, drops noise words, and tests each remaining token. Current behavior breaks on the FIRST hit; proposed behavior counts all hits and requires ≥ 2.
- **Finding**: Single-keyword agent queries are rare in practice — both the SDK multi-query path (`agent/memory_hook.py:200`) and the hook recall path (`memory_bridge.py:482`) build cluster queries by joining 5 keywords. The CLI search path could pass a single keyword, but searching for one word is an anti-pattern (low precision regardless of threshold). Prefetch (`memory_bridge.py:608`) uses the full user prompt — typically dozens of tokens. Risk of regression is low.
- **Confidence**: high
- **Impact on plan**: Bloom check upgrades from "any hit passes" to "≥ 2 hits passes" in **FOUR** sites — `tools/memory_search/__init__.py`, `_recall_with_query`, `recall()`'s pre-cluster gate, and `check_and_inject()`'s pre-cluster gate. (Revision-pass correction — the original spike write-up listed only the first two; re-reading the bridge revealed `recall()` calls `_recall_with_query` with `bloom_check=False` because it has its own multi-keyword bloom gate, and `check_and_inject()` mirrors that pattern.) The constant `BLOOM_MIN_HITS = 2` lives in `config/memory_defaults.py` for tunability. Single-keyword queries (rare) may produce no results — acceptable, since the relevance gate would likely have filtered them anyway.

### spike-3: Confirm the threshold default-OFF/default-ON behavior is API-clean

- **Assumption**: "Adding `min_rrf_score: float | None = None` to `retrieve_memories()` lets callers opt in/out without breaking existing tests."
- **Method**: code-read of all callers (`grep -rn "retrieve_memories"` returned 7 files: 4 production, 3 test).
- **Finding**: All four production callers can be updated atomically:
  1. `tools/memory_search/__init__.py:113` — CLI search; threshold defaults to `None` (OFF). New `--min-score` CLI flag exposes opt-in.
  2. `agent/memory_hook.py:202` — SDK multi-query `check_and_inject`; pass the configured default-ON threshold.
  3. `.claude/hooks/hook_utils/memory_bridge.py:352` — `_recall_with_query`; pass the threshold through. Both `recall()` (line 489) and `prefetch()` (line 608) inherit it.
  4. Test files don't pass `min_rrf_score`, so they exercise the default-OFF path. Existing tests stay green.
- **Confidence**: high
- **Impact on plan**: API shape: `retrieve_memories(query_text, project_key, limit=10, rrf_k=None, min_rrf_score=None)`. When `min_rrf_score` is None, fall back to no filtering (preserves CLI back-compat). When a numeric threshold is passed, drop fused records below that score BEFORE hydration to save Redis round-trips.

## Data Flow

End-to-end recall flow with the new gate:

1. **Entry point**: One of three callers invokes recall:
   - CLI: `python -m tools.memory_search search "..."` → `tools.memory_search.search()`
   - SDK PostToolUse: `agent/health_check.py::watchdog_hook` → `agent/memory_hook.check_and_inject()`
   - Claude Code hooks: `post_tool_use.py` → `memory_bridge.recall()` OR `user_prompt_submit.py` → `memory_bridge.prefetch()`
2. **Bloom pre-check** (CLI + hooks paths): tokenize query, drop noise words, count bloom hits. NEW: require `BLOOM_MIN_HITS >= 2` before proceeding (was `>= 1`). If fewer hits, return empty.
3. **`retrieve_memories()` fan-out**: BM25, relevance (decay), confidence, embedding signals each return up to 50 candidates with project filtering applied.
4. **`rrf_fuse()`**: combines the four ranked lists into a single score-sorted list of (key, fused_score) tuples.
5. **NEW: Post-fusion threshold gate**: if `min_rrf_score` is set, drop tuples with `fused_score < min_rrf_score` BEFORE hydration. Empty list short-circuits to empty.
6. **Hydration**: `Memory.query.get(key)` for each surviving key.
7. **Superseded filter**: drops records with `superseded_by` set (existing behavior).
8. **Category re-ranking**: callers apply `_apply_category_weights()` to multiply by category boosts (existing behavior).
9. **Output**: list of Memory records with `score` attribute set, capped at `limit`. Hook callers format as `<thought>` blocks; CLI returns dicts.

## Why Previous Fixes Failed

No previous fixes attempted to address this. The original RRF PR (#604) deliberately scoped out a threshold, deferring it to future tuning work. PR #1013 added the embedding signal's negative-similarity filter, which is structurally similar but only protects ONE signal — the fused result still includes records with no embedding hit but mediocre presence in temporal/confidence. This plan generalizes that filter to the fused score itself.

## Architectural Impact

- **New dependencies**: none. All changes are within existing modules.
- **Interface changes**:
  - `agent.memory_retrieval.retrieve_memories()` gains an optional `min_rrf_score: float | None = None` parameter. Callers that don't pass it are unaffected.
  - `agent.memory_retrieval.rrf_fuse()` may optionally gain `min_score` (TBD during build — alternative is to filter inside `retrieve_memories` after `rrf_fuse` returns; either is acceptable). Build picks the cleaner one.
  - `tools.memory_search.search()` gains a `min_rrf_score: float | None = None` kwarg threaded to `retrieve_memories`.
  - `tools.memory_search` CLI gains a `--min-score FLOAT` flag.
  - `_recall_with_query()` in `memory_bridge.py` gains an optional `min_rrf_score` parameter; default to the configured threshold for `recall()` and `prefetch()`.
  - `agent.memory_hook.check_and_inject()` does NOT change signature — internally passes the configured threshold to `retrieve_memories`.
- **Coupling**: unchanged. New parameter threads through existing call chains.
- **Data ownership**: unchanged. No model schema changes; no Redis schema changes.
- **Reversibility**: high. Set `RRF_MIN_SCORE = None` in `config/memory_defaults.py` to disable the gate globally. Single-line revert.

## Appetite

**Size:** Medium

**Team:** Solo dev (builder), validator

**Interactions:**
- PM check-ins: 1 (verify spike-1 calibration before merging)
- Review rounds: 1 (PR review for the gate logic and test coverage)

The work is mechanically straightforward — thread one parameter through three call paths, add a config constant, and write tests. The depth comes from getting the threshold value right (handled by spike-1) and ensuring all three call sites flip atomically without breaking the CLI back-compat contract.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Memory model importable | `python -c "from models.memory import Memory; print('ok')"` | Sanity check |
| Existing memory tests pass | `python -m pytest tests/unit/test_memory_retrieval.py -q` | Baseline before changes |
| Redis reachable | `python -m tools.memory_search status` | Required for any test using a real Memory store |

Run all checks: `python scripts/check_prerequisites.py docs/plans/memory-recall-relevance-threshold.md`

## Solution

### Key Elements

- **Post-fusion relevance gate**: a numeric RRF-score floor applied AFTER `rrf_fuse` returns and BEFORE Memory record hydration. Configured globally in `config/memory_defaults.py` as `RRF_MIN_SCORE`.
- **Tightened bloom pre-check**: require `BLOOM_MIN_HITS = 2` unique-token hits (up from any hit) before BM25 + RRF runs. Applied in **FOUR** sites: (1) `tools/memory_search/__init__.py` (CLI search), (2) `_recall_with_query` (hook prefetch path), (3) `recall()`'s pre-cluster bloom gate in `memory_bridge.py`, and (4) `check_and_inject()`'s pre-cluster bloom gate in `agent/memory_hook.py`. The `bloom_hits == 0` branch (deja-vu / novel-territory fallback) is PRESERVED unchanged in sites 2/3/4 — the new gate only kicks in for `1 <= bloom_hits < BLOOM_MIN_HITS`, returning empty without emitting the deja-vu thought.
- **Default-ON for recall paths, default-OFF for CLI**: the gate is enabled by default in the SDK PostToolUse hook (`agent/memory_hook.py`) and in the Claude Code hook bridge (`recall()` + `prefetch()`). The CLI `tools.memory_search.search()` defaults the gate OFF for back-compat with existing scripts but exposes a `--min-score` flag for debugging.
- **Calibrated threshold**: `RRF_MIN_SCORE = 1 / (RRF_K + 50)` (≈ 0.00909 with `RRF_K=60`). This requires a record to rank in the top-50 of at least one signal — a conservative gate that filters obvious noise while preserving legitimate single-strong-signal hits.
- **Documentation update**: `docs/features/subconscious-memory.md` gets a new "Relevance Threshold" subsection describing the gate, the default, and the opt-out path.

### Flow

CLI lookup (back-compat, no gate):
`python -m tools.memory_search search "foo"` → `search("foo")` → `retrieve_memories("foo", min_rrf_score=None)` → returns top-N regardless of relevance (today's behavior).

CLI lookup (with gate):
`python -m tools.memory_search search "foo" --min-score 0.01` → `search("foo", min_rrf_score=0.01)` → `retrieve_memories(..., min_rrf_score=0.01)` → drops fused results below 0.01.

Recall in PostToolUse hook (gate ON):
Watchdog → `check_and_inject()` → `retrieve_memories(..., min_rrf_score=RRF_MIN_SCORE)` → fused list filtered → category re-rank → `<thought>` blocks (or empty if all filtered).

Recall in Claude Code hooks (gate ON):
`post_tool_use.py` → `memory_bridge.recall()` → `_recall_with_query(..., min_rrf_score=RRF_MIN_SCORE)` → same path.
`user_prompt_submit.py` → `memory_bridge.prefetch()` → `_recall_with_query(..., min_rrf_score=RRF_MIN_SCORE)` → same path.

### Technical Approach

- **Add `min_rrf_score` parameter to `retrieve_memories()`** with default `None`. When `None`, behave exactly as today (back-compat). When numeric, filter fused tuples after `rrf_fuse` returns and before hydration. Filtering before hydration saves Redis `query.get` calls on records that would be dropped anyway.
- **Tighten bloom check from `>= 1` to `>= BLOOM_MIN_HITS` (=2)** in FOUR places (revision-pass correction — original plan listed only two):
  1. `tools/memory_search/__init__.py:89-108` — replace the `break`-on-first-hit loop with a counter; gate on `bloom_hits >= BLOOM_MIN_HITS`. No deja-vu branch in this path.
  2. `.claude/hooks/hook_utils/memory_bridge.py:317-346` (`_recall_with_query`) — already has a `bloom_hits` counter. Split the gate into two branches: keep `if bloom_hits == 0:` (deja-vu fallback path, preserved as-is); add `elif bloom_hits < BLOOM_MIN_HITS:` returning `[]` (no deja-vu emission). This preserves today's "novel territory" semantics while filtering single-hit noise.
  3. `.claude/hooks/hook_utils/memory_bridge.py:449-467` (`recall()`'s pre-cluster bloom gate) — same pattern as (2). Keep `if bloom_hits == 0:` deja-vu fallback unchanged; add `elif bloom_hits < BLOOM_MIN_HITS:` returning `None` (recall returns Optional[str]) and saving sidecar state.
  4. `agent/memory_hook.py:168-188` (`check_and_inject()`'s pre-cluster bloom gate) — same pattern as (3). Keep `if bloom_hits == 0:` deja-vu fallback unchanged; add `elif bloom_hits < BLOOM_MIN_HITS:` returning `None`.
- **Add config constants** to `config/memory_defaults.py`:
  - `RRF_MIN_SCORE: float | None = 1 / (RRF_K + 50)` (set to a numeric default, not None — recall paths will pass it explicitly; CLI defaults to None).
  - `BLOOM_MIN_HITS: int = 2`.
  - Document both with comments explaining tuning intent.
- **Plumb the threshold through three call sites**:
  1. `agent/memory_hook.py:202` — `retrieve_memories(..., min_rrf_score=RRF_MIN_SCORE)`.
  2. `.claude/hooks/hook_utils/memory_bridge.py:352` — `_recall_with_query` accepts `min_rrf_score: float | None = None` and threads to `retrieve_memories`. Both `recall()` (line 489) and `prefetch()` (line 608) pass the configured `RRF_MIN_SCORE`.
  3. `tools/memory_search/__init__.py:113` — `search()` accepts `min_rrf_score: float | None = None`. CLI `__main__.py` (or wherever argparse lives) adds `--min-score FLOAT` and passes it through.
- **Test the nonsense-query case** by adding a unit test in `tests/unit/test_memory_retrieval.py` that:
  - Saves a Memory record with `content="abc def"`.
  - Calls `retrieve_memories("xyz_unrelated_qqqq", min_rrf_score=RRF_MIN_SCORE)` and asserts an empty list.
  - Calls the same query with `min_rrf_score=None` and asserts a non-empty list (regression guard for back-compat).
- **Test the bloom tightening** with a unit test that:
  - Patches `bloom_field.might_exist` to return True for exactly one token and False for the rest.
  - Asserts `tools.memory_search.search()` returns empty (not enough bloom hits).
  - Patches it to return True for two tokens, asserts `search()` proceeds to retrieval.
- **Update docs** at `docs/features/subconscious-memory.md` with a "Relevance Threshold" subsection and a config-table row for `RRF_MIN_SCORE` + `BLOOM_MIN_HITS`.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `agent/memory_retrieval.py::retrieve_memories` wraps everything in `try/except Exception`. The new threshold filter must not introduce a code path that raises before the existing `except` clause. Add a unit test asserting the function returns `[]` when `min_rrf_score` is malformed (e.g., a string instead of float).
- [ ] `tools/memory_search/__init__.py::search` wraps in `try/except`. Bloom-tightening change must respect this — assert `search()` returns the empty-results dict when `bloom_field.might_exist` raises.
- [ ] `_recall_with_query` (hook bridge) wraps in `try/except`. Threshold filter must not introduce new uncaught raises. Test with mocked `retrieve_memories` raising — assert empty list returned.

### Empty/Invalid Input Handling

- [ ] Empty query string: existing test `test_empty_query_returns_empty` covers this. Confirm threshold path doesn't change behavior when query is empty (still returns empty).
- [ ] `min_rrf_score = 0`: should behave as "always pass" — equivalent to `None`. Add explicit test.
- [ ] `min_rrf_score = float('inf')`: should always return empty. Add test.
- [ ] No exception handlers in scope are silently swallowed — all paths log at WARNING level.

### Error State Rendering

- [ ] CLI `python -m tools.memory_search search "..."` with `--min-score` and zero results: must print `No memories matched 'query'` (not crash, not return error). Verify by integration test.
- [ ] PostToolUse hook with all results filtered: returns `None` (no `<thought>` injection). Verify the existing failure path test (if any) still passes; add one if missing.

## Test Impact

- [ ] `tests/unit/test_memory_retrieval.py::TestRetrieveMemories::test_returns_empty_on_exception` — UPDATE: add a parameterized variant that passes `min_rrf_score` to confirm the gate path also handles exceptions cleanly.
- [ ] `tests/unit/test_memory_retrieval.py::TestRetrieveMemories::test_empty_query_returns_empty` — UPDATE: add an assertion that an empty query with `min_rrf_score` set still returns `[]` (not `None`, not crash).
- [ ] `tests/unit/test_memory_retrieval.py::TestRetrieveMemories` — ADD `TestRelevanceThreshold` class with at least:
  - `test_nonsense_query_returns_empty_with_threshold` (the acceptance-criteria headliner)
  - `test_nonsense_query_returns_results_without_threshold` (back-compat regression guard)
  - `test_threshold_zero_equivalent_to_none`
  - `test_threshold_inf_returns_empty`
  - `test_high_relevance_record_survives_threshold` (positive case)
- [ ] `tests/unit/test_memory_retrieval.py::TestRrfFuse` — ADD a test for the in-place threshold filter if implemented at the `rrf_fuse` layer (TBD by build). Otherwise skip.
- [ ] `tests/unit/test_memory_hook.py` — UPDATE: any existing test that mocks `retrieve_memories` and asserts the call args — extend to verify `min_rrf_score=RRF_MIN_SCORE` is passed.
- [ ] `tests/unit/test_memory_bridge.py` — UPDATE: any test mocking `_recall_with_query` or `retrieve_memories` — extend to assert the threshold is passed through from `recall()` AND `prefetch()`.
- [ ] `tests/unit/test_memory_search.py` (if it exists; if not, create) — ADD `test_search_min_score_default_none_for_back_compat` and `test_search_min_score_flag_filters_results`.
- [ ] `tests/unit/test_memory_bridge.py::TestBloomCheck` (or equivalent) — UPDATE: any test asserting "any single bloom hit passes" — change to "two hits pass, one hit does not." Verify BOTH `_recall_with_query` and `recall()` (the pre-cluster gate) honor `BLOOM_MIN_HITS`.
- [ ] `tests/unit/test_memory_bridge.py::TestRecallBloomGate` — ADD: cover `recall()`'s pre-cluster bloom gate with three states: (a) `bloom_hits == 0` returns the deja-vu thought when `len(unique_keywords) >= NOVEL_TERRITORY_KEYWORD_THRESHOLD` (regression guard), (b) `bloom_hits == 1` returns `None` and emits NO deja-vu (new gate), (c) `bloom_hits >= 2` proceeds to `_recall_with_query`.
- [ ] `tests/unit/test_memory_hook.py::TestCheckAndInjectBloomGate` — ADD: cover `check_and_inject()`'s pre-cluster bloom gate with the same three states as above. Confirm the deja-vu fallback at `bloom_hits == 0` still fires unchanged.

## Rabbit Holes

- **Implementing autocut score-gap detection**: tempting because it adapts to corpus shape, but harder to calibrate and reason about. Defer to a v2 if the fixed threshold proves too rigid.
- **Re-architecting the four-signal RRF design itself**: explicitly out of scope per the issue. Don't touch the signals or fusion math beyond the threshold.
- **Bulk-pruning the existing junk corpus**: that's issue 1212. Stay focused — threshold filtering and corpus cleanup are complementary; this plan only does the former.
- **Adding per-signal min-score gates** (e.g., "BM25 must score above X, embedding must score above Y"): more configuration surface, harder to tune, easy to get wrong. The fused-score gate is sufficient for the issue's acceptance criteria.
- **Auto-tuning the threshold based on corpus size**: tempting because `1/(k+N/2)` scales with N, but reading the corpus size on every recall adds Redis round-trips. The fixed `1/(k+50)` floor is good enough for the corpus sizes this system sees (50-1000 records).
- **Calibrating against a held-out evaluation set**: would be ideal but requires hand-labeling relevance, which is a separate effort. Use the spike-1 math + the live nonsense-query repro as the empirical anchor.

## Risks

### Risk 1: Threshold too aggressive — drops legitimate hits

**Impact:** Useful memories silently never surface. Agent loses helpful context.
**Mitigation:**
- Spike-1 calibrated the threshold to require only "top-50 of one signal" — extremely permissive. A legitimate hit would need to fail across all four signals to be dropped.
- The fixed value lives in `config/memory_defaults.py` and can be lowered (or set to `None`) via a single-line change.
- After deployment, monitor `memory.recall_attempt` analytics with `hits=0` rate. If it spikes above ~20% of recall attempts, the threshold needs lowering.
- The `--min-score` CLI flag plus default-OFF in `tools.memory_search` lets debugging sessions verify what the gate is dropping.

### Risk 2: Threshold too lenient — issue not actually fixed

**Impact:** Nonsense queries still return junk; acceptance criteria not met.
**Mitigation:**
- The acceptance test (`test_nonsense_query_returns_empty_with_threshold`) is a HARD assertion. If the threshold is too lenient, that test fails and the PR can't merge.
- Spike-1 chose a value that makes the test pass for a 173-record corpus. Validated empirically before merge.

### Risk 3: Bloom tightening regresses single-keyword queries

**Impact:** CLI users who search for one word get empty results unexpectedly.
**Mitigation:**
- Spike-2 found the SDK and hook paths use multi-keyword cluster queries; only the CLI is at risk.
- The change applies to both `tools/memory_search/__init__.py` and `_recall_with_query`. The CLI defaults the threshold OFF, but the bloom tightening is independent of the threshold.
- Add a single-keyword regression test: assert `search("redis")` returns results when at least one Memory contains "redis" (same as today). The bloom tightening kicks in only when the query has ≥ 2 distinct tokens.
- Document in the CLI help text: single-keyword searches are imprecise; prefer multi-word queries.

### Risk 4: Cross-process inconsistency between CLI and hook callers

**Impact:** A user runs `python -m tools.memory_search search "foo"` and gets 5 results, but the agent's PostToolUse hook returns 0 for the same query — confusing.
**Mitigation:**
- This is intentional and documented: the CLI is for debugging/inspection (default-OFF gate); the hook is for prompt injection (default-ON gate). Doc clearly states this divergence.
- The CLI's `--min-score` flag lets users replicate the hook's behavior on demand.

## Race Conditions

No race conditions identified — all operations are synchronous reads from Redis. The threshold filter operates on an in-memory list returned by `rrf_fuse`; no shared state is mutated. Bloom-filter reads are O(1) Redis ops with no side effects.

## No-Gos (Out of Scope)

- Changing the four-signal RRF design itself (separate effort, would need its own plan).
- Pruning the existing junk corpus — that's issue 1212 and is independent.
- Adding per-signal score thresholds (BM25-only floor, embedding-only floor, etc.) — the fused gate is sufficient.
- Implementing autocut / dynamic gap detection — deferred to a v2 if the fixed threshold proves rigid.
- Auto-tuning the threshold based on corpus size at runtime — fixed value is sufficient for current scale.
- Adding a `min_rrf_score` field to the Memory model — this is a query-time parameter, not stored data.
- Changing recall behavior in `tools.memory_search.timeline()` (the time-range query path) — that path doesn't use RRF at all; it pulls by sorted-set score range.

## Update System

No update system changes required. This is a purely internal change:
- No new dependencies.
- No new config files (constants live in the existing `config/memory_defaults.py`).
- No migration steps for existing installations — the threshold is opt-in by default for the CLI and applied automatically once the new code lands for the recall hooks.
- No bridge restart needed beyond the standard `valor-service.sh restart` after deploying agent code.

## Agent Integration

No agent integration required — this is a bridge/SDK-internal change. The existing recall pathways (`agent/memory_hook.py`, `.claude/hooks/hook_utils/memory_bridge.py`) are already wired into the agent runtime; this plan modifies their behavior, not their integration surface.

The CLI (`python -m tools.memory_search search ...`) gains a `--min-score` flag, but that CLI is already an established entry point — no changes needed to `pyproject.toml [project.scripts]` or `.mcp.json`.

Integration test: a real Memory + real Redis test in `tests/integration/test_memory_recall_threshold.py` (NEW) saves a record, queries with a nonsense string + threshold ON, asserts empty result. This validates the SDK path end-to-end.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/subconscious-memory.md`:
  - Add a new "Relevance Threshold" subsection under "Architecture" or near "Category-Weighted Recall" describing the post-fusion gate and bloom tightening.
  - Add `RRF_MIN_SCORE` and `BLOOM_MIN_HITS` rows to the Configuration table.
  - Document the default-OFF/default-ON divergence between CLI and recall paths.
- [ ] No update to `docs/features/README.md` index needed (the feature is already listed; this is an enhancement).

### External Documentation Site

This repo does not use Sphinx / Read the Docs / MkDocs. No external docs site to update.

### Inline Documentation

- [ ] Docstring on `retrieve_memories` updated to document the new `min_rrf_score` parameter, including the default-None back-compat behavior.
- [ ] Docstring on `_recall_with_query` updated similarly.
- [ ] Comment block in `config/memory_defaults.py` explaining the calibration math for `RRF_MIN_SCORE`.

## Success Criteria

- [ ] Recall returns 0 results for queries with no semantic or keyword overlap (verified by `tests/integration/test_memory_recall_threshold.py` against a real Memory store).
- [ ] `retrieve_memories` exposes `min_rrf_score: float | None = None` parameter; passing `None` preserves today's behavior exactly.
- [ ] Bloom pre-check requires `BLOOM_MIN_HITS = 2` at all FOUR sites (`tools/memory_search/__init__.py`, `_recall_with_query`, `recall()`, `check_and_inject()`); passing the gate with one hit fails the new tests at each site.
- [ ] Deja-vu / novel-territory branch (`bloom_hits == 0` AND `len(unique_keywords) >= NOVEL_TERRITORY_KEYWORD_THRESHOLD`) still emits the deja-vu thought at all three sites that have it (`_recall_with_query`, `recall()`, `check_and_inject()`); regression test asserts this for each.
- [ ] PostToolUse hook (`agent/memory_hook.py::check_and_inject`) calls `retrieve_memories` with `min_rrf_score=RRF_MIN_SCORE` (verified by mock-call-args test).
- [ ] Claude Code hook bridge `_recall_with_query` threads `min_rrf_score` through; both `recall()` and `prefetch()` pass `RRF_MIN_SCORE` (verified by mock-call-args tests on both call sites).
- [ ] CLI search keeps `min_rrf_score=None` default; `--min-score FLOAT` flag exposes opt-in (verified by CLI-level test).
- [ ] Unit test `test_nonsense_query_returns_empty_with_threshold` passes against a real Memory store (acceptance-criteria headliner).
- [ ] `docs/features/subconscious-memory.md` documents the relevance threshold behavior (visible in the diff of the PR).
- [ ] All existing memory tests still pass (`pytest tests/unit/test_memory_retrieval.py tests/unit/test_memory_hook.py tests/unit/test_memory_bridge.py -q`).
- [ ] Lint clean (`python -m ruff check .`).
- [ ] Format clean (`python -m ruff format --check .`).
- [ ] Live verification: `python -m tools.memory_search search "PHRASE_THAT_DEFINITELY_DOES_NOT_APPEAR_ANYWHERE_QQQQ" --min-score 0.009` returns 0 results.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (threshold-gate)**
  - Name: `threshold-builder`
  - Role: Implement the post-fusion threshold filter in `retrieve_memories`, add config constants, thread the parameter through all three call sites, update the CLI argparse layer.
  - Agent Type: builder
  - Resume: true

- **Builder (bloom-tightening)**
  - Name: `bloom-builder`
  - Role: Tighten the bloom pre-check in `tools/memory_search/__init__.py` and `_recall_with_query` from `>= 1 hit` to `>= BLOOM_MIN_HITS`. Preserve the deja-vu / novel-territory semantics that depend on `bloom_hits == 0`.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: `recall-test-engineer`
  - Role: Add the nonsense-query acceptance test (real Memory store), the bloom-tightening regression tests, the back-compat tests, and the call-args verification tests for both recall paths.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `recall-documentarian`
  - Role: Update `docs/features/subconscious-memory.md` with the Relevance Threshold subsection and config table rows.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `threshold-validator`
  - Role: Run the full memory-test suite, verify the acceptance test passes, run the live nonsense-query verification command, confirm lint/format clean.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

(Standard agent types; see template for full list.)

## Step by Step Tasks

### 1. Add config constants and threshold filter

- **Task ID**: build-threshold-core
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_retrieval.py` (must keep passing); new `TestRelevanceThreshold` class added.
- **Informed By**: spike-1 (calibrated `RRF_MIN_SCORE = 1/(RRF_K + 50)`), spike-3 (default-None signature for back-compat).
- **Assigned To**: threshold-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `RRF_MIN_SCORE = 1 / (RRF_K + 50)` and `BLOOM_MIN_HITS = 2` to `config/memory_defaults.py` with comments explaining the calibration math.
- Add `min_rrf_score: float | None = None` parameter to `agent.memory_retrieval.retrieve_memories`. When numeric, filter fused tuples after `rrf_fuse` returns and before hydration. Update docstring.
- Update `agent.memory_hook.check_and_inject` to pass `min_rrf_score=RRF_MIN_SCORE` to `retrieve_memories`.
- Update `_recall_with_query` in `.claude/hooks/hook_utils/memory_bridge.py` to accept `min_rrf_score: float | None = None` and thread it to `retrieve_memories`. Both `recall()` and `prefetch()` pass `RRF_MIN_SCORE`.

### 2. Tighten bloom pre-check (FOUR sites)

- **Task ID**: build-bloom-tighten
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_bridge.py`, `tests/unit/test_memory_hook.py`, and `tests/unit/test_memory_search.py` (create if missing). Bloom regression tests in all three.
- **Informed By**: spike-2 (single-keyword regression risk is low; multi-cluster paths build queries from 5+ keywords). Revision-pass code re-read identified two ADDITIONAL bloom-gate sites missed in the original plan.
- **Assigned To**: bloom-builder
- **Agent Type**: builder
- **Parallel**: true
- **Site 1**: Replace the `break`-on-first-hit loop in `tools/memory_search/__init__.py:89-108` with a counter; gate on `bloom_hits >= BLOOM_MIN_HITS`. No deja-vu branch in this path.
- **Site 2**: In `_recall_with_query` (`memory_bridge.py:317-346`), keep `if bloom_hits == 0:` deja-vu branch unchanged. Insert `elif bloom_hits < BLOOM_MIN_HITS:` that returns `[]` (no deja-vu emission). The new branch kicks in for `1 <= bloom_hits < BLOOM_MIN_HITS`.
- **Site 3**: In `recall()` (`memory_bridge.py:449-467`), keep `if bloom_hits == 0:` deja-vu branch unchanged. Insert `elif bloom_hits < BLOOM_MIN_HITS:` that saves sidecar state and returns `None` (no `<thought>` injection). PRESERVE the existing `bloom_hits == 0` branch including the `len(unique_keywords) >= NOVEL_TERRITORY_KEYWORD_THRESHOLD` deja-vu emission.
- **Site 4**: In `check_and_inject()` (`agent/memory_hook.py:168-188`), keep `if bloom_hits == 0:` deja-vu branch unchanged. Insert `elif bloom_hits < BLOOM_MIN_HITS:` that returns `None`. PRESERVE the existing `bloom_hits == 0` branch and the deja-vu emission.

### 3. Expose CLI `--min-score` flag

- **Task ID**: build-cli-flag
- **Depends On**: build-threshold-core
- **Validates**: new `test_search_cli_min_score_flag` in `tests/unit/test_memory_search.py`.
- **Assigned To**: threshold-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `--min-score FLOAT` to the `tools.memory_search` argparse layer (likely in `tools/memory_search/__main__.py`). Pass to `search()`. Default `None` so existing CLI invocations are unaffected.
- Update CLI `--help` output to document the flag.

### 4. Write unit tests for threshold and bloom

- **Task ID**: write-tests-threshold-bloom
- **Depends On**: build-threshold-core, build-bloom-tighten, build-cli-flag
- **Validates**: itself.
- **Assigned To**: recall-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestRelevanceThreshold` class to `tests/unit/test_memory_retrieval.py` covering: nonsense query empty-with-threshold, nonsense query non-empty-without-threshold, threshold=0 equivalent to None, threshold=inf returns empty, high-relevance record survives threshold.
- Add `TestBloomTightening` class to `tests/unit/test_memory_search.py` (create if missing) covering: one bloom hit returns empty, two bloom hits proceed to retrieval, zero bloom hits returns empty (existing behavior).
- Add `TestRecallBloomGate` class to `tests/unit/test_memory_bridge.py` covering `recall()`'s pre-cluster bloom gate: `bloom_hits == 0` with sufficient keywords returns deja-vu thought (regression guard); `bloom_hits == 1` returns `None` and emits NO deja-vu (new gate); `bloom_hits >= 2` proceeds.
- Add `TestCheckAndInjectBloomGate` class to `tests/unit/test_memory_hook.py` covering `check_and_inject()`'s pre-cluster bloom gate with the same three states.
- Add call-args verification tests in `tests/unit/test_memory_hook.py` and `tests/unit/test_memory_bridge.py` confirming `min_rrf_score=RRF_MIN_SCORE` is passed in both `check_and_inject` and `_recall_with_query` (latter from BOTH `recall()` and `prefetch()`).

### 5. Write integration acceptance test

- **Task ID**: write-test-acceptance
- **Depends On**: build-threshold-core, build-bloom-tighten, build-cli-flag
- **Validates**: itself; this is the issue's headline acceptance test.
- **Assigned To**: recall-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/test_memory_recall_threshold.py` with a single test that:
  - Saves a Memory record with content "the quick brown fox" against a real Memory store.
  - Calls `retrieve_memories("xyz_unrelated_qqqqqq", min_rrf_score=RRF_MIN_SCORE)` — asserts empty list.
  - Calls the same query with `min_rrf_score=None` — asserts non-empty list (back-compat).
  - Calls `retrieve_memories("quick fox", min_rrf_score=RRF_MIN_SCORE)` — asserts the saved record IS returned (positive case).
  - Cleans up the test record after.

### 6. Validation pass

- **Task ID**: validate-threshold-impl
- **Depends On**: build-threshold-core, build-bloom-tighten, build-cli-flag, write-tests-threshold-bloom, write-test-acceptance
- **Assigned To**: threshold-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -m pytest tests/unit/test_memory_retrieval.py tests/unit/test_memory_hook.py tests/unit/test_memory_bridge.py tests/unit/test_memory_search.py -q` — all green.
- Run `python -m pytest tests/integration/test_memory_recall_threshold.py -q` — green.
- Run `python -m ruff check .` — exit 0.
- Run `python -m ruff format --check .` — exit 0.
- Run the live verification: `python -m tools.memory_search search "PHRASE_THAT_DEFINITELY_DOES_NOT_APPEAR_ANYWHERE_QQQQ" --min-score 0.009` — must return 0 results. Save a screenshot/output snippet for the PR description.

### 7. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-threshold-impl
- **Assigned To**: recall-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md`:
  - Add a "Relevance Threshold" subsection under the architecture/recall area.
  - Add `RRF_MIN_SCORE` and `BLOOM_MIN_HITS` rows to the Configuration table.
  - Document the default-OFF (CLI) vs default-ON (recall hooks) divergence.
- Verify the doc renders cleanly (no broken links, valid markdown).

### 8. Final validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: threshold-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the full memory test suite + lint + format.
- Verify `docs/features/subconscious-memory.md` includes the new section.
- Generate a final report: confirm all success-criteria checkboxes can be ticked.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Memory unit tests pass | `python -m pytest tests/unit/test_memory_retrieval.py tests/unit/test_memory_hook.py tests/unit/test_memory_bridge.py -q` | exit code 0 |
| Memory search tests pass | `python -m pytest tests/unit/test_memory_search.py -q` | exit code 0 |
| Acceptance integration test passes | `python -m pytest tests/integration/test_memory_recall_threshold.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Threshold constant present | `grep -n "RRF_MIN_SCORE" config/memory_defaults.py` | output contains `RRF_MIN_SCORE` |
| Bloom-min-hits constant present | `grep -n "BLOOM_MIN_HITS" config/memory_defaults.py` | output contains `BLOOM_MIN_HITS` |
| Recall path passes threshold | `grep -n "min_rrf_score=RRF_MIN_SCORE" agent/memory_hook.py .claude/hooks/hook_utils/memory_bridge.py` | output > 1 |
| Bloom gate tightened at all four sites | `grep -n "BLOOM_MIN_HITS" tools/memory_search/__init__.py agent/memory_hook.py .claude/hooks/hook_utils/memory_bridge.py` | at least 4 hits across the three files (note: memory_bridge has 2 sites — `_recall_with_query` and `recall()`) |
| Live nonsense query empty | `python -m tools.memory_search search "PHRASE_THAT_DEFINITELY_DOES_NOT_APPEAR_ANYWHERE_QQQQ" --min-score 0.009` | output contains `Found 0 memories` or `No memories matched` |
| Docs section present | `grep -n "Relevance Threshold" docs/features/subconscious-memory.md` | output contains `Relevance Threshold` |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Revision-pass code re-read | Plan listed only 2 bloom-gate sites; there are actually 4. `recall()` (memory_bridge.py:449-467) and `check_and_inject()` (memory_hook.py:168-188) each run their own multi-keyword bloom gate BEFORE calling `_recall_with_query` with `bloom_check=False`. Tightening only `_recall_with_query` would leave both call paths gated at the old threshold. | Solution / Technical Approach / Step 2 / Test Impact | Apply BLOOM_MIN_HITS gating at all four sites. Preserve the `bloom_hits == 0` deja-vu branch unchanged; insert a NEW `elif bloom_hits < BLOOM_MIN_HITS:` branch that returns empty/None without emitting deja-vu. Site-by-site enumeration added to Step 2. |
| CONCERN | Revision-pass code re-read | The Acceptance Criterion "PostToolUse hook calls recall with the threshold enabled" must NOT cause regressions in deja-vu / novel-territory thought emission, which `recall()` and `check_and_inject()` both rely on for the "new territory" signal. | Solution / Step 2 | The `bloom_hits == 0` deja-vu branch is explicitly preserved verbatim at all three sites that have it. The new `< BLOOM_MIN_HITS` gate only catches the `1 <= bloom_hits < 2` middle ground. Test Impact adds a regression guard asserting deja-vu still fires at `bloom_hits == 0`. |

---

## Open Questions

The issue's design-intent question ("always-surface vs. only-relevant") was resolved during plan research by reading PR #604, which explicitly states the design favors precision over recency. **Recommendation: only-relevant**. The plan adopts this; no further input needed.

No remaining open questions. All other planning decisions are anchored either by spike findings (threshold value, bloom tightening risk) or by the issue's explicit guidance (default-OFF for CLI back-compat, default-ON for recall hooks).
