---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-07
tracking: https://github.com/tomcounsell/ai/issues/298
---

# Fix SDLC Pipeline Stall After Plan Phase

## Problem

When running `/sdlc` on a cross-project target (e.g., psyoptimal), the pipeline creates the plan successfully but then stalls instead of proceeding to `/do-build`. The agent gets stuck in the `checking-system-logs` background skill and never advances beyond the PLAN stage.

**Current behavior:**
1. `/sdlc` invokes `/do-plan` -- plan is created and committed successfully
2. `/do-plan` marks the PLAN stage as completed via `session_progress.py`
3. The agent produces a "plan created" output message
4. The auto-continue system classifies this output -- but the coaching message for the continuation does not explicitly re-invoke the SDLC pipeline
5. The agent gets sidetracked into a background reference skill (`checking-system-logs`) instead of returning to the SDLC dispatcher to invoke `/do-build`
6. Session runs for 45+ seconds without progressing, then times out or is killed

**Desired outcome:**
After `/do-plan` completes within an `/sdlc` pipeline run, the auto-continue coaching message should explicitly guide the agent back to the SDLC dispatcher's "After Dispatching" step, causing it to assess state and invoke `/do-build`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- all changes are internal to existing bridge/agent code.

## Solution

### Key Elements

- **SDLC-aware coaching**: When the auto-continue system re-enqueues a continuation for an SDLC job, the coaching message should include an explicit instruction to return to the SDLC pipeline and invoke the next stage
- **Stronger stage-aware coaching context**: The `_enqueue_continuation()` function should inject SDLC pipeline context (current stage progress, what stage comes next) into the coaching message

### Flow

Agent executes SDLC -> invokes `/do-plan` -> plan output produced -> stage-aware auto-continue fires (SDLC job with remaining stages) -> `_enqueue_continuation()` builds coaching message -> **coaching message now includes explicit SDLC next-step instruction** -> agent reads coaching and invokes `/do-build` -> pipeline continues

### Technical Approach

The fix is in `bridge/coach.py` and `agent/job_queue.py`. Two changes:

#### Change 1: SDLC pipeline coaching in `build_coaching_message()`

Currently, `build_coaching_message()` has tiers:
1. LLM coaching (from classifier rejection)
2. Skill-aware coaching (from plan file)
3. Plain "continue"

For SDLC stage-aware auto-continues, the coaching message is built with a `ClassificationResult` of `STATUS_UPDATE` with reason "Stage-aware auto-continue". The function then falls through to tier 2 or 3.

**The fix:** Add a new parameter `sdlc_stage_progress` (dict) to `build_coaching_message()`. When provided:
- Parse the progress dict to determine the current and next stages
- Build an explicit coaching message that says: "The SDLC pipeline is at stage X. Y is complete. Invoke /do-{next_stage} to continue the pipeline. Do NOT investigate logs or start other work."
- This takes priority over the generic skill coaching (insert as Tier 1c, after LLM coaching but before skill-aware coaching)

#### Change 2: Pass stage progress through `_enqueue_continuation()`

In `agent/job_queue.py`, the `_enqueue_continuation()` function is called from the stage-aware auto-continue path in `send_to_chat()`. Currently it calls `build_coaching_message()` without SDLC context.

**The fix:** When `coaching_source == "stage_aware"`, re-read the `AgentSession` to get current stage progress via `get_stage_progress()`, then pass it to `build_coaching_message()` as the new `sdlc_stage_progress` parameter.

## Failure Path Test Strategy

### Exception Handling Coverage
- No new exception handlers introduced. The new code is pure string formatting.
- Existing `try/except` in `_enqueue_continuation()` already handles session lookup failures.

### Empty/Invalid Input Handling
- [ ] Test `build_coaching_message()` with empty `sdlc_stage_progress` dict -- should fall through to existing tiers
- [ ] Test with all stages "pending" (edge case where progress tracking failed) -- should produce a safe generic SDLC message
- [ ] Test with all stages "completed" -- should not occur in practice (auto-continue wouldn't fire), but should produce a sensible message

### Error State Rendering
- Not applicable -- coaching messages are internal system prompts, not user-visible output

## Rabbit Holes

- Changing the auto-continue classification logic -- that's working correctly, it's the coaching message that's the problem
- Adding new SDLC-specific classification types -- the stage-aware routing already correctly identifies these as SDLC jobs with remaining stages
- Modifying the `checking-system-logs` skill -- it's a background reference skill, the issue is the agent drifting to it, not the skill itself
- Fixing cross-project working directory routing -- that's working correctly per the routing logic in `sdk_client.py`

## Risks

### Risk 1: Coaching message too prescriptive
**Impact:** Agent ignores the coaching or fights against it if the message is too rigid
**Mitigation:** Use the same supportive "[System Coach]" tone as existing coaching. Give the agent the *what* (invoke the next stage) not the *how* (exact commands).

### Risk 2: Stage progress stale when coaching is built
**Impact:** Coaching message references wrong stage if session_progress writes haven't flushed yet
**Mitigation:** Re-read session from Redis in `_enqueue_continuation()` (already done for the session lookup). The stage entries are written by `session_progress.py` as subprocess calls which complete before the agent output is produced.

## Race Conditions

No race conditions identified. The coaching message is built synchronously after the agent output is produced and the stage progress has already been written to Redis by `session_progress.py` (called as a subprocess within the agent's execution).

## No-Gos (Out of Scope)

- Don't change the auto-continue routing logic in `send_to_chat()`
- Don't modify the classifier or its prompts
- Don't change how `/do-plan` or other sub-skills work
- Don't add new Redis fields
- Don't change the `checking-system-logs` skill

## Update System

No update system changes required -- this is a bridge-internal coaching message change with no new dependencies or config files.

## Agent Integration

No agent integration required -- this is a bridge-internal change affecting how continuation coaching messages are composed. No MCP server changes, no `.mcp.json` changes.

## Documentation

- [ ] Update `docs/features/coaching-loop.md` to document the new SDLC-aware coaching tier
- [ ] Add entry to `docs/features/README.md` index table if coaching-loop.md is new

## Success Criteria

- [ ] When an SDLC pipeline runs `/do-plan` and it completes, the auto-continue coaching message explicitly mentions the next SDLC stage to invoke
- [ ] The coaching message includes stage progress context (which stages are done, which is next)
- [ ] `build_coaching_message()` with `sdlc_stage_progress` produces an SDLC-specific coaching message
- [ ] `build_coaching_message()` without `sdlc_stage_progress` continues to work as before (no regression)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (coach-enhancer)**
  - Name: coach-enhancer
  - Role: Add SDLC-aware coaching to build_coaching_message and wire it through _enqueue_continuation
  - Agent Type: builder
  - Resume: true

- **Validator (coaching-validator)**
  - Name: coaching-validator
  - Role: Verify coaching messages contain SDLC context and existing tests still pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add SDLC stage progress coaching tier to build_coaching_message
- **Task ID**: build-sdlc-coaching
- **Depends On**: none
- **Assigned To**: coach-enhancer
- **Agent Type**: builder
- **Parallel**: false
- Add `sdlc_stage_progress: dict | None = None` parameter to `build_coaching_message()` in `bridge/coach.py`
- Add a new tier (1c) that fires when `sdlc_stage_progress` is provided and has remaining stages
- The coaching message should: identify which stages are done, name the next stage, and tell the agent to invoke the corresponding `/do-{stage}` skill
- Map stage names to skill names: PLAN -> do-plan, BUILD -> do-build, TEST -> do-test, REVIEW -> do-pr-review, DOCS -> do-docs
- Include an explicit "do NOT investigate logs or start other work" directive

### 2. Wire stage progress into _enqueue_continuation
- **Task ID**: build-wire-progress
- **Depends On**: build-sdlc-coaching
- **Assigned To**: coach-enhancer
- **Agent Type**: builder
- **Parallel**: false
- In `agent/job_queue.py`, `_enqueue_continuation()`: when `coaching_source == "stage_aware"`, look up the session's stage progress via `agent_session.get_stage_progress()`
- Pass the stage progress dict to `build_coaching_message()` as `sdlc_stage_progress`
- Ensure the agent_session is re-read from Redis for fresh data (already happens in the session lookup)

### 3. Write unit tests for SDLC coaching
- **Task ID**: build-tests
- **Depends On**: build-wire-progress
- **Assigned To**: coach-enhancer
- **Agent Type**: builder
- **Parallel**: false
- Test `build_coaching_message()` with various `sdlc_stage_progress` dicts:
  - PLAN completed, BUILD pending -> mentions `/do-build`
  - BUILD completed, TEST pending -> mentions `/do-test`
  - All stages completed -> falls through to existing tiers
  - Empty dict -> falls through to existing tiers
- Test that existing coaching (without sdlc_stage_progress) is unchanged

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: coaching-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify new coaching message format is clear and actionable
- Confirm no regressions in existing coaching tiers

## Validation Commands

- `python -m pytest tests/ -v --tb=short -k coach` - Coach-related tests pass
- `grep "sdlc_stage_progress" bridge/coach.py` - New parameter exists
- `grep "get_stage_progress" agent/job_queue.py` - Stage progress is read and passed
- `python -m pytest tests/ -v --tb=short` - All tests pass
