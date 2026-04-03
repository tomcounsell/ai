---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/656
last_comment_id:
plan_branch: main
---

# Wire Artifact Inference into Dashboard Data Layer

## Problem

The dashboard data layer (`ui/data/sdlc.py`) builds pipeline stage data by reading only the stored `stage_states` field from Redis. When hook-based tracking fails to record a stage transition -- a known, common problem -- the dashboard shows that stage as "pending" even though the artifact proving completion exists (e.g., a plan file on disk, a PR on GitHub).

**Current behavior:**
`_session_to_pipeline()` calls `_parse_stage_states()` which reads only stored state. Its only fallback is `_infer_stages_from_history()`, a deprecated heuristic that parses `[stage]` text markers from session history. The dashboard shows less accurate pipeline progress than the merge gate, which calls `PipelineStateMachine.get_display_progress(slug=...)` with artifact inference.

**Desired outcome:**
The dashboard shows the same artifact-enriched pipeline state that the merge gate sees. When a stage's stored state is "pending" but the artifact exists, the dashboard reflects the inferred completion. The deprecated history fallback is removed.

## Prior Art

- **Issue #645 / PR #647**: Implicit pipeline stage tracking via observable artifacts -- Merged. Added `_infer_stage_from_artifacts()` and the `slug` parameter to `get_display_progress()`. Dashboard was explicitly out of scope.
- **Issue #430**: Replace transcript-based stage detection with programmatic state machine -- Merged. Introduced `PipelineStateMachine` and hook-based tracking. The deprecated `_infer_stages_from_history()` is a remnant from before this work.

## Data Flow

1. **Entry point**: Dashboard HTTP request hits `get_all_sessions()` or `get_pipeline_detail()` in `ui/data/sdlc.py`
2. **Session query**: `AgentSession.query.all()` loads all sessions from Redis
3. **Conversion**: Each session passes through `_session_to_pipeline()` which calls `_parse_stage_states(session.stage_states)` to produce `StageState` objects
4. **Gap (current)**: If `stage_states` is empty, falls back to `_infer_stages_from_history()` which only checks text markers in session history
5. **Gap (fix)**: After parsing stored state, call `PipelineStateMachine.get_display_progress(slug=slug)` for sessions with a slug, merging artifact-inferred state into gaps
6. **Output**: `PipelineProgress` Pydantic model returned to FastAPI route handler for JSON rendering

## Architectural Impact

- **New dependencies**: `_session_to_pipeline()` gains a dependency on `PipelineStateMachine` from `bridge/pipeline_state.py`. This is already imported by other `ui/` modules indirectly through the models layer.
- **Interface changes**: No public API changes. `_session_to_pipeline()` is an internal function. The `PipelineProgress` model is unchanged.
- **Coupling**: Slightly increases coupling between dashboard data layer and `PipelineStateMachine`, but this is intentional -- the dashboard should use the same source of truth as the merge gate.
- **Data ownership**: No change. Stage state ownership remains with `PipelineStateMachine`.
- **Reversibility**: Fully reversible -- remove the `PipelineStateMachine` call and restore `_infer_stages_from_history()`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. `PipelineStateMachine` and `_infer_stage_from_artifacts()` already exist and are tested.

## Solution

### Key Elements

- **Artifact-enriched stage conversion**: `_session_to_pipeline()` calls `PipelineStateMachine.get_display_progress(slug=slug)` when a slug is available, using the enriched result instead of raw `_parse_stage_states()` output
- **TTL cache for artifact inference**: A module-level cache keyed by slug with a short TTL (30 seconds) to avoid repeated `gh pr view` subprocess calls when rendering multiple sessions on the list view
- **Deprecated code removal**: Delete `_infer_stages_from_history()` and all call sites

### Flow

**Dashboard request** -> `get_all_sessions()` -> `_session_to_pipeline(session)` -> `PipelineStateMachine(session).get_display_progress(slug=slug)` -> artifact-enriched `StageState` list -> `PipelineProgress` model -> JSON response

### Technical Approach

1. **In `_session_to_pipeline()`**, after extracting the slug, instantiate `PipelineStateMachine(session)` and call `get_display_progress(slug=slug)` when slug is non-empty. Convert the returned `{stage: status}` dict into `StageState` objects. When slug is empty, fall back to `_parse_stage_states()` only (no history fallback).

2. **Caching strategy**: Wrap `_infer_stage_from_artifacts()` output in a module-level dict cache keyed by `(slug, int(time.time() / 30))` so the same slug within a 30-second window reuses the cached result. This means a list view rendering 20 sessions with slugs makes at most 20 `gh pr view` calls, but subsequent page loads within 30 seconds make zero. The cache is a simple dict with bounded size (evict entries older than 60 seconds).

3. **Delete `_infer_stages_from_history()`** and the fallback block in `_session_to_pipeline()` that calls it. Legacy sessions without `stage_states` will simply show empty stages, which is acceptable since those sessions are months old and no longer actionable.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `PipelineStateMachine.__init__()` handles invalid `stage_states` JSON gracefully -- already tested, but verify the dashboard path catches any unexpected exceptions from instantiation
- [ ] The cache wrapper must not propagate exceptions from `_infer_stage_from_artifacts()` -- subprocess failures should result in stored-state-only display, not dashboard crashes

### Empty/Invalid Input Handling
- [ ] Sessions with no slug get `_parse_stage_states()` only -- no artifact inference attempted
- [ ] Sessions with empty string slug are treated the same as no slug
- [ ] Sessions with no `stage_states` and no slug produce an empty stages list (no crash)

### Error State Rendering
- [ ] When artifact inference fails (e.g., `gh` not available), the dashboard degrades gracefully to stored state only -- no error visible to the user

## Test Impact

- [ ] `tests/unit/test_ui_sdlc_data.py::TestHistoryFallback::test_infer_empty_history` -- DELETE: tests removed function
- [ ] `tests/unit/test_ui_sdlc_data.py::TestHistoryFallback::test_infer_no_stage_entries` -- DELETE: tests removed function
- [ ] `tests/unit/test_ui_sdlc_data.py::TestHistoryFallback::test_infer_single_stage` -- DELETE: tests removed function
- [ ] `tests/unit/test_ui_sdlc_data.py::TestHistoryFallback::test_infer_multiple_stages` -- DELETE: tests removed function
- [ ] `tests/unit/test_ui_sdlc_data.py::TestHistoryFallback::test_session_to_pipeline_uses_history_fallback` -- REPLACE: rewrite to test artifact inference via `PipelineStateMachine.get_display_progress()` mock

## Rabbit Holes

- Modifying `_infer_stage_from_artifacts()` itself (e.g., adding new artifact checks) -- out of scope, this issue is about wiring existing inference into the dashboard
- Making the cache distributed or Redis-backed -- a simple in-memory dict is sufficient for a single-process dashboard
- Optimizing `gh pr view` calls to batch multiple slugs -- the TTL cache is sufficient for normal dashboard usage patterns

## Risks

### Risk 1: List view latency from `gh pr view` calls
**Impact:** First dashboard load after cache expiry could be slow if many sessions have slugs
**Mitigation:** 30-second TTL cache means subsequent loads are fast. The `gh pr view` call already has a 5-second timeout per call. Sessions without slugs skip artifact inference entirely. If still too slow, the cache TTL can be increased.

### Risk 2: `PipelineStateMachine` import coupling
**Impact:** If `bridge/pipeline_state.py` changes its interface, dashboard could break
**Mitigation:** `get_display_progress()` is a stable public API used by the merge gate. Interface changes would break the merge gate first, making them highly visible.

## Race Conditions

No race conditions identified -- `_session_to_pipeline()` is synchronous and read-only. The in-memory cache is only accessed from the main thread (FastAPI runs sync handlers in a threadpool, but the cache is per-worker and eventual consistency is acceptable for display data).

## No-Gos (Out of Scope)

- Adding new artifact checks to `_infer_stage_from_artifacts()`
- Making artifact inference real-time (WebSocket push on stage change)
- Backfilling `stage_states` on legacy sessions
- Modifying the dashboard UI rendering (only the data layer changes)

## Update System

No update system changes required -- this feature modifies only `ui/data/sdlc.py` and its tests. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this is a dashboard-internal change. No MCP servers, bridge imports, or `.mcp.json` changes needed.

## Documentation

- [ ] Update `docs/features/web-dashboard.md` to document that the dashboard now uses artifact inference for pipeline stage display
- [ ] Update `docs/features/pipeline-graph.md` to note that artifact inference is used by both the merge gate and the dashboard

### Inline Documentation
- [ ] Docstring updates on `_session_to_pipeline()` to document artifact inference behavior
- [ ] Cache module docstring explaining TTL strategy

## Success Criteria

- [ ] `_session_to_pipeline()` uses `PipelineStateMachine.get_display_progress(slug=slug)` for sessions with a slug
- [ ] Dashboard stage indicators reflect artifact-inferred completions (e.g., session with plan file shows PLAN as completed even if `stage_states` has it as "pending")
- [ ] List view performance is acceptable: cached `gh pr view` calls with 30-second TTL
- [ ] `_infer_stages_from_history()` is fully removed from `ui/data/sdlc.py`
- [ ] Sessions without slugs still render correctly (no regression)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (dashboard-data)**
  - Name: dashboard-builder
  - Role: Wire artifact inference into `_session_to_pipeline()`, add cache, remove deprecated code
  - Agent Type: builder
  - Resume: true

- **Validator (dashboard-data)**
  - Name: dashboard-validator
  - Role: Verify artifact inference integration and cache behavior
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Wire artifact inference and add cache
- **Task ID**: build-artifact-inference
- **Depends On**: none
- **Validates**: tests/unit/test_ui_sdlc_data.py
- **Assigned To**: dashboard-builder
- **Agent Type**: builder
- **Parallel**: true
- Add artifact inference cache (module-level dict, 30s TTL, keyed by slug)
- In `_session_to_pipeline()`, after extracting slug, instantiate `PipelineStateMachine(session)` and call `get_display_progress(slug=slug)` when slug is non-empty
- Convert returned `{stage: status}` dict into `StageState` objects using existing `_parse_stage_states()` pattern
- Delete `_infer_stages_from_history()` function entirely
- Remove the history fallback block in `_session_to_pipeline()`
- Update tests: delete `TestHistoryFallback` tests for removed function, add new tests for artifact inference integration (mock `PipelineStateMachine.get_display_progress`)
- Add cache tests: verify cache hit within TTL, cache miss after TTL

### 2. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-artifact-inference
- **Assigned To**: dashboard-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `_infer_stages_from_history` has zero references in codebase
- Run full test suite: `pytest tests/unit/test_ui_sdlc_data.py -v`
- Verify no import errors in `ui/data/sdlc.py`
- Confirm `PipelineStateMachine` is correctly imported and used

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: dashboard-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/web-dashboard.md` with artifact inference details
- Update `docs/features/pipeline-graph.md` with dashboard usage note

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: dashboard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full unit test suite
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_ui_sdlc_data.py -v` | exit code 0 |
| Lint clean | `python -m ruff check ui/data/sdlc.py` | exit code 0 |
| Format clean | `python -m ruff format --check ui/data/sdlc.py` | exit code 0 |
| No deprecated function | `grep -rn '_infer_stages_from_history' ui/` | exit code 1 |
| Artifact inference imported | `grep -n 'PipelineStateMachine' ui/data/sdlc.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the solution is well-scoped and all components already exist.
