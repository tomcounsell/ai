---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-15
tracking: https://github.com/tomcounsell/ai/issues/414
last_comment_id:
---

# Observer Graph Routing Fix

## Problem

PR #412 upgraded the SDLC pipeline to a directed graph and removed pipeline navigation language from `/do-*` skills. But the Observer's typed outcome fast path still relies on `outcome.next_skill` to build the coaching message — which is now `None`.

**Current behavior:**
After `/do-build` completes successfully, the Observer sends: *"BUILD completed successfully. Continue with the next pipeline stage."* The worker agent doesn't know which skill to invoke and stops. The pipeline stalls after BUILD.

**Desired outcome:**
The Observer resolves the next skill from the pipeline graph when `outcome.next_skill` is `None`, producing: *"BUILD completed successfully. Continue with `/do-test`."* The pipeline continues without stalling.

## Prior Art

- **PR #412**: Upgrade SDLC pipeline to directed graph with cycles — introduced this regression by removing `next_skill` from skill outputs without updating the Observer's typed outcome handler.

## Data Flow

1. **Worker agent** completes `/do-build` and emits a `SkillOutcome` with `status="success"`, `stage="BUILD"`, `next_skill=None`
2. **Observer.run()** parses the outcome at line 529
3. **Typed outcome fast path** (line 557): checks `outcome.status == "success"` and `session.has_remaining_stages()`
4. **Line 559**: `next_skill = outcome.next_skill or "the next pipeline stage"` — resolves to vague string
5. **Coaching message** sent back to worker with no explicit skill reference
6. **Worker** doesn't know what to do → stops → pipeline stalls

**Fixed flow** (step 4): When `outcome.next_skill` is `None`, call `_next_sdlc_skill(self.session)` to resolve the next `(stage, skill)` from the pipeline graph. Coaching message then explicitly names `/do-test`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this is a targeted code fix.

## Solution

### Key Elements

- **Graph-aware fallback**: When `outcome.next_skill` is `None`, resolve the next skill using `_next_sdlc_skill()` which already queries the pipeline graph
- **Test coverage**: Add a test that verifies the Observer produces an explicit skill reference when `outcome.next_skill` is `None`

### Technical Approach

In `bridge/observer.py`, line 559, replace:

```python
next_skill = outcome.next_skill or "the next pipeline stage"
```

With:

```python
if outcome.next_skill:
    next_skill = outcome.next_skill
else:
    next_info = _next_sdlc_skill(self.session)
    next_skill = next_info[1] if next_info else "the next pipeline stage"
```

This reuses the existing `_next_sdlc_skill()` function (lines 244-306) which already:
- Reads stage progress from the session
- Calls `get_next_stage()` from the pipeline graph
- Handles cycle counting and the REVIEW/PR guard

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] If `_next_sdlc_skill()` raises an exception, fall back to the vague string (defensive)

### Empty/Invalid Input Handling
- [ ] When `_next_sdlc_skill()` returns `None` (all stages complete or unknown state), the fallback string "the next pipeline stage" is used

### Error State Rendering
- Not applicable — this is internal routing logic, not user-facing output

## Rabbit Holes

- **Refactoring the entire typed outcome flow** — Don't restructure the Observer's decision tree. Just bridge the gap at line 559.
- **Removing `next_skill` from `SkillOutcome`** — It's still useful for skills that want to explicitly override the graph. Keep the field, just don't rely on it.

## Risks

### Risk 1: `_next_sdlc_skill()` returns stale data if stage transitions haven't been applied yet
**Impact:** Could route to the wrong stage
**Mitigation:** The stage detector runs before the typed outcome handler (line 531), so stage progress is already up-to-date when line 559 executes.

## Race Conditions

No race conditions identified. The Observer runs synchronously — `_next_sdlc_skill()` reads session state that was just updated by `apply_transitions()` on the same call path.

## No-Gos (Out of Scope)

- No changes to the pipeline graph itself
- No changes to `/do-*` skills
- No refactoring of the Observer decision tree
- No changes to `SkillOutcome` dataclass

## Update System

No update system changes required — this is a bug fix to an existing module.

## Agent Integration

No agent integration required — this is an internal Observer routing fix.

## Documentation

No documentation changes needed. This is a 3-line bug fix in `bridge/observer.py` that restores the routing behavior already described in `docs/features/pipeline-graph.md` (created by PR #412). The fix makes the implementation match the existing documentation — no new concepts or interfaces are introduced.

## Success Criteria

- [ ] When `outcome.next_skill` is `None`, the coaching message includes an explicit `/do-*` skill reference resolved from the pipeline graph
- [ ] When `outcome.next_skill` is set, it is used directly (existing behavior preserved)
- [ ] When `_next_sdlc_skill()` returns `None`, the fallback string is used
- [ ] Unit test added covering the `next_skill=None` scenario
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (fix)**
  - Name: observer-fixer
  - Role: Apply the fix and add test
  - Agent Type: builder
  - Resume: true

- **Validator (fix)**
  - Name: fix-validator
  - Role: Verify fix works and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Apply Observer fix
- **Task ID**: build-fix
- **Depends On**: none
- **Assigned To**: observer-fixer
- **Agent Type**: builder
- **Parallel**: true
- Modify `bridge/observer.py` line 559: replace `outcome.next_skill or "the next pipeline stage"` with graph-aware resolution via `_next_sdlc_skill(self.session)`
- Add unit test in `tests/unit/test_observer.py` covering: typed outcome success with `next_skill=None` produces explicit skill reference

### 2. Validate fix
- **Task ID**: validate-fix
- **Depends On**: build-fix
- **Assigned To**: fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_observer.py -x -q`
- Run `pytest tests/unit/test_pipeline_graph.py -x -q`
- Run `python -m ruff check bridge/observer.py`
- Verify coaching message contains `/do-test` when BUILD succeeds

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-fix
- **Assigned To**: fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_observer.py tests/unit/test_pipeline_graph.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/observer.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/observer.py` | exit code 0 |
