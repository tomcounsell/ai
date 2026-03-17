---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/252
last_comment_id:
---

# Template Reflection Task and Developer Guide

## Problem

The reflections system has 15 steps but no canonical pattern for adding new ones. Contributors must reverse-engineer existing steps to understand the method signature, findings collection, progress recording, and test structure.

**Current behavior:**
Adding a new reflection step requires reading multiple existing steps and tests to piece together the pattern. No documentation exists explaining the conventions.

**Desired outcome:**
A copy-paste-ready template step (disk space check) and a developer guide that makes adding new steps straightforward.

## Prior Art

- **PR #245**: Refactor daydream to reflections -- established the current multi-step architecture
- **PR #259**: Remove LessonLearned, add branch & plan cleanup step -- most recent step addition (step 14 at the time), demonstrates the pattern
- **PR #136**: Reactivate Daydream with self-reflection -- original system design

No prior issues found for a template/guide specifically.

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

- **Template step (step 16)**: `step_disk_space_check` -- a minimal, self-contained step that checks available disk space on the project volume
- **Developer guide**: `docs/features/adding-reflection-tasks.md` with copy-paste template, registration instructions, test pattern, naming conventions
- **Template test**: Test class in `tests/unit/test_reflections.py` covering the new step

### Technical Approach

The new step must follow the established pattern visible in all existing steps:

1. `async def step_disk_space_check(self) -> None:` with docstring
2. Local `findings: list[str]` collected during execution
3. Findings added via `self.state.add_finding("disk_space_check", text)` for each finding
4. `self.state.step_progress["disk_space_check"] = {"findings": len(findings), ...}` at the end
5. Wrapped in try/except with `logger.exception()` for graceful failure
6. Registered as `(16, "Disk Space Check", self.step_disk_space_check)` in `self.steps`

Implementation: use `shutil.disk_usage()` to check available space on the project volume. Record a finding if free space is below 10GB.

Note: The issue says 14 steps and Step 15, but the codebase currently has 15 steps (through "Feature Docs Audit"). The new step will be Step 16.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new step has a try/except block -- test must verify that exceptions are caught and logged, not propagated

### Empty/Invalid Input Handling
- [ ] Test behavior when `shutil.disk_usage()` is called on a non-existent path (mocked)

### Error State Rendering
- Not applicable -- no user-visible output beyond findings list

## Rabbit Holes

- Do not add alerting, notifications, or auto-cleanup for low disk space -- just report
- Do not monitor multiple volumes or remote filesystems -- project volume only
- Do not add configurable thresholds -- hardcode 10GB, can be parameterized later if needed

## Risks

### Risk 1: Step numbering drift
**Impact:** Issue says Step 15 but current code already has 15 steps
**Mitigation:** Use Step 16 and update the module docstring step list accordingly

## Race Conditions

No race conditions identified -- disk space check is a pure read operation with no shared mutable state.

## No-Gos (Out of Scope)

- Automated disk cleanup or remediation
- Configurable thresholds via environment variables
- Multi-volume or network filesystem monitoring
- Integration with external monitoring systems

## Update System

No update system changes required -- this is a purely internal addition to an existing script with no new dependencies.

## Agent Integration

No agent integration required -- reflections runs as a standalone scheduled script, not through the agent/MCP pipeline.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/adding-reflection-tasks.md` -- the developer guide (this IS the deliverable)
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/reflections.md` to mention 16 steps (was 14)

### Inline Documentation
- [ ] Docstring on `step_disk_space_check` serving as the canonical example
- [ ] Update module-level docstring step list in `scripts/reflections.py`

## Success Criteria

- [ ] `step_disk_space_check` present and registered as Step 16 in `self.steps`
- [ ] Step follows all standard patterns (findings, step_progress, try/except)
- [ ] `docs/features/adding-reflection-tasks.md` created with copy-paste template and test pattern
- [ ] Corresponding test class in `tests/unit/test_reflections.py`
- [ ] `docs/features/README.md` index updated
- [ ] Module docstring in `scripts/reflections.py` updated to list Step 16
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (reflection-step)**
  - Name: step-builder
  - Role: Implement disk space check step and developer guide
  - Agent Type: builder
  - Resume: true

- **Validator (reflection-step)**
  - Name: step-validator
  - Role: Verify step follows all patterns and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add disk space check step
- **Task ID**: build-step
- **Depends On**: none
- **Assigned To**: step-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `step_disk_space_check` method to `ReflectionRunner` in `scripts/reflections.py`
- Register as `(16, "Disk Space Check", self.step_disk_space_check)` in `self.steps`
- Update module-level docstring to include Step 16
- Use `shutil.disk_usage()` on `PROJECT_ROOT`, finding if free < 10GB

### 2. Add test for disk space check step
- **Task ID**: build-test
- **Depends On**: none
- **Assigned To**: step-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `TestDiskSpaceCheck` class to `tests/unit/test_reflections.py`
- Test normal case (plenty of space, no findings)
- Test low space case (mock disk_usage to return <10GB free, expect finding)
- Test exception handling (mock disk_usage to raise, verify graceful handling)

### 3. Create developer guide
- **Task ID**: build-guide
- **Depends On**: build-step
- **Assigned To**: step-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/adding-reflection-tasks.md`
- Include copy-paste step method template
- Include step registration instructions
- Include standard test pattern
- Include naming conventions (step key, finding key, progress key)

### 4. Update documentation index
- **Task ID**: build-docs-index
- **Depends On**: build-guide
- **Assigned To**: step-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add "Adding Reflection Tasks" entry to `docs/features/README.md`
- Update `docs/features/reflections.md` step count

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-step, build-test, build-guide, build-docs-index
- **Assigned To**: step-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_reflections.py -x -q`
- Verify step is registered in `self.steps` list
- Verify developer guide contains template code
- Verify README.md has new entry

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_reflections.py -x -q` | exit code 0 |
| Step registered | `grep -c "Disk Space Check" scripts/reflections.py` | output > 0 |
| Guide exists | `test -f docs/features/adding-reflection-tasks.md` | exit code 0 |
| README updated | `grep -c "Adding Reflection Tasks" docs/features/README.md` | output > 0 |
| Format clean | `python -m ruff format --check scripts/reflections.py` | exit code 0 |
