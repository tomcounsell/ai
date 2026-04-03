---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/645
last_comment_id:
plan_branch: main
---

# Implicit Pipeline Stage Tracking via Observable Artifacts

## Problem

The SDLC pipeline progress display (`get_display_progress()`) reports stale or empty state because the hook-based "push" model for recording stage transitions fails silently. Both `pre_tool_use` and `subagent_stop` hooks wrap all PipelineStateMachine operations in try/except blocks, so when the parent session lookup fails, the hook does not fire, or `session.save()` fails, stage state is silently lost.

**Current behavior:**
The pre-merge nudge in `/do-merge` calls `get_display_progress()` and shows stages as "pending" even after they completed. The PM cannot trust the pipeline status display when deciding whether to merge. `record_stage_completion()` has zero callers in the codebase -- it is dead code.

**Desired outcome:**
`get_display_progress()` returns accurate state by checking observable artifacts (plan file exists, PR exists, review comments exist, doc files changed) as a fallback when stored state is missing. The pre-merge nudge shows trustworthy pipeline status. The PM feels "the nudge is smarter now" without any added latency.

## Prior Art

- **PR #490 (issue #488)**: Consolidate SDLC stage tracking -- Merged. Introduced `record_stage_completion()` as a convenience helper, but it was never wired to callers. Phase 1 (wire skills) was documented in instructions only.
- **PR #472 (issue #463)**: Add CRITIQUE stage to pipeline -- Merged. Extended DISPLAY_STAGES and pipeline graph but did not address tracking reliability.
- **PR #356 (issue #309)**: Observer-steered worker, rewrite /sdlc as single-stage router -- Merged. Established the ChatSession/DevSession split. Stage tracking hooks were added later.
- **Issue #489**: Test SDLC pipeline state injection -- Closed. Tested that hooks fire correctly but did not address silent failure paths.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #490 | Created `record_stage_completion()` helper | Never wired -- skills don't call it. The "push" model requires every callsite to explicitly record transitions, and none do. |
| Hook-based tracking (PR #433 + #492) | Wired `start_stage()`/`complete_stage()` into SDK hooks | Hooks catch all exceptions silently. Parent session lookup fails when session_id is missing or Redis is slow. No fallback when push fails. |

**Root cause pattern:** The push model is inherently fragile because it requires N callsites to all succeed independently, and each failure path is swallowed silently. A pull model that infers state from artifacts does not depend on any single callsite firing correctly.

## Data Flow

1. **Entry point**: `/do-merge` is invoked with a PR number
2. **Slug extraction**: PR branch name `session/{slug}` is parsed to get the slug
3. **Session lookup**: `AgentSession.get_by_slug(slug)` loads the session from Redis
4. **State machine creation**: `PipelineStateMachine(session)` loads `stage_states` JSON from the session
5. **Display progress**: `get_display_progress()` returns `{stage: status}` dict from stored state only
6. **Nudge rendering**: The merge skill renders icons for each stage status and warns about skipped stages

**The gap is at step 5**: when stored state is empty/stale, every stage shows as "pending" regardless of what actually happened. The fix adds artifact inference at step 5 as a fallback.

## Architectural Impact

- **New dependencies**: None. Artifact checks use `pathlib`, `subprocess` (for `gh` CLI), and existing imports.
- **Interface changes**: `get_display_progress()` gains an optional `slug` parameter for artifact lookups. `AgentSession.get_stage_progress()` gains a matching optional `slug` parameter that it forwards. Existing callers without the parameter get the same behavior.
- **Coupling**: Slightly increases coupling between `pipeline_state.py` and filesystem/GitHub conventions (plan file path, branch naming). These conventions are already canonical across the codebase.
- **Data ownership**: No change. Stage state is still owned by `PipelineStateMachine`; artifacts are read-only signals.
- **Reversibility**: Fully reversible -- remove the artifact inference function and the optional parameter.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All artifact checks use existing filesystem paths and the `gh` CLI which is already available.

## Solution

### Key Elements

- **Artifact inference function**: A new `_infer_stage_from_artifacts(slug)` method on `PipelineStateMachine` that checks observable signals for each stage and returns inferred statuses
- **Fallback layering in `get_display_progress()`**: When stored state for a stage is "pending" or "ready", check artifact inference as a fallback. Stored "completed"/"in_progress"/"failed" always wins.
- **AgentSession.get_stage_progress() update**: The wrapper in `models/agent_session.py` must accept an optional `slug` parameter and pass it through to `get_display_progress(slug=slug)`, so summarizer and coach callers also benefit from artifact inference.
- **Dead code removal**: Delete `record_stage_completion()` which has zero callers
- **Logging improvement**: Verify `_save()` logs at warning level on failure (already does -- just confirm not swallowed upstream)

### Flow

**`/do-merge` called** --> extract slug from PR branch --> `PipelineStateMachine(session)` --> `get_display_progress(slug=slug)` --> for each "pending" stage, check artifacts --> return merged state --> render nudge with accurate status

### Technical Approach

- Artifact checks are local filesystem lookups (fast, no API calls in the hot path):
  - **ISSUE**: `tracking:` URL in plan frontmatter contains an issue number (or skip -- ISSUE is always done if a plan exists)
  - **PLAN**: `docs/plans/{slug}.md` exists
  - **CRITIQUE**: Plan frontmatter has `status: Ready` (critique sets this) or plan has non-empty `## Critique Results` section
  - **BUILD**: `gh pr list --head session/{slug} --state open --json number` returns a result (single `gh` call, cached for the request)
  - **TEST**: Inferred from `statusCheckRollup` in the `gh pr view` response -- if any check context contains "test" or "ci" with a `SUCCESS` conclusion, TEST is completed. If no check data is available, TEST is marked "not inferable" and left to hooks only.
  - **REVIEW**: PR has `reviewDecision: APPROVED` (from the same `gh pr view` call used for BUILD)
  - **DOCS**: PR `files` array (from the same `gh pr view` call) contains at least one entry where `path` starts with `docs/`. Single API call, no second `gh` invocation needed.
  - **MERGE**: PR state is "MERGED" (already terminal -- not needed for pre-merge nudge)

- A single `gh pr view --json number,reviewDecision,state,statusCheckRollup,files` call provides BUILD, TEST, REVIEW, and DOCS signals. This is one API call total, not per-stage.
- All `subprocess.run()` calls for `gh` must use `timeout=5` to prevent hangs from blocking the merge flow.
- Results are computed once per `get_display_progress(slug=...)` call, not cached across calls (stateless).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_infer_stage_from_artifacts()` wraps all checks in try/except -- test that a missing plan file, failed `gh` call, or malformed frontmatter returns empty inference (not crash)
- [ ] `_save()` already logs at warning level -- add a test confirming the warning is emitted when `session.save()` raises

### Empty/Invalid Input Handling
- [ ] `get_display_progress(slug=None)` returns stored state only (no artifact inference attempted)
- [ ] `get_display_progress(slug="nonexistent")` returns stored state only (artifact checks find nothing)
- [ ] Plan file with empty or malformed frontmatter does not crash inference

### Error State Rendering
- [ ] Pre-merge nudge renders correctly when artifact inference upgrades some stages from "pending" to "completed"

## Test Impact

- [ ] `tests/unit/test_pipeline_state_machine.py::TestGetDisplayProgress` -- UPDATE: add new test cases for artifact-based inference with slug parameter
- [ ] `tests/unit/test_pipeline_state_machine.py` -- UPDATE: remove any tests referencing `record_stage_completion` if present
- [ ] `tests/integration/test_agent_session_lifecycle.py::TestGetStageProgress` -- UPDATE: add test case verifying `get_stage_progress(slug="x")` passes slug through to `get_display_progress`

No other existing tests affected -- the artifact inference is purely additive to `get_display_progress()` and existing callers without the `slug` parameter get identical behavior.

## Rabbit Holes

- **Full GitHub API integration for every stage**: Do not make individual API calls per stage. One `gh pr view` call provides BUILD/REVIEW/DOCS signals. Do not add calls to check CI status, individual review comments, etc.
- **Caching layer**: Do not build a persistent cache for artifact inference. The function runs once per merge-gate check -- caching adds complexity with no measurable benefit.
- **Rewriting the push model**: Do not refactor the hook-based tracking. This is additive -- layer artifacts on top, do not replace hooks.
- **Wiring `record_stage_completion()` to callers**: The issue says "wire or delete." Delete is the right call since the pull model makes explicit recording unnecessary as a primary mechanism.

## Risks

### Risk 1: `gh` CLI call adds latency to merge flow
**Impact:** PM feels merge is slower.
**Mitigation:** Single `gh pr view` call (typically <500ms). This runs only when `slug` is provided, which only happens in the merge gate path. Not on every `get_display_progress()` call.

### Risk 2: Artifact inference disagrees with stored state
**Impact:** Confusing display if stored state says "failed" but artifacts say "completed."
**Mitigation:** Stored state always wins when it is not "pending"/"ready". Artifact inference only fills in gaps, never overwrites explicit state.

## Race Conditions

No race conditions identified -- `get_display_progress()` is a read-only query. Artifact inference reads filesystem and GitHub API state at call time. No shared mutable state is involved.

## No-Gos (Out of Scope)

- Hard-blocking merges on incomplete stages (explicitly out of scope per issue -- incentive system, not gate)
- Fixing the root cause of Redis save failures in hooks (separate concern)
- Adding artifact inference to `current_stage()` or `next_stage()` (those are routing functions, not display)
- Making the dashboard use artifact inference (dashboard can adopt this later if needed)

## Update System

No update system changes required -- this modifies `bridge/pipeline_state.py` and its tests, which are internal to the bridge. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this is a bridge-internal change to `PipelineStateMachine`. The agent does not call `get_display_progress()` directly. The `/do-merge` skill (a Claude Code command) calls it via inline Python, which will automatically pick up the new behavior.

## Documentation

- [ ] Update `docs/features/pipeline-state-machine.md` to describe artifact-based inference fallback
- [ ] Update `docs/features/pipeline-state-machine.md` to remove the `record_stage_completion()` reference from the API section and Files table (it will be deleted)
- [ ] Update inline docstrings in `bridge/pipeline_state.py` for `get_display_progress()` and the new `_infer_stage_from_artifacts()` method

## Success Criteria

- [ ] `get_display_progress(slug="X")` returns "completed" for stages with matching artifacts, even when stored state is "pending"
- [ ] `get_display_progress()` without slug returns identical behavior to current (backward compatible)
- [ ] `AgentSession.get_stage_progress(slug="X")` passes slug through to `get_display_progress()`
- [ ] Pre-merge nudge in `/do-merge` shows correct pipeline status when stored state is empty
- [ ] All `gh` subprocess calls use `timeout=5`
- [ ] TEST inference uses `statusCheckRollup` (not circular REVIEW dependency)
- [ ] DOCS inference uses `files` array from single `gh pr view` call
- [ ] `record_stage_completion()` is deleted from `bridge/pipeline_state.py`
- [ ] `record_stage_completion()` reference removed from `docs/features/pipeline-state-machine.md`
- [ ] Silent save failures in `_save()` confirmed logged at warning level (test exists)
- [ ] Existing hook-based tracking continues to work (no modifications to hooks)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (pipeline-state)**
  - Name: state-builder
  - Role: Implement artifact inference in PipelineStateMachine, delete dead code, update do-merge
  - Agent Type: builder
  - Resume: true

- **Validator (pipeline-state)**
  - Name: state-validator
  - Role: Verify artifact inference accuracy, backward compatibility, and merge nudge correctness
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add artifact inference to PipelineStateMachine
- **Task ID**: build-artifact-inference
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_state_machine.py (update)
- **Assigned To**: state-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_infer_stage_from_artifacts(slug: str) -> dict[str, str]` method that returns inferred statuses for stages based on observable artifacts
- Modify `get_display_progress()` to accept optional `slug` parameter; when provided and stored state is "pending"/"ready", check artifact inference as fallback
- Use a single `gh pr view --json number,reviewDecision,state,statusCheckRollup,files` call with `subprocess.run(timeout=5)` for all GitHub-derived signals (BUILD, TEST, REVIEW, DOCS)
- TEST inference: check `statusCheckRollup` for any check with "test"/"ci" in the name and `SUCCESS` conclusion; if no check data, leave TEST as stored state (not inferable from artifacts alone)
- DOCS inference: check `files` array for any entry with `path` starting with `docs/`
- Wrap all artifact checks in try/except so failures return empty dict (never crash)
- Update `AgentSession.get_stage_progress()` in `models/agent_session.py` to accept optional `slug` parameter and pass it through to `sm.get_display_progress(slug=slug)`
- Delete `record_stage_completion()` function (zero callers confirmed)

### 2. Update do-merge to pass slug
- **Task ID**: build-merge-integration
- **Depends On**: build-artifact-inference
- **Validates**: manual review of `.claude/commands/do-merge.md`
- **Assigned To**: state-builder
- **Agent Type**: builder
- **Parallel**: false
- Update the pre-merge pipeline check in `.claude/commands/do-merge.md` to pass `slug` to `get_display_progress(slug=slug)`
- Update the prerequisites check to also pass `slug`

### 3. Add tests for artifact inference
- **Task ID**: build-tests
- **Depends On**: build-artifact-inference
- **Validates**: tests/unit/test_pipeline_state_machine.py
- **Assigned To**: state-builder
- **Agent Type**: builder
- **Parallel**: false
- Test: `get_display_progress(slug=None)` returns stored state only
- Test: `get_display_progress(slug="x")` with no artifacts returns stored state
- Test: artifact inference upgrades "pending" PLAN to "completed" when plan file exists
- Test: stored "failed" state is NOT overridden by artifact inference
- Test: `_infer_stage_from_artifacts()` handles missing files, failed subprocess calls gracefully
- Test: `subprocess.run()` for `gh` is called with `timeout=5`
- Test: TEST inference uses `statusCheckRollup` data (not circular REVIEW dependency)
- Test: DOCS inference checks `files` array for `docs/` paths from `gh pr view` response
- Test: `AgentSession.get_stage_progress(slug="x")` passes slug through to `get_display_progress()`
- Test: `_save()` logs warning when `session.save()` raises
- Test: `record_stage_completion` no longer importable (deleted)

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: state-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pipeline-state-machine.md` to describe artifact-based fallback
- Update docstrings in `bridge/pipeline_state.py`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: state-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_pipeline_state_machine.py -v`
- Run `python -m ruff check bridge/pipeline_state.py`
- Verify `get_display_progress` backward compatibility (no slug = same behavior)
- Verify `record_stage_completion` is gone from codebase

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Dead code removed | `grep -rn "record_stage_completion" bridge/ tests/` | exit code 1 |
| Artifact inference importable | `python -c "from bridge.pipeline_state import PipelineStateMachine; sm = PipelineStateMachine.__dict__; assert '_infer_stage_from_artifacts' in sm"` | exit code 0 |

## Critique Results

| CONCERN | Agent | Concern | Resolution |
|---------|-------|---------|------------|
| 1 | Operator | `AgentSession.get_stage_progress()` wrapper does not pass slug -- summarizer/coach get stale state | FIXED: Plan now includes updating the wrapper to accept and forward optional `slug` parameter |
| 2 | Skeptic | TEST inference "completed if REVIEW completed" is circular process assumption, not artifact-based | FIXED: TEST now inferred from `statusCheckRollup` in `gh pr view` response; if no check data, left as stored state (not inferable) |
| 3 | Adversary | `gh` CLI subprocess has no timeout -- could hang and block merge flow | FIXED: Plan now requires `subprocess.run(timeout=5)` on all `gh` calls, with test coverage |
| 4 | Simplifier | DOCS inference underspecified -- unclear whether to use `gh pr diff` or plan checkbox parsing | FIXED: Use `files` array from the same `gh pr view --json` call (single API call). No second `gh` invocation needed. |
| 5 | Archaeologist | `docs/features/pipeline-state-machine.md` references `record_stage_completion()` which will be deleted | FIXED: Added explicit documentation task to remove the reference from API section and Files table |

---

## Open Questions

No open questions -- the issue is well-scoped with clear acceptance criteria and the solution approach is straightforward.
