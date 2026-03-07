---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-03-07
tracking: https://github.com/yudame/cuttlefish/issues/127
---

# UI/UX Tests for Episode Editor User Journey

## Problem

The episode editor has a documented 9-stage user journey (`docs/plans/episode-editor-user-journey.md`) with 5 critical gaps, 4 important gaps, and 4 nice-to-haves identified. But there are no tests that systematically verify what exists and fail on what's missing.

**Current behavior:**
Existing tests (`test_views.py`, `test_workflow_views.py`) cover page loads, auth, and basic HTMX but don't test the user journey end-to-end. No tests verify that episode creation has a form, that the workflow page has editable fields, that artifacts are viewable, or that publishing has a confirmation step.

**Desired outcome:**
Two test files that map 1:1 to the user journey stages. Tests for existing features pass; tests for missing features are marked `xfail` with descriptive reasons, creating a concrete build backlog from test failures.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work uses Django test client and existing test infrastructure.

## Solution

### Key Elements

- **UI Test Suite** (`test_ui_episode_editor.py`): Django TestCase tests verifying page loads, element presence, form fields, HTMX responses, and access controls for every podcast route
- **UX Test Suite** (`test_ux_episode_flows.py`): Django TestCase tests verifying end-to-end user flows through the 9 journey stages, including transitions between stages

### Flow

Each test maps to a user journey stage from `docs/plans/episode-editor-user-journey.md`:

**Stage 1** (Navigate) → **Stage 2** (Create Draft) → **Stage 3** (Edit Details) → **Stage 4-6** (Research Pipeline) → **Stage 7** (Post-Production) → **Stage 8** (Publish) → **Stage 9** (View Published)

### Technical Approach

- Use Django `TestCase` with `self.client` (same pattern as existing `test_views.py`)
- Use `@override_settings(STORAGES=SIMPLE_STORAGES)` for file storage
- Mark missing features with `@pytest.mark.xfail(reason="Gap: ...", strict=True)` so they:
  - Document exactly what's missing
  - Fail loudly if someone accidentally makes them pass
  - Don't break CI (xfail is expected failure)
- Group tests by journey stage using test class hierarchy
- Reference existing URL patterns from `apps/podcast/urls.py`

### Test Coverage Map

#### UI Tests (`test_ui_episode_editor.py`)

| Stage | What to Test | Expected |
|-------|-------------|----------|
| 1 | Podcast list loads, detail loads, "+ New Episode" button for staff | PASS |
| 2 | Episode creation form has title/description fields | XFAIL |
| 2 | Owner (not just staff) can create episodes | XFAIL |
| 3 | Workflow page step 1 has editable title field | XFAIL |
| 3 | Workflow page step 1 has description/brief textarea | XFAIL |
| 4-6 | Workflow page shows artifact content inline | XFAIL |
| 6 | Workflow page has audio upload form | XFAIL |
| 6 | Workflow page has audio preview player | XFAIL |
| 7 | Workflow page has metadata edit form | XFAIL |
| 7 | Workflow page has cover art preview | XFAIL |
| 8 | Workflow step 12 has publish confirmation | XFAIL |
| 9 | Episode detail shows audio player, resources | PASS |
| 9 | Episode detail has navigation back to podcast | PASS |

#### UX Tests (`test_ux_episode_flows.py`)

| Stage | What to Test | Expected |
|-------|-------------|----------|
| 1→2 | Staff navigates to podcast, clicks New Episode, lands on creation form | XFAIL |
| 2→3 | After creating episode with title, redirects to workflow with title set | XFAIL |
| 3→4 | User edits description on workflow, clicks Start Pipeline | XFAIL (edit part) |
| 4→6 | Workflow shows quality gate pause, user can review content | XFAIL |
| 6→7 | Audio generation completes, user can preview before proceeding | XFAIL |
| 7→8 | User reviews metadata, can edit before publishing | XFAIL |
| 8→9 | After publishing, success page shows links to episode | XFAIL |
| Full | Complete happy path: idea → published episode | XFAIL |

## Rabbit Holes

- **Browser-based E2E tests**: Tempting to use Playwright/Selenium but Django test client is sufficient for verifying element presence and form behavior. Browser tests add complexity without proportional value here.
- **Testing actual pipeline execution**: The pipeline tests exist in `test_task_steps.py`. These tests verify the UI around the pipeline, not the pipeline itself.
- **Real audio/file uploads**: Use mocked storage. Testing actual Supabase uploads is out of scope.

## Risks

### Risk 1: xfail tests become stale
**Impact:** Tests marked xfail might silently start passing when features are built, without anyone noticing.
**Mitigation:** Use `strict=True` on xfail markers. If a test unexpectedly passes, pytest treats it as a failure, forcing someone to remove the xfail marker.

## Race Conditions

No race conditions identified -- all tests use Django's `TestCase` with transaction isolation.

## No-Gos (Out of Scope)

- Building the missing features (that's what the tests identify for future work)
- Browser automation / Playwright / Selenium tests
- Testing the 12-phase pipeline execution itself
- Load testing or performance testing
- Testing external services (Supabase, NotebookLM, etc.)

## Update System

No update system changes required -- this is test code only, internal to the cuttlefish repo.

## Agent Integration

No agent integration required -- these are standard Django tests run via pytest.

## Documentation

- [ ] Update `docs/plans/episode-editor-user-journey.md` with a "Test Coverage" section linking to the test files
- [ ] Inline docstrings on each test class referencing the journey stage it covers

## Success Criteria

- [ ] `test_ui_episode_editor.py` exists with tests for all 9 journey stages
- [ ] `test_ux_episode_flows.py` exists with end-to-end flow tests
- [ ] Tests for existing features (stages 1, 9, workflow basic loads) PASS
- [ ] Tests for missing features are marked `xfail` with descriptive reasons
- [ ] `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_ui_episode_editor.py apps/podcast/tests/test_ux_episode_flows.py -v` passes (xfails expected)
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (tests)**
  - Name: test-builder
  - Role: Write both test files
  - Agent Type: builder
  - Resume: true

- **Validator (tests)**
  - Name: test-validator
  - Role: Verify tests run correctly, xfails are properly marked
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using `builder` and `validator` from Tier 1.

## Step by Step Tasks

### 1. Write UI Test Suite
- **Task ID**: build-ui-tests
- **Depends On**: none
- **Assigned To**: test-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `apps/podcast/tests/test_ui_episode_editor.py`
- Write test classes for each journey stage (1-9)
- Tests for stages 1 and 9 should PASS (existing features)
- Tests for stages 2-8 gaps should use `@pytest.mark.xfail(reason="Gap: ...", strict=True)`
- Follow existing test patterns from `test_views.py` (TestCase, SIMPLE_STORAGES, setUp fixtures)

### 2. Write UX Flow Test Suite
- **Task ID**: build-ux-tests
- **Depends On**: none
- **Assigned To**: test-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `apps/podcast/tests/test_ux_episode_flows.py`
- Write test classes for multi-stage user flows
- Test transitions between journey stages
- Mark flows that hit missing features as xfail
- Include a "full happy path" test that exercises the complete journey

### 3. Validate Tests
- **Task ID**: validate-tests
- **Depends On**: build-ui-tests, build-ux-tests
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_ui_episode_editor.py apps/podcast/tests/test_ux_episode_flows.py -v`
- Verify passing tests actually pass
- Verify xfail tests fail for the right reasons
- Report test count: X passed, Y xfailed

### 4. Documentation
- **Task ID**: document-tests
- **Depends On**: validate-tests
- **Assigned To**: test-builder
- **Agent Type**: builder
- **Parallel**: false
- Add "Test Coverage" section to `docs/plans/episode-editor-user-journey.md`
- Ensure all test docstrings reference the journey stage

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-tests
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v`
- Verify no regressions in existing tests
- Verify all success criteria met

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_ui_episode_editor.py -v` - UI tests run
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_ux_episode_flows.py -v` - UX tests run
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` - All podcast tests pass
- `python -m ruff check apps/podcast/tests/test_ui_episode_editor.py apps/podcast/tests/test_ux_episode_flows.py` - Code quality
