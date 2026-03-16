---
status: Ready
type: feature
appetite: Small
owner: Valor
created: 2026-03-16
tracking: https://github.com/tomcounsell/ai/issues/424
last_comment_id:
---

# Test Impact Plan Section

## Problem

When a planned feature changes existing system behavior, existing tests break. The plan template has no section requiring planners to identify affected tests upfront. Builder agents discover stale or broken tests at build time — context-switching from implementation to test archaeology with no guidance on whether to modify, replace, or delete failing tests.

**Current behavior:**
- The [Failure Path Test Strategy](https://github.com/tomcounsell/ai/blob/main/.claude/skills/do-plan/PLAN_TEMPLATE.md#L146-L162) section only covers new failure paths (exceptions, empty inputs, error rendering)
- The `Validates` field in Step-by-Step Tasks notes `(create)` for new tests but doesn't map existing test breakage
- Builders discover broken assertions mid-build with zero upfront guidance

**Desired outcome:**
- Plans that change behavior include an upfront audit of affected existing tests
- Each affected test listed with disposition: UPDATE / DELETE / REPLACE
- Validation hook enforces the section exists with actionable content or explicit exemption
- Builders start work already knowing which tests to modify

## Prior Art

- **[#422](https://github.com/tomcounsell/ai/issues/422)**: Enhance do-plan with spike tasks, RFC review, INFRA doc, and test mapping — identified this gap; shipped other enhancements but test impact was not addressed
- **[PR #423](https://github.com/tomcounsell/ai/pull/423)**: Shipped #422 enhancements (6 files, 244 lines) — confirmed `Validates` field only covers new test creation
- **[#330](https://github.com/tomcounsell/ai/issues/330)**: Machine-readable Definition of Done — established the pattern of validation hooks enforcing plan sections (led to `validate_file_contains.py`, `validate_documentation_section.py`)

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Three files to modify, one to create. Established patterns to follow. Straightforward.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Template section**: New `## Test Impact` section in `PLAN_TEMPLATE.md` with structured guidance
- **Validation hook**: `validate_test_impact_section.py` enforcing section completeness
- **Hook registration**: Add to `settings.json` Write/Edit hooks and `validate_file_contains.py` invocation
- **CLAUDE.md update**: Document as a required plan section alongside existing three

### Flow

**Planner writes plan** → `## Test Impact` section filled with affected test audit → **Write hook fires** → `validate_file_contains.py` checks section exists → `validate_test_impact_section.py` checks content quality → **Builder reads plan** → clear test modification guidance available

### Technical Approach

- Mirror `validate_documentation_section.py` structure exactly — same `find_newest_plan_file()`, same extract/validate pattern, same exit codes (0 pass, 2 block)
- Section positioned after `## Failure Path Test Strategy`, before `## Rabbit Holes` — groups all test-related planning together
- Accept two forms of valid content: (1) checklist items listing affected tests with dispositions, or (2) explicit "No existing tests affected" with justification (50+ chars)
- Add `'## Test Impact'` to the existing `--contains` flag list in `validate_file_contains.py` hook invocation in `settings.json`

## Test Impact

No existing tests affected — this is a new template section and validation hook. No existing test assertions depend on the plan template's section list or the set of registered validation hooks.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `validate_test_impact_section.py` must handle missing files, empty content, and malformed plans without crashing (mirror `validate_documentation_section.py` error handling)
- [ ] If no plan file auto-detected, validator exits 0 (pass-through, not block)

### Empty/Invalid Input Handling
- [ ] Validator handles empty `## Test Impact` section content (returns failure with guidance)
- [ ] Validator handles section with only placeholder text like `[TBD]` or `...`

### Error State Rendering
- [ ] Validation failure message includes the template example so the agent can self-correct

## Rabbit Holes

- Don't auto-detect which tests will break — that's the planner's job using `code_impact_finder` and test grep. The section is a structured prompt, not automation.
- Don't retroactively add `## Test Impact` to existing plan documents in `docs/plans/` — only enforce on new/modified plans going forward.
- Don't create a separate tool for test impact analysis — the existing `Validates` field + grep workflow is sufficient for discovery; this section is about documenting the result.

## Risks

### Risk 1: Existing plans trigger validation failures
**Impact:** Editing any old plan would fail the new hook
**Mitigation:** `validate_file_contains.py` and `validate_test_impact_section.py` only check the *newest* plan file (by git status + mtime), not all plan files. Existing plans are untouched.

## Race Conditions

No race conditions identified — all operations are synchronous file writes and reads during hook execution, single-threaded.

## No-Gos (Out of Scope)

- Automated test impact detection tooling
- Retroactive updates to existing plan documents
- Changes to the `Validates` field semantics in Step-by-Step Tasks
- Integration with CI/CD test runners

## Update System

No update system changes required — this feature modifies only plan template, validation hooks, and documentation within the repo. No new dependencies, no config propagation needed.

## Agent Integration

No agent integration required — this is a plan template and validation hook change. The hooks are already registered via `.claude/settings.json` which is read by Claude Code directly. No MCP server, bridge, or tool changes needed.

## Documentation

- [ ] Create `docs/features/test-impact-plan-section.md` describing the new required section, its purpose, and examples
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update inline comments in `PLAN_TEMPLATE.md` with clear guidance on filling the section

## Success Criteria

- [ ] `PLAN_TEMPLATE.md` contains `## Test Impact` section after `## Failure Path Test Strategy`
- [ ] `validate_test_impact_section.py` exists and follows `validate_documentation_section.py` patterns
- [ ] Validator accepts checklist content (UPDATE/DELETE/REPLACE dispositions)
- [ ] Validator accepts explicit "No existing tests affected" exemption (50+ chars with justification)
- [ ] Validator rejects empty sections, placeholders, and too-brief content
- [ ] `settings.json` registers the new validator on Write events for plan files
- [ ] `settings.json` adds `'## Test Impact'` to `validate_file_contains.py` `--contains` list
- [ ] `CLAUDE.md` documents `## Test Impact` as a required plan section
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (template-and-hooks)**
  - Name: template-builder
  - Role: Add template section, create validator, register hooks, update CLAUDE.md
  - Agent Type: builder
  - Resume: true

- **Validator (verification)**
  - Name: plan-validator
  - Role: Verify all acceptance criteria are met
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add Test Impact section to PLAN_TEMPLATE.md
- **Task ID**: build-template
- **Depends On**: none
- **Validates**: tests/unit/test_validate_test_impact.py (create)
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `## Test Impact` section to `.claude/skills/do-plan/PLAN_TEMPLATE.md` after `## Failure Path Test Strategy` (after line 162) and before `## Rabbit Holes`
- Section content: structured guidance with UPDATE/DELETE/REPLACE disposition categories, checklist format, and exemption pattern
- Create `validate_test_impact_section.py` in `.claude/hooks/validators/` mirroring `validate_documentation_section.py`
- Register the new validator in `.claude/settings.json`: add to Write matcher hooks AND add `--contains '## Test Impact'` to the `validate_file_contains.py` invocation
- Update `CLAUDE.md` Plan Requirements section: add `### ## Test Impact (Required)` entry

### 2. Validate implementation
- **Task ID**: validate-all
- **Depends On**: build-template
- **Assigned To**: plan-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `PLAN_TEMPLATE.md` has the new section in correct position
- Verify `validate_test_impact_section.py` exists with correct structure
- Verify `settings.json` has both hook registrations
- Verify `CLAUDE.md` documents the new required section
- Run the validator against a test plan to confirm it works

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: template-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/test-impact-plan-section.md`
- Add entry to `docs/features/README.md` index table

### 4. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: plan-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Template has section | `grep -c '## Test Impact' .claude/skills/do-plan/PLAN_TEMPLATE.md` | output > 0 |
| Validator exists | `test -f .claude/hooks/validators/validate_test_impact_section.py` | exit code 0 |
| Hook registered | `grep -c 'test_impact' .claude/settings.json` | output > 0 |
| CLAUDE.md updated | `grep -c 'Test Impact' CLAUDE.md` | output > 0 |

## Open Questions

None — the issue is well-defined and the implementation mirrors established patterns exactly.
