---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/676
last_comment_id:
---

# Summarizer Integration Audit

## Problem

The summarizer (`bridge/summarizer.py`) has grown to 1,481 lines with LLM-based classification, anti-fabrication rules, SDLC template rendering, and multi-backend fallback chains. A previous manual audit graded it C+, but that was output-focused. No systematic integration audit has been run using the `/do-integration-audit` skill's 12-check methodology.

**Current behavior:**
No integration-focused audit exists. The existing `docs/guides/summarizer-output-audit.md` only covers output quality, not wiring health.

**Desired outcome:**
A structured integration audit report saved to `docs/guides/summarizer-integration-audit.md` with all 12 checks run, findings grouped by severity, and actionable next steps for any CRITICAL or WARNING findings.

## Prior Art

- **Issue #653**: Fix stale summarizer claim in pipeline docs -- corrected outdated doc references
- **Issue #654**: Remove dead coach module -- cleaned up `bridge/coach.py` and tests
- **Issue #668**: Remove references to non-existent bridge/coach.py in pipeline docs
- **Issue #227**: SDLC-first agent architecture with reliable summarizer template
- **PR #197**: Auto-continue audit with 7 reliability fixes

No prior integration audit of the summarizer exists.

## Data Flow

Not applicable -- this is a read-only audit that produces a report. No data flow changes.

## Architectural Impact

No architectural changes. This is a read-only audit producing a documentation artifact.

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

- **Integration audit execution**: Run the `/do-integration-audit summarizer` skill's 12-check methodology against the codebase
- **Report generation**: Save findings to `docs/guides/summarizer-integration-audit.md`
- **Follow-up tracking**: Create GitHub issues for any CRITICAL or WARNING findings that need remediation

### Flow

**Codebase** -> Discovery (grep/glob for summarizer references) -> Surface classification -> 12 audit checks -> Severity-grouped report -> `docs/guides/summarizer-integration-audit.md`

### Technical Approach

- Discover all files referencing the summarizer (implementation, entry points, tests, docs, config)
- Classify each file by surface type
- Run each of the 12 audit checks from the `do-integration-audit` skill definition
- Group findings by severity (CRITICAL, WARNING, INFO)
- Include file paths and line numbers for every finding
- Note any coaching terminology findings as expected-to-change per #674 dependency
- Save the report as a markdown document

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is a documentation-only deliverable

### Empty/Invalid Input Handling
- Not applicable -- no code changes

### Error State Rendering
- Not applicable -- no code changes

## Test Impact

No existing tests affected -- this is a documentation-only chore that produces an audit report without modifying any source code or test files.

## Rabbit Holes

- Do not attempt to fix any findings discovered during the audit -- findings only
- Do not refactor the summarizer as part of this work
- Do not block on #674 (coach rename) completing -- note any coaching terminology as expected-to-change

## Risks

### Risk 1: Coaching terminology noise
**Impact:** Findings about stale coach/coaching references may be noise if #674 hasn't merged yet
**Mitigation:** Flag any coaching-related findings as "expected-to-change per #674" in the report

## Race Conditions

No race conditions identified -- this is a read-only audit with no concurrent operations.

## No-Gos (Out of Scope)

- No code modifications to the summarizer or any other file
- No fixing of discovered issues (separate follow-up work)
- No re-running the output-quality audit from `docs/guides/summarizer-output-audit.md`

## Update System

No update system changes required -- this is a documentation-only deliverable.

## Agent Integration

No agent integration required -- this is a read-only audit producing a markdown report.

## Documentation

- [ ] Create `docs/guides/summarizer-integration-audit.md` with the full 12-check audit report
- [ ] No index updates needed -- guides directory is not indexed

## Success Criteria

- [ ] All 12 audit checks executed and documented
- [ ] Findings grouped by severity (CRITICAL, WARNING, INFO) with file paths and line numbers
- [ ] Any CRITICAL or WARNING findings have recommended next steps
- [ ] Audit report saved to `docs/guides/summarizer-integration-audit.md`
- [ ] Lint clean (`python -m ruff check .`)
- [ ] Format clean (`python -m ruff format --check .`)

## Team Orchestration

### Team Members

- **Auditor (summarizer)**
  - Name: summarizer-auditor
  - Role: Execute 12-check integration audit against summarizer feature
  - Agent Type: builder
  - Resume: true

- **Validator (report)**
  - Name: report-validator
  - Role: Verify all 12 checks are covered and findings are actionable
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Execute Integration Audit
- **Task ID**: audit-summarizer
- **Depends On**: none
- **Validates**: docs/guides/summarizer-integration-audit.md exists and contains all 12 check headings
- **Assigned To**: summarizer-auditor
- **Agent Type**: builder
- **Parallel**: false
- Discover all files referencing summarizer (grep for imports, function calls, doc references)
- Classify each discovered file by surface type (implementation, entry point, test, doc, config)
- Run all 12 audit checks per the do-integration-audit SKILL.md definitions
- Write findings report to `docs/guides/summarizer-integration-audit.md`

### 2. Validate Report
- **Task ID**: validate-report
- **Depends On**: audit-summarizer
- **Assigned To**: report-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all 12 checks are present in the report
- Verify findings include file paths and line numbers
- Verify severity grouping is correct
- Report pass/fail status

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Report exists | `test -f docs/guides/summarizer-integration-audit.md` | exit code 0 |
| All 12 checks | `grep -c '### [0-9]*\.' docs/guides/summarizer-integration-audit.md` | output > 11 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the scope is well-defined by the issue and the audit skill definition.
