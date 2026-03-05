---
status: Ready
type: bug
appetite: Small
owner: Solo dev
created: 2026-03-05
tracking: https://github.com/yudame/cuttlefish/issues/122
---

# Fix Pre-existing Test Failures on Main

## Problem

There are 4 test failures and 1 collection error on `main` that predate recent PRs. These failures block CI and mask new regressions.

**Current behavior:**
- `apps/ai/code_execution/tests/test_security.py` cannot be collected due to a `TypeError` from using `any` (builtin function) instead of `Any` (typing) in a type hint.
- `test_import_skips_dedup_when_audio_url_blank` fails with `ValueError: Podcast privacy cannot be changed after creation` because `import_podcast_feed` passes `privacy` in `update_or_create` defaults.
- Three `TestCraftTargetedPrompts` tests fail with `ValidationError: claude_prompt: Field required` because test fixtures lack the new `claude_prompt` field.

**Desired outcome:**
All 5 failures resolved; full test suite passes on `main`.

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

- **Fix 1 -- Type hint**: Change `any | None` to `Any | None` in `apps/ai/code_execution/exceptions.py:130`, adding `from typing import Any` import.
- **Fix 2 -- Import command**: Remove `privacy` from `update_or_create` defaults in `apps/podcast/management/commands/import_podcast_feed.py` so it is only set on creation, not on update. Move `privacy` to `create_defaults` (Django 5+ parameter) so it applies only when creating a new record.
- **Fix 3 -- Test fixtures**: Add `claude_prompt` field to all three `TargetedResearchPrompts` fixtures in `apps/podcast/tests/test_ai_tools/test_craft_research_prompt.py`.

### Technical Approach

**Fix 1** is a one-line type hint correction plus an import addition.

**Fix 2** uses Django's `update_or_create(create_defaults=...)` parameter. The `privacy` field moves from `defaults` to `create_defaults`, ensuring it is set only during initial creation and never triggers the `save()` validation on updates.

**Fix 3** adds `claude_prompt="..."` to each of the three `TargetedResearchPrompts(...)` constructor calls in the test file.

## Rabbit Holes

- Do not refactor the import command beyond the minimal fix. The `is_public` property and privacy model are unrelated concerns.
- Do not add new tests for the privacy validation in `Podcast.save()` -- that is existing tested behavior.

## Risks

### Risk 1: create_defaults availability
**Impact:** If Django version is <5.0, `create_defaults` is not available.
**Mitigation:** This project uses Django 6.0, confirmed by `pyproject.toml`. No issue.

## No-Gos (Out of Scope)

- Refactoring `import_podcast_feed` beyond the privacy fix
- Changing the `Podcast.save()` privacy validation logic
- Adding new feature functionality

## Update System

No update system changes required -- this is purely a bug fix for test failures.

## Agent Integration

No agent integration required -- these are internal code/test fixes.

## Documentation

No documentation changes required -- these are bug fixes with no user-facing impact.

## Success Criteria

- [ ] `apps/ai/code_execution/tests/test_security.py` collects without errors
- [ ] `test_import_skips_dedup_when_audio_url_blank` passes
- [ ] `test_returns_targeted_prompts` passes
- [ ] `test_prompt_includes_question_discovery` passes
- [ ] `test_logs_usage` (targeted prompts) passes
- [ ] Full test suite passes (`DJANGO_SETTINGS_MODULE=settings pytest`)

## Team Orchestration

### Team Members

- **Builder (fix-tests)**
  - Name: test-fixer
  - Role: Apply all three fixes
  - Agent Type: builder
  - Resume: true

- **Validator (verify-fixes)**
  - Name: test-validator
  - Role: Run test suite and verify all 5 failures are resolved
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix type hint in exceptions.py
- **Task ID**: fix-type-hint
- **Depends On**: none
- **Assigned To**: test-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add `from typing import Any` to `apps/ai/code_execution/exceptions.py`
- Change `any | None` to `Any | None` on line 130

### 2. Fix import command privacy handling
- **Task ID**: fix-import-privacy
- **Depends On**: none
- **Assigned To**: test-fixer
- **Agent Type**: builder
- **Parallel**: true
- In `apps/podcast/management/commands/import_podcast_feed.py`, move `"privacy": "public"` from `defaults` to `create_defaults` in the `update_or_create` call

### 3. Fix test fixtures for claude_prompt
- **Task ID**: fix-test-fixtures
- **Depends On**: none
- **Assigned To**: test-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add `claude_prompt="..."` to all three `TargetedResearchPrompts(...)` calls in `test_craft_research_prompt.py`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: fix-type-hint, fix-import-privacy, fix-test-fixtures
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest` and verify all 5 failures are resolved
- Confirm no new failures introduced

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/ai/code_execution/tests/test_security.py --collect-only` - verifies collection error is fixed
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_import_command.py::ImportBlankAudioUrlTestCase -v` - verifies import test passes
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_ai_tools/test_craft_research_prompt.py::TestCraftTargetedPrompts -v` - verifies all 3 targeted prompt tests pass
