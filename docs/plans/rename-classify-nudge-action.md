---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/688
last_comment_id:
---

# Rename classify_nudge_action to determine_delivery_action

## Problem

The nudge loop has two classifier functions with confusingly similar `classify_*` names that do completely different things. A developer reading the code must inspect both implementations to understand which does what.

**Current behavior:**
`classify_nudge_action()` (sync, pure function returning delivery action strings) and `classify_output()` (async, LLM-based content classifier) both start with `classify_`, despite operating at different levels of the pipeline.

**Desired outcome:**
The delivery routing function is named `determine_delivery_action()`, making the distinction immediately clear: one function *determines a delivery action*, the other *classifies content type*.

## Prior Art

- **Issue #674 / PR #682**: Renamed vestigial "coach" terminology to "nudge_feedback" -- related naming cleanup, merged successfully
- **Issue #676**: Summarizer integration audit -- produced the audit report that originally flagged this naming confusion
- **Issue #683 / PR #684**: Summarizer integration cleanup -- fixed stale docs and consolidated config, merged

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (automated PR review)

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Function rename**: Change the function name from `classify_nudge_action` to `determine_delivery_action` in its definition and all references
- **Import updates**: Update all import statements in test files
- **Documentation updates**: Update all markdown references

### Flow

Mechanical find-and-replace across 12 files, no logic changes.

### Technical Approach

- Global rename of the function name (definition, call sites, imports, comments, docs)
- Verify with `grep -rn "classify_nudge_action"` returning zero results
- All existing tests pass unchanged (only the name changes, not behavior)

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is a pure rename with no logic changes

### Empty/Invalid Input Handling
- No new or modified functions -- the function signature and behavior are unchanged

### Error State Rendering
- No user-visible output changes

## Test Impact

- [ ] `tests/unit/test_nudge_loop.py::TestNonSDLCTeammateNudge` -- UPDATE: rename import and all call sites from `classify_nudge_action` to `determine_delivery_action`
- [ ] `tests/unit/test_qa_nudge_cap.py::TestQANudgeCap` -- UPDATE: rename import and all call sites from `classify_nudge_action` to `determine_delivery_action`
- [ ] `tests/unit/test_duplicate_delivery.py` -- UPDATE: rename comment reference on line 156

## Rabbit Holes

- Do not refactor the function's internals or change its return values
- Do not rename `classify_output()` in the same change -- that is a separate concern if needed
- Do not reorganize imports or move the function to a different module

## Risks

### Risk 1: Missed reference
**Impact:** Runtime NameError or broken documentation link
**Mitigation:** Post-rename grep for `classify_nudge_action` across all `.py` and `.md` files must return zero results. Verification table includes this check.

## Race Conditions

No race conditions identified -- this is a static rename with no runtime behavior changes.

## No-Gos (Out of Scope)

- Renaming `classify_output()` or any other function
- Changing function signatures, return values, or behavior
- Refactoring the nudge loop logic
- Moving the function to a different module

## Update System

No update system changes required -- this is a code rename with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this rename affects internal function names only. No MCP servers, bridge imports, or tool registrations are involved.

## Documentation

### Inline Documentation
- [ ] Update docstring of `determine_delivery_action()` if it references its own old name

### Existing Docs
- [ ] Update references in `docs/guides/summarizer-integration-audit.md` (3 occurrences)
- [ ] Update references in `docs/features/summarizer-format.md` (1 occurrence)
- [ ] Update references in `docs/features/reaction-semantics.md` (1 occurrence)
- [ ] Update references in `docs/plans/pm-telegram-tool.md` (2 occurrences)

No new feature documentation needed -- this is a rename of an existing internal function.

## Success Criteria

- [ ] `classify_nudge_action` renamed to `determine_delivery_action` in function definition
- [ ] All imports updated
- [ ] All call sites updated
- [ ] All documentation references updated
- [ ] All existing tests pass with no behavior changes
- [ ] `grep -rn "classify_nudge_action" --include="*.py" --include="*.md"` returns 0 results
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (rename)**
  - Name: renamer
  - Role: Execute the mechanical rename across all files
  - Agent Type: builder
  - Resume: true

- **Validator (verify)**
  - Name: rename-validator
  - Role: Confirm zero stale references and all tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rename production code
- **Task ID**: build-rename-prod
- **Depends On**: none
- **Validates**: tests/unit/test_nudge_loop.py, tests/unit/test_qa_nudge_cap.py
- **Assigned To**: renamer
- **Agent Type**: builder
- **Parallel**: true
- Rename function definition in `agent/agent_session_queue.py:57`
- Rename call site in `agent/agent_session_queue.py:2044`

### 2. Rename test references
- **Task ID**: build-rename-tests
- **Depends On**: none
- **Validates**: tests/unit/test_nudge_loop.py, tests/unit/test_qa_nudge_cap.py, tests/unit/test_duplicate_delivery.py
- **Assigned To**: renamer
- **Agent Type**: builder
- **Parallel**: true
- Update import and call sites in `tests/unit/test_nudge_loop.py`
- Update import and call sites in `tests/unit/test_qa_nudge_cap.py`
- Update comment in `tests/unit/test_duplicate_delivery.py:156`

### 3. Update documentation
- **Task ID**: build-rename-docs
- **Depends On**: none
- **Assigned To**: renamer
- **Agent Type**: builder
- **Parallel**: true
- Update 3 references in `docs/guides/summarizer-integration-audit.md`
- Update 1 reference in `docs/features/summarizer-format.md`
- Update 1 reference in `docs/features/reaction-semantics.md`
- Update 2 references in `docs/plans/pm-telegram-tool.md`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-rename-prod, build-rename-tests, build-rename-docs
- **Assigned To**: rename-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn "classify_nudge_action" --include="*.py" --include="*.md"` and confirm zero results
- Run `pytest tests/unit/test_nudge_loop.py tests/unit/test_qa_nudge_cap.py tests/unit/test_duplicate_delivery.py -x -q`
- Run `python -m ruff check agent/agent_session_queue.py`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_nudge_loop.py tests/unit/test_qa_nudge_cap.py tests/unit/test_duplicate_delivery.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py` | exit code 0 |
| No stale references | `grep -rn "classify_nudge_action" --include="*.py" --include="*.md"` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- this is a fully scoped mechanical rename with no ambiguity.
