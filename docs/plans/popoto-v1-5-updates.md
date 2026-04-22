---
status: Planning
type: chore
appetite: Small
owner: valorengels
created: 2026-04-22
tracking: https://github.com/valorengels/ai/issues/1110
last_comment_id:
---

# Popoto v1.5.0 Integration: `"used"` Outcome, `RetrievalQuality`, and `error_summary`

## Problem

The ai repo integrates Popoto as its Redis ORM for the subconscious memory system. Popoto v1.5.0 shipped four additive features; this repo currently ignores all of them, and one causes active data corruption.

**Current behavior:**
- `_judge_outcomes_llm()` in `agent/memory_extraction.py:547` accepts only `"acted"` and `"dismissed"`, silently coercing any other string (including the new `"used"` outcome) to `"dismissed"`. Memories that were consumed but didn't drive a decision receive incorrect negative confidence updates.
- `OUTCOME_JUDGMENT_PROMPT` (line 440) does not offer `"used"` as an option, so the LLM cannot produce it even if it would be the correct classification.
- `_update_memory_metadata()` has no `"used"` branch; `dismissal_count` is incorrectly incremented for consumed memories.
- `tools/memory_search.search()` has no `assess_quality` path — the `RetrievalQuality` metacognitive layer is entirely untapped.
- `Memory.error_summary()` (via `PredictionLedgerMixin`) is untested; the v1.5.0 bugfix for the `group_by=None` edge case has no coverage.

**Desired outcome:**
- `"used"` is a first-class outcome throughout the pipeline: LLM prompt, coercion guard, metadata update.
- `search(assess_quality=True)` returns a `"quality"` key with a `RetrievalQuality` dict.
- `Memory.error_summary()` with no predictions returns the expected empty stats dict; test guards the edge case.
- The installed popoto version is bumped to `>=1.5.0`.

## Freshness Check

**Baseline commit:** `c2af09602f9997b935f2cbe651488f98566cedb9`
**Issue filed at:** 2026-04-22T04:34:48Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/memory_extraction.py:440-449` — `OUTCOME_JUDGMENT_PROMPT` offers only `"acted"`, `"echoed"`, `"dismissed"` — still holds
- `agent/memory_extraction.py:547` — `elif outcome not in ("acted", "dismissed"): outcome = "dismissed"` — still holds
- `agent/memory_extraction.py:633,649` — only `"dismissed"` and `"acted"` branches in `_update_memory_metadata()` — still holds
- `tools/memory_search/__init__.py:51` — `search()` signature has no `assess_quality` param — still holds
- `tests/unit/test_memory_model.py` — no `error_summary` or `PredictionLedgerMixin` coverage — still holds

**Cited sibling issues/PRs re-checked:**
- #394 — closed 2026-03-24, merged PR #515 (Subconscious Memory) — established the Memory model and retrieval pipeline this issue extends; no conflict
- #393 — closed 2026-03-25, merged PR #517 — Behavioral Episode Memory on top of Memory stack; no overlap

**Commits on main since issue was filed (touching referenced files):**
- None — issue was filed today; referenced files are unmodified

**Active plans in `docs/plans/` overlapping this area:** none — no active plans touch `memory_extraction.py`, `memory_search`, or `models/memory.py`

**Notes:** Discovered during freshness check that popoto v1.5.0 source defines 5 valid outcomes (`acted`, `dismissed`, `deferred`, `contradicted`, `used`). The ai repo's `ObservationProtocol.on_context_used()` call only ever receives `"acted"` or `"dismissed"` today, so there's no runtime `ValueError` risk from the gap — but the `"used"` signal is silently discarded.

## Prior Art

- **#598** (closed 2026-03-31): Add BM25Field + RRF fusion — established the retrieval pipeline. No outcome-judgment work.
- **#583** (closed 2026-03-28): Structured metadata + effectiveness tracking — added `outcome_history`, `dismissal_count`, `act_rate`. Established the `"acted"`/`"dismissed"` binary that this plan extends.
- **#613** (closed 2026-03-31): Outcome tracking + routine compression — added `detect_outcomes_async`, ObservationProtocol wiring. The binary outcome model was intentional at the time; `"used"` didn't exist in popoto pre-1.5.0.

No prior attempts to add `"used"` or `assess_quality`. All changes are net-new.

## Research

**Queries used:**
- `popoto python redis ORM 1.5.0 changelog ObservationProtocol "used" outcome RetrievalQuality 2026`

**Key findings:**
- Popoto v1.5.0 is available on PyPI (`pip index versions popoto` confirms). Web search returned no changelog detail, but the local popoto source at `/Users/valorengels/src/popoto/` (which matches the published package) was read directly.
- `VALID_OUTCOMES` in `popoto/fields/observation.py` is `{"acted", "dismissed", "deferred", "contradicted", "used"}`. `on_context_used()` raises `ValueError` for any string not in this set — so our coercion to `"dismissed"` is currently safe, but adding `"used"` to our pipeline is straightforward.
- `_apply_used()` confirms staged reads (AccessTrackerMixin) and calls `PredictionLedgerMixin.auto_resolve(instance, "used")`. It does NOT touch `ConfidenceField`, `CyclicDecayField`, or `DecayingSortedField`. This is exactly the semantics we want: consumed but not acted on.
- `ContextAssembler(model_class=Memory, score_weights={"relevance": 0.6, "confidence": 0.3})` is the minimal constructor; `assess({"query": text})` returns a `RetrievalQuality` dataclass with `avg_confidence`, `score_spread`, `fok_score`, `staleness_ratio`.
- `PredictionLedgerMixin.error_summary(Memory, partition="default")` returns `{"__all__": stats_dict}` with `count=0` when the error set is empty; does not raise.

## Data Flow

**"used" outcome through the pipeline:**

1. **LLM judge** (`_judge_outcomes_llm`): prompt now offers `"used"` as a choice; LLM returns `"used"` in JSON.
2. **Coercion guard** (line 547): guard now allows `"acted" | "used" | "dismissed"` (echoed still maps to dismissed).
3. **`detect_outcomes_async`** → collects `{memory_key: "used"}` in `outcome_map`.
4. **`ObservationProtocol.on_context_used(memories, outcome_map)`**: popoto's `_apply_used()` confirms staged reads and auto-resolves PredictionLedger with moderate error (0.3). No confidence/cycle effects.
5. **`_update_memory_metadata(memories, outcome_map)`**: new `"used"` branch appends to `outcome_history`, sets `last_outcome = "used"`, leaves `dismissal_count` unchanged.
6. **Persisted** to Redis via `m.save()`.

**`assess_quality` through the search path:**

1. **Caller**: `search("deploy pipeline", assess_quality=True)`
2. **`search()`**: runs existing BM25+RRF retrieval as before.
3. **Post-retrieval**: if `assess_quality=True`, instantiate `ContextAssembler(Memory, score_weights)` and call `assembler.assess({"query": query})`.
4. **Return dict**: `{"results": [...], "error": None, "quality": {"avg_confidence": ..., "score_spread": ..., ...}}`

## Architectural Impact

- **New dependencies**: none — `ContextAssembler` and `RetrievalQuality` are already in `popoto>=1.5.0`, which this plan requires.
- **Interface changes**: `search()` gains an optional `assess_quality: bool = False` kwarg — fully backward-compatible (default=False).
- **Coupling**: adds a direct reference to `ContextAssembler` inside `tools/memory_search/__init__.py`. This is reasonable; the memory search module already imports from `models.memory`.
- **Data ownership**: no change. Memory records own their own confidence/history.
- **Reversibility**: trivial — remove the `"used"` branch and `assess_quality` param.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| popoto v1.5.0 available | `pip index versions popoto 2>/dev/null \| grep 1.5.0` | New outcome + RetrievalQuality APIs |

Run all checks: `python scripts/check_prerequisites.py docs/plans/popoto-v1-5-updates.md`

## Solution

### Key Elements

- **Version bump**: `pyproject.toml` → `popoto>=1.5.0`; re-lock with `uv lock`.
- **Outcome pipeline**: `OUTCOME_JUDGMENT_PROMPT` + coercion guard + `_update_memory_metadata()` all recognize `"used"`.
- **Quality probe**: `search(assess_quality=True)` calls `ContextAssembler.assess()` and attaches result as `"quality"` in return dict.
- **Test coverage**: three new unit tests covering `"used"` pipeline, `error_summary()`, and `assess_quality`.

### Flow

`search(query, assess_quality=True)` → BM25+RRF retrieval (unchanged) → `ContextAssembler.assess({"query": query})` → `{"results": [...], "quality": RetrievalQuality.__dict__}`

`LLM judge` → `"used"` in JSON → coercion guard passes it through → `ObservationProtocol.on_context_used(memories, {"key": "used"})` → `_apply_used()` (confirm reads, moderate PredictionLedger error) → `_update_memory_metadata()` (`last_outcome="used"`, `dismissal_count` unchanged)

### Technical Approach

1. **`pyproject.toml`**: change `"popoto>=1.4.4"` to `"popoto>=1.5.0"`. Run `uv lock` to update lock file.

2. **`agent/memory_extraction.py`**:
   - `OUTCOME_JUDGMENT_PROMPT`: add `"used"` option with definition: `"used" — agent consumed the memory (read + reasoned) but it did not drive the response`. Update the JSON schema line to include `"used"` alongside `"acted"`, `"echoed"`, `"dismissed"`.
   - Coercion guard (line ~547): change `elif outcome not in ("acted", "dismissed"):` to `elif outcome not in ("acted", "used", "dismissed"):`. Keep the `"echoed"` → `"dismissed"` mapping above it unchanged.
   - `_update_memory_metadata()`: add `elif outcome == "used":` branch after the `"dismissed"` block — set `meta["last_outcome"] = "used"`, leave `dismissal_count` untouched. Add inline comments at each branch explaining the semantics.
   - Docstring for `_update_memory_metadata()`: update `outcome_map` type to `"acted"|"used"|"dismissed"`.

3. **`tools/memory_search/__init__.py`**:
   - Add `assess_quality: bool = False` to `search()` signature and docstring.
   - After the existing retrieval block (before the final return), if `assess_quality=True`: import `ContextAssembler` from `popoto.recipes`, instantiate with `Memory` and `{"relevance": 0.6, "confidence": 0.3}` score weights, call `assembler.assess({"query": query})`, convert result to dict via `dataclasses.asdict()`, attach as `result["quality"]`. Wrap in try/except — quality probe failure must never break retrieval.
   - When `assess_quality=False` (default): return dict does NOT include `"quality"` key (no change to existing callers).

4. **`tests/unit/test_memory_extraction.py`**: add `test_used_outcome_not_remapped` — mock `_judge_outcomes_llm` to return `"used"`, assert the outcome survives through `detect_outcomes_async` without coercion to `"dismissed"`.

5. **`tests/unit/test_memory_model.py`**: add `test_error_summary_empty` — call `PredictionLedgerMixin.error_summary(Memory, partition="test-empty-{uuid}")` with no recorded predictions; assert result is a dict with `"__all__"` key and `count == 0`.

6. **`tests/unit/test_memory_retrieval.py`** (or create `tests/unit/test_memory_search_quality.py`): add `test_search_assess_quality_returns_quality_key` — call `search("test query", assess_quality=True)` and assert the returned dict has a `"quality"` key that is not `None`. Use the existing Redis test fixture.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_update_memory_metadata()` wraps each record in `except Exception: continue` — existing coverage in `test_memory_extraction.py`. The new `"used"` branch sits inside this guard; no additional exception test needed.
- [ ] `search()` quality probe wrapped in try/except — add assertion in `test_search_assess_quality_returns_quality_key` that the main `"results"` key is present even when `assess_quality=True` fails (simulate by mocking `ContextAssembler.assess` to raise).

### Empty/Invalid Input Handling
- [ ] `search("", assess_quality=True)` → existing early-return guard already handles empty query; `assess_quality` branch is never reached. Verified by reading `search()` lines 55-57.
- [ ] `error_summary(Memory, partition=<empty-partition>)` → returns `{"__all__": {count: 0, ...}}` per popoto source; covered by the new test.

### Error State Rendering
- [ ] `"quality"` is only returned when explicitly requested and quality probe succeeds; callers that don't pass `assess_quality=True` see no change. No user-visible error rendering needed.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py` — UPDATE: add `test_used_outcome_not_remapped`. No existing tests need modification — they don't cover the `"used"` outcome path.
- [ ] `tests/unit/test_memory_model.py` — UPDATE: add `test_error_summary_empty`. No existing tests removed.
- [ ] No existing tests break — all changes are additive (new outcome option, new optional param with default `False`).

## Rabbit Holes

- **`AdaptiveAssembler` integration**: the ai repo uses a custom BM25+RRF pipeline, not a vanilla `ContextAssembler.assemble()`. `AdaptiveAssembler` wraps `ContextAssembler` and cannot be dropped in without restructuring. Explicitly out of scope per issue.
- **`"deferred"` and `"contradicted"` outcomes**: popoto v1.5.0 defines two more outcomes beyond `"used"`. These require LLM prompt additions and semantic decisions that are out of scope for this plan.
- **Tuning `ContextAssembler` score_weights**: the `assess()` probe uses the same weights as the assembler. Calibrating these weights to match the RRF pipeline is a separate optimization concern.
- **`RetrievalQuality` in agent context injection**: using `fok_score` to skip retrieval when knowledge is absent is a follow-on feature, not part of this plan.

## Risks

### Risk 1: popoto v1.5.0 introduces a breaking API change not covered by our tests
**Impact:** Worker crashes on import or on first ObservationProtocol call.
**Mitigation:** Read the popoto source directly (done in Phase 0.7). `VALID_OUTCOMES` and all method signatures are backward-compatible. Unit tests run against the new version before shipping.

### Risk 2: `ContextAssembler` initialization fails at runtime due to missing field name
**Impact:** `search(assess_quality=True)` crashes instead of returning results.
**Mitigation:** Quality probe is wrapped in try/except; on failure, returns result without `"quality"` key. Test explicitly covers the failure path.

## Race Conditions

No race conditions identified — `_update_memory_metadata()` runs per-record with synchronous Redis saves; `search()` with `assess_quality=True` is a read-only probe (no writes).

## No-Gos (Out of Scope)

- `AdaptiveAssembler` integration (deferred to separate issue per recon)
- `"deferred"` and `"contradicted"` outcome support
- Using `RetrievalQuality.fok_score` to gate retrieval (follow-on feature)
- Tuning `ContextAssembler` score weights to match RRF pipeline
- Changing `compute_act_rate()` to account for `"used"` in the denominator (separate semantic question)

## Update System

`uv lock` is the only artifact that changes. The update script (`scripts/remote-update.sh`) runs `uv sync` on all machines, which will install popoto 1.5.0 automatically. No manual migration step needed.

## Agent Integration

The `search()` function is already exposed to the agent via `mcp_servers/memory_server.py`. The `assess_quality` parameter is optional with `default=False`; the agent's existing MCP tool signature is unchanged. No `.mcp.json` changes required.

If a future plan wants to expose retrieval quality to the agent, the MCP tool signature can be extended independently.

## Documentation

- [ ] Update `docs/features/subconscious-memory.md` to document the `"used"` outcome semantics and `assess_quality` parameter.
- [ ] Add a note to the Memory system section describing the three-tier outcome model: `"acted"` (drove response), `"used"` (consumed, no response), `"dismissed"` (ignored).

## Success Criteria

- [ ] `"used"` appears as a valid outcome option in `OUTCOME_JUDGMENT_PROMPT` with a one-line definition.
- [ ] `_judge_outcomes_llm()` does not remap `"used"` to `"dismissed"`.
- [ ] `_update_memory_metadata()` has a `"used"` branch that leaves `dismissal_count` unchanged.
- [ ] `search(query, assess_quality=True)` returns a `"quality"` key in the result dict.
- [ ] `test_used_outcome_not_remapped` passes.
- [ ] `test_error_summary_empty` passes — `error_summary()` returns `{"__all__": {...}}` with `count=0`.
- [ ] `test_search_assess_quality_returns_quality_key` passes.
- [ ] `pytest tests/unit/ -x -q` exits 0.
- [ ] `python -m ruff check .` exits 0.

## Team Orchestration

### Team Members

- **Builder (outcome-pipeline)**
  - Name: outcome-builder
  - Role: Implement `"used"` outcome in memory_extraction.py and bump popoto version
  - Agent Type: builder
  - Resume: true

- **Builder (assess-quality)**
  - Name: quality-builder
  - Role: Implement `assess_quality` in tools/memory_search/__init__.py
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: test-builder
  - Role: Write all three new unit tests
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Verify all success criteria, run full unit test suite
  - Agent Type: validator
  - Resume: false

### Available Agent Types

*(see plan template)*

## Step by Step Tasks

### 1. Bump popoto version and implement `"used"` outcome pipeline
- **Task ID**: build-outcome-pipeline
- **Depends On**: none
- **Parallel**: true
- **Assigned To**: outcome-builder
- **Agent Type**: builder
- In `pyproject.toml`: change `"popoto>=1.4.4"` to `"popoto>=1.5.0"`. Run `uv lock`.
- In `agent/memory_extraction.py`:
  - Add `"used"` to `OUTCOME_JUDGMENT_PROMPT` (after `"echoed"` line, before the closing); update the JSON schema hint to include `"used"`.
  - Update coercion guard: `elif outcome not in ("acted", "used", "dismissed"):` (keep `"echoed"` → `"dismissed"` mapping above).
  - Add `elif outcome == "used":` branch in `_update_memory_metadata()`: set `meta["last_outcome"] = "used"`, leave `dismissal_count` unchanged, append to `outcome_history` (already done by common block above).
  - Add inline comments at each outcome branch explaining semantics.
  - Update `_update_memory_metadata()` docstring: `outcome_map` type → `"acted"|"used"|"dismissed"`.

### 2. Implement `assess_quality` in `search()`
- **Task ID**: build-assess-quality
- **Depends On**: none
- **Parallel**: true
- **Assigned To**: quality-builder
- **Agent Type**: builder
- Add `assess_quality: bool = False` param to `tools/memory_search/__init__.py:search()`.
- Update docstring with param description and `"quality"` key in return shape.
- After existing retrieval block: if `assess_quality=True`, import `ContextAssembler` from `popoto.recipes`, call `assembler.assess({"query": query})`, convert to dict with `dataclasses.asdict()`, attach as `result["quality"]`. Wrap in try/except — failure must return result without `"quality"` key (not crash).

### 3. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-outcome-pipeline, build-assess-quality
- **Parallel**: false
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- `tests/unit/test_memory_extraction.py`: add `test_used_outcome_not_remapped` — mock `_judge_outcomes_llm` to return `{"memory_key": {"outcome": "used", "reasoning": "..."}}`, call `detect_outcomes_async`, assert the outcome in the returned map is `"used"` not `"dismissed"`.
- `tests/unit/test_memory_model.py`: add `test_error_summary_empty` — call `PredictionLedgerMixin.error_summary(Memory, partition=f"test-empty-{uuid4()}")`, assert result has `"__all__"` key and `result["__all__"]["count"] == 0`.
- `tests/unit/test_memory_retrieval.py`: add `test_search_assess_quality_returns_quality_key` — call `search("deploy", assess_quality=True)` and assert `"quality"` in result and result["quality"] is not None; also test that `search("deploy", assess_quality=False)` does NOT include `"quality"` key. Mock `ContextAssembler.assess` to raise in a third test; assert `"results"` still present (failure path).

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` — assert exit code 0.
- Run `python -m ruff check .` and `python -m ruff format --check .` — assert exit code 0.
- Verify all Success Criteria checkboxes are met.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `"used"` in prompt | `grep '"used"' agent/memory_extraction.py` | output contains `used` |
| `assess_quality` in search | `grep 'assess_quality' tools/memory_search/__init__.py` | output contains `assess_quality` |
| `"used"` branch in metadata update | `grep "last_outcome.*used" agent/memory_extraction.py` | output contains `last_outcome` |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — all scope is confirmed by recon and freshness check. Ready to build.
