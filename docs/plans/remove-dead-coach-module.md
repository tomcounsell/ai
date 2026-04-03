---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/654
last_comment_id:
---

# Remove dead coach module (bridge/coach.py)

## Problem

`bridge/coach.py` is dead code. It was originally the coaching message builder for context-aware auto-continue, but its sole exported function `build_coaching_message` is not imported or called by any production code. The only consumer is its own test file `tests/unit/test_coach.py`.

**Current behavior:**
The module and its 54 tests exist, pass, and add to test suite runtime and maintenance burden without providing any value. Several documentation files reference it as if it were active, creating a misleading picture of the system architecture.

**Desired outcome:**
The module, its tests, and all stale references are removed. Documentation accurately reflects that coaching is handled inline by the summarizer's `coaching_message` field and the nudge loop.

## Prior Art

- **Issue #130**: "Merge coach and classifier into a single LLM pass" -- Closed. The classifier absorbed coaching responsibility via the `coaching_message` field on `ClassificationResult`.
- **Issue #124 / PR #126**: "Coaching loop: context-aware auto-continue messages" -- Original implementation of `bridge/coach.py`.
- **Issue #563 / PR #601**: "Wire SDLC pipeline graph routing into runtime" -- Updated coach to use graph-based routing, but the module was already unused by this point.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None -- no production code imports from `bridge/coach.py`
- **Coupling**: Decreases -- removes a dead module that imports from `bridge/pipeline_graph.py` and `bridge/summarizer.py`
- **Data ownership**: No change
- **Reversibility**: Trivial -- can be restored from git history

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Delete `bridge/coach.py`**: Remove the dead module entirely
- **Delete `tests/unit/test_coach.py`**: Remove the orphaned test file
- **Clean documentation references**: Update docs that reference `bridge/coach.py` as an active component

### Technical Approach

1. Delete `bridge/coach.py` and `tests/unit/test_coach.py`
2. Update documentation files that reference `bridge/coach.py`:
   - `docs/features/sdlc-stage-handoff.md` (line 91): Remove or correct the coach reference
   - `docs/features/pipeline-graph.md` (lines 101, 109, 120): Remove coach from integration points
   - `tests/README.md` (line 123): Remove the test_coach.py row from the test index
3. Verify no Python imports reference the deleted module

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is a deletion-only change

### Empty/Invalid Input Handling
- Not applicable -- no new or modified functions

### Error State Rendering
- Not applicable -- no user-visible output changes

## Test Impact

- [ ] `tests/unit/test_coach.py` (all 54 tests) -- DELETE: tests for removed module

## Rabbit Holes

- Do not refactor or modify the `coaching_message` field on `ClassificationResult` or the summarizer's coaching logic -- those are the live replacement and work correctly
- Do not touch `bridge/message_quality.py` NARRATION_COACHING_MESSAGE -- that is unrelated to the dead coach module
- Do not modify `agent/agent_session_queue.py` coaching_message parameter -- that is live nudge loop code

## Risks

### Risk 1: Hidden import discovered at runtime
**Impact:** ImportError if something dynamically imports bridge.coach
**Mitigation:** grep confirms zero imports outside of test_coach.py. The code impact finder also found zero references.

## Race Conditions

No race conditions identified -- this is a file deletion with no runtime behavior changes.

## No-Gos (Out of Scope)

- Refactoring the live coaching_message flow in the summarizer
- Modifying the nudge loop's coaching_message parameter
- Updating completed plan docs that reference coach (e.g., `docs/plans/wire-pipeline-graph-563.md`) -- those are historical records

## Update System

No update system changes required -- this is purely removing dead code. No new dependencies, no config changes.

## Agent Integration

No agent integration required -- bridge/coach.py was not exposed via MCP and had no agent-facing interface.

## Documentation

- [ ] Update `docs/features/pipeline-graph.md` to remove coach references from integration points
- [ ] Update `docs/features/sdlc-stage-handoff.md` to remove stale coach reference
- [ ] Update `tests/README.md` to remove test_coach.py row from the test index

### Inline Documentation
- No new code comments needed -- this is a deletion

## Success Criteria

- [ ] `bridge/coach.py` deleted
- [ ] `tests/unit/test_coach.py` deleted
- [ ] `grep -r "from bridge.coach\|import coach\|bridge/coach" --include="*.py"` returns zero results
- [ ] `grep -r "bridge/coach" docs/features/` returns zero results
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Delete dead module and update references
  - Agent Type: builder
  - Resume: true

- **Validator (cleanup)**
  - Name: cleanup-validator
  - Role: Verify no remaining references
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Delete dead module and tests
- **Task ID**: build-delete
- **Depends On**: none
- **Validates**: tests/unit/ (run full suite to confirm nothing breaks)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `bridge/coach.py`
- Delete `tests/unit/test_coach.py`
- Delete `tests/unit/__pycache__/test_coach.cpython-312-pytest-*.pyc` if present

### 2. Update documentation references
- **Task ID**: build-docs
- **Depends On**: none
- **Validates**: grep for stale references
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove coach row from `tests/README.md` line 123
- Remove coach references from `docs/features/pipeline-graph.md` lines 101, 109, 120
- Remove coach reference from `docs/features/sdlc-stage-handoff.md` line 91

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-delete, build-docs
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -r "from bridge.coach\|import coach\|bridge/coach" --include="*.py"` -- expect zero results
- Run `grep -r "bridge/coach" docs/features/` -- expect zero results
- Run `pytest tests/ -x -q` -- expect all pass
- Verify success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No Python imports | `grep -r "from bridge.coach\|from bridge import coach" --include="*.py" .` | exit code 1 |
| No doc references | `grep -r "bridge/coach" docs/features/` | exit code 1 |
| Module deleted | `test ! -f bridge/coach.py` | exit code 0 |
| Tests deleted | `test ! -f tests/unit/test_coach.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None -- scope is clear and fully validated by grep.
