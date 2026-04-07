# Plan: Comprehensive Test Suite for Agent Memory System

**Issue:** #516
**Slug:** `memory-test-suite-516`
**Status:** draft

## Prior Art: PR #606 Post-Mortem

PR #606 implemented a first pass at this test suite but was reverted (commit 9591bc61) after a post-merge review found 6 defects. The new implementation must prevent each of these:

1. **MEDIUM: Duplicated helper functions** -- `_unique_key()` and `_cleanup_memories()` were copy-pasted across 3 test files. **Prevention:** Define shared fixtures in `tests/conftest.py` or a dedicated `tests/helpers/memory_helpers.py` module. No helper duplication across test files.
2. **LOW: FakeRecord class defined 3 times** -- Each file defined its own identical `FakeRecord`. **Prevention:** Define `FakeRecord` once in the shared helpers module; import it where needed.
3. **LOW: Weak assertions in injection pipeline tests** -- Tests only verified "no crash" (smoke tests) but docstrings claimed behavioral validation. **Prevention:** Every test must have assertions that verify behavioral outcomes (return values, state changes, data content), not just absence of exceptions. Docstrings must accurately reflect assertion strength.
4. **LOW: test_safe_save_returns_none_on_bad_kwargs tested wrong thing** -- Conflated write-filter testing with error-path testing. **Prevention:** Separate write-filter enforcement tests (below-threshold importance is silently dropped) from error-path tests (bad kwargs cause safe failure).
5. **INFO: Hardcoded threshold 0.15** -- Should reference `Memory._wf_min_threshold`. **Prevention:** Import the constant from the model; never hardcode threshold values in tests.
6. **INFO: AI judge model hardcoded as "gemma3:4b"** -- Inconsistent with existing `judge.py` convention (`JudgeConfig` default is `"gemma2:3b"`). **Prevention:** Use `JudgeConfig` defaults from `tests/ai_judge/judge.py`; do not hardcode model names in test files.

## Problem

The memory system has grown significantly -- Memory model, memory hook (thought injection), memory extraction (outcome detection), memory ingestion (Telegram), memory bridge (Claude Code hooks), memory search tool, and KnowledgeDocument model. Unit test coverage exists for individual modules (totaling ~156 tests across 7 files), but there are critical gaps:

1. **No integration tests for memory lifecycle.** The save-search-recall-forget cycle is only tested in `tools/memory_search/tests/test_memory_search.py`, not in the main test suite.
2. **No tests for the full injection pipeline.** `check_and_inject()` interacts with bloom filters, ContextAssembler, and category re-ranking -- none of this is tested end-to-end against real Redis.
3. **No AI judge tests for memory usefulness.** The issue explicitly calls for evaluation of whether surfaced memories are *useful*, not just non-empty.
4. **No tests for outcome detection with real memories.** `detect_outcomes_async()` and `_persist_outcome_metadata()` are tested with mocks but never against persisted Memory records.
5. **No tests for knowledge document companion memories.** The `reference` field and `SOURCE_KNOWLEDGE` source type have only serialization tests, not lifecycle tests.

## Solution

Add three layers of new tests:

### Layer 1: Integration tests (real Redis, no API calls)

New file `tests/integration/test_memory_lifecycle.py` covering:

- [ ] **Save-search-recall cycle**: Save memories with varying importance and source types, search via ContextAssembler, verify correct records surface
- [ ] **Bloom filter integration**: Save content, verify `might_exist()` returns True for fingerprinted terms, False for unrelated terms
- [ ] **Decay behavior**: Save memories at different importance levels, verify DecayingSortedField ordering reflects importance-weighted decay
- [ ] **Write filter enforcement**: Verify memories below `Memory._wf_min_threshold` are silently dropped, those above persist (import constant from model, do not hardcode threshold value)
- [ ] **Confidence updates via ObservationProtocol**: Save memory, simulate acted/dismissed outcomes, verify confidence changes
- [ ] **Dismissal tracking**: Save memory, simulate consecutive dismissals, verify importance decays after threshold (3 dismissals)
- [ ] **Category re-ranking**: Save memories with different categories (correction, pattern), verify `_apply_category_weights()` re-orders results correctly
- [ ] **Knowledge companion memories**: Save memory with `source="knowledge"` and JSON `reference` field, verify it persists and is searchable
- [ ] **Project isolation**: Save memories under different `project_key` values, verify searches are partitioned correctly

### Layer 2: Hook pipeline integration tests (real Redis, mocked Anthropic)

New file `tests/integration/test_memory_injection_pipeline.py` covering:

- [ ] **Full check_and_inject flow**: Populate Redis with test memories, call `check_and_inject()` with tool calls that should trigger bloom hits, verify `<thought>` blocks are returned
- [ ] **Sliding window rate limiting**: Verify injection only fires every `WINDOW_SIZE` tool calls, not on every call
- [ ] **Novel territory detection**: Call with keywords that have zero bloom hits but exceed `NOVEL_TERRITORY_KEYWORD_THRESHOLD`, verify "new territory" thought
- [ ] **Deja vu detection**: Trigger bloom hits but no ContextAssembler results, verify "vague recognition" thought when hits >= `DEJA_VU_BLOOM_HIT_THRESHOLD`
- [ ] **Multi-query decomposition**: Provide >5 keywords, verify `_cluster_keywords()` decomposes and queries each cluster
- [ ] **Session cleanup**: Verify `clear_session()` removes all session-scoped state

### Layer 3: AI judge tests (requires Ollama with gemma2:3b)

New file `tests/ai_judge/test_memory_usefulness.py` covering:

- [ ] **Retrieval relevance**: Save 10 diverse memories, query with a specific topic, use AI judge to score whether returned memories are topically relevant (threshold: 70% relevant)
- [ ] **Extraction quality**: Feed a real-ish agent response to `_parse_categorized_observations()`, use AI judge to evaluate whether extracted observations are specific and novel (not generic platitudes)
- [ ] **Thought injection quality**: Generate `<thought>` blocks from real memories, use AI judge to evaluate whether they provide actionable context (not noise)

All AI judge tests must:
- Use `JudgeConfig` from `tests/ai_judge/judge.py` with default model (do not hardcode model names)
- Use Ollama as primary inference backend, OpenRouter as fallback (matching existing `judge.py` convention)
- Skip cleanly when Ollama is not available (not when `ANTHROPIC_API_KEY` is missing)
- Include behavioral assertions beyond pass/fail -- verify the judge's reasoning references expected criteria

### Shared test helpers

New file `tests/helpers/memory_helpers.py` containing:

- [ ] `unique_project_key()` -- returns a UUID-prefixed project key for Redis isolation
- [ ] `cleanup_memories(project_key)` -- deletes all Memory records for a given project key
- [ ] `FakeRecord` class -- a minimal mock record for pipeline tests that need a record-like object

These shared helpers prevent the duplication that caused defects in PR #606. All three test files import from this single source.

### Build prerequisite: FEATURE_MAP update

Before creating test files, update `tests/conftest.py` FEATURE_MAP to add memory-specific patterns. These entries MUST come before the generic "lifecycle" and "pipeline" entries because matching is first-match-wins:

- [ ] Add `"memory_lifecycle": "models"` -- prevents `test_memory_lifecycle.py` from matching the generic `"lifecycle": "sessions"` pattern
- [ ] Add `"memory_injection": "models"` -- prevents `test_memory_injection_pipeline.py` from matching the generic `"pipeline": "sdlc"` pattern
- [ ] Add `"memory_usefulness": "models"` -- tags the AI judge memory tests correctly
- [ ] Verify placement: these three entries must appear in the dict BEFORE the `"pipeline": "sdlc"` and `"lifecycle": "sessions"` entries

### Test isolation strategy

- All integration tests use a unique `project_key` per test (UUID-prefixed) for Redis isolation, via the shared `unique_project_key()` helper
- All created memories are cleaned up in fixture teardown via the shared `cleanup_memories()` helper
- AI judge tests are marked `@pytest.mark.slow` and skipped when Ollama is not available (consistent with existing `tests/ai_judge/` convention -- tests use local Ollama, not Anthropic API)
- AI judge tests use `JudgeConfig` defaults from `tests/ai_judge/judge.py` (model `"gemma2:3b"`) -- no hardcoded model names in test files

## Scope

### In scope
- Integration tests for Memory model lifecycle (Layer 1)
- Integration tests for injection pipeline (Layer 2)
- AI judge tests for usefulness evaluation (Layer 3)
- Shared test helpers module (`tests/helpers/memory_helpers.py`)
- FEATURE_MAP updates in `tests/conftest.py` for memory test auto-tagging
- Test fixtures for memory test isolation

### Out of scope
- Refactoring existing unit tests (they are solid and cover pure logic well)
- Testing the bridge-side memory ingestion path (bridge tests are a separate concern)
- Performance benchmarks for memory operations (covered by `tests/performance/test_benchmarks.py`)
- Testing KnowledgeDocument indexer/watcher (separate from memory system proper)

## No-Gos

- No mocking Redis -- integration tests use real Redis per project testing philosophy
- No mocking popoto internals (ContextAssembler, ObservationProtocol) -- test the real thing
- No testing Anthropic API directly in integration tests -- only in AI judge layer via Ollama (local) with OpenRouter fallback
- No modifying existing unit test files -- only adding new files

## Update System

No update system changes required. This adds test files only -- no new dependencies, no config changes, no migration steps.

## Agent Integration

No agent integration required. These are test files that validate existing agent functionality. No MCP server changes, no `.mcp.json` changes, no bridge changes.

## Failure Path Test Strategy

- [ ] Verify `Memory.safe_save()` returns None (not raises) when given intentionally invalid kwargs (error-path test -- separate from write-filter test)
- [ ] Verify write-filter enforcement: memories with importance below `Memory._wf_min_threshold` are silently dropped by `safe_save()` (import constant, do not hardcode `0.15`)
- [ ] Verify `check_and_inject()` returns None (not raises) when bloom filter query fails
- [ ] Verify `detect_outcomes_async()` returns empty dict when memory lookup fails
- [ ] Verify `_persist_outcome_metadata()` skips individual records on error without aborting the batch

## Test Impact

- [ ] `tests/conftest.py::FEATURE_MAP` -- UPDATE: Add memory-specific patterns before the generic "lifecycle" and "pipeline" entries to prevent misclassification (see build task below)

New test files added (no existing tests modified):
- `tests/helpers/memory_helpers.py` (new shared helpers module)
- `tests/integration/test_memory_lifecycle.py` (new)
- `tests/integration/test_memory_injection_pipeline.py` (new)
- `tests/ai_judge/test_memory_usefulness.py` (new)

Existing memory unit tests (`tests/unit/test_memory_*.py`) are not modified.

## Rabbit Holes

- **Embedding-based search testing**: KnowledgeDocument uses EmbeddingField which requires an API call to generate embeddings. Do not try to test embedding search in the integration layer -- that belongs in the AI judge layer or a separate knowledge-search test suite.
- **Time-dependent decay testing**: DecayingSortedField uses wall-clock time. Do not try to mock time or sleep to test decay progression -- test relative ordering instead.
- **Bloom filter false positives**: ExistenceFilter has a 1% error rate. Do not write tests that assume zero false positives -- test for true positives only.

## Success Criteria

- [ ] `pytest tests/integration/test_memory_lifecycle.py -v` passes with 0 failures
- [ ] `pytest tests/integration/test_memory_injection_pipeline.py -v` passes with 0 failures
- [ ] `pytest tests/ai_judge/test_memory_usefulness.py -v` passes (or skips cleanly without Ollama)
- [ ] `pytest -m models --collect-only -q` includes all new memory tests auto-tagged via FEATURE_MAP
- [ ] No existing tests broken: `pytest tests/unit/test_memory_*.py -v` still green
- [ ] No existing tests misclassified: `test_memory_lifecycle.py` maps to `models` marker (not `sessions`), `test_memory_injection_pipeline.py` maps to `models` marker (not `sdlc`)

## Documentation

- [ ] Add memory test entries to `tests/README.md` under the `models` feature section
- [ ] Update `tests/README.md` Known Blind Spots to remove memory-related gaps if applicable
