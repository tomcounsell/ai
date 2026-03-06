---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-06
tracking: https://github.com/valorengels/ai/issues/264
---

# Fix Stale SDLC Test Assertions

## Problem

Two tests in `tests/unit/test_sdk_client_sdlc.py` fail on `main` because they assert an outdated ordering of the system prompt.

**Current behavior:**
`load_system_prompt()` in `agent/sdk_client.py` puts SDLC_WORKFLOW **first**, then `---`, then SOUL.md, then `---`, then completion criteria. But two tests in `TestLoadSystemPromptInjection` assert the old ordering where SOUL.md came before SDLC_WORKFLOW:

1. `test_sdlc_workflow_is_between_soul_and_criteria` asserts `soul_pos < sdlc_pos` (SOUL before SDLC) — wrong.
2. `test_prompt_contains_separator_before_sdlc` looks for `---` in the 50 chars preceding SDLC_WORKFLOW — but SDLC is now the very first block, so there is no preceding separator.

**Desired outcome:**
Both tests pass on `main` by asserting the actual ordering: SDLC_WORKFLOW first, then SOUL.md, then completion criteria.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Test 1 fix**: Reverse the ordering assertion so `sdlc_pos < soul_pos` (SDLC before SOUL).
- **Test 2 fix**: Check for `---` separator **after** SDLC_WORKFLOW (between SDLC and SOUL), not before it.

### Technical Approach

In `tests/unit/test_sdk_client_sdlc.py`, class `TestLoadSystemPromptInjection`:

1. `test_sdlc_workflow_is_between_soul_and_criteria` (line ~99):
   - Change `assert soul_pos < sdlc_pos` to `assert sdlc_pos < soul_pos`
   - Update the assertion message to reflect the new ordering
   - Keep the `criteria_pos` check (SDLC before criteria is still true)

2. `test_prompt_contains_separator_before_sdlc` (line ~117):
   - Rename to `test_prompt_contains_separator_between_sdlc_and_soul`
   - Instead of checking for `---` in the 50 chars **before** SDLC, check for `---` in the text **between** SDLC_WORKFLOW and SOUL.md content

## Rabbit Holes

- Do not refactor or restructure the test file beyond the two failing tests
- Do not change the docstring at the top of the file (it references the old ordering but is just a comment)

## Risks

### Risk 1: Docstring at top of file references old ordering
**Impact:** Misleading comment, but no functional issue
**Mitigation:** Update the module-level docstring line that says "injects SDLC_WORKFLOW between SOUL.md and completion criteria" to reflect "injects SDLC_WORKFLOW before SOUL.md and completion criteria"

## No-Gos (Out of Scope)

- No changes to `agent/sdk_client.py` — the source of truth is correct
- No changes to any other test classes in the file
- No refactoring of the prompt structure

## Update System

No update system changes required — this is a test-only fix with no deployment impact.

## Agent Integration

No agent integration required — this is a test-only fix.

## Documentation

No documentation changes needed. This is a test-only bug fix that corrects two stale assertions in an existing unit test file. No feature docs, external docs, or API documentation are affected.

## Success Criteria

- [ ] `test_sdlc_workflow_is_between_soul_and_criteria` passes
- [ ] `test_prompt_contains_separator_before_sdlc` (renamed) passes
- [ ] All other tests in `test_sdk_client_sdlc.py` still pass
- [ ] `python -m ruff check tests/unit/test_sdk_client_sdlc.py` passes
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (test-fix)**
  - Name: test-fixer
  - Role: Update the two failing test assertions
  - Agent Type: builder
  - Resume: true

- **Validator (test-fix)**
  - Name: test-validator
  - Role: Verify all tests in the file pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix the two failing tests
- **Task ID**: build-test-fix
- **Depends On**: none
- **Assigned To**: test-fixer
- **Agent Type**: builder
- **Parallel**: false
- Update module-level docstring to reflect correct ordering
- Fix `test_sdlc_workflow_is_between_soul_and_criteria`: assert `sdlc_pos < soul_pos`
- Fix `test_prompt_contains_separator_before_sdlc`: check separator between SDLC and SOUL blocks

### 2. Validate all tests pass
- **Task ID**: validate-tests
- **Depends On**: build-test-fix
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdk_client_sdlc.py -v`
- Verify all tests pass including the two fixed ones
- Run `python -m ruff check tests/unit/test_sdk_client_sdlc.py`

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-tests
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full validation commands
- Verify all success criteria met

## Validation Commands

- `pytest tests/unit/test_sdk_client_sdlc.py -v` — all tests pass
- `python -m ruff check tests/unit/test_sdk_client_sdlc.py` — no lint errors
