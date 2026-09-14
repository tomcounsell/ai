---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-02
tracking: https://github.com/tomcounsell/ai/issues/627
last_comment_id:
---

# Memory Recall Hook: Import Tax + Noisy Deja Vu

## Problem

The PostToolUse memory recall hook adds 160-470ms latency per tool call while producing zero useful results in build sessions. Profiled against a real 46-minute build session (session `88755716`, 179 tool calls):

| Path | Wall time | Frequency |
|------|-----------|-----------|
| Non-recall (no memory imports triggered) | 160-177ms | 2 of every 3 calls |
| Recall cycle (bloom + BM25 retrieval) | 425-470ms | 1 of every 3 calls |

Two root causes:

1. **Import tax (344ms):** `recall()` in `memory_bridge.py` lazy-imports `extract_topic_keywords` from `agent.memory_hook` (line 208). Python's package loading triggers `agent/__init__.py`, which eagerly imports `agent_session_queue` -> `claude_agent_sdk` (162ms), `mcp.types` (191ms), `telethon` (61ms), `fastmcp` (65ms). The actual retrieval logic takes ~5ms -- the import chain is 70x more expensive.

2. **Noisy keywords -> useless deja vu thoughts:** `extract_topic_keywords()` extracts raw file path segments as keywords. Reading `/Users/valorengels/src/ai/agent/agent_session_queue.py` produces `['users', 'valorengels', 'agent', 'agent_session_queue']`. Common segments like `users`, `valorengels`, `agent` always hit the bloom filter but `retrieve_memories()` returns 0 results. This triggers the deja vu fallback at `memory_bridge.py` line 287: "I have encountered something related to users, valorengels, agent before" -- pure noise, every 3rd tool call.

**Session data:** 30 recall cycles, 0 useful injections, 0 memories in `injected` list. The hook spent ~9 seconds total on import overhead and injected only meaningless deja vu thoughts.

**Desired outcome:**
- Non-recall calls: <20ms (from ~170ms)
- Recall calls: <100ms (from ~450ms)
- Zero deja vu thoughts from path-derived keywords

## Prior Art

- **PR #525:** Wire Claude Code hooks to subconscious memory system -- initial implementation
- **PR #604:** Add BM25+RRF fusion retrieval, replace ContextAssembler -- current retrieval pipeline
- **Issue #613:** Memory trigger training -- outcome tracking and stress testing (closed)

## Data Flow

### Current (slow path)

1. PostToolUse hook fires -> `memory_bridge.recall()` called
2. Every 3rd call: `from agent.memory_hook import extract_topic_keywords` (line 208)
3. Python loads `agent/__init__.py` -> imports 6 submodules -> 344ms spent on imports
4. Keywords extracted from file paths -> raw segments like `users`, `valorengels`, `agent`
5. Bloom check: generic segments hit bloom filter (they exist in many memories)
6. BM25 retrieval: returns 0 results (queries too vague)
7. Deja vu fallback fires: `bloom_hits >= 3` -> injects useless "encountered something related" thought

### Desired (fast path)

1. PostToolUse hook fires -> `memory_bridge.recall()` called
2. Every 3rd call: `from utils.keyword_extraction import extract_topic_keywords` (no agent deps)
3. Python loads `utils/keyword_extraction.py` -> imports only `re` and `config.memory_defaults` -> <5ms
4. Keywords extracted with project-path stopwords filtered -> meaningful terms only
5. Bloom check: only meaningful keywords checked -> fewer false positives
6. Deja vu fallback: only fires when non-stopword keywords hit bloom filter, or removed entirely

## Architectural Impact

- **New module:** `utils/keyword_extraction.py` -- stdlib + config only, no agent/bridge/models deps
- **Interface changes:** None -- same function signatures, new import path
- **Coupling:** Reduces coupling -- hooks no longer depend on the agent package at all for keyword extraction
- **Reversibility:** Fully reversible -- old imports can be restored, functions are identical

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- Review rounds: 1 (code review)

Three focused changes in a well-scoped area. The functions being extracted have zero external dependencies beyond stdlib and config.

## Prerequisites

No prerequisites -- uses only existing dependencies and infrastructure.

## Solution

### Key Elements

- **Extract keyword utilities to `utils/keyword_extraction.py`**: Move `extract_topic_keywords()`, `_cluster_keywords()`, `_NOISE_WORDS`, and `_apply_category_weights()` out of `agent/memory_hook.py` into a new lightweight module. These functions depend only on `re` and `config.memory_defaults`.
- **Add project-path stopwords**: Filter common project-specific path segments (`users`, `valorengels`, `src`, `ai`, `agent`, `bridge`, `models`, `tools`, `config`, `tests`, `hooks`, `claude`, `scripts`, `docs`, `data`, `logs`) and tool-name noise (`grep`, `edit`, `bash`, `read`, `write`, `glob`).
- **Fix deja vu to require non-stopword bloom hits**: Only count bloom hits from keywords that survived stopword filtering. If all bloom hits come from generic terms that were already filtered, suppress deja vu.

### Technical Approach

**Change 1: Break the import chain**

Create `utils/keyword_extraction.py` containing:
- `extract_topic_keywords(tool_name, tool_input) -> list[str]` (from `agent/memory_hook.py` line 42-85)
- `_cluster_keywords(keywords, max_clusters) -> list[list[str]]` (from `agent/memory_hook.py` line 125-155)
- `_NOISE_WORDS` frozenset (from `agent/memory_hook.py` line 89-122)
- `_apply_category_weights(records) -> list` (from `agent/memory_hook.py` line 158-205)

Update import sites:
- `memory_bridge.py` line 208: change `from agent.memory_hook import extract_topic_keywords` to `from utils.keyword_extraction import extract_topic_keywords`
- `memory_bridge.py` line 254: change `from agent.memory_hook import _cluster_keywords` to `from utils.keyword_extraction import _cluster_keywords`
- `memory_bridge.py` line 297: change `from agent.memory_hook import _apply_category_weights` to `from utils.keyword_extraction import _apply_category_weights`
- `agent/memory_hook.py`: replace moved functions with re-exports from `utils.keyword_extraction` so agent-side callers (`agent/memory_extraction.py` line 663, `agent/health_check.py` line 457) continue working without changes.

**Change 2: Fix keyword extraction**

Expand `_NOISE_WORDS` with project-path stopwords:
- Directory names: `users`, `valorengels`, `home`, `desktop`, `agent`, `bridge`, `models`, `tools`, `config`, `tests`, `hooks`, `claude`, `scripts`, `docs`, `data`, `logs`, `utils`, `monitoring`, `sessions`
- Tool names: `grep`, `edit`, `glob`, `read`, `write` (some already present)
- Generic dev terms: `init`, `main`, `index`, `setup`, `base`, `core`, `common`, `abstract`, `interface`, `module`, `package`

Add smarter path extraction: instead of splitting on `/` and `.` blindly, strip the known project root prefix (`/Users/valorengels/src/ai/` or cwd) before splitting, so only project-relative segments are considered. For file stems (last path segment without extension), always include them as they tend to be the most meaningful.

**Change 3: Fix deja vu threshold**

Option chosen: **Remove deja vu fallback entirely.** The "I have encountered something related to X before, but the details are unclear" thought has zero utility. If retrieval returns 0 results, the keywords were too vague to surface anything useful -- injecting a vague thought just wastes context tokens. Both locations need updating:
- `agent/memory_hook.py` line 313-319: remove deja vu block (bloom hits but no results -> return None)
- `memory_bridge.py` line 285-292: remove deja vu block (same logic in hooks path)

Keep the "novel territory" signal (bloom_hits == 0 with many keywords) -- that one is less noisy and potentially useful.

### Flow

**Hook fires** -> `recall()` -> every 3rd call -> `from utils.keyword_extraction import extract_topic_keywords` (fast, no agent deps) -> extract keywords with stopwords filtered -> bloom check -> retrieve -> inject actual memories or return None (no deja vu noise)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `extract_topic_keywords()` with malformed file paths (embedded spaces, unicode, no segments) -- should return empty or minimal keyword list without raising
- [ ] `_apply_category_weights()` with records missing `score` or `metadata` attributes -- should return unmodified list
- [ ] `_cluster_keywords()` with single keyword or empty list -- should return valid cluster structure

### Empty/Invalid Input Handling
- [ ] `extract_topic_keywords("", {})` returns empty list
- [ ] `extract_topic_keywords("Read", "not a dict")` returns list with tool name parts only
- [ ] `extract_topic_keywords("Read", {"file_path": ""})` handles empty path gracefully
- [ ] `_cluster_keywords([], max_clusters=0)` returns empty list

### Error State Rendering
- [ ] No error states to render -- all functions are pure computation with no I/O

## Test Impact

- [ ] `tests/unit/test_memory_hook.py::TestExtractTopicKeywords` -- UPDATE: change imports from `agent.memory_hook` to `utils.keyword_extraction` for the 7 tests that directly test `extract_topic_keywords`. Also add new test cases for project-path stopword filtering.
- [ ] `tests/unit/test_memory_hook.py::TestClusterKeywords` -- UPDATE: change imports from `agent.memory_hook` to `utils.keyword_extraction` for the 7 tests that directly test `_cluster_keywords`.
- [ ] `tests/unit/test_memory_hook.py::TestApplyCategoryWeights` -- UPDATE: change imports from `agent.memory_hook` to `utils.keyword_extraction` for the 9 tests that directly test `_apply_category_weights`.
- [ ] `tests/unit/test_memory_hook.py::TestDejaVuSignals::test_vague_recognition_signal` -- DELETE: the vague recognition (deja vu) path is being removed; this test validates behavior that will no longer exist.
- [ ] `tests/unit/test_memory_hook.py::TestDejaVuSignals::test_no_signal_below_thresholds` -- UPDATE: with deja vu removed, bloom hits with no results should always return None regardless of count.
- [ ] `tests/unit/test_memory_hook.py::TestCheckAndInject` -- UPDATE: patch paths change from `agent.memory_hook.extract_topic_keywords` to the new module location (or keep as-is if re-exports are used).

## Rabbit Holes

- **Making `agent/__init__.py` lazy-load** -- too broad a refactor for this issue. Extracting the 4 functions is simpler and sufficient.
- **Changing WINDOW_SIZE from 3** -- reduces recall frequency but does not fix quality or latency.
- **Implementing semantic keyword extraction with LLM** -- overkill for a hook that must complete in <100ms in a subprocess.
- **Caching imports across hook invocations** -- hooks run as fresh subprocesses; there is no process to cache in.
- **Restructuring `agent/__init__.py`** -- explicitly out of scope per issue constraints.

## Risks

### Risk 1: Re-export from agent/memory_hook.py breaks introspection
**Impact:** If any caller uses `inspect` or `__module__` on the re-exported functions, they would see `utils.keyword_extraction` instead of `agent.memory_hook`.
**Mitigation:** No callers use introspection on these functions. All usage is direct function calls.

### Risk 2: Stopword list too aggressive, filters meaningful keywords
**Impact:** A memory about "agent configuration" could be missed if "agent" and "config" are both stopwords.
**Mitigation:** Only filter stopwords during path segment extraction. Grep pattern keywords and command keywords bypass the path-stopword list. The file stem (e.g., `agent_session_queue`) is kept as a compound term, not split into `agent` and `session` individually.

## Race Conditions

No race conditions -- all functions are pure computation with no shared mutable state. The sidecar file I/O in `memory_bridge.py` is unchanged.

## No-Gos (Out of Scope)

- Do NOT restructure `agent/__init__.py` (per issue constraint)
- New module must NOT import from `agent/`, `bridge/`, or `models/` -- only stdlib and `config/`
- Do NOT change `WINDOW_SIZE` or `BUFFER_SIZE`
- Do NOT modify the BM25+RRF retrieval pipeline
- Do NOT change the hook timeout (remains 5s)
- Do NOT add new dependencies

## Update System

No update system changes required -- this moves internal functions between modules within the same repository. No new dependencies, config files, or migration steps. The `utils/` directory already exists.

## Agent Integration

No agent integration required -- this is an internal refactor of keyword extraction utilities and deja vu behavior. No new MCP tools, no changes to `.mcp.json`, no bridge imports affected. The agent-side path (`agent/memory_hook.py`) continues to work via re-exports.

## Documentation

- [ ] Create `docs/features/memory-hook-performance.md` describing the import chain problem, the extraction to `utils/keyword_extraction.py`, and the deja vu removal
- [ ] Update `docs/features/subconscious-memory.md` to reference the new module location and removed deja vu behavior
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] Non-recall hook calls complete in <50ms end-to-end
- [ ] Recall hook calls complete in <100ms end-to-end
- [ ] `extract_topic_keywords()` does not produce path segments like `users`, `valorengels`, `src`, `ai` from file path inputs
- [ ] Deja vu fallback ("encountered something related to X") is removed -- no vague thoughts injected
- [ ] No `from agent.memory_hook import` in `memory_bridge.py` (uses `utils.keyword_extraction`)
- [ ] Agent-side callers (`memory_extraction.py`, `health_check.py`) still work via re-exports
- [ ] All existing tests updated and passing
- [ ] New test cases for project-path stopword filtering

## Step by Step Tasks

### 1. Create utils/keyword_extraction.py
- **Task ID**: extract-module
- **Depends On**: none
- **Validates**: import succeeds with no agent deps
- Move `extract_topic_keywords()`, `_cluster_keywords()`, `_NOISE_WORDS`, `_apply_category_weights()` from `agent/memory_hook.py` to `utils/keyword_extraction.py`
- Imports: only `re`, `typing`, `config.memory_defaults`
- Expand `_NOISE_WORDS` with project-path stopwords: `users`, `valorengels`, `home`, `desktop`, `agent`, `bridge`, `models`, `tools`, `config`, `tests`, `hooks`, `claude`, `scripts`, `docs`, `data`, `logs`, `utils`, `monitoring`, `sessions`, `init`, `main`, `index`, `setup`, `base`, `core`, `common`
- Add project root stripping in `extract_topic_keywords()`: detect and strip `/Users/valorengels/src/ai/` prefix (or configurable via env) before splitting path segments
- Preserve file stem as a compound keyword (e.g., `agent_session_queue` stays intact, not split into `agent`, `session`, `queue`)

### 2. Update agent/memory_hook.py with re-exports
- **Task ID**: update-reexports
- **Depends On**: extract-module
- **Validates**: `from agent.memory_hook import extract_topic_keywords` still works
- Remove the moved function bodies from `agent/memory_hook.py`
- Add re-exports: `from utils.keyword_extraction import extract_topic_keywords, _cluster_keywords, _NOISE_WORDS, _apply_category_weights`
- Verify `check_and_inject()` still works (it calls these functions directly)

### 3. Update memory_bridge.py imports
- **Task ID**: update-bridge-imports
- **Depends On**: extract-module
- **Validates**: `memory_bridge.recall()` uses new import path
- Change line 208: `from agent.memory_hook import extract_topic_keywords` -> `from utils.keyword_extraction import extract_topic_keywords`
- Change line 254: `from agent.memory_hook import _cluster_keywords` -> `from utils.keyword_extraction import _cluster_keywords`
- Change line 297: `from agent.memory_hook import _apply_category_weights` -> `from utils.keyword_extraction import _apply_category_weights`

### 4. Remove deja vu fallback
- **Task ID**: remove-deja-vu
- **Depends On**: none
- **Validates**: no "encountered something related" thoughts injected
- In `agent/memory_hook.py` `check_and_inject()` (lines 313-319): when `not all_records` and `bloom_hits >= threshold`, return `None` instead of the deja vu thought
- In `memory_bridge.py` `recall()` (lines 285-292): same change -- return `None` instead of deja vu thought
- Keep the "novel territory" signal (bloom_hits == 0) unchanged

### 5. Update tests
- **Task ID**: update-tests
- **Depends On**: extract-module, update-reexports, remove-deja-vu
- **Validates**: all tests pass
- Update `TestExtractTopicKeywords` imports to use `utils.keyword_extraction` (or keep `agent.memory_hook` since re-exports exist -- either works, but prefer testing the canonical location)
- Update `TestClusterKeywords` imports similarly
- Update `TestApplyCategoryWeights` imports similarly
- Delete `test_vague_recognition_signal` (deja vu path removed)
- Update `test_no_signal_below_thresholds` to verify None is returned when bloom hits exist but no results (deja vu removed)
- Add new test: `test_filters_project_path_segments` -- verify `extract_topic_keywords("Read", {"file_path": "/Users/valorengels/src/ai/agent/memory_hook.py"})` does NOT contain `users`, `valorengels`, `src`, `ai`, `agent`; DOES contain `memory_hook`
- Add new test: `test_preserves_file_stem_compound` -- verify file stems are kept as compound terms
