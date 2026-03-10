---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-09
tracking: https://github.com/tomcounsell/ai/issues/325
---

# Clean Up Hook Dead Code from Observer Agent Migration

## Problem

After the Observer Agent migration (PR #321, issue #309), the hook files `.claude/hooks/pre_tool_use.py` and `.claude/hooks/post_tool_use.py` retain dead code that was converted to no-ops rather than fully removed during the migration to minimize diff churn.

**Current behavior:**
- `SKILL_TO_STAGE` dicts exist in both files but are only consumed by no-op functions
- `mark_stage_in_progress()` in `pre_tool_use.py` is called from `main()` but does nothing useful (the Observer Agent now handles stage tracking)
- `update_stage_progress_for_skill()` in `post_tool_use.py` is called from `main()` but is similarly dead (Observer Agent handles this)

**Desired outcome:**
- Both hook files contain zero dead code
- No functional behavior changes -- these functions already did nothing

## Prior Art

- **Issue #309**: Observer Agent -- replace auto-continue/summarizer with stage-aware SDLC steerer. This migration made the stage-tracking code in hooks redundant.
- **PR #321**: Observer Agent implementation. During patch rounds, the functions were converted to no-ops to reduce diff churn but the shells were left in place.
- **Issue #117**: Repo cleanup: delete obsolete files and directories -- prior cleanup precedent.

## Data Flow

Not applicable -- this is pure dead code removal with no data flow changes.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None -- removing private functions with no external callers
- **Coupling**: Decreases coupling by removing unused `tools.session_progress` subprocess calls
- **Data ownership**: No change
- **Reversibility**: Trivially reversible via git revert

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (automated)

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **pre_tool_use.py cleanup**: Remove `SKILL_TO_STAGE` dict, `mark_stage_in_progress()` function, and its call in `main()`
- **post_tool_use.py cleanup**: Remove `SKILL_TO_STAGE` dict, `update_stage_progress_for_skill()` function, and its call in `main()`
- **Docstring update**: Update module docstrings to no longer mention stage tracking

### Flow

Read files -> Delete dead code -> Verify no references remain -> Run tests -> Ship

### Technical Approach

- Grep confirms `SKILL_TO_STAGE`, `mark_stage_in_progress`, and `update_stage_progress_for_skill` are only referenced within these two files
- Straight deletion -- no refactoring or replacement needed
- Update module-level docstrings to accurately reflect what the hooks still do

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- the dead code being removed contains `except Exception: pass` blocks, but since the code is being deleted entirely, no new tests are needed

### Empty/Invalid Input Handling
- Not applicable -- pure deletion, no new or modified functions

### Error State Rendering
- Not applicable -- no user-visible output changes

## Rabbit Holes

- Do not refactor the remaining hook logic while removing dead code -- keep scope tight
- Do not remove `SKILL_TO_STAGE`-like mappings from other files (they may still be in use elsewhere)

## Risks

### Risk 1: Accidental removal of live code
**Impact:** Hook stops tracking SDLC state for file writes or bash commands
**Mitigation:** Only remove the three explicitly identified dead symbols. Verify with grep that no other code references them. The remaining functions (`update_sdlc_state_for_file_write`, `update_sdlc_state_for_bash`, `check_file_reminders`, `capture_git_baseline_once`) are all live and must be preserved.

## Race Conditions

No race conditions identified -- this is pure code deletion with no concurrency implications.

## No-Gos (Out of Scope)

- Refactoring remaining hook logic
- Removing other potentially unused code from unrelated files
- Changing any functional behavior of the hooks

## Update System

No update system changes required -- this is purely internal dead code removal with no new dependencies or config changes.

## Agent Integration

No agent integration required -- this is a hook-internal cleanup with no MCP server or bridge changes.

## Documentation

- [ ] No standalone feature documentation needed -- this is a chore removing dead code
- [ ] Update inline docstrings in both hook files to remove references to stage tracking

## Success Criteria

- [ ] `SKILL_TO_STAGE` removed from `pre_tool_use.py`
- [ ] `mark_stage_in_progress()` and its call removed from `pre_tool_use.py`
- [ ] `SKILL_TO_STAGE` removed from `post_tool_use.py`
- [ ] `update_stage_progress_for_skill()` and its call removed from `post_tool_use.py`
- [ ] `grep -r "SKILL_TO_STAGE\|mark_stage_in_progress\|update_stage_progress_for_skill" .claude/hooks/` returns nothing
- [ ] Module docstrings updated to reflect current functionality
- [ ] Existing tests pass (`/do-test`)
- [ ] No unused imports left behind after removal

## Team Orchestration

### Team Members

- **Builder (hook-cleanup)**
  - Name: hook-cleaner
  - Role: Remove dead code from both hook files
  - Agent Type: builder
  - Resume: true

- **Validator (hook-cleanup)**
  - Name: hook-validator
  - Role: Verify no references remain and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Remove dead code from pre_tool_use.py
- **Task ID**: build-pre-hook
- **Depends On**: none
- **Assigned To**: hook-cleaner
- **Agent Type**: builder
- **Parallel**: true
- Delete `SKILL_TO_STAGE` dict (lines 22-31)
- Delete `mark_stage_in_progress()` function (lines 34-76)
- Remove `mark_stage_in_progress(hook_input)` call from `main()` (line 125)
- Remove comment above the call (line 124)
- Update module docstring (line 2) to remove "mark SDLC stages in_progress"
- Remove unused imports (`os`, `subprocess`) if no longer needed

### 2. Remove dead code from post_tool_use.py
- **Task ID**: build-post-hook
- **Depends On**: none
- **Assigned To**: hook-cleaner
- **Agent Type**: builder
- **Parallel**: true
- Delete `SKILL_TO_STAGE` dict (lines 40-49)
- Delete `update_stage_progress_for_skill()` function (lines 258-330)
- Remove `update_stage_progress_for_skill(hook_input)` call from `main()` (line 358)
- Update module docstring (line 2) to remove stage tracking reference if present

### 3. Validate cleanup
- **Task ID**: validate-cleanup
- **Depends On**: build-pre-hook, build-post-hook
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -r "SKILL_TO_STAGE\|mark_stage_in_progress\|update_stage_progress_for_skill" .claude/hooks/` -- expect no output
- Verify no unused imports remain
- Run `python -m ruff check .claude/hooks/`
- Run `pytest tests/` to confirm nothing breaks

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-cleanup
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met
- Generate final report

## Validation Commands

- `grep -r "SKILL_TO_STAGE\|mark_stage_in_progress\|update_stage_progress_for_skill" .claude/hooks/` - should return nothing
- `python -m ruff check .claude/hooks/pre_tool_use.py .claude/hooks/post_tool_use.py` - no lint errors
- `python -m ruff format --check .claude/hooks/pre_tool_use.py .claude/hooks/post_tool_use.py` - properly formatted
- `pytest tests/` - all tests pass
