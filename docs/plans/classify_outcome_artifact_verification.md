---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/665
last_comment_id:
---

# classify_outcome Must Verify Artifacts, Not Just Text Patterns

## Problem

When issues #653-657 were processed in parallel, the REVIEW stage was marked "completed" on all 5 PRs without a single actual GitHub PR review being posted. The `classify_outcome()` method in `bridge/pipeline_state.py` trusts dev-session output text instead of verifying actual artifacts.

**Current behavior:**
A dev-session that outputs "review passed" without posting a GitHub PR review gets marked as success. The entire enforcement chain (`/do-merge` -> `get_display_progress()`) trusts this text-only classification.

**Desired outcome:**
`classify_outcome()` verifies actual artifacts (GitHub API, file existence, structured output) before marking critical stages as successful. Text pattern matching remains as a fallback when artifact checks are unavailable.

## Prior Art

- **Issue #563 / PR #601**: "SDLC pipeline graph routing not wired into runtime" — Wired `classify_outcome()` into the runtime pipeline via `subagent_stop.py`. Made classify_outcome live code instead of dead code. This is the foundation that the current fix builds on.
- **PR #433**: "Replace inference-based stage tracking with PipelineStateMachine" — Created the state machine and `classify_outcome()` method. Established the text-pattern approach that this issue replaces with artifact verification.
- **Issue #463 / PR #472**: "Add CRITIQUE stage to SDLC pipeline" — Added CRITIQUE patterns to classify_outcome. Same text-pattern approach.

## Data Flow

1. **Entry point**: Dev-session completes a stage (e.g., REVIEW)
2. **subagent_stop.py** `_record_stage_on_parent()`: Extracts output tail (~500 chars), calls `sm.classify_outcome(stage, stop_reason, output_tail)`
3. **pipeline_state.py** `classify_outcome()`: Tier 1 checks stop_reason, Tier 2 matches text patterns -> returns "success"/"fail"/"ambiguous"
4. **subagent_stop.py**: Routes to `sm.complete_stage()` or `sm.fail_stage()` based on outcome
5. **Pipeline progression**: ChatSession reads `get_display_progress()` -> dispatches next stage or reports completion

The bug is at step 3: Tier 2 trusts text patterns without verifying the artifact actually exists.

## Architectural Impact

- **No new dependencies**: Uses existing `subprocess` + `gh` CLI infrastructure already in `_infer_stage_from_artifacts()`
- **Interface changes**: `classify_outcome()` signature unchanged. Internal behavior adds Tier 3 artifact verification after Tier 2 text match returns "success"
- **Coupling**: Slightly increases coupling to GitHub API, but this is intentional — the whole point is ground-truth verification
- **Reversibility**: Easy — remove the Tier 3 checks and fall back to current behavior

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work uses existing `gh` CLI and `subprocess` infrastructure already in the file.

## Solution

### Key Elements

- **Tier 3 artifact verification**: After Tier 2 text patterns return "success", verify the artifact actually exists before confirming
- **Graceful fallback**: If artifact verification fails (network error, timeout), fall back to the Tier 2 text-pattern result per acceptance criteria
- **Shared verification logic**: Extract reusable helpers from `_infer_stage_from_artifacts()` to avoid duplicating `gh` call logic

### Flow

**Stage completes** -> Tier 1 (stop_reason) -> Tier 2 (text patterns) -> **Tier 3 (artifact check)** -> final classification

### Technical Approach

- Add a `_verify_stage_artifact(stage)` method that performs API/filesystem checks per stage
- For REVIEW: Call `gh pr view session/{slug} --json reviewDecision` and verify `reviewDecision` is non-empty
- For BUILD: Call `gh pr view session/{slug} --json number` and verify PR exists
- For TEST: Parse output tail for structured exit code patterns (e.g., `N passed, M failed`) with numeric extraction rather than substring matching
- For DOCS: Check `gh pr diff session/{slug} --name-only` for `docs/` paths
- When `self.session.slug` is None (no slug available), skip Tier 3 and rely on Tier 2 result
- Set a tight timeout (3s) on subprocess calls to avoid blocking the pipeline

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_verify_stage_artifact()` method wraps all subprocess calls in try/except and returns None on failure (graceful fallback)
- [ ] Test that subprocess.TimeoutExpired, OSError, and JSONDecodeError all trigger fallback to Tier 2 result

### Empty/Invalid Input Handling
- [ ] Test `_verify_stage_artifact()` with None slug returns None (skip verification)
- [ ] Test with empty output_tail still works correctly through Tier 2

### Error State Rendering
- [ ] Not applicable — classify_outcome has no user-visible output

## Test Impact

- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_end_turn_with_test_pass` — UPDATE: mock artifact verification to return True
- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_end_turn_build_with_pr` — UPDATE: mock artifact verification to return True
- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_none_stop_reason_uses_patterns` — UPDATE: mock artifact verification for ISSUE stage
- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_critique_ready_to_build_is_success` — UPDATE: mock artifact verification for CRITIQUE stage
- [ ] `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcome::test_critique_needs_revision_is_fail` — no change needed (fail path, no artifact check)

## Rabbit Holes

- Replacing all text patterns with purely API-based classification — overkill; some stages (ISSUE, PLAN, CRITIQUE, PATCH) have no meaningful external artifact to verify
- Building a generic artifact registry — unnecessary abstraction for a focused fix
- Adding retry logic to `gh` calls — one call with a timeout is sufficient; retries add complexity and latency

## Risks

### Risk 1: Subprocess latency in hot path
**Impact:** Each `classify_outcome()` call could add up to 3s of latency if the `gh` call is slow
**Mitigation:** Tight timeout (3s), only called when Tier 2 returns "success" (not on every call), and graceful fallback on timeout

### Risk 2: GitHub API rate limiting
**Impact:** Frequent `gh pr view` calls could hit rate limits during parallel SDLC processing
**Mitigation:** Only one `gh` call per stage completion (not per classification attempt). The existing `_infer_stage_from_artifacts()` already makes similar calls without issues.

## Race Conditions

No race conditions identified — `classify_outcome()` is called synchronously within `_record_stage_on_parent()`, and each stage completion is processed sequentially per session.

## No-Gos (Out of Scope)

- Refactoring `_infer_stage_from_artifacts()` — that method serves a different purpose (display progress inference vs. outcome classification)
- Adding artifact verification to ISSUE, PLAN, CRITIQUE, PATCH, or MERGE stages — these either lack verifiable external artifacts or are low-risk
- Implementing webhook-based verification instead of polling — separate architectural change

## Update System

No update system changes required — this is a bridge-internal change to `pipeline_state.py` with no new dependencies or configuration.

## Agent Integration

No agent integration required — `classify_outcome()` is called internally by the subagent_stop hook. No MCP server changes or bridge import changes needed.

## Documentation

- [ ] Update `docs/features/pipeline-state-machine.md` to document the three-tier classification approach
- [ ] Add inline docstrings on `_verify_stage_artifact()` method

## Success Criteria

- [ ] REVIEW classification checks GitHub API for actual PR review, not just text patterns
- [ ] BUILD classification verifies PR exists via GitHub API
- [ ] TEST classification uses structured numeric parsing, not just "passed" substring
- [ ] DOCS classification checks for actual docs/ file changes in PR
- [ ] Graceful fallback to text patterns when artifact verification fails (timeout, network error, missing slug)
- [ ] All existing `TestClassifyOutcome` tests updated and passing
- [ ] New tests cover artifact verification success, failure, and fallback paths
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (artifact-verification)**
  - Name: artifact-builder
  - Role: Implement _verify_stage_artifact() and integrate into classify_outcome()
  - Agent Type: builder
  - Resume: true

- **Validator (artifact-verification)**
  - Name: artifact-validator
  - Role: Verify implementation meets all success criteria
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard tier 1 agents sufficient for this work.

## Step by Step Tasks

### 1. Add _verify_stage_artifact() method
- **Task ID**: build-artifact-verification
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_state_machine.py
- **Assigned To**: artifact-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_verify_stage_artifact(self, stage: str) -> bool | None` method to PipelineStateMachine
- For REVIEW: `gh pr view session/{slug} --json reviewDecision` — verify reviewDecision is non-empty
- For BUILD: `gh pr view session/{slug} --json number` — verify number exists
- For TEST: Parse output_tail for numeric pass/fail counts using regex (e.g., `(\d+) passed`)
- For DOCS: `gh pr diff session/{slug} --name-only` — check for `docs/` paths
- Return True (verified), False (artifact missing), or None (unable to verify)
- Wrap all subprocess calls with 3s timeout, return None on any exception

### 2. Integrate Tier 3 into classify_outcome()
- **Task ID**: build-tier3-integration
- **Depends On**: build-artifact-verification
- **Validates**: tests/unit/test_pipeline_state_machine.py
- **Assigned To**: artifact-builder
- **Agent Type**: builder
- **Parallel**: false
- After Tier 2 returns "success" for REVIEW, BUILD, TEST, DOCS: call `_verify_stage_artifact(stage)`
- If artifact check returns False -> override to "ambiguous" (don't trust the text)
- If artifact check returns None -> keep the Tier 2 "success" result (graceful fallback)
- If artifact check returns True -> confirm "success"

### 3. Update existing tests and add new tests
- **Task ID**: build-tests
- **Depends On**: build-tier3-integration
- **Validates**: tests/unit/test_pipeline_state_machine.py
- **Assigned To**: artifact-builder
- **Agent Type**: builder
- **Parallel**: false
- Update all existing TestClassifyOutcome tests to mock `_verify_stage_artifact`
- Add tests: artifact verification returns True (confirm success)
- Add tests: artifact verification returns False (override to ambiguous)
- Add tests: artifact verification returns None (fallback to text result)
- Add tests: subprocess timeout triggers graceful fallback
- Add tests: missing slug skips artifact verification

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: artifact-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_pipeline_state_machine.py -v`
- Run `python -m ruff check bridge/pipeline_state.py`
- Run `python -m ruff format --check bridge/pipeline_state.py`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_pipeline_state_machine.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/pipeline_state.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/pipeline_state.py` | exit code 0 |
| Artifact method exists | `grep -c '_verify_stage_artifact' bridge/pipeline_state.py` | output > 0 |
| Tier 3 integrated | `grep -c '_verify_stage_artifact' bridge/pipeline_state.py` | output > 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions — the issue is well-defined, the code is well-understood, and the approach is straightforward.
