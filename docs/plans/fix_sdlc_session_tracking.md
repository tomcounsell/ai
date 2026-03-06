---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-06
tracking: https://github.com/valorengels/ai/issues/276
---

# Fix SDLC Session Tracking

## Problem

Two critical bugs prevent SDLC sessions from being tracked and routed correctly:

**Bug 1: Classifier gap.** `tools/classifier.py` only outputs `bug|feature|chore`, never `"sdlc"`. The `is_sdlc_job()` method's primary check (`classification_type == "sdlc"`) can never be true from the classifier alone. Messages like "SDLC issue 274" or anything referencing `/sdlc` get misclassified as `feature` or `chore`.

**Bug 2: Auto-continue session orphaning.** When `_enqueue_continuation()` fires (lines 1028-1040 in `agent/job_queue.py`), it calls `enqueue_job()` but does NOT pass `classification_type` from the original job. The continuation job gets `classification_type=None`, which means `_push_job()` creates a new `AgentSession` record with no classification. The SDLC mode, links (issue/plan/PR URLs), history entries, and stage progress from the original session are lost. The response renders without the structured SDLC template (stage progress line + link footer).

**Current behavior:**
- SDLC work is classified as `feature` or `chore` instead of `sdlc`
- Auto-continued SDLC sessions lose their stage progress and links
- Telegram responses for SDLC work lack the structured progress display

**Desired outcome:**
- Messages mentioning "SDLC", "/sdlc", "issue #N" are classified as `sdlc`
- Auto-continue preserves session identity (same session_id, same classification_type)
- SDLC sessions always render with stage progress and link footer

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

- **Classifier `sdlc` category**: Add `"sdlc"` to the classifier's output categories and prompt so it recognizes SDLC-related messages
- **Auto-continue classification propagation**: Pass `classification_type` through to continuation jobs in `_enqueue_continuation()`
- **Test coverage**: Add tests for all four scenarios identified in the issue

### Flow

Message arrives -> classifier outputs `sdlc` -> `is_sdlc_job()` returns True -> stage-aware auto-continue fires -> continuation preserves `classification_type=sdlc` -> all continuations route through SDLC pipeline -> structured template renders

### Technical Approach

1. **`tools/classifier.py`**: Add `"sdlc"` to the classification prompt's category list and the validation check. The prompt should instruct Haiku to classify messages containing SDLC references (issue numbers, `/sdlc`, pipeline language) as `sdlc`.

2. **`agent/job_queue.py` `_enqueue_continuation()`**: Add `classification_type=job.classification_type` to the `enqueue_job()` call so continuations inherit the original session's type.

3. **Tests**: Four test cases covering:
   - Classifier outputs `sdlc` for SDLC-related messages
   - `is_sdlc_job()` returns True for sessions with `classification_type="sdlc"`
   - Auto-continue passes `classification_type` through to the continuation job
   - SDLC sessions render with structured template (stage progress + link footer)

## Rabbit Holes

- Do NOT refactor the classifier into a more complex multi-step classification system -- a simple category addition is sufficient
- Do NOT attempt to retroactively fix sessions that were already misclassified in Redis
- Do NOT change the summarizer's template rendering logic -- the rendering already works correctly when `is_sdlc_job()` returns True

## Risks

### Risk 1: Classifier regression on existing categories
**Impact:** Non-SDLC messages could be misclassified as `sdlc`
**Mitigation:** The prompt change is narrow (adding one category with clear criteria). Existing test coverage for `bug|feature|chore` catches regressions.

### Risk 2: Auto-continue creates duplicate sessions in Redis
**Impact:** Multiple AgentSession records with the same session_id
**Mitigation:** This is existing behavior (each `_push_job` creates a new record). The fix only ensures the new record has the right `classification_type`. The `is_sdlc_job()` check on the fresh record will now work.

## No-Gos (Out of Scope)

- Refactoring the auto-continue architecture to reuse the same AgentSession record
- Changing how session_id is derived or how sessions are looked up
- Adding persistence for stage progress across sessions (the `[stage]` history entries already handle this via the fallback in `is_sdlc_job()`)

## Update System

No update system changes required -- this is a bug fix to internal bridge/classifier logic. No new dependencies or config files.

## Agent Integration

No agent integration required -- the classifier and job queue are bridge-internal components. No MCP server changes needed. The bridge already imports and calls the affected code directly.

## Documentation

- [ ] Update `docs/features/coaching-loop.md` to note the `sdlc` classification type and auto-continue propagation fix
- [ ] Add inline code comments on the `classification_type` propagation in `_enqueue_continuation()`

## Success Criteria

- [ ] `tools/classifier.py` accepts and outputs `"sdlc"` as a valid classification type
- [ ] Messages like "SDLC issue 274", "/sdlc fix the bug", "run the pipeline for issue #42" are classified as `sdlc`
- [ ] `is_sdlc_job()` returns `True` for sessions with `classification_type="sdlc"`
- [ ] `_enqueue_continuation()` passes `classification_type` to `enqueue_job()`
- [ ] Auto-continued SDLC sessions retain their `classification_type` across continuations
- [ ] Tests cover all four scenarios from issue #276
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (classifier-fix)**
  - Name: classifier-builder
  - Role: Fix classifier to support `sdlc` type and fix auto-continue propagation
  - Agent Type: builder
  - Resume: true

- **Test Engineer (session-tracking)**
  - Name: session-test-engineer
  - Role: Write tests for all four scenarios
  - Agent Type: test-engineer
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify all fixes work end-to-end
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix classifier to support sdlc type
- **Task ID**: build-classifier
- **Depends On**: none
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `"sdlc"` to `CLASSIFICATION_PROMPT` categories in `tools/classifier.py`
- Add `"sdlc"` to the validation check (`result["type"] not in [...]`)
- Update prompt to describe when `sdlc` should be used

### 2. Fix auto-continue classification propagation
- **Task ID**: build-propagation
- **Depends On**: none
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `classification_type=job.classification_type` to `_enqueue_continuation()` call to `enqueue_job()` in `agent/job_queue.py`

### 3. Write test coverage
- **Task ID**: build-tests
- **Depends On**: build-classifier, build-propagation
- **Assigned To**: session-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Test classifier outputs `sdlc` for SDLC messages
- Test `is_sdlc_job()` with `classification_type="sdlc"`
- Test `_enqueue_continuation` passes classification_type
- Test SDLC template rendering with stage progress

### 4. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/test_work_request_classifier.py tests/test_agent_session_lifecycle.py tests/test_stage_aware_auto_continue.py`
- Verify all success criteria met

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: classifier-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update coaching-loop docs
- Add inline comments

## Validation Commands

- `pytest tests/test_work_request_classifier.py -v` - classifier tests
- `pytest tests/test_agent_session_lifecycle.py -v` - session lifecycle tests
- `pytest tests/test_stage_aware_auto_continue.py -v` - auto-continue tests
- `python -m ruff check tools/classifier.py agent/job_queue.py` - lint check
