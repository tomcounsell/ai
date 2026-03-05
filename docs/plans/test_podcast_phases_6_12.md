---
status: Ready
type: chore
appetite: Medium
owner: Claude
created: 2026-03-05
tracking: https://github.com/yudame/cuttlefish/issues/91
---

# Test Podcast Pipeline Phases 6-12

## Problem

The podcast production pipeline has 12 phases. Phases 1-5 were tested on 2026-02-19 and are working. Phases 6-12 (Cross-Validation through Publishing) have no automated test coverage for their task-level orchestration and service-layer integration.

**Current behavior:**
The task functions in `apps/podcast/tasks.py` for Phases 6-12 (`step_cross_validation`, `step_master_briefing`, `step_synthesis`, `step_episode_planning`, `step_audio_generation`, `step_transcribe_audio`, `step_generate_chapters`, `step_cover_art`, `step_metadata`, `step_companions`, `step_publish`) lack unit tests that verify:
- Correct workflow advancement after each step
- Proper fan-in/fan-out for parallel steps (Publishing Assets)
- Quality gate behavior (wave_1, wave_2)
- Error handling / fail_step calls
- Correct next-step enqueueing

**Desired outcome:**
Comprehensive unit tests covering each task step in Phases 6-12 with mocked AI/external service calls, verifying workflow state transitions, artifact creation, and error handling.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. Tests mock all AI and external API calls.

## Solution

### Key Elements

- **Task step tests**: Unit tests for each `@task` function in Phases 6-12, verifying workflow state machine transitions
- **Service integration tests**: Tests for service functions (`cross_validate`, `write_briefing`, `synthesize_report`, `plan_episode_content`, `transcribe_audio`, `generate_episode_chapters`, `generate_cover_art`, `write_episode_metadata`, `generate_companions`, `publish_episode`) with mocked AI backends
- **Quality gate tests**: Dedicated tests for `check_quality_gate` with both wave_1 and wave_2 gates, including edge cases
- **Fan-in signal tests for Publishing Assets**: Tests verifying the `post_save` signal correctly detects when all publishing artifacts (cover-art, metadata, companion-*) are complete

### Flow

**Test setup** -> Create Episode + EpisodeWorkflow at correct step -> Call task function with mocked services -> **Assert** workflow state, artifacts, and next enqueued task

### Technical Approach

- All AI tool calls mocked with `unittest.mock.patch` -- no real API calls
- Each test creates DB fixtures (Podcast, Episode, EpisodeWorkflow, EpisodeArtifact) at the correct workflow state for the phase being tested
- Use `.call()` method on tasks (Django's ImmediateBackend) instead of `.enqueue()` to run synchronously
- Mock `.enqueue()` on downstream tasks to verify correct chaining without actually running them
- Test both happy path and error/failure paths for each step

## Rabbit Holes

- Do not attempt to run end-to-end with real AI services -- mocking is sufficient
- Do not refactor the task pipeline itself -- this is a testing-only change
- Do not add integration tests that require audio files, Supabase, or OpenRouter

## Risks

### Risk 1: Mocking complexity for nested service calls
**Impact:** Tests could become brittle if mock paths change
**Mitigation:** Mock at the service function level (e.g., `analysis.cross_validate`), not deep inside AI tool internals. This matches the existing test patterns in `test_services_analysis.py`.

## No-Gos (Out of Scope)

- No changes to production code (tasks.py, services/, models/)
- No end-to-end tests with real API calls
- No audio file processing tests
- No RSS feed integration tests (already covered in `test_feeds.py`)

## Update System

No update system changes required -- this is purely test code.

## Agent Integration

No agent integration required -- this is test infrastructure.

## Documentation

### Inline Documentation
- [ ] Test docstrings explaining what each test verifies

## Success Criteria

- [ ] Tests for `step_cross_validation` -- happy path + error handling
- [ ] Tests for `step_master_briefing` -- happy path + wave_1 gate pass + wave_1 gate fail
- [ ] Tests for `step_synthesis` -- happy path + error handling
- [ ] Tests for `step_episode_planning` -- happy path + wave_2 gate pass + wave_2 gate fail
- [ ] Tests for `step_transcribe_audio` -- happy path + error handling
- [ ] Tests for `step_generate_chapters` -- happy path + fan-out to publishing
- [ ] Tests for `step_cover_art` -- happy path + error handling
- [ ] Tests for `step_metadata` -- happy path + error handling
- [ ] Tests for `step_companions` -- happy path + error handling
- [ ] Tests for `step_publish` -- happy path, verifies workflow reaches "complete"
- [ ] Tests for publishing assets fan-in signal
- [ ] All existing tests continue to pass
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (test-writer)**
  - Name: test-builder
  - Role: Write all test files for Phases 6-12
  - Agent Type: test-engineer
  - Resume: true

- **Validator (test-runner)**
  - Name: test-validator
  - Role: Run the full test suite and verify pass rates
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create test file for task steps Phases 6-12
- **Task ID**: build-task-tests
- **Depends On**: none
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `apps/podcast/tests/test_task_steps.py`
- Write tests for each task function: `step_cross_validation`, `step_master_briefing`, `step_synthesis`, `step_episode_planning`, `step_transcribe_audio`, `step_generate_chapters`, `step_cover_art`, `step_metadata`, `step_companions`, `step_publish`
- Each test class sets up fixtures at the correct workflow step
- Mock AI service calls and `.enqueue()` on downstream tasks
- Test both happy path and error/failure paths

### 2. Add publishing assets fan-in signal tests
- **Task ID**: build-signal-tests
- **Depends On**: none
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Extend `apps/podcast/tests/test_signals.py` with tests for publishing assets fan-in
- Test detection of when cover-art + metadata + companion-* artifacts are all populated

### 3. Run full test suite
- **Task ID**: validate-all
- **Depends On**: build-task-tests, build-signal-tests
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v`
- Verify all new tests pass
- Verify no existing tests broken

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_task_steps.py -v` -- new task step tests
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_signals.py -v` -- signal tests including new publishing fan-in
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` -- full podcast test suite
