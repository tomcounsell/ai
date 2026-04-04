---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-04
tracking: https://github.com/tomcounsell/ai/issues/444
last_comment_id:
---

# Retroactive Plan Audit: Triage Gaps, Then Delete Old Plans

## Problem

The SDLC pipeline has historically dropped plan requirements without detection. Issue #443 added `scripts/validate_build.py` to prevent future drops, but we have no visibility into past damage.

**Current behavior:**
232 plan files have been deleted from `docs/plans/` over the project's history. Some were genuinely completed with merged PRs, some were bulk-deleted as stale, some were abandoned. We don't know how many "completed" plans had undelivered items.

**Desired outcome:**
A structured triage report that surfaces gaps from completed plans — presented to Tom for personal review. He approves which gaps warrant new issues, and which to permanently dismiss. Once triage is complete, all old plans are deleted. Feature documentation (`docs/features/`) is the gold standard going forward.

**Key constraint:** This repo evolves quickly. Many "missed" items from old plans may no longer be valid or relevant. The human decides what matters, not the script.

## Prior Art

- **#443**: Pipeline drops plan requirements (CLOSED) — created `scripts/validate_build.py` with deterministic file-assertion, verification-table, and success-criteria checking. Core dependency.
- **#424 / PR #425**: Added required Test Impact section to plan template
- **#203 / PR #204**: SDLC checkpoint improvements from 5 Whys analysis

## Data Flow

1. **Entry point**: Git history — `git log --all --diff-filter=D -- 'docs/plans/*.md'` recovers deletion commits
2. **Plan reconstruction**: `git show {commit}^:docs/plans/{slug}.md` recovers plan content before deletion
3. **Classification**: Parse frontmatter (`status:`, `tracking:`) to categorize each plan as completed/stale/abandoned
4. **Validation**: Reuse `validate_build.py` functions against HEAD to find gaps in completed plans
5. **Triage report**: Generate structured output for human review — each gap is approve (create issue) or dismiss (forget permanently)
6. **Cleanup**: After triage, delete remaining old plans. Feature docs are the single source of truth.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (Tom reviews triage report and approves/dismisses items)
- Review rounds: 1

## Prerequisites

No prerequisites — uses only git history and existing `scripts/validate_build.py` functions.

## Solution

### Key Elements

- **Plan Reconstructor**: Git history walker that recovers deleted plan content and metadata
- **Plan Classifier**: Categorizes plans as completed (had merged PR), stale (bulk-deleted), or abandoned (no PR)
- **Gap Detector**: Reuses `validate_build.py` parsers to find undelivered items in completed plans
- **Triage Report**: Human-readable output organized for quick approve/dismiss decisions per gap
- **Plan Cleanup**: After triage, delete all old plans — feature docs are the gold standard

### Flow

**Git history** → Recover deleted plans → Classify by status → Find gaps in completed plans → Generate triage report → **Tom reviews & approves/dismisses** → Clean up old plans

### Technical Approach

- Single standalone script: `scripts/retroactive_plan_audit.py`
- Import and reuse functions from `scripts/validate_build.py` (no duplication)
- Use `gh` CLI to look up PRs that closed tracking issues
- File-existence checks only (no command execution from old plans — too risky against current codebase)
- Output triage report as structured JSON (`data/plan_audit_triage.json`) + human-readable markdown summary
- Plans pre-dating structured format (no frontmatter) are counted but skipped for validation
- **No automatic fixes** — the script finds gaps, Tom decides what's still relevant
- Triage JSON includes per-gap fields: `slug`, `gap_description`, `category`, `disposition` (starts as `pending`, Tom sets to `approve` or `dismiss`)

### Triage Report Format

```json
{
  "summary": {
    "plans_audited": 87,
    "plans_with_gaps": 28,
    "total_gaps": 94,
    "gap_categories": {"docs": 45, "tests": 31, "config": 18}
  },
  "gaps": [
    {
      "id": 1,
      "plan_slug": "some-feature",
      "gap_description": "docs/features/some-feature.md was never created",
      "category": "docs",
      "plan_created": "2025-11-15",
      "disposition": "pending"
    }
  ]
}
```

Tom reviews and sets `disposition` to `approve` (creates a new issue) or `dismiss` (permanently forgotten). A second pass of the script reads the triage file and creates issues for approved gaps.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Git operations (show, log) may fail for corrupted history — each wrapped with try/except logging the plan slug and continuing
- [ ] Frontmatter parsing may fail on malformed YAML — gracefully skip with warning

### Empty/Invalid Input Handling
- [ ] Plans with empty content after recovery are skipped with a warning
- [ ] Plans with no checkboxes or no file assertions produce "0 validatable items" not errors
- [ ] Plans without tracking issues are classified as "untracked" not errored

### Error State Rendering
- [ ] The report clearly distinguishes "no assertions found" from "assertions found but all failed"
- [ ] Plans that could not be recovered from git are counted in a separate "unrecoverable" bucket

## Test Impact

No existing tests affected — this is a greenfield standalone script with no prior test coverage. The script imports from `validate_build.py` but does not modify it.

## Rabbit Holes

- **Subagent deep review**: LLM-judged validation of SKIP items adds massive complexity. Defer — the deterministic checker + human triage is the right approach.
- **Diff-based validation**: Checking if merged PRs actually touched referenced files is complex and unnecessary — file-exists-at-HEAD + human judgment is sufficient.
- **Fixing discovered gaps automatically**: This is a triage tool, not a fix tool. Approved gaps become new issues through the normal SDLC.
- **Preserving old plans**: Old plans get deleted after triage. Feature docs are the gold standard.

## Risks

### Risk 1: Noisy results from stale plan assertions
**Impact:** File paths referenced in old plans may have been moved/renamed, producing false FAIL results
**Mitigation:** Triage report includes plan creation date and deletion date. Tom triages — stale items get dismissed, not auto-actioned.

### Risk 2: GitHub API rate limiting when looking up tracking issues
**Impact:** Slow execution if many plans have tracking issues
**Mitigation:** Use `gh` CLI which handles auth/rate limiting. Add small delay between API calls. Cache results.

## Race Conditions

No race conditions — single-threaded, read-only audit script.

## No-Gos (Out of Scope)

- No LLM-based validation
- No automatic code fixes based on findings
- No diff-based PR validation
- No modification to `validate_build.py`
- No changes to the SDLC pipeline
- No auto-creating issues without Tom's explicit approval per gap
- No preserving old plans — they get deleted after triage

## Update System

No update system changes required — standalone audit script that runs on-demand, not a deployed feature.

## Agent Integration

No agent integration required — developer-facing audit script run manually via `python scripts/retroactive_plan_audit.py`. Not exposed as an MCP tool.

## Documentation

- [ ] Create `docs/features/retroactive-plan-audit.md` describing the audit script, triage workflow, and how to interpret/action results
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] `python scripts/retroactive_plan_audit.py` runs to completion without errors
- [ ] Triage report includes plan inventory with categorization (completed/stale/abandoned counts)
- [ ] Triage report includes per-gap entries with slug, description, category, and pending disposition
- [ ] Aggregate statistics printed (top dropped categories, gap counts)
- [ ] JSON output written to `data/plan_audit_triage.json`
- [ ] `python scripts/retroactive_plan_audit.py --apply-triage data/plan_audit_triage.json` creates issues for approved gaps
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
  - `recover_deleted_plans()` — walks git history, recovers plan content before deletion
  - `parse_plan_metadata(content)` — extracts frontmatter (status, type, tracking, created date)
  - `classify_plan(metadata)` — returns completed/stale/abandoned based on tracking issue + PR status
  - Import `parse_file_assertions`, `extract_section` from `scripts/validate_build.py`
- Handle edge cases: plans with no frontmatter, malformed YAML, empty content
- Use `subprocess.run(["git", ...])` for git operations, `subprocess.run(["gh", ...])` for GitHub lookups

### 2. Implement gap detection and triage report generation
- **Task ID**: build-reporter
- **Depends On**: build-reconstructor
- **Validates**: tests/unit/test_retroactive_plan_audit.py
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `detect_gaps(plan_content)` — reuses validate_build.py parsers for file-exists checks only
- Add `generate_triage_report(results)` — produces:
  - Aggregate stats (plans audited, plans with gaps, total gaps, gap categories)
  - Per-gap entries with slug, description, category, and `disposition: pending`
- Write JSON to `data/plan_audit_triage.json`
- Print human-readable summary to stdout
- Add `--apply-triage` mode: reads triage JSON, creates GitHub issues for `disposition: approve` gaps via `gh issue create`
- Add `main()` with argparse for `--output` path override, `--apply-triage`, and `--verbose` flag

### 3. Validate audit output
- **Task ID**: validate-audit
- **Depends On**: build-reporter
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python scripts/retroactive_plan_audit.py` and verify it completes
- Check JSON output is valid and contains expected fields
- Verify plan counts are reasonable (>0 completed, >0 stale)
- Check that per-gap entries have required fields (slug, description, category, disposition)
- Check that plans with no checkboxes are gracefully handled

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-audit
- **Assigned To**: audit-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/retroactive-plan-audit.md` covering:
  - What the script does
  - How to run it
  - The triage workflow (generate → Tom reviews → apply)
  - Report format and field definitions
- Add entry to `docs/features/README.md` index table

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all unit tests
- Verify all success criteria met

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

None — the key design decision (human triage gate) is resolved.
