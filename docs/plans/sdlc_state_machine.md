---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-03-17
tracking: https://github.com/tomcounsell/ai/issues/430
last_comment_id:
---

# SDLC Pipeline State Machine

## Problem

Stage tracking in the SDLC pipeline is unreliable because it infers state from unstructured agent transcript text instead of recording it programmatically when transitions happen.

**Current behavior:**
- Completed stages render as unchecked (☐) in Telegram because regex didn't match the agent's wording
- Four independent systems try to determine stage state and frequently disagree: stage detector regex, Observer LLM, SkillOutcome parser, goal gates
- Stage data is stored as freetext strings (`"[stage] BUILD COMPLETED"`) in an append-only history list, parsed back by `get_stage_progress()` with string matching
- Every reliability patch (typed outcomes, cross-checking, deterministic guards, gate enforcement) adds more code trying to reconstruct facts that were already known at the time they happened

**Desired outcome:**
- Stage status is always correct because it's set at the programmatic points where transitions actually happen: job enqueue (→ `in_progress`) and job completion (→ `completed`/`failed`)
- The pipeline graph enforces ordering: can't start BUILD until PLAN is completed
- The Observer LLM is stripped down to a binary classifier: "steer (auto-continue) vs deliver (needs human input)"
- ~6,000 lines deleted by removing the transcript parsing, outcome cross-checking, and history reconstruction layers

## Prior Art

- **[#331](https://github.com/tomcounsell/ai/issues/331)**: Goal gates to prevent silent stage skipping — added gate enforcement as a workaround for unreliable stage detection. **Closed, merged.**
- **[#332](https://github.com/tomcounsell/ai/issues/332)**: Checkpoint/resume for abandoned sessions — added checkpoint persistence that the state machine subsumes. **Closed, merged.**
- **[PR #321](https://github.com/tomcounsell/ai/pull/321)**: Observer Agent: replaced auto-continue/summarizer with stage-aware steerer — introduced the current Observer architecture. **Merged.**
- **[PR #351](https://github.com/tomcounsell/ai/pull/351)**: Typed outcomes from /do-* skills — added SkillOutcome parsing as a cross-check for regex. **Merged.**
- **[PR #378](https://github.com/tomcounsell/ai/pull/378)**: Fix Observer SDLC pipeline: cross-repo gh, classification race, typed outcome merge — patched SkillOutcome cross-checking to compensate for regex misses. **Merged.**
- **[PR #412](https://github.com/tomcounsell/ai/pull/412)**: Upgrade SDLC pipeline to directed graph with cycles — created `pipeline_graph.py`. **Merged.**
- **[PR #419](https://github.com/tomcounsell/ai/pull/419)**: Session hardening, URL validation, merge guard — another reliability layer. **Merged.**
- **[PR #421](https://github.com/tomcounsell/ai/pull/421)**: Enforce mandatory REVIEW and DOCS stages — yet another enforcement layer because stages were silently skipped. **Merged.**

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #351 | Added typed SkillOutcome blocks agents print at end of skill runs | Unreliable — depends on LLM remembering to emit structured block |
| PR #378 | Cross-checks typed outcomes against regex detections, merges misses | Adds complexity without fixing root cause — still infers from text |
| PR #421 | Gate enforcement blocks delivery if REVIEW/DOCS not completed | Another enforcement layer on top of broken detection — doesn't fix detection itself |
| PR #419 | URL validation, session hardening | Patches symptoms (bad URLs, stale sessions) without addressing why state is wrong |

**Root cause pattern:** Every fix adds another layer that tries to infer stage state from unstructured text. The system already knows when a stage starts (it chose the skill) and when it ends (the job returned). The fix is to record state at those known points, not to add more inference.

## Data Flow

### Current flow (inference-based)

1. **Observer decides to steer** → computes `stage_name` and `skill_cmd` → buries them in `coaching_message` string → enqueues job
2. **Agent runs skill** → produces unstructured transcript text → returns to bridge
3. **Stage detector** → parses transcript with regex → detects skill invocations and completion markers → writes `[stage]` entries to `session.history`
4. **SkillOutcome parser** → searches transcript for structured outcome block → cross-checks against regex
5. **Observer reads session** → calls `get_stage_progress()` → parses `[stage]` strings from history list → determines what's pending/completed
6. **Summarizer renders** → calls `_render_stage_progress()` → re-reads session from Redis → parses history again → renders ☑/☐

**Every step 3-6 is an opportunity for information loss or misparse.** Steps 1-2 already have the information needed.

### New flow (state machine)

1. **Observer decides to steer** → calls `state_machine.start_stage("BUILD")` → state machine validates predecessor is completed, sets BUILD to `in_progress` → enqueues job
2. **Agent runs skill** → produces transcript → returns to bridge
3. **Job completes** → `send_to_chat()` calls `state_machine.complete_stage("BUILD")` (or `fail_stage("BUILD")` on failure) → state machine updates status
4. **Summarizer renders** → calls `state_machine.get_display_progress()` → returns dict of stage → status → renders ☑/☐

**Steps 3-6 of the old flow are replaced by a single method call at step 3.**

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: `AgentSession` gains a `stage_states` field (JSON dict) replacing `[stage]` history parsing. Observer return dict gains `completed_stage` and `next_stage` structured fields.
- **Coupling**: Decreases significantly — stage detector, skill outcome, checkpoint, and goal gate enforcement all removed. State machine is a single module imported by observer and summarizer.
- **Data ownership**: `PipelineStateMachine` owns all stage transitions. `pipeline_graph.py` remains the transition table. `AgentSession` stores the state.
- **Reversibility**: Medium — this is a large deletion. But the deleted code is all inference layers with no business logic. The pipeline graph and session model are preserved.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 0-1 (scope is clear from conversation)
- Review rounds: 1 (validate that stage display works correctly end-to-end)

This is primarily deletion work with a small new module. The pipeline graph already exists and is clean. The main risk is missing a callsite that still imports deleted code.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`bridge/pipeline_state.py`** — New module: `PipelineStateMachine` class that wraps `pipeline_graph.py` and manages stage statuses (`pending`, `ready`, `in_progress`, `completed`, `failed`). Enforces ordering via graph edges.
- **`AgentSession.stage_states`** — New JSON field replacing the `[stage]` history entries. Stores `{"ISSUE": "completed", "PLAN": "in_progress", "BUILD": "pending", ...}`. The state machine reads/writes this field.
- **Observer return dict** — `steer` decisions include `completed_stage` (what just finished) and `next_stage` (what we're starting) as structured fields. `send_to_chat()` in `job_queue.py` uses these to call state machine transitions directly.
- **Simplified Observer** — Remove phases 1 (stage detection), 1.5 (deterministic SDLC guard), typed outcome routing, and gate enforcement. Keep only the LLM judgment for "steer vs deliver" and the deterministic "remaining stages → steer" logic (which now reads the state machine instead of parsing history).
- **Summarizer reads state machine** — `_render_stage_progress()` calls `state_machine.get_display_progress()` instead of parsing history entries.

### Flow

**Observer steers** → `state_machine.complete_stage(current)` + `state_machine.start_stage(next)` → enqueue job → **Agent runs** → job returns → `state_machine.complete_stage(stage)` or `state_machine.fail_stage(stage)` → **Summarizer renders** → `state_machine.get_display_progress()` → ☑/☐

### Technical Approach

- `PipelineStateMachine.__init__(session)` loads `session.stage_states` (or initializes default)
- `start_stage(name)` validates predecessor is completed (via `PIPELINE_EDGES`), sets to `in_progress`, saves
- `complete_stage(name)` validates stage is `in_progress`, sets to `completed`, marks next stage as `ready`, saves
- `fail_stage(name)` sets to `failed`, saves
- `classify_outcome(stage, stop_reason, output_tail)` determines success/failure from SDK stop_reason + deterministic output patterns scoped to the known stage. Returns `"success"`, `"fail"`, or `"ambiguous"` (for Observer LLM fallback).
- `get_display_progress()` returns `{stage: status}` for `DISPLAY_STAGES` only (excludes PATCH)
- `current_stage()` returns the stage currently `in_progress`, or None
- `next_stage(outcome)` delegates to `pipeline_graph.get_next_stage()` using current state
- PATCH cycle counting: state machine tracks cycle count internally, delegates to `get_next_stage(cycle_count=N)`
- State is persisted as a JSON dict on `AgentSession.stage_states` — one Redis field, no history parsing
- **No special cases for any stage**: The pipeline always starts at ISSUE. If an existing issue number is known, the steering message passes it to `/do-issue` which validates/enriches it before marking ISSUE complete. All stages follow the same lifecycle.

Observer simplification:
- Delete `_next_sdlc_skill()` — replaced by `state_machine.next_stage()`
- Delete phase 1 (typed outcome parsing + stage detector) — state machine is updated by `send_to_chat()` at job boundaries
- Delete phase 1.75 (deterministic SDLC guard) — replaced by `if state_machine.has_remaining_stages(): steer`
- Delete `_check_mandatory_gates()` — ordering is enforced by the state machine
- Keep phase 2 (LLM Observer) for "needs human input?" judgment only
- Remove `update_session` tool's `issue_url`/`pr_url` params — URL extraction handled separately (#416)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `PipelineStateMachine.start_stage()` raises `ValueError` if predecessor not completed — test with out-of-order transitions
- [ ] `PipelineStateMachine.complete_stage()` raises `ValueError` if stage not `in_progress` — test with double-complete
- [ ] `state_machine.fail_stage()` on already-completed stage is a no-op with warning log — test idempotency

### Empty/Invalid Input Handling
- [ ] `PipelineStateMachine(session)` with `stage_states=None` initializes default pending states
- [ ] `start_stage("")` or `start_stage("INVALID")` raises `ValueError` with clear message
- [ ] `get_display_progress()` with no transitions returns all-pending dict

### Error State Rendering
- [ ] Failed stages render with distinct indicator in Telegram display
- [ ] Summarizer gracefully handles state machine returning unexpected status values

## Test Impact

- [ ] `tests/unit/test_observer.py` (1,893 lines) — REPLACE: gut and rewrite. Remove all stage detection, SkillOutcome, typed outcome, and gate enforcement tests. Keep steer/deliver LLM judgment tests, adapt to new state machine interface.
- [ ] `tests/unit/test_skill_outcome.py` (315 lines) — DELETE: tests code being deleted
- [ ] `tests/unit/test_pipeline_integrity.py` (232 lines) — UPDATE: remove `stage_detector` imports, keep pipeline graph tests, add state machine ordering tests
- [ ] `tests/unit/test_goal_gates.py` (351 lines) — DELETE or REPLACE: gate enforcement moves into state machine preconditions
- [ ] `tests/unit/test_checkpoint.py` (245 lines) — DELETE: checkpoint module being deleted
- [ ] `tests/unit/test_summarizer.py` (2,070 lines) — UPDATE: `_render_stage_progress` tests updated to use state machine mock instead of session history
- [ ] `tests/unit/test_stop_reason_observer.py` (177 lines) — UPDATE: adapt to simplified Observer
- [ ] `tests/unit/test_observer_message_for_user.py` (249 lines) — UPDATE: adapt to simplified Observer
- [ ] `tests/unit/test_telemetry.py` (305 lines) — UPDATE: remove `stage_detector.apply_transitions` telemetry tests
- [ ] `tests/integration/test_stage_aware_auto_continue.py` (517 lines) — REPLACE: rewrite against state machine API
- [ ] `tests/integration/test_enqueue_continuation.py` (585 lines) — UPDATE: continuation now includes structured `next_stage` field
- [ ] `tests/integration/test_agent_session_lifecycle.py` (887 lines) — UPDATE: `get_stage_progress` tests become `stage_states` field tests

## Rabbit Holes

- **Persisting state machine as a separate Redis model**: Just use a JSON field on `AgentSession`. No new model, no sync issues.
- **Making the state machine handle non-SDLC jobs**: Non-SDLC jobs don't use stages. The state machine is only instantiated for SDLC sessions.
- **Refactoring the Observer LLM prompt extensively**: Keep it simple — remove the stage management instructions, keep the steer/deliver judgment. Don't redesign the prompt.
- **Adding new stage statuses beyond the five**: `pending`, `ready`, `in_progress`, `completed`, `failed` are sufficient. Don't add `skipped`, `blocked`, `cancelled`, etc.
- **Integrating URL extraction into the state machine**: URL handling is a separate concern addressed by #416. State machine only tracks stage status.

## Risks

### Risk 1: Missing import sites for deleted modules
**Impact:** ImportError crashes at runtime for code paths not covered by tests
**Mitigation:** Before deleting each file, grep all imports across the codebase (including worktrees, scripts, hooks). Run full test suite after each deletion.

### Risk 2: Observer LLM behavior change
**Impact:** The simplified Observer might make worse steer/deliver decisions without the detailed stage context
**Mitigation:** The Observer still reads `stage_states` via `read_session` — it has the same information, just from a reliable source. The deterministic "remaining stages → steer" guard is preserved, just reads from state machine.

### Risk 3: State machine desync from session
**Impact:** State machine in memory diverges from Redis if concurrent writes happen
**Mitigation:** State machine always reads from and writes to `session.stage_states` directly. No in-memory caching across requests. Each Observer run creates a fresh state machine from the session.

## Race Conditions

### Race 1: Concurrent stage writes during Observer execution
**Location:** `bridge/pipeline_state.py` (new) and `agent/job_queue.py:send_to_chat()`
**Trigger:** Observer reads session, meanwhile another process writes to `stage_states`
**Data prerequisite:** `session.stage_states` must reflect all prior transitions
**State prerequisite:** Session must be re-read from Redis before writing transitions
**Mitigation:** The Observer already re-reads the session before `_handle_update_session()` (line 643). The state machine follows the same pattern: read session → compute transition → write. Only one Observer runs per session at a time (enforced by job queue serialization).

## No-Gos (Out of Scope)

- URL extraction or GitHub repo resolution (addressed by #416)
- Redesigning the pipeline graph edges or adding new stages
- Changing the Observer's steer/deliver decision quality (just simplifying its inputs)
- Modifying skill prompts (`/do-build`, `/do-plan`, etc.) — they don't need to know about stage tracking
- Persisting stage transition timestamps or duration metrics (can be added later)
- Changing how non-SDLC jobs are routed

## Update System

No update system changes required — this is bridge-internal refactoring. The state machine is a new Python module with no external dependencies or config files.

## Agent Integration

No agent integration required — stage tracking is bridge infrastructure. The agent's skills (`/do-build`, `/do-plan`, etc.) are unaffected. No MCP servers or tools change. The agent doesn't know or care about stage tracking — that's the whole point.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/observer-agent.md` to reflect simplified Observer role
- [ ] Create `docs/features/pipeline-state-machine.md` describing the state machine architecture
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstrings on `PipelineStateMachine` class and all public methods
- [ ] Update `bridge/pipeline_graph.py` module docstring to reference state machine as consumer
- [ ] Update `CLAUDE.md` SDLC section if stage tracking details change

## Success Criteria

- [ ] `bridge/stage_detector.py` deleted
- [ ] `agent/skill_outcome.py` deleted
- [ ] `agent/checkpoint.py` deleted
- [ ] `PipelineStateMachine` module exists with `start_stage`, `complete_stage`, `fail_stage`, `get_display_progress` methods
- [ ] State machine enforces ordering: `start_stage("BUILD")` raises if PLAN not completed
- [ ] Telegram stage display (☑/☐/▶) matches actual stage states for a full pipeline run
- [ ] Observer `run()` method is < 200 lines (currently ~400 lines in phases 1-1.75 alone)
- [ ] `grep -rn "stage_detector\|skill_outcome\|from agent.checkpoint" --include='*.py' | grep -v tests/ | grep -v .claude/worktrees | grep -v __pycache__` returns 0 results
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (state-machine)**
  - Name: state-machine-builder
  - Role: Create `bridge/pipeline_state.py`, add `stage_states` field to `AgentSession`, wire transitions into `send_to_chat()`
  - Agent Type: builder
  - Resume: true

- **Builder (observer-simplify)**
  - Name: observer-simplifier
  - Role: Strip Observer down to steer/deliver classifier, remove stage detection phases, update system prompt
  - Agent Type: builder
  - Resume: true

- **Builder (deletion)**
  - Name: deletion-builder
  - Role: Delete `stage_detector.py`, `skill_outcome.py`, `checkpoint.py`, clean all imports, remove dead code from `goal_gates.py` and `agent_session.py`
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: pipeline-validator
  - Role: Verify no import errors, stage display correctness, Observer still routes correctly
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: pipeline-docs
  - Role: Update Observer docs, create state machine docs, update feature index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create pipeline state machine module
- **Task ID**: build-state-machine
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_state.py (create)
- **Assigned To**: state-machine-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/pipeline_state.py` with `PipelineStateMachine` class
- Add `stage_states` JSON field to `AgentSession` model
- Implement `start_stage()`, `complete_stage()`, `fail_stage()`, `get_display_progress()`, `current_stage()`, `next_stage()`, `has_remaining_stages()`
- Enforce ordering via `PIPELINE_EDGES` lookup
- Track PATCH cycle count
- Write unit tests for all transitions, ordering enforcement, and edge cases

### 2. Wire state machine into job queue
- **Task ID**: build-wire-job-queue
- **Depends On**: build-state-machine
- **Validates**: tests/integration/test_stage_aware_auto_continue.py (update)
- **Assigned To**: state-machine-builder
- **Agent Type**: builder
- **Parallel**: false
- In `send_to_chat()`: after Observer returns `steer`, call `state_machine.complete_stage(completed_stage)` and `state_machine.start_stage(next_stage)` using structured fields from decision dict
- In `send_to_chat()`: after Observer returns `deliver`, call `state_machine.complete_stage(current)` if job succeeded
- Add `completed_stage` and `next_stage` to Observer steer return dict
- Update `_enqueue_continuation()` to pass stage context

### 3. Simplify Observer
- **Task ID**: build-simplify-observer
- **Depends On**: build-wire-job-queue
- **Validates**: tests/unit/test_observer.py (rewrite)
- **Assigned To**: observer-simplifier
- **Agent Type**: builder
- **Parallel**: false
- Delete phase 1 (typed outcome parsing + stage detector invocation)
- Delete phase 1.75 (deterministic SDLC guard) — replace with simple `if state_machine.has_remaining_stages(): steer` check before LLM call
- Delete `_check_mandatory_gates()` — ordering enforced by state machine
- Delete `_next_sdlc_skill()` — replaced by `state_machine.next_stage()`
- Remove `issue_url`/`pr_url` from `update_session` tool (URL extraction is #416's scope)
- Simplify `OBSERVER_SYSTEM_PROMPT` — remove stage detection instructions, keep steer/deliver judgment guidance
- Update `_handle_read_session()` to include `stage_states` from state machine instead of `get_stage_progress()` from history

### 4. Update summarizer
- **Task ID**: build-update-summarizer
- **Depends On**: build-state-machine
- **Validates**: tests/unit/test_summarizer.py (update)
- **Assigned To**: observer-simplifier
- **Agent Type**: builder
- **Parallel**: true (parallel with task 3)
- Update `_render_stage_progress()` to read from `state_machine.get_display_progress()` instead of `session.get_stage_progress()`
- Remove dependency on `[stage]` history entry format

### 5. Delete inference layers
- **Task ID**: build-delete-inference
- **Depends On**: build-simplify-observer, build-update-summarizer
- **Assigned To**: deletion-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `bridge/stage_detector.py`
- Delete `agent/skill_outcome.py`
- Delete `agent/checkpoint.py`
- Remove all imports of deleted modules across codebase
- Remove `get_stage_progress()`, `has_remaining_stages()`, `has_failed_stage()` from `AgentSession` (replaced by state machine methods)
- Clean dead code from `agent/goal_gates.py`
- Delete stale tests: `test_skill_outcome.py`, `test_checkpoint.py`
- Update remaining tests that imported deleted modules

### 6. Validate
- **Task ID**: validate-pipeline
- **Depends On**: build-delete-inference
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn "stage_detector\|skill_outcome\|from agent.checkpoint" --include='*.py' | grep -v tests/ | grep -v .claude/worktrees` — must return 0
- Run `pytest tests/ -x -q` — all tests pass
- Run `ruff check . && ruff format --check .` — clean
- Verify `_render_stage_progress()` produces correct output for mock state machine data
- Verify Observer `run()` is under 200 lines

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-pipeline
- **Assigned To**: pipeline-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pipeline-state-machine.md`
- Update `docs/features/observer-agent.md`
- Add entry to `docs/features/README.md` index table
- Update CLAUDE.md SDLC section if needed

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all success criteria checks
- Verify all documentation exists
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No deleted imports | `grep -rn 'stage_detector\|skill_outcome\|from agent.checkpoint' --include='*.py' \| grep -v 'tests/\|.claude/worktrees\|__pycache__' \| wc -l` | output contains 0 |
| State machine exists | `python -c "from bridge.pipeline_state import PipelineStateMachine; print('ok')"` | output contains ok |
| Observer simplified | `wc -l bridge/observer.py \| awk '{print $1}'` | output < 600 |

---

## Resolved Questions

1. **Failure detection**: Two-tier approach. First, `stop_reason` from the SDK: anything other than `"end_turn"` (e.g., `budget_exceeded`, `rate_limited`) is a process failure — mark stage as failed. Second, for `end_turn` completions, deterministic tail patterns on the worker output scoped to the known current stage: `/do-test` output with `N passed, 0 failed` → success; `N failed` → failure; `/do-build` output with a PR URL → success; etc. The Observer LLM remains as fallback for ambiguous cases, but most completions are classifiable from `stop_reason` + output tail.

2. **ISSUE stage**: No special case. Every stage follows the same pattern — the pipeline always starts at ISSUE. If an issue already exists, the steering message passes the issue number to `/do-issue`, which enriches/validates the existing issue (ensuring it meets the quality bar from the do-issue skill) and marks ISSUE complete. This keeps all stages uniform and ensures issues always have the full descriptions required by downstream `/do-plan`.
