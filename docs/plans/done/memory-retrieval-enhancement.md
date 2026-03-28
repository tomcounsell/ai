---
status: Critiqued
type: enhancement
appetite: Large
owner: Valor
created: 2026-03-28
tracking: https://github.com/tomcounsell/ai/issues/583
---

# Enhance Memory Retrieval: Structured Metadata, Effectiveness Tracking, Multi-Query Decomposition

## Problem

The memory system stores flat text observations with no structured metadata, no persistent feedback on thought effectiveness, and a single-query retrieval path that flattens complex topics.

1. **Flat text only** -- Post-session Haiku extraction categorizes observations (CORRECTION, DECISION, PATTERN, SURPRISE) but discards the category, referenced file paths, and tool context after parsing. Retrieval relies entirely on bloom keyword matching + text similarity. There is no way to filter by "show me all corrections" or "memories about bridge/ files."

2. **Outcome data is session-scoped** -- `detect_outcomes_async()` classifies injected thoughts as "acted" or "dismissed" and feeds results into ObservationProtocol, which adjusts the `confidence` field. But the raw outcome is not persisted on the Memory record. We cannot identify chronically dismissed thoughts or build importance decay for ignored memories.

3. **Single flat query** -- `check_and_inject()` joins `unique_keywords[:5]` into one string and sends it to ContextAssembler as a single `query_cues={"topic": ...}`. Memories stored under related but different terminology are missed.

## Scope

Three independent wins, each deliverable as a separate commit/phase:

| Win | Files Changed | New Files |
|-----|--------------|-----------|
| 1. Structured metadata | `models/memory.py`, `agent/memory_extraction.py`, `.claude/hooks/hook_utils/memory_bridge.py`, `tools/memory_search/` | None |
| 2. Dismissal tracking | `models/memory.py`, `agent/memory_extraction.py`, `.claude/hooks/hook_utils/memory_bridge.py`, `config/memory_defaults.py` | None |
| 3. Multi-query decomposition | `agent/memory_hook.py`, `.claude/hooks/hook_utils/memory_bridge.py` | None |

**Primary constraint:** Must not change Memory model's key structure (`memory_id`, `agent_id`, `project_key` as KeyField/AutoKeyField). Adding new non-key fields is safe -- Popoto/Redis stores fields per-hash, so new fields appear on new records and return defaults on old records.

## Prior Art

- [#514](https://github.com/tomcounsell/ai/issues/514): Established core Memory model, bloom filter, ContextAssembler integration
- [#519](https://github.com/tomcounsell/ai/issues/519): Extended memory to Claude Code CLI sessions via hook-based sidecar files
- [#524](https://github.com/tomcounsell/ai/pull/524): Added intentional memory saves via `python -m tools.memory_search save`
- [#393](https://github.com/tomcounsell/ai/issues/393): Earlier behavioral episode memory design (closed, partially superseded)

## Data Flow

### Current: Extraction discards metadata
```
Haiku output: "CORRECTION: Redis SCAN preferred over KEYS"
    |
    v
_parse_categorized_observations() -> ("Redis SCAN preferred over KEYS", 4.0)
    |
    v
Memory.safe_save(content="Redis SCAN preferred over KEYS", importance=4.0)
    # Category "correction" is LOST -- only importance level preserved
```

### After Win 1: Metadata preserved
```
Haiku output (JSON): {"category": "correction", "observation": "Redis SCAN preferred...", "file_paths": ["bridge/telegram_bridge.py"], "tags": ["redis", "performance"]}
    |
    v
Memory.safe_save(content="Redis SCAN preferred...", importance=4.0, metadata={"category": "correction", "file_paths": [...], "tags": [...]})
    # Category, file paths, tags all queryable
```

### Current: Outcomes affect confidence only
```
detect_outcomes_async() -> {"mem_abc": "dismissed"}
    |
    v
ObservationProtocol.on_context_used() -> confidence -= weaken_factor
    # Raw "dismissed" outcome is NOT stored on the record
```

### After Win 2: Dismissals tracked and decay importance
```
detect_outcomes_async() -> {"mem_abc": "dismissed"}
    |
    v
ObservationProtocol.on_context_used() -> confidence -= weaken_factor
Memory.metadata["dismissal_count"] += 1
    # If dismissal_count >= threshold -> importance *= decay_factor
```

### Current: Single query
```
keywords = ["refactor", "memory", "pipeline", "bloom", "extraction"]
query = "refactor memory pipeline bloom extraction"
    -> ContextAssembler.assemble(query_cues={"topic": query})
    # One semantic neighborhood searched
```

### After Win 3: Multi-query decomposition
```
keywords = ["refactor", "memory", "pipeline", "bloom", "extraction"]
clusters = [["refactor", "pipeline"], ["memory", "bloom", "extraction"]]
    -> ContextAssembler.assemble(query_cues={"topic": "refactor pipeline"})
    -> ContextAssembler.assemble(query_cues={"topic": "memory bloom extraction"})
    # Merge + deduplicate results -- two semantic neighborhoods covered
```

## Architectural Impact

- **No new dependencies**: Popoto `DictField` is already available in imports (confirmed). No new infrastructure.
- **Interface changes**: `Memory.metadata` DictField added. Extraction prompt output changes from line-per-observation to JSON. `memory_search search` gains `--category` and `--tag` filter flags.
- **Coupling**: Low -- each win touches its own pipeline stage. Win 1 is extraction, Win 2 is outcome detection, Win 3 is retrieval.
- **Reversibility**: High -- DictField returns empty dict for records that predate the field. Dismissal tracking is additive. Multi-query can fall back to single query.

## Appetite

**Size:** Large (3 independent wins, each medium complexity)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The wins are independent so they can be built and tested sequentially. The extraction prompt change (Win 1) is the most delicate since it changes Haiku's output format from line-based to JSON.

## Prerequisites

No prerequisites. All three wins build on the existing Memory model and extraction pipeline.

## Solution

### Win 1: Structured Metadata on Memory Records

#### 1a. Add `metadata` DictField to Memory model

Add a Popoto `DictField` to `models/memory.py`:

```python
from popoto import DictField  # already importable

class Memory(WriteFilterMixin, AccessTrackerMixin, Model):
    # ... existing fields ...
    metadata = DictField(default=dict)
```

This field stores a dict with optional keys: `category` (str), `file_paths` (list[str]), `tags` (list[str]), `tool_names` (list[str]). All keys are optional -- old records return `{}`.

#### 1b. Change extraction prompt to return structured JSON

Replace the `EXTRACTION_PROMPT` in `agent/memory_extraction.py` to request JSON output:

```python
EXTRACTION_PROMPT = (
    "Extract novel observations from this agent session response.\n"
    "Return a JSON array of objects, each with:\n"
    '  "category": one of "correction", "decision", "pattern", "surprise"\n'
    '  "observation": the observation text (one sentence, specific)\n'
    '  "file_paths": list of file paths referenced (empty list if none)\n'
    '  "tags": list of domain tags (1-3 short keywords)\n'
    "\n"
    "Only include genuinely novel, specific observations.\n"
    'If none, return: []\n'
    "\n"
    "Example:\n"
    '[{"category": "decision", "observation": "chose blue-green deployment", '
    '"file_paths": ["deploy/config.yaml"], "tags": ["deployment", "infrastructure"]}]'
)
```

Update `_parse_categorized_observations()` to parse JSON, with fallback to the existing line-based parser for robustness:

```python
def _parse_categorized_observations(raw_text: str) -> list[tuple[str, float, dict]]:
    """Parse Haiku output into (content, importance, metadata) tuples."""
    # Try JSON first
    try:
        import json
        data = json.loads(raw_text)
        if isinstance(data, list):
            results = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                category = item.get("category", "").lower()
                observation = item.get("observation", "")
                if not observation or len(observation) < 10:
                    continue
                importance = CATEGORY_IMPORTANCE.get(category, DEFAULT_CATEGORY_IMPORTANCE)
                metadata = {
                    "category": category,
                    "file_paths": item.get("file_paths", []),
                    "tags": item.get("tags", []),
                }
                results.append((observation, importance, metadata))
            if results:
                return results
    except (json.JSONDecodeError, TypeError):
        pass  # Fall through to line-based parser

    # Fallback: existing line-based parser (returns empty metadata)
    # ... existing logic, returning (content, importance, {}) tuples
```

Update `extract_observations_async()` to pass metadata to `Memory.safe_save()`:

```python
m = Memory.safe_save(
    agent_id=f"extraction-{session_id}",
    project_key=project_key,
    content=obs_content[:500],
    importance=importance,
    source=SOURCE_AGENT,
    metadata=metadata,  # NEW
)
```

#### 1c. Update memory_search CLI to support metadata filtering

Add `--category` and `--tag` flags to the `search` subcommand. Filtering happens post-retrieval (since ContextAssembler does not support field-level filtering beyond `partition_filters`):

```python
# In search function: after ContextAssembler returns results
if category_filter:
    results = [r for r in results if r.metadata.get("category") == category_filter]
if tag_filter:
    results = [r for r in results if tag_filter in r.metadata.get("tags", [])]
```

#### 1d. Update Claude Code memory bridge

The bridge's `extract()` function calls `extract_observations_async()` which saves via `Memory.safe_save()`. No bridge-specific changes needed for Win 1 since the extraction pipeline is shared. The bridge path inherits the metadata automatically.

### Win 2: Persistent Thought Effectiveness Tracking

#### 2a. Add dismissal tracking constants

Add to `config/memory_defaults.py`:

```python
DISMISSAL_DECAY_THRESHOLD = 3      # consecutive dismissals before importance decays
DISMISSAL_IMPORTANCE_DECAY = 0.7   # multiply importance by this on threshold breach
MIN_IMPORTANCE_FLOOR = 0.2         # never decay below this
```

#### 2b. Persist outcome data in metadata

In `detect_outcomes_async()`, after ObservationProtocol adjustment, update the Memory's metadata dict:

```python
for m in memories:
    mid = getattr(m, "memory_id", "")
    if mid in outcome_map:
        outcome = outcome_map[mid]
        try:
            meta = getattr(m, "metadata", {}) or {}
            if outcome == "dismissed":
                meta["dismissal_count"] = meta.get("dismissal_count", 0) + 1
                meta["last_outcome"] = "dismissed"
                # Check threshold
                if meta["dismissal_count"] >= DISMISSAL_DECAY_THRESHOLD:
                    current_importance = getattr(m, "importance", 1.0)
                    new_importance = max(
                        current_importance * DISMISSAL_IMPORTANCE_DECAY,
                        MIN_IMPORTANCE_FLOOR,
                    )
                    m.importance = new_importance
                    meta["dismissal_count"] = 0  # reset after decay
            elif outcome == "acted":
                meta["dismissal_count"] = 0  # reset on positive signal
                meta["last_outcome"] = "acted"
            m.metadata = meta
            m.save()
        except Exception:
            continue  # fail-silent
```

#### 2c. Update Claude Code bridge outcome detection

The bridge's `extract()` function calls `detect_outcomes_async()` from `agent/memory_extraction.py`. The outcome persistence logic in 2b is in that shared function, so the bridge path inherits it automatically. No separate bridge changes needed.

#### 2d. Surface dismissal data in memory_search inspect

Update the `inspect` command output to show dismissal count and last outcome when present in metadata:

```python
meta = result.get("metadata", {})
if meta.get("dismissal_count"):
    print(f"  Dismissal count: {meta['dismissal_count']}")
if meta.get("last_outcome"):
    print(f"  Last outcome: {meta['last_outcome']}")
```

### Win 3: Multi-Query Decomposition

#### 3a. Keyword clustering function

Add a `_cluster_keywords()` function to `agent/memory_hook.py`:

```python
def _cluster_keywords(keywords: list[str], max_clusters: int = 3) -> list[list[str]]:
    """Group keywords into topical clusters for multi-query retrieval.

    Strategy: group keywords that co-occurred in the same tool call window.
    Falls back to even splitting if co-occurrence data is unavailable.
    """
    if len(keywords) <= 5:
        return [keywords]  # single cluster, no decomposition needed

    # Split into clusters of ~3-5 keywords
    cluster_size = max(3, len(keywords) // max_clusters)
    clusters = []
    for i in range(0, len(keywords), cluster_size):
        chunk = keywords[i:i + cluster_size]
        if chunk:
            clusters.append(chunk)

    # Merge tiny trailing cluster into previous
    if len(clusters) > 1 and len(clusters[-1]) < 2:
        clusters[-2].extend(clusters.pop())

    return clusters[:max_clusters]
```

#### 3b. Update check_and_inject() for multi-query

Replace the single ContextAssembler call with a loop over clusters:

```python
clusters = _cluster_keywords(unique_keywords)

all_records = []
seen_ids = set()

for cluster in clusters:
    result = assembler.assemble(
        query_cues={"topic": " ".join(cluster[:5])},
        agent_id=project_key,
        partition_filters={"project_key": project_key},
    )
    for record in (result.records or []):
        rid = getattr(record, "memory_id", "")
        if rid not in seen_ids:
            seen_ids.add(rid)
            all_records.append(record)
```

Then format the top MAX_THOUGHTS records as thought blocks, same as current logic.

#### 3c. Mirror in Claude Code memory bridge

Apply the same multi-query pattern in `memory_bridge.py`'s `recall()` function. The clustering function is imported from `agent.memory_hook._cluster_keywords()`.

#### 3d. Latency guard

Add a timing check to ensure multi-query stays within budget:

```python
import time
start = time.monotonic()
# ... multi-query logic ...
elapsed_ms = (time.monotonic() - start) * 1000
if elapsed_ms > 15:
    logger.warning(f"[memory_hook] Multi-query took {elapsed_ms:.1f}ms (budget: 15ms)")
```

If latency consistently exceeds 15ms in testing, reduce `max_clusters` to 2.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `Memory.safe_save()` with metadata=None should use default empty dict
- [ ] `Memory.safe_save()` with metadata containing non-serializable values should fail-silent
- [ ] JSON parsing failure in extraction falls back to line-based parser
- [ ] Dismissal tracking on a Memory that fails to re-save should not crash
- [ ] Multi-query with empty clusters should fall back to single query

### Empty/Invalid Input Handling
- [ ] Metadata field returns `{}` for pre-existing records (no migration needed)
- [ ] `dismissal_count` missing from metadata defaults to 0
- [ ] `_cluster_keywords([])` returns `[[]]` or empty list (no crash)
- [ ] Extraction prompt returning malformed JSON triggers line-based fallback

### Error State Rendering
- [ ] `memory_search inspect` gracefully handles records with no metadata field
- [ ] `memory_search search --category X` returns empty results (not error) when no matches

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations` -- UPDATE: return type changes from `list[tuple[str, float]]` to `list[tuple[str, float, dict]]`; all 6 test cases in this class need updated assertions
- [ ] `tests/unit/test_memory_extraction.py::TestRunPostSessionExtraction` -- UPDATE: mock Haiku responses need to return JSON format; existing line-based test cases become fallback-path tests
- [ ] `tests/unit/test_memory_extraction.py::TestDetectOutcomes` -- UPDATE: add assertions for metadata persistence (dismissal_count, last_outcome) after outcome detection
- [ ] `tests/unit/test_memory_hook.py::TestCheckAndInject` -- UPDATE: add test cases for multi-query decomposition path (keywords > 5 triggering clustering)
- [ ] `tests/unit/test_memory_hook.py` -- no changes needed for `TestExtractTopicKeywords` (keyword extraction is unchanged)

## Rabbit Holes

- **ContextAssembler field filtering**: The issue notes that `partition_filters` is ContextAssembler's only filter parameter. Do NOT attempt to add metadata-level filtering into ContextAssembler -- that is a Popoto-level change. Instead, filter post-retrieval in the search CLI and pre-retrieval via `Memory.query.filter()` when needed.
- **Sophisticated clustering**: Do not build NLP-based topic modeling or embedding-based clustering for Win 3. Simple positional splitting of the keyword list is sufficient for v1. If recall improves, we can iterate.
- **Migration of existing records**: Do NOT attempt to backfill metadata on existing Memory records. DictField returns `{}` for old records, which is the correct default. Metadata will accrue naturally on new records.
- **Changing key structure**: The Memory model's key fields (`memory_id`, `agent_id`, `project_key`) must not be modified. This would invalidate existing Redis keys.
- **Vector embeddings**: Do not add vector similarity search. The bloom + ContextAssembler pipeline is the established approach. Multi-query decomposition extends it without changing the retrieval engine.

## Risks

### Risk 1: Haiku JSON output reliability
**Impact:** If Haiku sometimes returns non-JSON despite the prompt requesting JSON, extraction silently falls back to line-based parsing, losing metadata.
**Mitigation:** The fallback parser preserves all current functionality. Over time, most new records will have metadata. Monitor via `memory_search inspect --stats` to check metadata coverage.

### Risk 2: Multi-query latency exceeds 15ms budget
**Impact:** Each additional ContextAssembler call adds ~5ms. With 3 clusters, total could reach 15-20ms.
**Mitigation:** Cap at 2 clusters by default. Add timing instrumentation. Only decompose when keyword count exceeds threshold (>5 unique keywords).

### Risk 3: Dismissal decay reduces importance of useful memories
**Impact:** A memory might be "dismissed" because bigram overlap is imperfect, not because the memory is bad.
**Mitigation:** Threshold of 3 consecutive dismissals before decay. Acting on a memory once resets the counter. Importance floor of 0.2 prevents complete erasure.

## Race Conditions

No race conditions. Memory records are processed sequentially within a session. Outcome detection runs post-session (single-threaded). The metadata dict is updated atomically via a single `m.save()` call.

## No-Gos (Out of Scope)

- Modifying Popoto's ContextAssembler to support field-level filtering
- Adding vector embedding storage or retrieval
- Changing Memory model key structure (would invalidate existing records)
- Backfilling metadata on existing Memory records
- Session trace storage (full tool call sequence persistence)
- NLP-based topic clustering for keyword grouping

## Update System

No update system changes required. All changes are to existing Python modules within the repository. No new dependencies, no new config files, no migration scripts. The `metadata` DictField is additive and requires no Redis schema changes.

## Agent Integration

No agent integration changes required. The memory system is an internal pipeline (extraction, injection, outcome detection). It does not expose new MCP tools or modify `.mcp.json`. The `memory_search` CLI tool gains new filter flags but is already registered and accessible. The bridge (`bridge/telegram_bridge.py`) does not need changes -- it calls `Memory.safe_save()` which will simply ignore the metadata field for human message ingestion (metadata is populated by extraction, not by direct message saves).

## Documentation

- [ ] Update `docs/features/subconscious-memory.md` to document: metadata field schema, dismissal tracking behavior, multi-query decomposition, and updated data flow diagrams
- [ ] Add entry to `docs/features/README.md` index table for the enhancement (or update existing subconscious-memory entry)
- [ ] Update `CLAUDE.md` quick reference table with new `memory_search` filter flags (`--category`, `--tag`)

## Success Criteria

- [ ] Memory records from extraction contain structured metadata (category, file_paths, tags)
- [ ] `python -m tools.memory_search search "query" --category correction` filters correctly
- [ ] A memory dismissed 3+ times has reduced importance (verified in test)
- [ ] `check_and_inject()` produces 2+ sub-queries when keyword buffer exceeds 5 unique keywords
- [ ] All operations wrapped in try/except, logged at WARNING on failure
- [ ] Retrieval latency under 15ms (measured with timing instrumentation)
- [ ] Existing test suites pass (updated as noted in Test Impact)
- [ ] Claude Code memory bridge has parity with SDK agent path for all three wins

## Team Orchestration

### Team Members

- **Builder (memory-retrieval)**
  - Name: memory-builder
  - Role: Implement all three wins sequentially
  - Agent Type: builder
  - Resume: true

- **Validator (memory-retrieval)**
  - Name: memory-validator
  - Role: Verify tests pass, latency budget met, fail-silent contract maintained
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add metadata DictField to Memory model
- **Task ID**: add-metadata-field
- **Depends On**: none
- **Validates**: manual -- `Memory(metadata={"category": "test"}).metadata` returns the dict
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `from popoto import DictField` to existing import block in `models/memory.py`
- Add `metadata = DictField(default=dict)` field to Memory class
- Update docstring to document the metadata field and its optional keys

### 2. Update extraction prompt and parser for structured JSON
- **Task ID**: structured-extraction
- **Depends On**: add-metadata-field
- **Validates**: tests/unit/test_memory_extraction.py
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `EXTRACTION_PROMPT` with JSON-requesting version
- Rewrite `_parse_categorized_observations()` to try JSON parsing first, fall back to line-based
- Change return type to `list[tuple[str, float, dict]]` (content, importance, metadata)
- Update `extract_observations_async()` to pass metadata to `Memory.safe_save()`
- Update existing tests to expect new return type; add fallback-path test cases

### 3. Add metadata filtering to memory_search CLI
- **Task ID**: metadata-search-filters
- **Depends On**: structured-extraction
- **Validates**: tests/unit/test_memory_search.py (create if needed)
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `--category` and `--tag` flags to `search` subcommand in `tools/memory_search/cli.py`
- Implement post-retrieval filtering in `tools/memory_search/__init__.py` search function
- Update `inspect` output to show metadata when present
- Add test coverage for filter flags

### 4. Implement dismissal tracking in outcome detection
- **Task ID**: dismissal-tracking
- **Depends On**: add-metadata-field
- **Validates**: tests/unit/test_memory_extraction.py
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `DISMISSAL_DECAY_THRESHOLD`, `DISMISSAL_IMPORTANCE_DECAY`, `MIN_IMPORTANCE_FLOOR` to `config/memory_defaults.py`
- Update `detect_outcomes_async()` to persist dismissal_count and last_outcome in metadata
- Implement importance decay when dismissal_count reaches threshold
- Reset dismissal_count on "acted" outcome
- Add test cases for dismissal tracking, threshold breach, and importance floor

### 5. Implement multi-query decomposition
- **Task ID**: multi-query
- **Depends On**: none
- **Validates**: tests/unit/test_memory_hook.py
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_cluster_keywords()` function to `agent/memory_hook.py`
- Update `check_and_inject()` to use clustering when unique_keywords > 5
- Merge and deduplicate results from multiple ContextAssembler calls
- Add timing instrumentation with 15ms warning threshold
- Add test cases for clustering logic and multi-query path

### 6. Update Claude Code memory bridge for parity
- **Task ID**: bridge-parity
- **Depends On**: multi-query
- **Validates**: manual testing via Claude Code session
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: false
- Import `_cluster_keywords` from `agent.memory_hook` in `memory_bridge.py`
- Update `recall()` to use multi-query decomposition (same pattern as check_and_inject)
- Verify extraction path inherits metadata and dismissal tracking automatically (shared code)

### 7. Update and run test suite
- **Task ID**: validate-tests
- **Depends On**: structured-extraction, dismissal-tracking, multi-query, bridge-parity
- **Validates**: full test suite
- **Assigned To**: memory-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_memory_hook.py tests/unit/test_memory_extraction.py -v`
- Run `python -m ruff check . && python -m ruff format --check .`
- Verify all fail-silent contracts (grep for bare `raise` in memory code)
- Verify no changes to Memory key structure

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-tests
- **Validates**: docs exist and are accurate
- **Assigned To**: memory-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` with metadata schema, dismissal tracking, multi-query
- Update `docs/features/README.md` index
- Update `CLAUDE.md` with new memory_search filter flags

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_memory_hook.py tests/unit/test_memory_extraction.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Metadata field works | `python -c "from models.memory import Memory; m = Memory(metadata={'category': 'test'}); print(m.metadata)"` | `{'category': 'test'}` |
| No key structure changes | `grep -n "KeyField\|AutoKeyField" models/memory.py` | Same fields as before |

## Critique Results (v2 -- 2026-03-28)

**Plan**: docs/plans/memory-retrieval-enhancement.md
**Issue**: #583
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Source files verified**: 11 files read and cross-referenced against plan
**Findings**: 0 blockers, 6 concerns, 3 nits

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1-8 sequential, no gaps |
| Dependencies valid | PASS | All Depends On references resolve to valid task IDs |
| File paths exist | PASS | 11 of 13 exist; `memory_bridge.py` is a shorthand (full path `.claude/hooks/hook_utils/memory_bridge.py` exists); `tests/unit/test_memory_search.py` marked "(create if needed)" |
| Prerequisites met | PASS | No prerequisites declared |
| Cross-references | PASS | Test Impact class names match actual test file contents (verified against source) |
| DictField import | PASS | `from popoto import DictField` confirmed importable; default returns `{}` as plan expects |

### Concerns

#### C1. Win 2b adds m.save() but current ObservationProtocol path does not call m.save()
- **Severity**: CONCERN
- **Critics**: Skeptic, Adversary
- **Location**: Solution, Win 2 section (2b)
- **Finding**: The current `detect_outcomes_async()` (lines 329-365 of `agent/memory_extraction.py`) delegates persistence to `ObservationProtocol.on_context_used()`, which internally adjusts confidence on the loaded Memory instances. The plan proposes adding an explicit `m.save()` call after updating metadata. This introduces a second save that may conflict with or duplicate ObservationProtocol's internal persistence. If ObservationProtocol calls `save()` internally, and then the plan's code also calls `m.save()`, the second save could overwrite ObservationProtocol's confidence adjustment (or vice versa, depending on timing and whether Popoto caches field state).
- **Suggestion**: Verify whether `ObservationProtocol.on_context_used()` calls `save()` internally. If it does, place the metadata update BEFORE the ObservationProtocol call so both changes are persisted in one write. If it does not, the plan's explicit `m.save()` is correct but should be documented as the sole persistence point.

#### C2. Read-modify-write race on metadata in multi-session environments
- **Severity**: CONCERN
- **Critics**: Adversary
- **Location**: Solution, Win 2 section (2b); Race Conditions section
- **Finding**: The plan's Race Conditions section states "No race conditions." However, `detect_outcomes_async()` does a read-modify-write on `m.metadata["dismissal_count"]`: load the memory, read metadata, increment counter, save. If two sessions process outcomes for the same memory_id concurrently (e.g., a bridge session and a Claude Code session finishing at the same time), the last writer wins and a dismissal count increment is lost. This is unlikely but possible.
- **Suggestion**: Acknowledge this as a known low-probability race in the Race Conditions section. The consequence (losing a single dismissal count) is benign, but the section should not claim "no race conditions."

#### C3. Old records will gain metadata dicts through outcome detection (undocumented backfill)
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Solution, Win 2 section (2b)
- **Finding**: The plan's code uses `meta = getattr(m, "metadata", {}) or {}` for old records. When `m.save()` is called, these old records will gain a metadata dict containing `dismissal_count` and `last_outcome`. This is actually desirable behavior but contradicts the Rabbit Holes section which says "Do NOT attempt to backfill metadata on existing Memory records." The backfill is implicit via outcome detection, not explicit, but the plan should acknowledge this.
- **Suggestion**: Add a note clarifying that outcome detection will naturally backfill metadata on old records as a side effect, and that this is intentional and distinct from the explicit backfill banned in Rabbit Holes.

#### C4. Multi-query latency budget assumes ~5ms per ContextAssembler call without measurement
- **Severity**: CONCERN
- **Critics**: Operator, Skeptic
- **Location**: Risks, Risk 2; Solution, Win 3 section (3d)
- **Finding**: The plan states "each additional ContextAssembler call adds ~5ms" and sets a 15ms budget. The current single-query path (lines 199-212 of `agent/memory_hook.py`) has no timing instrumentation, so the baseline is unknown. If a single call already takes 10ms (plausible for a Redis round-trip with scoring), then 2-3 calls would exceed 15ms routinely, and the latency guard in 3d would fire on every multi-query invocation, producing log noise.
- **Suggestion**: Before building Win 3, measure the current single-query ContextAssembler latency. Set the warning threshold to 2x the measured baseline rather than a fixed 15ms.

#### C5. Bridge parity (Task 6) validates via "manual testing" -- no automated verification
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Task 6 (bridge-parity)
- **Finding**: Task 6 is the only task with "manual testing via Claude Code session" as its validation method. The bridge `recall()` function (lines 174-305 of `memory_bridge.py`) closely mirrors `check_and_inject()` and uses the same mock-friendly patterns (bloom field, ContextAssembler). It should be testable with the same approach used in `test_memory_hook.py::TestDejaVuSignals`.
- **Suggestion**: Add a unit test for the bridge `recall()` multi-query path, even if it duplicates some logic from `test_memory_hook.py`.

#### C6. _cluster_keywords returns [keywords] for <=5 keywords -- multi-query never fires for typical sessions
- **Severity**: CONCERN
- **Critics**: Skeptic, User
- **Location**: Solution, Win 3 section (3a)
- **Finding**: The proposed `_cluster_keywords()` returns `[keywords]` (single cluster, no decomposition) when `len(keywords) <= 5`. The current `check_and_inject()` already caps `unique_keywords` at 15 (line 170 of `agent/memory_hook.py`), and `extract_topic_keywords()` caps per-tool-call keywords at 10 (line 85). However, with `INJECTION_BUFFER_SIZE = 9` and deduplication, many real sessions may produce only 4-6 unique keywords per window. The threshold of >5 to trigger multi-query may rarely be met, meaning Win 3 has limited practical impact unless the threshold is tuned lower.
- **Suggestion**: After building, measure how often the multi-query path actually fires in production. Consider lowering the threshold to >3 if data shows >5 is rarely reached.

### Nits

#### N1. Task 3 validates against ambiguous test file path
- **Severity**: NIT
- **Critics**: Archaeologist
- **Location**: Task 3 (metadata-search-filters)
- **Finding**: Task 3 validates against `tests/unit/test_memory_search.py` with "(create if needed)". An existing test file lives at `tools/memory_search/tests/`. The builder should know which location to use.
- **Suggestion**: Specify the exact path. The existing `tools/memory_search/tests/` directory is the natural home.

#### N2. Extraction prompt example may cause Haiku to return dict instead of list
- **Severity**: NIT
- **Critics**: Simplifier
- **Location**: Solution, Win 1 section (1b)
- **Finding**: The extraction prompt example shows a single-item JSON array. Haiku models sometimes return a bare `{}` object instead of `[{}]` when only one observation is found. The parser's `isinstance(data, list)` check (line 173 of plan) would silently skip a valid single observation.
- **Suggestion**: Add a guard: if `json.loads()` returns a `dict` instead of `list`, wrap it in `[data]`.

#### N3. Success criterion "retrieval latency under 15ms" has no baseline
- **Severity**: NIT
- **Critics**: User
- **Location**: Success Criteria
- **Finding**: "Retrieval latency under 15ms" is meaningful only with a known baseline. Without measuring the current single-query latency, this criterion is untestable.
- **Suggestion**: Rephrase to "Retrieval latency does not regress more than 2x from measured baseline."

### Verdict

**READY TO BUILD** -- No blockers. The six concerns are acknowledged risks and minor gaps, not plan defects. C1 (save conflict with ObservationProtocol) is the most actionable -- the builder should verify ObservationProtocol's save behavior before implementing Win 2b. All other concerns can be addressed during implementation without plan revision.

---

## Open Questions

No open questions. The three wins are well-scoped and independent. The main design decision -- using DictField for metadata rather than separate typed fields -- trades query efficiency for schema flexibility, which is the right tradeoff given that ContextAssembler does not support field-level filtering anyway.
