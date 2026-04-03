---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/655
last_comment_id:
plan_branch: main
---

# Add Integration Tests for Artifact Inference (No Mocks)

## Problem

The artifact inference system (`_infer_stage_from_artifacts()` and `get_display_progress()` in `bridge/pipeline_state.py`) was added in PR #647 but only has unit tests that mock subprocess and filesystem calls. Per the project's testing philosophy ("Real integration testing - No mocks, use actual APIs"), these methods need integration tests that exercise the real `gh` CLI and real filesystem to validate correctness against actual GitHub and plan file state.

**Current behavior:**
All 15 artifact inference tests in `tests/unit/test_pipeline_state_machine.py::TestArtifactInference` use `@patch("bridge.pipeline_state.subprocess.run")` and `@patch("bridge.pipeline_state.Path")`. They validate logic correctly but do not catch integration failures (e.g., `gh pr view` output format changes, plan file path resolution from wrong working directory, JSON field name mismatches with real GitHub API responses).

**Desired outcome:**
An integration test file that calls `_infer_stage_from_artifacts()` and `get_display_progress()` against real artifacts: actual plan files on disk and actual GitHub PRs in this repository. Tests validate that the inference produces correct results from real data, not synthetic mocks.

## Prior Art

- **PR #647**: Add artifact-based inference to pipeline state display -- shipped the feature with unit tests only, no integration tests.
- **PR #430**: Replace transcript-based stage detection with programmatic state machine -- shipped `PipelineStateMachine` with unit tests only.

No prior integration tests exist for artifact inference.

## Architectural Impact

- **New dependencies**: None. Tests use existing `gh` CLI and filesystem.
- **Interface changes**: None. Tests are purely additive.
- **Coupling**: No change. Tests import from `bridge.pipeline_state` which is already tested.
- **Reversibility**: Trivially reversible (delete the test file).

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- `gh` CLI must be authenticated (already is on all dev machines) and the repo must have at least one merged PR with a `session/` branch prefix and at least one plan file in `docs/plans/`.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` CLI authenticated | `gh auth status` | GitHub API access for `gh pr view` |
| Plan files exist | `ls docs/plans/*.md \| head -1` | Filesystem artifact for PLAN inference |
| Merged PR exists | `gh pr list --state merged --limit 1 --json number -q '.[0].number'` | GitHub artifact for BUILD inference |

## Solution

### Key Elements

- **Test file**: `tests/integration/test_artifact_inference.py` -- integration tests for `_infer_stage_from_artifacts()` and `get_display_progress()`
- **Real plan files**: Tests use actual plan files from `docs/plans/` (both active and completed) to validate PLAN/ISSUE/CRITIQUE inference
- **Real GitHub PRs**: Tests use actual merged PRs from this repo to validate BUILD/REVIEW/DOCS/MERGE inference
- **Mock session only**: The `AgentSession` is still mocked (it requires Redis), but all artifact checks use real filesystem and real `gh` CLI

### Flow

**Test setup** -> Create mock AgentSession (no stage_states) -> Call `get_display_progress(slug=REAL_SLUG)` -> Assert inferred stages match reality

### Technical Approach

- Discover a known-good slug at test collection time by scanning `docs/plans/` for an existing plan file
- Discover a known-good merged PR by querying `gh pr list --state merged` with a `session/` branch prefix
- Extract the slug from the branch name (strip `session/` prefix)
- Use these real slugs to test inference against actual artifacts
- Tests that require GitHub API access are marked with `pytest.mark.integration` (auto-applied by conftest)
- Tests validate both the internal `_infer_stage_from_artifacts()` and the public `get_display_progress()` API

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Test that `_infer_stage_from_artifacts()` with a nonexistent slug returns empty dict (no crash)
- [ ] Test that `get_display_progress()` with a slug whose PR branch does not exist returns stored state only

### Empty/Invalid Input Handling
- [ ] Test with empty string slug
- [ ] Test with slug containing special characters

### Error State Rendering
- Not applicable -- these are data-layer methods with no user-visible rendering

## Test Impact

No existing tests affected -- this is a purely additive integration test file. The existing unit tests in `tests/unit/test_pipeline_state_machine.py::TestArtifactInference` remain unchanged and continue to validate logic with mocks.

## Rabbit Holes

- Do not attempt to create temporary plan files or PRs for testing. Use real existing artifacts.
- Do not test `classify_outcome()` or state transitions -- those are already covered by unit tests.
- Do not add Redis integration testing for `AgentSession` persistence -- the session is correctly mocked since we only test artifact inference, not session state management.

## Risks

### Risk 1: Flaky tests from GitHub API rate limits or network issues
**Impact:** Tests fail spuriously in CI or on slow connections
**Mitigation:** Tests are in `tests/integration/` (not run by default in quick test runs). The `gh pr view` call has a 5-second timeout already built into the production code.

### Risk 2: Test assumptions break when referenced PRs or plan files are deleted
**Impact:** Tests fail because the expected artifact no longer exists
**Mitigation:** Tests discover artifacts dynamically at runtime rather than hardcoding specific PR numbers or slug names. Use `pytest.skip()` if no suitable artifact is found.

## Race Conditions

No race conditions identified -- tests are read-only queries against existing artifacts.

## No-Gos (Out of Scope)

- Dashboard integration changes (that is issue #656, separate scope)
- Modifying `_infer_stage_from_artifacts()` itself -- tests validate existing behavior
- Redis-based integration tests requiring a live `AgentSession`
- CI/CD pipeline changes

## Update System

No update system changes required -- this is a test-only change.

## Agent Integration

No agent integration required -- this is a test-only change with no new tools or bridge modifications.

## Documentation

- [ ] Add entry to `tests/README.md` under the `sdlc` marker section for the new test file
- [ ] Docstrings in the test file itself documenting test strategy and prerequisites

## Success Criteria

- [ ] `tests/integration/test_artifact_inference.py` exists with at least 8 test cases
- [ ] Tests run against real plan files and real GitHub PRs (no mocks on subprocess or Path)
- [ ] `pytest tests/integration/test_artifact_inference.py -v` passes
- [ ] Tests skip gracefully when artifacts are unavailable (no hard failures)
- [ ] Tests validate: PLAN inference from plan file, ISSUE inference from plan file, CRITIQUE inference from frontmatter, BUILD inference from PR existence, REVIEW inference from review decision, DOCS inference from PR files, MERGE inference from merged PR state
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (tests)**
  - Name: test-builder
  - Role: Implement integration test file
  - Agent Type: test-engineer
  - Resume: true

- **Validator (tests)**
  - Name: test-validator
  - Role: Verify tests pass and cover all scenarios
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create Integration Test File
- **Task ID**: build-tests
- **Depends On**: none
- **Validates**: `tests/integration/test_artifact_inference.py`
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/integration/test_artifact_inference.py`
- Implement dynamic artifact discovery fixtures (find real plan files, find real merged PRs)
- Implement test cases for each inference type (PLAN, ISSUE, CRITIQUE, BUILD, REVIEW, DOCS, MERGE)
- Implement edge case tests (nonexistent slug, empty slug, no PR found)
- Ensure all tests use real filesystem and real `gh` CLI calls

### 2. Update Test README
- **Task ID**: update-docs
- **Depends On**: build-tests
- **Assigned To**: test-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add new test file entry to `tests/README.md` under the `sdlc` marker section

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, update-docs
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_artifact_inference.py -v`
- Verify all tests pass
- Verify no mocks on subprocess or Path are used
- Verify tests skip gracefully when artifacts unavailable

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/integration/test_artifact_inference.py -v` | exit code 0 |
| No mocks used | `grep -c 'unittest.mock\|@patch\|MagicMock' tests/integration/test_artifact_inference.py` | output contains 0 |
| Lint clean | `python -m ruff check tests/integration/test_artifact_inference.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/integration/test_artifact_inference.py` | exit code 0 |
