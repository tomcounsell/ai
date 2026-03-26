---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/547
last_comment_id:
---

# PR Review Audit Reflection Step

## Problem

When the PM merges a PR with unaddressed findings -- tech debt "noted for future work", test gaps, or nits left behind -- those items disappear. Nobody tracks them, nobody comes back to fix them, and they accumulate as invisible debt. The reflections system audits logs, docs, branches, and sessions, but not PR review quality.

**Current behavior:**
- PM merges PR with "3 tech debt items noted for future work" and items vanish
- PR review says "three minor test gaps noted" then merged without patching -- gaps forgotten
- No automated process reads merged PR reviews to extract leftover findings
- Evidence: PR #527 (ai), PR #532 (ai), PR #276 (popoto), PR #261 (popoto) all had unaddressed findings that were never resurfaced

**Desired outcome:**
- A reflection step scans recently merged PRs for unaddressed review findings
- Each finding is classified by criticality: critical, standard, or trivial
- Unaddressed findings are filed as GitHub issues with severity labels and original review links
- Critical and trivial items (must-fixes and quick wins) are flagged for immediate SDLC work
- Deduplication prevents re-filing issues for already-audited review comments

## Prior Art

- **Issue #544**: PM SDLC decision rules -- addresses the root cause (PM should patch before merging). This issue is the safety net for when things still slip through.
- **Issue #534 / PR #536**: do-patch skill workflow improvements -- improved the patch invocation rules but does not cover post-merge auditing.
- The reflections system already creates GitHub issues via `step_create_github_issue` (step 10) and `step_auto_fix_bugs` (step 8) -- this follows the same pattern for PR review findings.

## Data Flow

1. **Entry point**: `step_pr_review_audit` runs as step 20 in the ReflectionRunner sequence
2. **PR discovery**: For each project in `projects.json` with a `github` config, run `gh pr list --state merged` to find PRs merged since the last reflection run (using `self.state.date` minus 1 day as the cutoff)
3. **Review comment fetch**: For each merged PR, fetch review comments via `gh api repos/{owner}/{repo}/pulls/{number}/reviews` and `gh api repos/{owner}/{repo}/pulls/{number}/comments`
4. **Finding extraction**: Parse review comments for structured finding format (`**Severity:** blocker | tech_debt | nit`, `**File:**`, `**Issue:**`, `**Fix:**`). These are the fields defined in `.claude/skills/do-pr-review/SKILL.md`
5. **Address check**: For each finding, check if the file/line was modified in commits after the review comment timestamp (via `gh api` commit history on the PR). If modified, consider it addressed.
6. **Deduplication**: Check Redis `PRReviewAudit` model for the review comment ID. If already audited, skip.
7. **Classification**: Map severity -- `blocker` -> critical, `tech_debt` -> standard, `nit` -> trivial
8. **Issue filing**: File GitHub issues for unaddressed findings using `gh issue create` with labels (`critical`, `tech-debt`, or `nit`) and body containing original PR link, review comment link, severity, file path, code quote, and suggested fix
9. **State recording**: Mark comment IDs as audited in Redis, add findings to `self.state.findings`, update `self.state.step_progress`
10. **Output**: Findings appear in the daily reflection report and Telegram summary via existing `step_create_github_issue` and `step_post_to_telegram` infrastructure

## Architectural Impact

- **New dependencies**: None -- uses existing `gh` CLI, subprocess, Redis/Popoto, and json parsing
- **Interface changes**: One new Popoto model (`PRReviewAudit`) for deduplication state. One new async method on `ReflectionRunner`.
- **Coupling**: Depends on the structured review format from `/do-pr-review` SKILL.md. If the format changes, the parser must be updated. This is acceptable because the format is well-documented and stable.
- **Data ownership**: Deduplication state owned by Redis via Popoto. PR review data owned by GitHub (read-only via `gh api`).
- **Reversibility**: Fully reversible -- remove the step and model. Filed issues remain as artifacts but the step can be cleanly deleted.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The step follows established patterns (async method, state tracking, `gh` CLI usage, issue creation). The main complexity is parsing the structured review format and cross-referencing with commit history to determine if findings were addressed.

## Prerequisites

No prerequisites -- this work uses existing `gh` CLI, Redis, and Popoto infrastructure already available in the reflections environment.

## Solution

### Key Elements

- **`step_pr_review_audit` method**: New async step on `ReflectionRunner` that scans merged PRs, extracts unaddressed review findings, and files GitHub issues
- **`PRReviewAudit` Popoto model**: Redis-backed deduplication tracker keyed by review comment ID, storing audit date and filed issue URL
- **Finding parser**: Regex-based extraction of the structured `**Severity:**`/`**File:**`/`**Issue:**`/`**Fix:**` format from review comment bodies
- **Address checker**: Compares review comment timestamps against subsequent commit timestamps to determine if findings were addressed

### Flow

**Reflection run starts** -> step 20 fires -> iterate projects with github config -> `gh pr list --state merged` since last run -> for each PR, fetch reviews and comments -> parse structured findings -> check if addressed via commit history -> check Redis dedup -> file GitHub issues for unaddressed findings -> record in Redis -> add to reflection findings for report

### Technical Approach

- **PR time window**: Use `--search "merged:>={yesterday}"` with `gh pr list` where yesterday is derived from `self.state.date`. This gives a 24-hour window matching the daily reflection cadence.
- **Structured finding regex**: Match the exact format from do-pr-review SKILL.md: `\*\*Severity:\*\*\s*(blocker|tech_debt|nit)` plus `\*\*File:\*\*`, `\*\*Issue:\*\*`, `\*\*Fix:\*\*` fields. Only parse comments that match the structured format -- ignore free-text comments.
- **Address detection**: For each finding's file path, check if the file appears in any PR commit made after the review comment timestamp. This is a heuristic -- if the file was touched, assume the finding was addressed. Conservative approach: only mark as addressed if the specific file was modified.
- **Issue grouping**: File one GitHub issue per PR that has unaddressed findings (not one per finding). The issue body lists all findings from that PR, grouped by severity. This keeps the issue tracker manageable.
- **Label mapping**: `blocker` -> label `critical`, `tech_debt` -> label `tech-debt`, `nit` -> label `nit`. Also add label `pr-review-audit` for easy filtering.
- **Deduplication key**: Redis key is `{repo}:{pr_number}:{comment_id}`. Check before filing. Record after filing.
- **Error handling**: Each project is processed independently with try/except. A failure on one project does not block others. `gh api` timeouts are caught and logged.
- **Rate limiting**: Process at most 20 merged PRs per project per run to avoid excessive API calls. Log a warning if more exist.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `gh pr list` failure (network, auth) logs warning and skips project -- does not halt run
- [ ] `gh api` timeout for review comments logs warning and skips that PR
- [ ] `gh issue create` failure logs warning and continues to next finding group
- [ ] Redis unavailable for dedup check falls back to in-memory set for current run (no persistent dedup, but no crash)

### Empty/Invalid Input Handling
- [ ] PR with no review comments is skipped silently
- [ ] Review comment with no structured format markers is skipped (only parse well-formed findings)
- [ ] Project with no merged PRs in the time window produces no findings and no errors
- [ ] Empty `github` config in `projects.json` is handled by existing project filtering

### Error State Rendering
- [ ] Step progress dict records error counts alongside success counts for observability
- [ ] Findings added to `self.state.findings` with project-scoped keys (e.g., `ai:pr_review_audit`) for proper namespacing in the report

## Test Impact

No existing tests affected -- this is a greenfield feature adding a new reflection step. No existing step methods, models, or report formatting is modified. The new step is appended to the steps list and uses new model/parser code.

## Rabbit Holes

- **Free-text scanning of PR descriptions**: Scanning for informal "TODO" or "future work" phrases in PR descriptions and commit messages is too noisy. Focus on structured review findings only. Free-text scanning can be a follow-up.
- **Per-line diff analysis**: Checking if a specific line (not just file) was modified after the review is fragile with line number drift. File-level checking is sufficient for v1.
- **Auto-patching unaddressed findings**: Automatically invoking `/do-patch` for critical findings is tempting but out of scope. File the issue and let the PM prioritize.
- **Backfilling historical PRs**: Do not retroactively scan old PRs. Start from the first run date forward.
- **Parsing non-standard review formats**: Third-party review tools or manually-typed reviews with different formats are out of scope. Only parse the do-pr-review structured format.

## Risks

### Risk 1: GitHub API rate limiting
**Impact:** If a project has many merged PRs, the `gh api` calls for reviews and comments could hit rate limits
**Mitigation:** Cap at 20 PRs per project per run. Use `gh api` which handles auth and rate limit headers. Log warning if truncated.

### Risk 2: Review format evolution
**Impact:** If do-pr-review changes its output format, the parser breaks silently (finds nothing)
**Mitigation:** Log a metric for "PRs with reviews but zero parsed findings" to detect format drift. If this ratio is consistently high, it signals a parser update is needed.

### Risk 3: False positives from address detection
**Impact:** A file touched for unrelated reasons is treated as "finding addressed" when it was not
**Mitigation:** Acceptable for v1 -- conservative heuristic favors fewer false issues over noise. The safety net catches most things; perfection is not the goal.

## Race Conditions

No race conditions identified -- the reflection runner executes steps sequentially (single-threaded async). Redis dedup writes are atomic per-key. The `gh` CLI subprocess calls are synchronous within the step.

## No-Gos (Out of Scope)

- Auto-patching or auto-SDLC for unaddressed findings (file issues only)
- Backfilling historical PRs before the first run
- Free-text scanning of PR descriptions or commit messages
- Per-line (vs per-file) address detection
- Parsing non-standard review formats from third-party tools
- Real-time PR monitoring (this is a daily batch process)

## Update System

No update system changes required -- this feature adds a new reflection step and Popoto model within the existing `scripts/reflections.py` and `models/` directory structure. No new dependencies, config files, or migration steps. The new model auto-creates in Redis on first use.

## Agent Integration

No agent integration required -- this is a reflections-internal change. The step runs as part of the daily maintenance process via `scripts/reflections.py`. No new MCP server, no `.mcp.json` changes, no bridge modifications. The output (filed GitHub issues) is consumed via normal GitHub workflows.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reflections.md` to document the new step 20 (PR Review Audit) in the step listing and registered reflections table
- [ ] Add entry to `docs/features/README.md` index table if a new feature doc is warranted

### Inline Documentation
- [ ] Docstring on `step_pr_review_audit` method explaining purpose, data flow, and error handling
- [ ] Docstring on `PRReviewAudit` model explaining field semantics and dedup key format
- [ ] Code comments on the structured finding regex explaining the expected format from do-pr-review

## Success Criteria

- [ ] New reflection step `step_pr_review_audit` exists in `scripts/reflections.py` as step 20
- [ ] Scans merged PRs since last reflection run across all configured projects in `projects.json`
- [ ] Extracts unaddressed findings from PR review comments using the structured `**Severity:**` format
- [ ] Classifies findings as critical (blocker), standard (tech_debt), or trivial (nit)
- [ ] Files GitHub issues for unaddressed findings with severity labels and original review links
- [ ] Critical and trivial items are flagged in the reflection report for immediate SDLC attention
- [ ] `PRReviewAudit` Redis model prevents re-filing issues for already-audited review comments
- [ ] Integrates with existing reflection report output and Telegram posting via `self.state.findings`
- [ ] Step handles errors gracefully (per-project isolation, timeout handling, missing data)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (reflection-step)**
  - Name: audit-builder
  - Role: Implement step_pr_review_audit, PRReviewAudit model, finding parser, and address checker
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: audit-validator
  - Role: Verify step runs correctly against real merged PRs, dedup works, issues are filed
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update reflections feature documentation with new step
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create PRReviewAudit Popoto model
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: tests/unit/test_pr_review_audit.py (create)
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `PRReviewAudit` model to `models/reflections.py` with fields: `audit_id` (AutoKeyField), `repo` (KeyField), `pr_number` (IntField), `comment_id` (UniqueKeyField), `severity` (Field), `filed_issue_url` (Field, null=True), `audited_at` (SortedField, type=float)
- Add `is_audited(comment_id)` classmethod for dedup check
- Add `mark_audited(comment_id, repo, pr_number, severity, issue_url)` classmethod for recording
- Add `cleanup_expired(max_age_days=90)` classmethod for TTL cleanup

### 2. Implement finding parser
- **Task ID**: build-parser
- **Depends On**: none
- **Validates**: tests/unit/test_pr_review_audit.py (create)
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: true
- Create parser function (in `scripts/reflections.py` or a helper module) that extracts structured findings from review comment body text
- Parse `**Severity:**`, `**File:**`, `**Code:**`, `**Issue:**`, `**Fix:**` fields using regex
- Return list of finding dicts with keys: severity, file_path, code, issue_description, suggested_fix
- Handle partial matches (some fields missing) gracefully -- require at minimum Severity and Issue

### 3. Implement step_pr_review_audit method
- **Task ID**: build-step
- **Depends On**: build-model, build-parser
- **Validates**: tests/unit/test_pr_review_audit.py (create)
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `step_pr_review_audit` as async method on `ReflectionRunner`
- Register as step 20 in `self.steps` list: `(20, "PR Review Audit", self.step_pr_review_audit)`
- Add step 20 to `gh_steps` set in `_preflight_check` (requires `gh` CLI)
- For each project with github config: fetch merged PRs, fetch reviews/comments, parse findings, check if addressed, check dedup, file issues
- Group unaddressed findings per PR into a single GitHub issue with labels `pr-review-audit` plus severity labels
- Add findings to `self.state.findings` with key `{slug}:pr_review_audit` for report integration
- Update `self.state.step_progress["pr_review_audit"]` with metrics: prs_scanned, findings_total, findings_unaddressed, issues_filed

### 4. Add address detection logic
- **Task ID**: build-address-check
- **Depends On**: none
- **Validates**: tests/unit/test_pr_review_audit.py (create)
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: true
- Create function that takes a PR number, a review comment timestamp, and a file path
- Fetch PR commits via `gh api repos/{owner}/{repo}/pulls/{number}/commits`
- Check if any commit after the review timestamp modified the file (via commit file list)
- Return boolean: True if addressed, False if not

### 5. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-step
- **Validates**: tests/unit/test_pr_review_audit.py (create)
- **Assigned To**: audit-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Test finding parser with sample review comment bodies (well-formed, partial, malformed, empty)
- Test severity classification mapping (blocker -> critical, tech_debt -> standard, nit -> trivial)
- Test dedup logic (mock Redis calls to PRReviewAudit model)
- Test address detection logic with mock commit data
- Test issue body formatting (includes PR link, review link, severity, file path, code, fix)
- Test per-project error isolation (one project failure does not block others)

### 6. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-tests
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite including new tests
- Verify step appears in ReflectionRunner.steps as step 20
- Verify step_progress key is set after step runs
- Verify findings are namespaced correctly for report integration

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` with step 20 description
- Update step listing table with PR Review Audit entry
- Add inline docstrings to new methods and model

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Step registered | `python -c "from scripts.reflections import ReflectionRunner; r = ReflectionRunner(); assert any(s[0] == 20 for s in r.steps)"` | exit code 0 |
| Model importable | `python -c "from models.reflections import PRReviewAudit"` | exit code 0 |
| Step 20 in preflight | `grep -c 'gh_steps' scripts/reflections.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the solution follows established reflection step patterns and the PR review format is well-documented in the do-pr-review SKILL.md. The issue provides concrete evidence from four PRs across two repos demonstrating the problem.
