---
status: Merged
type: bug
appetite: Medium
owner: Valor
created: 2026-03-30
tracking: https://github.com/tomcounsell/ai/issues/563
last_comment_id:
---

# Wire SDLC Pipeline Graph into Runtime

## Problem

The SDLC pipeline has a graph-based routing system (`PIPELINE_EDGES` in `bridge/pipeline_graph.py`) that is well-designed, well-tested, and completely disconnected from the runtime execution path. Three separate routing implementations exist, only two are used at runtime, and neither uses the graph.

**Current behavior:**
- `_build_sdlc_stage_coaching()` in `bridge/coach.py` [NOTE: `bridge/coach.py` was deleted by PR #661; coaching logic moved to `bridge/session_coaching.py`] does a linear scan of `DISPLAY_STAGES` to find the first "pending" stage, ignoring the graph and its failure/cycle edges entirely
- `_record_stage_on_parent()` in `agent/hooks/subagent_stop.py` always calls `complete_stage()`, never `fail_stage()` -- failed dev-sessions (test failures, review rejections) are recorded as successes
- `classify_outcome()` in `bridge/pipeline_state.py` exists but is never called in production -- outcome detection from stop_reason and output patterns is dead code
- `fail_stage()` exists but is never called in production -- the PATCH cycle path is dead code
- `stage_states` is not initialized at session creation, so the dashboard shows no pipeline progress until a dev-session starts
- `_infer_stages_from_history()` in `ui/data/sdlc.py` exists as a workaround for missing `stage_states`

**Observable consequence:** PR #595 went through REVIEW which found tech debt, but the pipeline classified REVIEW as "success" and skipped directly to DOCS -> MERGE, bypassing the PATCH step. The `("REVIEW", "partial"): "PATCH"` edge in `PIPELINE_EDGES` never fired.

**Desired outcome:**
- Every stage completion is classified (success/fail/partial) using `classify_outcome()` before routing
- Failed stages trigger `fail_stage()` which routes through the graph to PATCH or escalation
- The coach uses the graph API (`sm.next_stage(outcome)`) instead of linear scanning
- `stage_states` is initialized at session creation and validated at every write
- The dashboard inference fallback is removed

## Prior Art

- **PR #433**: Replace inference-based stage tracking with PipelineStateMachine -- Merged. Introduced PipelineStateMachine as canonical tracking. Only the happy path (`complete_stage`) was wired.
- **PR #492 (issue #492)**: Wire PipelineStateMachine.start_stage() into SDLC dispatch -- Merged. Connected `start_stage()` to the pre_tool_use hook. Start path works correctly.
- **PR #490 (issue #490)**: Consolidate SDLC stage tracking, remove legacy fields -- Merged. Cleaned up old tracking in favor of PipelineStateMachine.

## Data Flow

The current (broken) flow when a dev-session completes:

1. **Entry point**: Dev session finishes execution, SDK fires `subagent_stop` hook
2. **`subagent_stop_hook()`** (`agent/hooks/subagent_stop.py`): Calls `_register_dev_session_completion()` which calls `_record_stage_on_parent()`
3. **`_record_stage_on_parent()`**: Loads parent PM session, creates `PipelineStateMachine`, finds `current_stage()`, **always calls `complete_stage()`** regardless of actual outcome
4. **Coach** (`bridge/coach.py` [deleted by PR #661; now `bridge/session_coaching.py`]): On next auto-continue, `_build_sdlc_stage_coaching()` scans `DISPLAY_STAGES` linearly for the first "pending" stage, ignoring graph edges
5. **Dashboard** (`ui/data/sdlc.py`): If `stage_states` is empty, falls back to `_infer_stages_from_history()` heuristic

The correct flow after this fix:

1. **Entry point**: Same -- Dev session finishes, SDK fires `subagent_stop` hook
2. **`subagent_stop_hook()`**: Calls `_register_dev_session_completion()` with stop_reason and output_tail
3. **`_record_stage_on_parent()`**: Loads parent, creates `PipelineStateMachine`, calls `classify_outcome(stage, stop_reason, output_tail)` to get "success"/"fail"/"ambiguous", then routes to `complete_stage()` or `fail_stage()` accordingly
4. **Coach**: `_build_sdlc_stage_coaching()` uses `sm.next_stage(outcome)` to determine the next stage from the graph, respecting failure edges and cycle counts
5. **Dashboard**: Reads `stage_states` directly (no inference fallback needed because states are initialized at session creation)

## Architectural Impact

- **Interface changes**: `_record_stage_on_parent()` gains `stop_reason` and `output_tail` parameters; `subagent_stop_hook()` passes these through
- **Coupling**: Reduces coupling -- coach and dashboard stop reimplementing routing logic and defer to the graph API
- **Data ownership**: `StageStates` Pydantic model becomes the shared validation layer, promoted from dashboard-only to `bridge/pipeline_state.py`
- **Reversibility**: High -- all changes are internal routing, no external API or data format changes

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on ambiguous outcome handling)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. The Popoto KeyField migration bug (tomcounsell/popoto#298) is a pre-existing issue that does not block this work since `stage_states` is a regular Field, not a KeyField.

## Solution

### Key Elements

- **Shared `StageStates` Pydantic model**: Promote from `ui/data/sdlc.py` to `bridge/pipeline_state.py`. Validate at read/write boundaries in PipelineStateMachine.
- **Outcome classification in subagent_stop**: Wire `classify_outcome()` into the dev-session completion path to determine success/fail/partial before routing.
- **fail_stage() activation**: When `classify_outcome()` returns "fail" or "partial", call `fail_stage()` to route through PATCH cycles.
- **Graph-based coach routing**: Replace linear `DISPLAY_STAGES` scan with `sm.next_stage(outcome)` call.
- **Eager stage_states initialization**: Initialize when SDLC sessions are created (ISSUE=ready, all others=pending).
- **Dashboard cleanup**: Remove `_infer_stages_from_history()` fallback.

### Flow

**Dev session completes** -> classify_outcome(stage, stop_reason, output_tail) -> **success**: complete_stage() -> next_stage(success) -> **Coach sends next skill**

**Dev session completes** -> classify_outcome(stage, stop_reason, output_tail) -> **fail**: fail_stage() -> next_stage(fail) -> **Coach sends PATCH skill**

**classify_outcome returns ambiguous** -> default to "success" (do not crash or escalate)

### Technical Approach

1. Promote `StageState` Pydantic model to `bridge/pipeline_state.py`, add `StageStates` container model with validation (allowed stage names, allowed statuses)
2. Add `_validate_states()` call in `PipelineStateMachine._save()` to enforce Pydantic validation at write time
3. Modify `_record_stage_on_parent()` to accept `stop_reason` and `output_tail`, call `classify_outcome()`, and branch on result
4. Modify `subagent_stop_hook()` to extract stop_reason from `input_data` and output_tail from the outcome summary, pass both to `_record_stage_on_parent()`
5. Rewrite `_build_sdlc_stage_coaching()` to use `PipelineStateMachine.next_stage()` instead of linear scan
6. Add stage_states initialization in `AgentSession.create_chat()` for SDLC sessions (when `classification_type == "sdlc"`) and in the bridge intake path when classification is determined
7. Remove `_infer_stages_from_history()` from `ui/data/sdlc.py` and its call site in `_session_to_pipeline()`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_record_stage_on_parent()` wraps everything in try/except -- test that `classify_outcome()` errors are caught and default to `complete_stage()` (not crash)
- [ ] `PipelineStateMachine._save()` validation errors -- test that invalid states log warning but do not crash the hook
- [ ] `_build_sdlc_stage_coaching()` -- test that `PipelineStateMachine` constructor errors fall through to lower coaching tiers

### Empty/Invalid Input Handling
- [ ] `classify_outcome()` with empty output_tail and None stop_reason returns "ambiguous"
- [ ] `classify_outcome()` with "ambiguous" result defaults to success path (not crash)
- [ ] `StageStates` model handles unknown stage names gracefully (drops them, does not crash)
- [ ] `StageStates` model handles unknown status values (treats as "pending")

### Error State Rendering
- [ ] Dashboard renders "failed" stage status correctly (already handled by existing StageState model)
- [ ] Coach produces valid coaching message when a stage is failed (not just pending/completed)

## Test Impact

- [ ] `tests/unit/test_subagent_stop_hook.py::TestRegisterDev sessionCompletion` -- UPDATE: `_record_stage_on_parent()` gains new parameters; tests must pass stop_reason and output_tail
- [ ] `tests/unit/test_subagent_stop_hook.py::TestSubagentStopHookDev session` -- UPDATE: mock the new `classify_outcome` call path
- [ ] `tests/unit/test_coach.py::TestSdlcStageCoaching` -- UPDATE: `_build_sdlc_stage_coaching()` changes from linear scan to graph-based; test inputs may need stage_states dict instead of simple progress dict
- [ ] `tests/unit/test_pipeline_state_machine.py` -- UPDATE: add tests for Pydantic validation at write boundaries
- [ ] `tests/unit/test_pipeline_state.py` -- UPDATE: verify `classify_outcome` integration tests still pass with new wiring

## Rabbit Holes

- **Making the `/sdlc` skill call Python graph APIs**: The skill is a Markdown prompt read by PM session, not Python code. Graph enforcement happens in hooks and coach, not in the skill.
- **Rewriting classify_outcome() with LLM classification**: The deterministic pattern-matching approach is sufficient and predictable. LLM classification would add latency and non-determinism.
- **Changing the stage_states JSON format**: Must remain backward-compatible with existing Redis data. The Pydantic model validates but does not change the serialization format.
- **Adding UI for PATCH stage visualization**: PATCH is intentionally a routing-only stage excluded from DISPLAY_STAGES. Dashboard changes beyond removing the inference fallback are out of scope.

## Risks

### Risk 1: Overly aggressive failure classification
**Impact:** Legitimate successes classified as failures, triggering unnecessary PATCH cycles that waste compute
**Mitigation:** "ambiguous" outcome defaults to "success", not "fail". Only clear failure patterns (SDK non-end_turn stop_reason, explicit "failed"/"error" in output) trigger fail_stage(). Add logging so misclassifications are observable.

### Risk 2: Backward incompatibility with existing stage_states in Redis
**Impact:** Old sessions with current format fail to parse, dashboard breaks for in-flight work
**Mitigation:** Pydantic model uses permissive parsing -- unknown keys are dropped, unknown statuses default to "pending". PipelineStateMachine constructor already handles None, empty string, dict, and JSON string formats.

### Risk 3: Infinite PATCH cycles
**Impact:** Agent loops forever between TEST->PATCH->TEST
**Mitigation:** Already handled by `MAX_PATCH_CYCLES = 3` in `pipeline_graph.py`. When limit is reached, `get_next_stage()` returns None and the coach escalates to human.

## Race Conditions

### Race 1: Concurrent stage_states writes from subagent_stop and coach
**Location:** `bridge/pipeline_state.py` `_save()`, `agent/hooks/subagent_stop.py`
**Trigger:** Coach reads stage_states, subagent_stop writes completion, coach overwrites with stale state
**Data prerequisite:** stage_states must reflect the latest completion before the coach reads it
**State prerequisite:** subagent_stop must finish before coach evaluates next stage
**Mitigation:** The existing architecture prevents this -- subagent_stop fires synchronously within the SDK hook before control returns to the PM session, which then triggers the coach. These are sequential, not concurrent.

## No-Gos (Out of Scope)

- Making the `/sdlc` Markdown skill call Python APIs directly -- graph enforcement is in hooks and coach
- LLM-based outcome classification -- deterministic patterns are sufficient
- Dashboard UI changes beyond removing inference fallback
- Changing the stage_states JSON serialization format
- Handling the Popoto KeyField migration bug (tomcounsell/popoto#298) -- separate issue

## Update System

No update system changes required -- this feature is purely internal bridge/hook logic. No new dependencies, config files, or migration steps needed.

## Agent Integration

No agent integration required -- this is a bridge-internal change. The modifications are in hooks (`subagent_stop.py`, `pre_tool_use.py`), the coach (`bridge/session_coaching.py`, formerly `bridge/coach.py` which was deleted by PR #661), and the pipeline state machine (`bridge/pipeline_state.py`). No MCP server changes, no `.mcp.json` changes, no new tools exposed to the agent.

## Documentation

- [ ] Update `docs/features/sdlc-stage-handoff.md` to document the classify_outcome -> fail_stage flow
- [ ] Add entry to `docs/features/README.md` index table if not already present
- [ ] Update docstrings on `_record_stage_on_parent()`, `_build_sdlc_stage_coaching()`, and `classify_outcome()`

## Success Criteria

- [ ] A shared `StageStates` Pydantic model validates stage_states JSON at all read/write boundaries in PipelineStateMachine
- [ ] `subagent_stop.py` calls `classify_outcome()` on dev-session completion and routes to `complete_stage()` or `fail_stage()` based on the result
- [ ] `fail_stage()` is called in production when outcome is "fail" or "partial", triggering PATCH cycles via `PIPELINE_EDGES`
- [ ] `_build_sdlc_stage_coaching()` uses `sm.next_stage(outcome)` instead of linear DISPLAY_STAGES scan
- [ ] SDLC sessions have `stage_states` initialized (ISSUE=ready, all others=pending) at creation time
- [ ] `_infer_stages_from_history()` is removed from the dashboard data layer
- [ ] Regression test: a REVIEW with tech_debt findings triggers PATCH, not DOCS
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (pipeline-wiring)**
  - Name: pipeline-builder
  - Role: Wire classify_outcome, fail_stage, graph-based coach, stage_states init, remove dashboard inference
  - Agent Type: builder
  - Resume: true

- **Validator (pipeline-wiring)**
  - Name: pipeline-validator
  - Role: Verify all seven acceptance criteria, run regression tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Promote StageStates Pydantic model
- **Task ID**: build-pydantic-model
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_state_machine.py (update)
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- Move `StageState` from `ui/data/sdlc.py` to `bridge/pipeline_state.py` (keep import alias in ui/data/sdlc.py for backward compat)
- Add `StageStates` container model with validation: allowed stage names from `ALL_STAGES`, allowed statuses from `VALID_STATUSES`
- Add `_validate_states()` call in `PipelineStateMachine._save()` before JSON serialization
- Ensure backward compatibility: unknown keys dropped, unknown statuses default to "pending"

### 2. Wire classify_outcome into subagent_stop
- **Task ID**: build-classify-outcome
- **Depends On**: none
- **Validates**: tests/unit/test_subagent_stop_hook.py (update), tests/unit/test_pipeline_state_machine.py
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- Modify `_record_stage_on_parent()` to accept `stop_reason` and `output_tail` parameters
- Call `sm.classify_outcome(current_stage, stop_reason, output_tail)` before deciding complete vs fail
- Route: "success" -> `complete_stage()`, "fail"/"partial" -> `fail_stage()`, "ambiguous" -> `complete_stage()` (safe default)
- **CRITIQUE FIX (blocker):** `SubagentStopHookInput` does NOT have a `stop_reason` field. Instead: (a) pass `stop_reason=None` by default (classify_outcome already handles None), and (b) extract output_tail by reading the last 500 chars from `input_data["agent_transcript_path"]` if the file exists, falling back to `_extract_outcome_summary()` output. Create a new `_extract_output_tail(input_data, max_chars=500)` helper that reads the transcript file tail.
- Wrap classify_outcome call in try/except -- on error, default to complete_stage()

### 3. Replace coach linear scan with graph API
- **Task ID**: build-coach-graph
- **Depends On**: none
- **Validates**: tests/unit/test_coach.py (update)
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite `_build_sdlc_stage_coaching()` to accept a session or stage_states dict
- Create `PipelineStateMachine` from the stage_states and call `sm.next_stage()` to get the graph-determined next stage
- **CRITIQUE FIX (concern):** Infer the outcome from stage statuses: if any stage has status "failed", pass outcome="fail" to `sm.next_stage()`; if the last completed stage is "completed", pass outcome="success". The coach does not receive an explicit outcome parameter -- it reads the state that was already written by the subagent_stop hook.
- Keep the same coaching message format (System Coach prefix, explicit skill directive)

### 4. Initialize stage_states at session creation
- **Task ID**: build-init-stages
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_state_machine.py (update)
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- **CRITIQUE FIX (concern):** Initialization must happen as a post-classification read-modify-write (not a parameter to `create_chat()`), since SDLC classification happens after session creation in the bridge flow. The builder must identify the exact call site where `classification_type` is set to "sdlc" and add the stage_states write there.
- Initialize stage_states on the PM session with ISSUE=ready, all others=pending
- Use `PipelineStateMachine` constructor to generate the initial state dict, then serialize and set on session
- Ensure non-SDLC sessions are not affected (only initialize when classification_type is "sdlc")

### 5. Remove dashboard inference fallback
- **Task ID**: build-remove-inference
- **Depends On**: build-init-stages
- **Validates**: tests/unit/test_pipeline_state_machine.py
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: false
- **CRITIQUE FIX (concern):** In-flight sessions created before this change will have no `stage_states`. Before removing the inference fallback, add a deprecation log when the fallback is hit, and keep it for one release cycle. Alternatively, backfill existing SDLC sessions with stage_states in the same PR. Builder should choose the simpler approach.
- Remove `_infer_stages_from_history()` function from `ui/data/sdlc.py` (or deprecate with logging -- see above)
- Remove its call in `_session_to_pipeline()` (the fallback branch)
- Keep `_parse_stage_states()` as the sole parsing path

### 6. Add regression test for REVIEW->PATCH routing
- **Task ID**: build-regression-test
- **Depends On**: build-classify-outcome
- **Validates**: tests/unit/test_subagent_stop_hook.py (create new test class)
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: false
- Add test: REVIEW stage with "changes requested" in output_tail triggers `fail_stage()` and routes to PATCH
- Add test: REVIEW stage with "approved" in output_tail triggers `complete_stage()` and routes to DOCS
- Add test: TEST stage with "failed" in output_tail triggers `fail_stage()` and routes to PATCH
- Add test: ambiguous outcome defaults to `complete_stage()`

### 7. Validate all acceptance criteria
- **Task ID**: validate-all
- **Depends On**: build-pydantic-model, build-classify-outcome, build-coach-graph, build-init-stages, build-remove-inference, build-regression-test
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/test_pipeline_state_machine.py tests/unit/test_subagent_stop_hook.py tests/unit/test_coach.py -v`
- Verify each of the 7 acceptance criteria from the issue
- Confirm backward compatibility with existing stage_states format

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: pipeline-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-stage-handoff.md` with classify_outcome flow
- Update docstrings on modified functions
- Add entry to `docs/features/README.md` if needed

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Pipeline unit tests | `pytest tests/unit/test_pipeline_state_machine.py tests/unit/test_subagent_stop_hook.py tests/unit/test_coach.py -v` | exit code 0 |
| classify_outcome not dead code | `grep -rn 'classify_outcome' agent/hooks/subagent_stop.py` | output contains classify_outcome |
| fail_stage wired | `grep -rn 'fail_stage' agent/hooks/subagent_stop.py` | output contains fail_stage |
| Inference removed | `grep -rn '_infer_stages_from_history' ui/data/sdlc.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-03-30. -->

| Severity | Critics | Finding | Resolution |
|----------|---------|---------|------------|
| BLOCKER | Skeptic, Adversary | `SubagentStopHookInput` has no `stop_reason` field (confirmed: only has `agent_id`, `agent_transcript_path`, `agent_type`, `stop_hook_active`, `hook_event_name`). Plan Task 2 assumes stop_reason is in input_data. Also, `_extract_outcome_summary()` truncates to 200 chars but `classify_outcome()` expects ~500 chars of output_tail. | **Must fix before build.** Builder must either (a) parse the agent transcript file (`agent_transcript_path`) to extract stop_reason and longer output tail, or (b) rely solely on output_tail pattern matching in `classify_outcome()` with stop_reason=None as default. Plan Task 2 must be updated to specify the actual data source. |
| CONCERN | Skeptic, Operator | Coach `_build_sdlc_stage_coaching()` needs an `outcome` parameter for `sm.next_stage(outcome)`, but the function receives only `stage_progress: dict` with no outcome context. | Builder should infer outcome from stage statuses: if a stage is "failed", use outcome="fail"; if last stage is "completed", use outcome="success". Plan Task 3 should specify this inference logic. |
| CONCERN | Operator, Simplifier | Task 4 says "initialize stage_states in the bridge intake path" but does not identify the specific file/function. SDLC classification happens after `create_chat()`, so initialization must be a post-classification update. | Builder should identify the exact call site during build. Plan should note this is a read-modify-write after classification, not a parameter to `create_chat()`. |
| CONCERN | Operator, Archaeologist | Task 5 removes `_infer_stages_from_history()` but in-flight sessions created before this change will have no `stage_states` and will lose their dashboard display. | Builder should either (a) add a backfill migration for existing SDLC sessions, or (b) keep inference as a deprecated fallback with a deprecation log for one cycle. |
| CONCERN | Skeptic, Operator | `_extract_outcome_summary()` returns max 200 chars but `classify_outcome()` docstring says "last ~500 chars". Pattern matching may miss signals in truncated output. | Builder should create a separate `_extract_output_tail()` with 500-char window for classify_outcome, keeping the existing 200-char summary for logging. |
| NIT | Simplifier | `bridge/pipeline_state.py` currently has no Pydantic imports. Adding StageStates model introduces a new dependency there. | Non-blocking. Pydantic is already in the process via other bridge modules. |
| NIT | Simplifier | Verification table has redundant test commands (`pytest tests/ -x -q` subsumes the specific pipeline test command). | Non-blocking. Keep both for convenience during development. |

**Structural checks:** All passed (required sections present, task numbering sequential, dependencies valid, file paths exist, prerequisites met, cross-references consistent).

**Verdict:** NEEDS REVISION -- 1 blocker must be resolved before build. The blocker is that `SubagentStopHookInput` does not contain `stop_reason`, so Task 2 must specify an alternative data source (transcript file parsing or defaulting to None).

---

## Open Questions

No open questions -- the issue provides clear acceptance criteria and the solution approach is straightforward. The key design decision (ambiguous outcome defaults to success) is specified in the issue constraints.
