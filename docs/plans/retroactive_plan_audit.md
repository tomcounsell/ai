---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-04
tracking: https://github.com/tomcounsell/ai/issues/444
last_comment_id:
---

# Retroactive Plan Audit: Validate Completed Plans Against Actual Deliverables

## Problem

The SDLC pipeline has historically dropped plan requirements without detection. Issue #443 added `scripts/validate_build.py` to prevent future drops, but we have no visibility into past damage.

**Current behavior:**
232 plan files have been deleted from `docs/plans/` over the project's history. Some were genuinely completed with merged PRs, some were bulk-deleted as stale (64 in one commit), and some were abandoned. We don't know how many "completed" plans had undelivered items.

**Desired outcome:**
A structured audit report that quantifies:
- How many completed plans had undelivered checklist items
- What categories of items were most commonly dropped (docs, tests, config, etc.)
- Which specific plans had the worst delivery gaps

## Prior Art

- **#443**: Pipeline drops plan requirements (CLOSED) -- created `scripts/validate_build.py` with deterministic file-assertion, verification-table, and success-criteria checking. This is the core dependency.
- **#424 / PR #425**: Added required Test Impact section to plan template -- improved plan structure
- **#203 / PR #204**: SDLC checkpoint improvements from 5 Whys analysis -- related prevention work

No prior attempt at retroactive plan auditing found.

## Data Flow

1. **Entry point**: Git history -- `git log --all --diff-filter=D -- 'docs/plans/*.md'` recovers deletion commits
2. **Plan reconstruction**: `git show {commit}^:docs/plans/{slug}.md` recovers plan content before deletion
3. **Classification**: Parse frontmatter (`status:`, `tracking:`) to categorize each plan as completed/stale/abandoned
4. **Validation**: Reuse `validate_build.py` functions (`parse_file_assertions`, `parse_verification_table`, `parse_success_criteria_commands`) against HEAD
5. **Report**: Aggregate results into JSON + human-readable summary

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (read-only audit, no behavior changes)
- Review rounds: 1 (review report findings)

## Prerequisites

No prerequisites -- this work has no external dependencies. Uses only git history and existing `scripts/validate_build.py` functions.

## Solution

### Key Elements

- **Plan Reconstructor**: Git history walker that recovers deleted plan content and metadata
- **Plan Classifier**: Categorizes plans as completed (had merged PR), stale (bulk-deleted), or abandoned (no PR)
- **Deterministic Validator**: Reuses `validate_build.py` parsers to check file assertions and success criteria against HEAD
- **Report Generator**: Aggregates results into structured JSON and human-readable markdown

### Flow

**Git history** -> Recover deleted plans -> Classify by status -> Validate completed plans against HEAD -> Aggregate -> Generate report

### Technical Approach

- Single standalone script: `scripts/retroactive_plan_audit.py`
- Import and reuse functions from `scripts/validate_build.py` (no duplication)
- Use `gh` CLI to look up PRs that closed tracking issues
- File-existence checks only (no command execution from old plans -- too risky against current codebase)
- Output both JSON (machine-readable) and markdown (human-readable) report
- Plans pre-dating structured format (no frontmatter) are counted but skipped for validation

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Git operations (show, log) may fail for corrupted history -- each wrapped with try/except logging the plan slug and continuing
- [ ] Frontmatter parsing may fail on malformed YAML -- gracefully skip with warning

### Empty/Invalid Input Handling
- [ ] Plans with empty content after recovery are skipped with a warning
- [ ] Plans with no checkboxes or no file assertions produce "0 validatable items" not errors
- [ ] Plans without tracking issues are classified as "untracked" not errored

### Error State Rendering
- [ ] The report clearly distinguishes "no assertions found" from "assertions found but all failed"
- [ ] Plans that could not be recovered from git are counted in a separate "unrecoverable" bucket

## Test Impact

No existing tests affected -- this is a greenfield standalone script with no prior test coverage. The script imports from `validate_build.py` but does not modify it.

## Rabbit Holes

- **Subagent deep review (Phase 3 in issue)**: LLM-judged validation of SKIP items is tempting but adds massive complexity and cost. The deterministic checker gives us the 80/20. Defer to a follow-up issue if the report shows too many SKIPs.
- **Diff-based validation**: Checking if merged PRs actually touched the files plans referenced requires correlating PR diffs with plan assertions. Complex and the file-exists-at-HEAD check is sufficient for the audit purpose.
- **Fixing discovered gaps**: This is a read-only audit. Do not fix anything found -- that's separate issues.

## Risks

### Risk 1: Noisy results from stale plan assertions
**Impact:** File paths referenced in old plans may have been moved/renamed, producing false FAIL results
**Mitigation:** Report includes the plan creation date and deletion date for context. Humans triage the report.

### Risk 2: GitHub API rate limiting when looking up tracking issues
**Impact:** Slow execution if many plans have tracking issues
**Mitigation:** Use `gh` CLI which handles auth/rate limiting. Add a small delay between API calls. Cache results.

## Race Conditions

No race conditions identified -- this is a single-threaded, read-only audit script.

## No-Gos (Out of Scope)

- No LLM-based validation (subagent deep review deferred)
- No code fixes based on findings
- No diff-based PR validation
- No modification to `validate_build.py`
- No changes to the SDLC pipeline

## Update System

No update system changes required -- this is a standalone audit script that runs on-demand, not a deployed feature.

## Agent Integration

No agent integration required -- this is a developer-facing audit script run manually via `python scripts/retroactive_plan_audit.py`. Not exposed as an MCP tool or bridge integration.

## Documentation

- [ ] Create `docs/features/retroactive-plan-audit.md` describing the audit script, its output format, and how to interpret results
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] `python scripts/retroactive_plan_audit.py` runs to completion without errors
- [ ] Report includes plan inventory with categorization (completed/stale/abandoned counts)
- [ ] Report includes per-plan validation results for completed plans
- [ ] Report includes aggregate statistics (top dropped categories, delivery rate)
- [ ] JSON output written to `data/plan_audit_report.json`
- [ ] Human-readable summary printed to stdout
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (audit-script)**
  - Name: audit-builder
  - Role: Implement the retroactive plan audit script
  - Agent Type: builder
  - Resume: true

- **Validator (audit-results)**
  - Name: audit-validator
  - Role: Verify script runs correctly and output is well-formed
  - Agent Type: validator
  - Resume: true

- **Documentarian (audit-docs)**
  - Name: audit-docs
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement plan reconstruction and classification
- **Task ID**: build-reconstructor
- **Depends On**: none
- **Validates**: tests/unit/test_retroactive_plan_audit.py (create)
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/retroactive_plan_audit.py` with:
  - `recover_deleted_plans()` -- walks git history, recovers plan content before deletion
  - `parse_plan_metadata(content)` -- extracts frontmatter (status, type, tracking, created date)
  - `classify_plan(metadata)` -- returns completed/stale/abandoned based on tracking issue + PR status
  - Import `parse_file_assertions`, `extract_section` from `scripts/validate_build.py`
- Handle edge cases: plans with no frontmatter, malformed YAML, empty content
- Use `subprocess.run(["git", ...])` for git operations, `subprocess.run(["gh", ...])` for GitHub lookups

### 2. Implement validation and report generation
- **Task ID**: build-reporter
- **Depends On**: build-reconstructor
- **Validates**: tests/unit/test_retroactive_plan_audit.py
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `validate_completed_plan(plan_content)` -- reuses validate_build.py parsers for file-exists checks only
- Add `generate_report(results)` -- produces:
  - Aggregate stats (plans audited, fully delivered, partially delivered, never delivered)
  - Per-plan breakdown with pass/fail/skip counts
  - Top dropped categories (docs, tests, config, etc.)
- Write JSON to `data/plan_audit_report.json`
- Print human-readable summary to stdout
- Add `main()` with argparse for `--output` path override and `--verbose` flag

### 3. Validate audit output
- **Task ID**: validate-audit
- **Depends On**: build-reporter
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python scripts/retroactive_plan_audit.py` and verify it completes
- Check JSON output is valid and contains expected fields
- Verify plan counts are reasonable (>0 completed, >0 stale)
- Check that plans with no checkboxes are gracefully handled

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-audit
- **Assigned To**: audit-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/retroactive-plan-audit.md`
- Add entry to `docs/features/README.md` index table

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all unit tests
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Script runs | `python scripts/retroactive_plan_audit.py --help` | exit code 0 |
| Lint clean | `python -m ruff check scripts/retroactive_plan_audit.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/retroactive_plan_audit.py` | exit code 0 |
| Unit tests | `pytest tests/unit/test_retroactive_plan_audit.py -x -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None -- this is a well-scoped read-only audit with clear inputs (git history) and outputs (report).
