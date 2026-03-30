---
status: Planning
type: enhancement
appetite: Medium
owner: Valor
created: 2026-03-30
tracking: https://github.com/tomcounsell/ai/issues/598
---

# Add BM25Field to Memory Model and Upgrade Retrieval with RRF Fusion

## Problem

Memory retrieval has no content match quality signal. The ExistenceFilter provides a binary maybe/no gate, and records that pass are ranked by ContextAssembler using `score_weights={"relevance": 0.6, "confidence": 0.3}`. A recently accessed but tangentially related memory (e.g., "redis pub/sub latency") can outrank an older but precisely relevant one (e.g., "redis connection pooling timeout fix") because neither the time-decay relevance nor the Bayesian confidence axis reflects keyword match quality.

**Current behavior:**
- Bloom filter gives a binary pass/fail per keyword
- ContextAssembler scores records by weighted sum of relevance (decay) and confidence (Bayesian)
- No signal measures how well a memory's text actually matches the query keywords

**Desired outcome:**
- Retrieval combines three independent signals via Reciprocal Rank Fusion: BM25 keyword match quality, temporal relevance (DecayingSortedField), and historical confidence (ConfidenceField)
- A memory whose content closely matches the query keywords ranks higher even if it is older or less frequently accessed

## Prior Art

- **#583 / PR #584**: Enhanced memory retrieval -- added structured metadata (DictField), dismissal tracking, and multi-query decomposition via keyword clustering
- **#586**: Memory agent integration -- scoped metadata-aware recall and retrieval recipes. Item 5 was a placeholder for "Popoto updates." Closed.
- **#519 / PR #522**: Claude Code memory integration -- full ingest/recall/extract/feedback loop for CLI sessions
- **popoto PR #306**: BM25Field + RRF fusion -- the upstream change that enables this work. Merged 2026-03-30.

## Data Flow

### Current Retrieval Path (both agent hook and memory bridge)

```
keywords -> bloom pre-check -> ContextAssembler.assemble(
    query_cues={"topic": cluster_text},
    score_weights={"relevance": 0.6, "confidence": 0.3}
) -> _apply_category_weights() -> <thought> blocks
```

### New Retrieval Path

```
keywords -> bloom pre-check -> Memory.keyword_search(cluster_text)
                                       |
                        returns BM25-ranked list
                                       |
                        Memory.relevance.get_ranked() -> time-decay ranked list
                        Memory.confidence.get_ranked() -> confidence ranked list
                                       |
                        fuse(bm25_list, relevance_list, confidence_list, k=RRF_K)
                                       |
                        -> _apply_category_weights() -> <thought> blocks
```

The bloom pre-check remains as the fast-path gate. BM25 replaces the ContextAssembler's internal scoring. The three ranked lists are fused via RRF instead of weighted sum.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

- popoto must be released with PR #306 included (BM25Field + fuse()). Bump `popoto>=X.Y.Z` in `pyproject.toml` once published. Currently pinned `>=1.4.2`.

## Solution

### Key Elements

1. **Bump popoto version** in `pyproject.toml` to include PR #306
2. **Add BM25Field to Memory model** (`models/memory.py`) indexed on `content` -- provides TF-IDF keyword ranking via Redis sorted sets + Lua scripts
3. **Add BM25/RRF config constants** to `config/memory_defaults.py` -- k1, b, RRF k parameters
4. **Replace ContextAssembler in agent/memory_hook.py** with `keyword_search()` + `fuse()` for each keyword cluster
5. **Replace ContextAssembler in .claude/hooks/hook_utils/memory_bridge.py** with the same pattern
6. **Update tools/memory_search.py** if it uses ContextAssembler for search (check at build time)
7. **Keep bloom pre-check** as fast-path optimization before the heavier BM25 query
8. **Keep _apply_category_weights()** as post-fusion re-ranking (deferred: making category a 4th ranked list)

### Technical Approach

**Memory model change** (`models/memory.py`):
```python
from popoto import BM25Field

class Memory(WriteFilterMixin, AccessTrackerMixin, Model):
    # ... existing fields ...
    bm25 = BM25Field(source_field="content")
```

BM25Field is additive -- existing Memory records without BM25 index data will continue to work. The BM25 index builds incrementally as records are saved or re-saved.

**Config constants** (`config/memory_defaults.py`):
```python
# BM25 tuning (popoto defaults: k1=1.2, b=0.75)
BM25_K1 = 1.2  # term frequency saturation
BM25_B = 0.75  # document length normalization

# Reciprocal Rank Fusion
RRF_K = 60  # ranking constant (higher = more uniform blending)
```

**Retrieval replacement** (identical pattern in both `agent/memory_hook.py` and `memory_bridge.py`):

Replace ContextAssembler instantiation and `assembler.assemble()` calls with:
1. `Memory.bm25.keyword_search(cluster_text)` -- returns BM25-ranked record IDs
2. `Memory.relevance.get_ranked(partition=project_key)` -- returns time-decay-ranked record IDs
3. `Memory.confidence.get_ranked()` -- returns confidence-ranked record IDs
4. `fuse(bm25_list, relevance_list, confidence_list, k=RRF_K)` -- returns RRF-fused ranking

The exact API for `keyword_search()`, `get_ranked()`, and `fuse()` will be confirmed from the popoto PR #306 implementation at build time. The pattern above reflects the expected interface from the PR description.

**Multi-query cluster fusion**: For each keyword cluster, run `keyword_search()` independently, then `fuse()` all per-cluster BM25 results together before combining with relevance and confidence lists. This preserves the existing multi-query decomposition strategy from `_cluster_keywords()`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `keyword_search()` failure must fall back to empty list (not crash the agent)
- [ ] `fuse()` failure must fall back to returning whatever records are available unfused
- [ ] Missing BM25 index data on old records must not cause errors during search
- [ ] Redis connection failure during BM25 query must be caught by the existing fail-silent wrapper

### Empty/Invalid Input Handling
- [ ] Empty keyword cluster produces empty BM25 result (not an error)
- [ ] Single-word query works correctly with BM25 (no minimum word count)
- [ ] Very long query (>100 words) is truncated before BM25 search

### Error State Rendering
- Not applicable -- memory injection is invisible to the user. Errors surface in bridge logs via existing `logger.warning()` calls.

## Test Impact

- [ ] `tests/unit/test_memory_hook.py::TestDejaVuSignals::test_vague_recognition_signal` -- UPDATE: currently mocks `popoto.ContextAssembler`, must mock `keyword_search()` + `fuse()` instead
- [ ] `tests/unit/test_memory_hook.py::TestDejaVuSignals::test_no_signal_below_thresholds` -- UPDATE: same ContextAssembler mock replacement
- [ ] `tests/unit/test_memory_hook.py::TestApplyCategoryWeights` -- no change (post-fusion re-ranking is preserved)
- [ ] `tests/unit/test_memory_bridge.py::TestRecall::test_recall_deja_vu_signal` -- UPDATE: mocks ContextAssembler, must mock new retrieval path
- [ ] `tests/unit/test_memory_bridge.py::TestRecallCategoryReranking::test_recall_calls_apply_category_weights` -- UPDATE: mocks ContextAssembler, must mock new retrieval path
- [ ] `tests/unit/test_memory_model.py::TestMemoryModel::test_memory_has_required_fields` -- UPDATE: add `bm25` to expected fields if it registers in `_meta.fields`

## Rabbit Holes

- **Outcome-driven BM25 tuning** (adjusting k1/b based on act/dismiss signals) -- speculative, revisit after observing BM25 score distributions in production
- **Making category weight a 4th ranked list in fuse()** -- tempting but adds complexity for uncertain benefit. Keep as post-fusion re-rank for now.
- **Backfilling BM25 index for all existing records** -- BM25Field builds incrementally on save. A backfill script could accelerate this, but is not required for correctness (unindexed records simply will not appear in BM25 results, but still appear via relevance/confidence lists which are already populated)
- **Custom tokenizer for BM25** -- PR #306 shares the same tokenizer as ExistenceFilter. No customization needed.
- **Removing ContextAssembler entirely from the codebase** -- it may still be used elsewhere. Only remove from the two retrieval paths in scope.

## Risks

### Risk 1: popoto BM25 API differs from expected interface
**Impact:** Build phase requires adaptation of retrieval code
**Mitigation:** Read actual popoto source at build time to confirm exact method signatures. The solution sketch is intentionally flexible on API details.

### Risk 2: BM25 query latency exceeds 15ms budget
**Impact:** Memory injection slows down agent tool calls
**Mitigation:** BM25 uses Redis sorted sets + Lua scripts (in-memory, single-roundtrip). Bloom pre-check filters most queries before BM25 is invoked. Monitor with existing `elapsed_ms` timing. If needed, reduce MAX_THOUGHTS or cluster count.

### Risk 3: Existing records missing BM25 index return empty results
**Impact:** Cold start period where BM25 returns nothing for old memories
**Mitigation:** RRF fusion combines three lists -- even if BM25 list is empty, relevance and confidence lists still produce results. The system degrades gracefully to pre-BM25 behavior for unindexed records.

## Race Conditions

No race conditions identified -- both retrieval paths (agent hook and memory bridge) are single-threaded. The agent hook runs in asyncio event loop, the memory bridge runs in isolated hook processes. BM25 queries are read-only Redis operations.

## No-Gos (Out of Scope)

- Not implementing outcome-driven BM25 tuning
- Not backfilling BM25 index for existing records (incremental build is sufficient)
- Not making category weight a 4th RRF signal (keep as post-fusion re-rank)
- Not modifying the bloom filter behavior (remains as fast-path gate)
- Not changing memory ingestion flow (BM25Field indexes automatically on save)
- Not removing ContextAssembler from other parts of the codebase

## Update System

No update system changes required -- `popoto` version bump propagates via `pip install` during the standard update flow. No new config files or environment variables.

## Agent Integration

No agent integration required -- the BM25 retrieval upgrade is internal to the memory recall pipeline. No changes to `.mcp.json` or `mcp_servers/`. The `tools/memory_search` CLI tool may need updating if it uses ContextAssembler directly, but this is a code change, not an MCP integration change.

## Documentation

- [ ] Update `docs/features/subconscious-memory.md` architecture diagram to show BM25 + RRF fusion replacing ContextAssembler in the retrieval path
- [ ] Update the "Data Flows" section in `docs/features/subconscious-memory.md` to describe the three-signal fusion approach
- [ ] Add inline docstring on `config/memory_defaults.py` BM25/RRF constants explaining tuning guidance

## Success Criteria

- [ ] Memory model declares a `BM25Field` indexed on `content`
- [ ] `tools/memory_search` uses `keyword_search()` + `fuse()` instead of ContextAssembler weight-based scoring
- [ ] `agent/memory_hook.py` retrieval uses `keyword_search()` + `fuse()` for each keyword cluster
- [ ] `.claude/hooks/hook_utils/memory_bridge.py` mirrors the same retrieval upgrade
- [ ] Bloom pre-check remains as fast-path gate before BM25 query
- [ ] `config/memory_defaults.py` has BM25/RRF tuning constants (k1, b, RRF k)
- [ ] All existing memory unit tests pass (updated for new mocks)
- [ ] New tests cover: BM25 retrieval ranking, RRF fusion of three signals, keyword cluster fusion
- [ ] Retrieval latency stays within 15ms budget per injection
- [ ] `popoto` version bumped in `pyproject.toml`

## Team Orchestration

### Team Members

- **Builder (retrieval)**
  - Name: retrieval-builder
  - Role: Implement BM25Field, RRF fusion, update both retrieval paths
  - Agent Type: builder
  - Resume: true

- **Validator (retrieval)**
  - Name: retrieval-validator
  - Role: Verify retrieval works end-to-end, latency within budget, tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Bump popoto Version
- **Task ID**: bump-popoto
- **Depends On**: none
- **Validates**: `pip install popoto>=X.Y.Z` succeeds, `from popoto import BM25Field, fuse` works
- **Assigned To**: retrieval-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `pyproject.toml` to require the popoto version containing PR #306
- Verify BM25Field and fuse() are importable

### 2. Add BM25/RRF Config Constants
- **Task ID**: add-config
- **Depends On**: none
- **Validates**: config/memory_defaults.py contains BM25_K1, BM25_B, RRF_K
- **Assigned To**: retrieval-builder
- **Agent Type**: builder
- **Parallel**: true
- Add BM25_K1, BM25_B, RRF_K constants to `config/memory_defaults.py` with docstrings
- Follow the existing tuning guide pattern in that file

### 3. Add BM25Field to Memory Model
- **Task ID**: add-bm25-field
- **Depends On**: bump-popoto
- **Validates**: `from models.memory import Memory; assert Memory._meta.fields.get("bm25")`
- **Assigned To**: retrieval-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `bm25 = BM25Field(source_field="content")` to the Memory class
- Confirm existing Memory records are unaffected (additive field)
- Update model docstring to document the new field

### 4. Replace ContextAssembler in agent/memory_hook.py
- **Task ID**: upgrade-agent-hook
- **Depends On**: add-bm25-field, add-config
- **Validates**: tests/unit/test_memory_hook.py passes
- **Assigned To**: retrieval-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `from popoto import ContextAssembler` and assembler instantiation
- For each keyword cluster: call `keyword_search()` to get BM25-ranked results
- Get relevance-ranked and confidence-ranked lists for the same partition
- Call `fuse()` to combine the three ranked lists
- Keep bloom pre-check, _apply_category_weights(), and all fail-silent wrappers
- Preserve the 15ms latency monitoring

### 5. Replace ContextAssembler in memory_bridge.py
- **Task ID**: upgrade-bridge-hook
- **Depends On**: add-bm25-field, add-config
- **Validates**: tests/unit/test_memory_bridge.py passes
- **Assigned To**: retrieval-builder
- **Agent Type**: builder
- **Parallel**: false
- Mirror the same retrieval pattern from step 4
- Remove `from popoto import ContextAssembler` and assembler instantiation
- Keep sidecar state management, fail-silent wrappers, and all existing behavior

### 6. Update Existing Tests
- **Task ID**: update-tests
- **Depends On**: upgrade-agent-hook, upgrade-bridge-hook
- **Validates**: `pytest tests/unit/test_memory_hook.py tests/unit/test_memory_bridge.py tests/unit/test_memory_model.py -x -q` passes
- **Assigned To**: retrieval-builder
- **Agent Type**: builder
- **Parallel**: false
- Update mocks in test_memory_hook.py: replace ContextAssembler mocks with keyword_search/fuse mocks
- Update mocks in test_memory_bridge.py: same replacement
- Update test_memory_model.py if BM25Field registers in _meta.fields

### 7. Write New Tests
- **Task ID**: new-tests
- **Depends On**: update-tests
- **Validates**: new test file passes
- **Assigned To**: retrieval-builder
- **Agent Type**: builder
- **Parallel**: false
- Test BM25 retrieval ranking: verify keyword_search returns results ordered by relevance
- Test RRF fusion: verify fuse() combines three ranked lists correctly
- Test keyword cluster fusion: verify multi-cluster results are merged and deduplicated
- Test graceful degradation: BM25 returns empty, fusion still works with relevance+confidence
- Test config constants are used (k1, b, RRF k)

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: new-tests
- **Assigned To**: retrieval-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify no remaining ContextAssembler usage in the two retrieval paths
- Lint and format check
- Verify popoto version is bumped

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_memory_hook.py tests/unit/test_memory_bridge.py tests/unit/test_memory_model.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check models/memory.py config/memory_defaults.py agent/memory_hook.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/memory.py config/memory_defaults.py agent/memory_hook.py` | exit code 0 |
| No ContextAssembler in hooks | `grep -rn 'ContextAssembler' agent/memory_hook.py .claude/hooks/hook_utils/memory_bridge.py` | exit code 1 |
| BM25Field on model | `python -c "from models.memory import Memory; assert Memory._meta.fields.get('bm25')"` | exit code 0 |
| Config constants exist | `python -c "from config.memory_defaults import BM25_K1, BM25_B, RRF_K"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Exact popoto API surface**: The method signatures for `keyword_search()`, `get_ranked()`, and `fuse()` need to be confirmed from the popoto PR #306 source at build time. The plan is intentionally flexible on these details.
2. **tools/memory_search.py**: Need to verify at build time whether this module uses ContextAssembler directly and needs the same upgrade.
