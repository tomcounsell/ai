---
status: In Progress
type: feature
appetite: Medium
owner: Valor
created: 2026-02-26
tracking: https://github.com/tomcounsell/ai/issues/178
---

# Stage-Aware Auto-Continue

## Problem

Auto-continue currently relies on an LLM classifier to decide whether agent output is a status update (auto-continue), a completion (deliver), or a question (pause). This is fragile:

- The classifier can misjudge completions as status updates, causing premature stops or runaway loops
- The MAX_AUTO_CONTINUES=3 cap exists purely because the classifier cannot reliably detect when work is truly done
- Non-SDLC messages and SDLC messages go through the same classification path despite having fundamentally different termination conditions

**Current behavior:**
Every agent output goes to `classify_output()` in `bridge/summarizer.py`. If classified as `STATUS_UPDATE` and `auto_continue_count < MAX_AUTO_CONTINUES`, the job is re-enqueued with a coaching message. The classifier has no knowledge of pipeline state -- it only sees prose output.

**Desired outcome:**
For SDLC jobs, auto-continue decisions are driven primarily by pipeline stage progress tracked in `AgentSession.history`. The classifier is only consulted as a final gate when all stages are complete or for non-SDLC jobs. Fewer false stops mid-pipeline, fewer runaway loops, and the auto-continue counter becomes a safety net rather than the primary termination mechanism.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on decision matrix edge cases)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites -- this work has no external dependencies. Issue #177 (AgentSession with history tracking) is already merged (PR #180).

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| AgentSession model with history field | `python -c "from models.agent_session import AgentSession, SDLC_STAGES; print(SDLC_STAGES)"` | Verify unified model is available |
| `get_stage_progress()` method exists | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'get_stage_progress')"` | Stage parsing is available |

## Solution

### Key Elements

- **Stage progress checker**: A function that reads `AgentSession.history` and determines remaining SDLC stages, used as the primary auto-continue signal for SDLC jobs
- **SDLC job detector**: Logic to determine whether a job is an SDLC pipeline job (has stage history entries) vs a casual/ad-hoc job
- **Decision matrix router**: Replaces the single `classify_output()` gate with a two-path decision: stage-aware path for SDLC jobs, classifier path for non-SDLC jobs

### Flow

**Job output produced** → Check if SDLC job (has stage history) → **Yes**: Check remaining stages → Stages remain? Auto-continue → All done? Classify final output → **No (non-SDLC)**: Use existing classifier-based routing

### Technical Approach

1. **Add `has_remaining_stages()` and `is_sdlc_job()` helpers to AgentSession**

   `is_sdlc_job()` returns `True` if the session's `history` contains at least one `[stage]` entry. `has_remaining_stages()` returns `True` if any SDLC stage in the progress dict is not `completed`.

2. **Modify `send_to_chat` closure in `agent/job_queue.py`**

   Before calling `classify_output()`, check if this is an SDLC job with remaining stages. If so, auto-continue without classification. The decision matrix:

   | Pipeline state | Output classification | Action |
   |---|---|---|
   | Stages remaining | (skipped) | Auto-continue |
   | All stages done | Completion | Deliver to user |
   | All stages done | Status (no evidence) | Coach + continue |
   | Any stage failed | Error/blocker | Deliver to user |
   | No stages (non-SDLC) | Question | Deliver to user |
   | No stages (non-SDLC) | Status | Auto-continue (existing behavior) |

3. **Relax MAX_AUTO_CONTINUES for SDLC jobs**

   For SDLC jobs with remaining stages, the counter is not consulted (stage progress is the natural termination condition). The counter still applies as a safety net for non-SDLC jobs and for the "all stages done" final classification gate.

4. **Detect failed stages from history**

   If a `[stage]` entry contains "FAILED" or "ERROR", treat it as a hard stop and deliver to the user instead of auto-continuing.

5. **Preserve existing behavior for non-SDLC jobs**

   Casual Q&A, one-off tasks, and non-pipeline messages continue using the classifier-based auto-continue unchanged. No change to the coaching system.

## Rabbit Holes

- **Modifying the SDLC skills to write more granular stage events**: The existing `append_history("stage", ...)` calls in the pipeline are sufficient. Don't redesign how stages get tracked.
- **Removing the classifier entirely**: The classifier is still needed for non-SDLC jobs and as a final gate. Don't remove it.
- **Adding stage-based coaching**: Stage awareness could inform coaching messages (e.g., "You just finished BUILD, next is TEST"). This is tempting but is a separate feature -- the existing coaching tiers are sufficient.
- **Persisting stage progress separately from history**: The `get_stage_progress()` method already parses history entries. Don't add a separate data structure.

## Risks

### Risk 1: Stage history not populated for some SDLC jobs
**Impact:** Jobs that should use stage-aware routing fall back to classifier routing
**Mitigation:** `is_sdlc_job()` returns False when no stage entries exist, so the fallback is the existing classifier path. No worse than current behavior.

### Risk 2: Stage detection is too aggressive (false positive SDLC detection)
**Impact:** Non-SDLC jobs get auto-continued past their natural stopping point
**Mitigation:** Detection requires actual `[stage]` entries in history, which are only written by SDLC skill invocations. Ad-hoc conversations won't have these.

### Risk 3: Infinite auto-continue if stages are never marked complete
**Impact:** Job runs forever, never delivering output
**Mitigation:** Keep MAX_AUTO_CONTINUES as a hard safety cap even for SDLC jobs, but set it higher (e.g., 10 instead of 3). Also rely on the existing job health monitor timeout (45min standard, 2.5hr build).

## No-Gos (Out of Scope)

- Redesigning the coaching system to be stage-aware (separate feature)
- Changing how SDLC skills record stage transitions in history
- Modifying the classifier prompt or classification logic
- Adding new SDLC stages or changing the pipeline order
- Changing job timeout values

## Update System

No update system changes required -- this feature is purely internal to the bridge auto-continue logic. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this is a bridge-internal change. The agent does not know about auto-continue; it just sees "continue" messages. The stage-aware logic reads from the existing `AgentSession.history` field which SDLC skills already populate.

## Documentation

- [ ] Update `docs/features/coaching-loop.md` to document the stage-aware routing path
- [ ] Add entry to `docs/features/README.md` index table for stage-aware auto-continue
- [ ] Code comments on the decision matrix in `agent/job_queue.py`
- [ ] Updated docstrings for new AgentSession methods

## Success Criteria

- [ ] SDLC jobs with remaining stages auto-continue without hitting the classifier
- [ ] SDLC jobs with all stages complete go through the classifier as a final gate
- [ ] Non-SDLC jobs use the existing classifier-based auto-continue unchanged
- [ ] Failed stage entries cause immediate delivery to user
- [ ] MAX_AUTO_CONTINUES still acts as a safety cap for SDLC jobs (set higher, e.g., 10)
- [ ] Existing auto-continue tests pass without modification
- [ ] New tests cover the stage-aware decision matrix
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (stage-aware-routing)**
  - Name: routing-builder
  - Role: Implement stage-aware auto-continue logic in job_queue.py and AgentSession
  - Agent Type: builder
  - Resume: true

- **Validator (stage-aware-routing)**
  - Name: routing-validator
  - Role: Verify stage-aware routing works correctly for SDLC and non-SDLC jobs
  - Agent Type: validator
  - Resume: true

- **Builder (test-suite)**
  - Name: test-builder
  - Role: Write tests for the stage-aware decision matrix
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (docs-update)**
  - Name: docs-writer
  - Role: Update coaching-loop docs and feature index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add AgentSession helper methods
- **Task ID**: build-session-helpers
- **Depends On**: none
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `is_sdlc_job()` method that checks if history contains `[stage]` entries
- Add `has_remaining_stages()` method that checks if any stage is not completed
- Add `has_failed_stage()` method that detects failed/error stage entries

### 2. Implement stage-aware routing in send_to_chat
- **Task ID**: build-routing
- **Depends On**: build-session-helpers
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Modify `send_to_chat` closure in `_execute_job` to check stage progress before calling `classify_output()`
- Implement the decision matrix: stages remaining -> auto-continue, all done -> classify, failed -> deliver
- Add SDLC-specific MAX_AUTO_CONTINUES constant (higher than 3)
- Ensure non-SDLC path is unchanged

### 3. Validate implementation
- **Task ID**: validate-routing
- **Depends On**: build-routing
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify stage-aware path is triggered for SDLC jobs
- Verify classifier path is used for non-SDLC jobs
- Verify failed stages cause delivery

### 4. Write tests for stage-aware decision matrix
- **Task ID**: build-tests
- **Depends On**: build-routing
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Test SDLC job with remaining stages -> auto-continue (no classifier call)
- Test SDLC job with all stages done -> classifier consulted
- Test non-SDLC job -> existing classifier path
- Test failed stage -> immediate delivery
- Test safety cap still applies to SDLC jobs
- Ensure existing tests in test_auto_continue.py still pass

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-routing, build-tests
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/coaching-loop.md` with stage-aware routing section
- Add entry to `docs/features/README.md` index table
- Add code comments on the decision matrix

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Validation Commands

- `pytest tests/test_auto_continue.py -v` - Existing auto-continue tests still pass
- `pytest tests/ -k "stage_aware" -v` - New stage-aware tests pass
- `black . && ruff check .` - Code quality
- `python -c "from models.agent_session import AgentSession; s = AgentSession(); assert hasattr(s, 'is_sdlc_job')"` - New methods exist
