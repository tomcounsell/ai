---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-30
tracking: https://github.com/tomcounsell/ai/issues/586
last_comment_id:
---

# Memory Agent Integration: Metadata-Aware Recall

## Problem

The memory system extracts rich structured metadata (category, tags, file paths) on every Memory record, but the agent-facing recall pipeline ignores all of it. Post-session extraction via Haiku writes category, tags, and file_paths to `Memory.metadata`, but `check_and_inject()` only filters by `project_key` partition. Agents have documentation on how to save memories but zero guidance on how to retrieve them by metadata dimensions.

**Current behavior:**

1. `check_and_inject()` passes only `partition_filters={"project_key": ...}` to ContextAssembler -- category, tags, and file paths are never used to filter or prioritize recalled memories.
2. The Claude Code memory bridge mirrors the same metadata-blind recall pattern.
3. Persona config documents save recipes but no retrieval recipes -- agents do not know they can filter by `--tag` or `--category`.
4. Post-merge extraction saves bare observations without metadata (no category, tags, or file_paths), unlike post-session extraction which populates all metadata fields.
5. All memories score equally during recall regardless of category -- a "correction" (something the agent got wrong) surfaces with the same priority as a "pattern" (a general observation).

**Desired outcome:**

- Recall pipeline uses metadata to prioritize contextually relevant memories (corrections over patterns when debugging)
- Agent documentation includes retrieval recipes alongside save recipes
- All memory creation paths populate metadata consistently
- Category-weight constants are configurable in `memory_defaults.py`

## Prior Art

- **PR #584 / Issue #583** (merged 2026-03-28): Structured metadata, dismissal tracking, multi-query decomposition -- built the metadata foundation this issue extends into the agent-facing layer.
- **PR #515 / Issue #514** (merged 2026-03-24): Subconscious Memory -- established Memory model, bloom filter, ContextAssembler integration, thought injection pipeline.
- **PR #525 / Issue #519** (merged): Claude Code memory integration -- extended memory to CLI sessions via hook-based sidecar files.
- **Issue #521** (closed): Intentional memory saves -- established importance tiers, save recipes in persona config, and extraction categories.

## Data Flow

### Current recall flow (metadata-blind)

1. **Entry point**: PostToolUse hook fires on every tool call
2. **Sliding window**: `check_and_inject()` accumulates tool calls, fires every WINDOW_SIZE calls
3. **Keyword extraction**: Extracts topic keywords from tool names and inputs
4. **Bloom pre-check**: O(1) bloom filter check for topic relevance
5. **ContextAssembler query**: `assembler.assemble(query_cues={"topic": ...}, partition_filters={"project_key": ...})` -- no metadata filtering
6. **Output**: Raw scored results formatted as `<thought>` blocks

### Desired recall flow (metadata-aware)

1-4. Same as current
5. **Pre-query filter**: Before ContextAssembler, optionally filter candidate memories by category/tags using Memory.query
6. **Post-query re-rank**: After ContextAssembler returns scored results, apply category-weight bonuses to adjust ordering
7. **Output**: Re-ranked results formatted as `<thought>` blocks with category context

The pre-query approach is necessary because ContextAssembler only supports `partition_filters` today. Post-query re-ranking is the safer path -- it layers on top of existing scoring without disrupting ContextAssembler internals.

## Architectural Impact

- **New dependencies**: None. All work uses existing Popoto and Memory model APIs.
- **Interface changes**: `check_and_inject()` gains optional `context_hints` parameter for callers to pass file paths or tool context. No breaking changes to existing callers.
- **Coupling**: Minimal increase. Category weights are centralized in `memory_defaults.py`. Re-ranking logic stays in `memory_hook.py`.
- **Data ownership**: No change. Memory model still owns all metadata.
- **Reversibility**: High. Category weights default to 1.0 (no-op). Re-ranking can be disabled by setting all weights equal.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope confirmation on ContextAssembler constraints)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes use existing Memory model, Popoto ContextAssembler, and CLI tool APIs.

## Solution

### Key Elements

- **Post-query category re-ranking**: After ContextAssembler returns scored results, multiply each result's effective score by a category weight before sorting. Corrections and decisions get a boost; patterns get neutral weight.
- **Category weight constants**: Configurable multipliers in `memory_defaults.py` keyed by category string. Default: corrections=1.5, decisions=1.3, patterns=1.0, surprises=1.0.
- **Post-merge metadata parity**: Update `extract_post_merge_learning()` to populate category ("decision"), tags (derived from PR title/labels), and file_paths (from diff summary).
- **Agent retrieval recipes**: Add `--category` and `--tag` filter examples to the "Intentional Memory" section in `_base.md`.
- **Bridge parity**: Mirror all recall changes in `memory_bridge.py` to maintain SDK/Claude Code equivalence.

### Flow

**Tool call** -> sliding window fires -> keyword extraction -> bloom check -> ContextAssembler query -> **category re-rank** -> thought injection

### Technical Approach

- **Post-query re-ranking over pre-query filtering**: ContextAssembler handles relevance scoring, decay, and confidence. We layer category weights on top rather than trying to pre-filter candidates. This avoids fighting the Popoto API surface and keeps latency low (no extra Redis queries).
- **Shared re-rank function**: A single `_apply_category_weights(records)` function used by both `memory_hook.py` and `memory_bridge.py`, imported from a shared location.
- **Metadata consistency via extraction prompt update**: Update `POST_MERGE_EXTRACTION_PROMPT` to request structured JSON output matching the post-session format (category, tags, file_paths).
- **Fail-silent everywhere**: All new code paths wrapped in try/except. Category weight lookup falls back to 1.0 for unknown categories.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_apply_category_weights()` must handle records with missing/malformed metadata gracefully (return unmodified order)
- [ ] Post-merge extraction with structured JSON prompt must fall back to bare save if Haiku returns non-JSON
- [ ] Re-ranking must not crash if `record.metadata` is None, not a dict, or missing "category" key

### Empty/Invalid Input Handling
- [ ] `_apply_category_weights([])` returns empty list
- [ ] Records with `metadata=None` or `metadata={}` get default weight (1.0)
- [ ] Post-merge extraction with empty PR title returns None (existing behavior preserved)

### Error State Rendering
- [ ] No user-visible output changes -- thought injection format is unchanged
- [ ] Latency warning logs fire if re-ranking pushes total time over 15ms

## Test Impact

- [ ] `tests/unit/test_memory_hook.py` -- UPDATE: add tests for category re-ranking behavior in `check_and_inject()`
- [ ] `tests/unit/test_memory_extraction.py` -- UPDATE: add tests for structured metadata in post-merge extraction output
- [ ] `tests/unit/test_memory_bridge.py` -- UPDATE: add tests for category re-ranking in bridge `recall()` function

No existing test cases need to be deleted or replaced. All changes are additive -- existing test assertions remain valid because re-ranking with default weights (1.0) preserves existing ordering.

## Rabbit Holes

- **Building full ContextAssembler metadata filtering**: Tempting to add native metadata filter support to ContextAssembler, but that requires upstream Popoto changes. Post-query re-ranking achieves the goal without Popoto modifications.
- **Dynamic weight tuning based on session context**: Adjusting category weights based on what the agent is doing (e.g., boost corrections during debugging) is appealing but adds complexity. Static weights are sufficient for v1.
- **Memory search CLI rewrite**: The existing `tools/memory_search.py` CLI already supports `--category` and `--tag` flags. No need to rework the tool itself.
- **Pre-query filtering via Memory.query()**: Running a separate `Memory.query.filter(metadata__category="correction")` before ContextAssembler adds a Redis round-trip and complexity. Post-query re-ranking avoids this entirely.

## Risks

### Risk 1: Re-ranking latency exceeds 15ms budget
**Impact:** Memory injection adds perceptible lag to tool calls.
**Mitigation:** Re-ranking is pure Python (dict lookup + multiplication + sort) on a max of MAX_THOUGHTS (3) records. Negligible compared to the ContextAssembler query itself. Monitor via existing latency warning log.

### Risk 2: Post-merge extraction prompt change breaks Haiku output
**Impact:** Post-merge learnings stop being saved.
**Mitigation:** Keep the existing bare-text fallback parser. If Haiku returns non-JSON, fall back to saving as an uncategorized memory (existing behavior).

## Race Conditions

No race conditions identified -- all operations are synchronous within a single tool-call handler. The sliding window is session-scoped (in-memory dict for SDK, JSON sidecar for hooks). No concurrent access to the same session state.

## No-Gos (Out of Scope)

- Native ContextAssembler metadata filtering (requires Popoto upstream changes -- tracked as item 5 in issue #586)
- Dynamic category weight adjustment based on session context
- Memory search CLI interface changes
- Changes to the Memory model key structure
- Tag-based or file-path-based pre-query filtering (deferred until Popoto supports it natively)

## Update System

No update system changes required -- this feature modifies internal agent behavior (recall scoring, extraction prompts, persona docs). No new dependencies, config files, or migration steps. The standard `git pull` update propagates all changes.

## Agent Integration

No new MCP server or tool registration needed. The changes affect:
1. **Passive recall pipeline** (`memory_hook.py`, `memory_bridge.py`) -- runs automatically via hooks, not agent-invoked
2. **Persona config** (`_base.md`) -- agent reads this on session start, no integration needed
3. **Extraction pipeline** (`memory_extraction.py`) -- runs post-session, not agent-invoked

The only agent-visible change is updated guidance in the "Intentional Memory" persona section showing retrieval recipes. This is documentation, not tool integration.

## Documentation

- [ ] Update `docs/features/subconscious-memory.md` to describe metadata-aware recall and category weighting
- [ ] Update `docs/features/claude-code-memory.md` to note bridge parity with new recall behavior
- [ ] Add retrieval recipe examples inline in `config/personas/_base.md` (part of the build, not separate doc task)

## Success Criteria

- [ ] `check_and_inject()` in `agent/memory_hook.py` applies category-weight re-ranking after ContextAssembler results
- [ ] Equivalent re-ranking implemented in `.claude/hooks/hook_utils/memory_bridge.py` (bridge parity)
- [ ] `config/personas/_base.md` "Intentional Memory" section includes retrieval examples using `--tag` and `--category`
- [ ] `extract_post_merge_learning()` outputs structured metadata (category, tags) matching post-session format
- [ ] `config/memory_defaults.py` includes `CATEGORY_RECALL_WEIGHTS` dict with configurable per-category multipliers
- [ ] All memory creation paths produce consistent metadata schema (verified by test)
- [ ] Retrieval latency remains under 15ms (existing warning log validates)
- [ ] All existing tests in `test_memory_hook.py`, `test_memory_extraction.py`, and `test_memory_bridge.py` pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (recall-pipeline)**
  - Name: recall-builder
  - Role: Implement category re-ranking in memory_hook.py and memory_bridge.py, add weight constants to memory_defaults.py
  - Agent Type: builder
  - Resume: true

- **Builder (extraction-parity)**
  - Name: extraction-builder
  - Role: Update post-merge extraction to output structured metadata, update persona config with retrieval recipes
  - Agent Type: builder
  - Resume: true

- **Validator (memory-system)**
  - Name: memory-validator
  - Role: Verify all recall, extraction, and bridge changes work end-to-end
  - Agent Type: validator
  - Resume: true

- **Documentarian (memory-docs)**
  - Name: memory-docs
  - Role: Update subconscious-memory.md and claude-code-memory.md feature docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add category recall weight constants
- **Task ID**: build-weights
- **Depends On**: none
- **Validates**: tests/unit/test_memory_hook.py (update)
- **Assigned To**: recall-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `CATEGORY_RECALL_WEIGHTS` dict to `config/memory_defaults.py` with keys: correction=1.5, decision=1.3, pattern=1.0, surprise=1.0, default=1.0
- Add `_apply_category_weights(records: list) -> list` function to `agent/memory_hook.py` that reads metadata.category, looks up weight, and re-sorts by weighted score
- Wire `_apply_category_weights()` into `check_and_inject()` after ContextAssembler results, before thought formatting

### 2. Bridge parity for category re-ranking
- **Task ID**: build-bridge-parity
- **Depends On**: build-weights
- **Validates**: tests/unit/test_memory_bridge.py (update)
- **Assigned To**: recall-builder
- **Agent Type**: builder
- **Parallel**: false
- Import and call `_apply_category_weights()` from `agent.memory_hook` in `memory_bridge.py` `recall()` function
- Ensure the same re-ranking logic runs in both SDK and hook paths

### 3. Post-merge extraction metadata parity
- **Task ID**: build-extraction
- **Depends On**: none
- **Validates**: tests/unit/test_memory_extraction.py (update)
- **Assigned To**: extraction-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `POST_MERGE_EXTRACTION_PROMPT` to request structured JSON output with category, tags, and file_paths fields
- Update `extract_post_merge_learning()` to parse JSON response and pass metadata dict to `Memory.safe_save()`
- Add fallback: if Haiku returns non-JSON, save as bare text with `metadata={"category": "decision"}` default
- Ensure saved memory includes metadata dict consistent with post-session extraction schema

### 4. Agent retrieval recipes in persona config
- **Task ID**: build-recipes
- **Depends On**: none
- **Assigned To**: extraction-builder
- **Agent Type**: builder
- **Parallel**: true
- Add "### When to Search" subsection to `config/personas/_base.md` after "### When NOT to Save"
- Include retrieval recipe examples: `python -m tools.memory_search search "query" --category correction`, `python -m tools.memory_search search "query" --tag redis`
- Document when agents should actively search vs relying on passive thought injection

### 5. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-weights, build-bridge-parity, build-extraction, build-recipes
- **Assigned To**: memory-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_memory_hook.py tests/unit/test_memory_extraction.py tests/unit/test_memory_bridge.py -v`
- Verify category weights are importable from `config.memory_defaults`
- Verify `_apply_category_weights` handles None metadata, missing category, and empty record list
- Verify post-merge extraction produces metadata dict with category key
- Run `python -m ruff check . && python -m ruff format --check .`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: memory-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` with category-weighted recall section
- Update `docs/features/claude-code-memory.md` to note bridge parity
- Verify docs reference the correct constant names and file paths

### 7. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: memory-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Run lint: `python -m ruff check .`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_memory_hook.py tests/unit/test_memory_extraction.py tests/unit/test_memory_bridge.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Weight constants exist | `python -c "from config.memory_defaults import CATEGORY_RECALL_WEIGHTS; assert 'correction' in CATEGORY_RECALL_WEIGHTS"` | exit code 0 |
| Re-rank function exists | `python -c "from agent.memory_hook import _apply_category_weights"` | exit code 0 |
| Post-merge metadata | `python -c "from agent.memory_extraction import POST_MERGE_EXTRACTION_PROMPT; assert 'category' in POST_MERGE_EXTRACTION_PROMPT"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. Should category weights be tunable per-project (via projects.json) or global-only? Current plan uses global constants in `memory_defaults.py`. Per-project weights add complexity but could be useful if different projects have different memory profiles.
2. The issue mentions a "placeholder for Popoto ORM enhancements" (item 5). Should the plan include any preparatory abstractions to make future ContextAssembler metadata filtering easier to adopt, or is that premature?
