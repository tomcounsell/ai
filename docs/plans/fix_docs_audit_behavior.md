---
status: Done
type: bug
appetite: Small
owner: Valor
created: 2026-03-14
tracking: https://github.com/tomcounsell/ai/issues/404
last_comment_id:
---

# Fix do-docs-audit: Separate Fixing from Reporting

## Problem

The `/do-docs-audit` skill generates detailed audit reports listing every broken reference it finds, but then stuffs that entire report into the commit message regardless of whether the issues were actually fixed.

**Current behavior:**
Commit `21b80575` claims "Updated: 93" docs but only changed 1 line in 1 file. The commit message is a 200+ line audit report listing what was *found*, not what was *changed*. The skill conflates "reporting findings" with "fixing findings."

**Desired outcome:**
Commit messages accurately describe only the changes that were actually made. When many issues are found, the detailed report goes into a GitHub issue instead of a commit message.

## Prior Art

- **Issue #145**: [Plan] Comprehensive Documentation Audit Skill -- Created the original skill. Successfully merged as PR #147.
- **Issue #158**: Fix 11 skill audit warnings across 8 skills -- Related cleanup work, not directly about audit behavior.
- **PR #147**: Add documentation audit skill and daydream integration -- Original implementation that established the current behavior.

## Data Flow

1. **Entry point**: User invokes `/do-docs-audit`
2. **Step 1-2**: Skill enumerates docs, spawns parallel audit agents per file
3. **Step 3**: Agents return verdicts (KEEP/UPDATE/DELETE) with rationales and corrections
4. **Step 4**: Skill executes verdicts -- applies edits for UPDATE, deletes for DELETE
5. **Step 7 (bug location)**: Skill commits with a message containing ALL verdicts/rationales regardless of actual changes made. The commit message template in Step 7 dumps every verdict, not just the ones that resulted in file changes.

The disconnect is in Step 7: the commit message lists all audit *findings* rather than filtering to only *changes actually applied*.

## Architectural Impact

- **No new dependencies**: Pure skill logic change
- **Interface changes**: None -- the skill's invocation interface stays the same
- **Coupling**: No change
- **Reversibility**: Trivially reversible (it's a single skill markdown file)

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

- **Threshold router**: After collecting verdicts, count how many files actually need changes (UPDATE + DELETE). Route to one of two paths based on count.
- **Hotfix path (<=5 changes)**: Apply fixes directly, commit with a concise message listing only the files changed and what was done.
- **Report path (>5 changes)**: Create a GitHub issue with the full audit report. Apply what can be fixed, commit with a short message referencing the issue.
- **Accurate commit messages**: The commit message template must list only files that were actually modified in the commit, not all files that were audited.

### Flow

**Audit runs** -> Verdicts collected -> Count changes needed -> **<=5?** -> Fix all, commit with accurate message -> **Done**

**Audit runs** -> Verdicts collected -> Count changes needed -> **>5?** -> Create GitHub issue with full report -> Fix what's feasible -> Commit referencing issue -> **Done**

### Technical Approach

Modify `.claude/skills/do-docs-audit/SKILL.md` Step 7 to:

1. Filter verdicts to only those that resulted in actual file changes (UPDATE files where edits were applied, DELETE files that were removed, RELOCATED files that were moved).
2. Replace the current commit message template that dumps all verdicts with one that lists only actual changes.
3. Add a new Step 6.5 that implements the threshold routing:
   - Count UPDATE + DELETE verdicts
   - If >5: create a GitHub issue with the full audit report before committing
   - The issue body contains the detailed per-file breakdown currently going into the commit message
4. Update the commit message format to be concise:
   - For <=5 changes: list each change inline in the commit message
   - For >5 changes: reference the created issue number, list only summary counts

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is a skill instruction file, not executable code.

### Empty/Invalid Input Handling
- [ ] Ensure the skill handles the case where 0 changes are needed (all KEEP) -- no commit should be made
- [ ] Ensure the skill handles the case where the GitHub issue creation fails gracefully

### Error State Rendering
- [ ] If >5 issues found and GH issue creation fails, the skill should still commit actual fixes with an inline summary rather than silently dropping the report

## Rabbit Holes

- Trying to make the audit agents themselves smarter about applying fixes -- the agents already generate corrections, the problem is purely in how results are committed
- Adding new verification logic -- the detection is fine, only the output behavior needs to change
- Automating the fix of all 93+ broken references in one pass -- the threshold exists specifically to avoid giant commits

## Risks

### Risk 1: Audit creates too many GitHub issues
**Impact:** Issue noise if audit is run frequently with >5 findings each time
**Mitigation:** The skill should check for an existing open "docs audit" issue before creating a new one, and append to it instead

## Race Conditions

No race conditions identified -- the audit runs synchronously in a single agent session.

## No-Gos (Out of Scope)

- Changing the audit detection/analysis logic (Steps 1-4)
- Changing the parallel agent spawning architecture
- Changing the verdict thresholds (KEEP/UPDATE/DELETE criteria)
- Adding automated testing of the skill itself (separate concern)

## Update System

No update system changes required -- this is a skill-internal change that propagates automatically via git pull.

## Agent Integration

No agent integration required -- this modifies an existing skill's behavior. The skill is already registered and invocable.

## Documentation

- [ ] Update `docs/features/documentation-audit.md` to describe the new threshold behavior (hotfix vs. issue creation)
- [ ] Ensure `docs/features/README.md` entry for documentation audit is accurate

## Success Criteria

- [ ] When <=5 issues found, skill fixes them directly and commits with a message listing only actual changes
- [ ] When >5 issues found, skill creates a GitHub issue containing the full audit report
- [ ] Commit messages never contain the full audit report -- only actual changes made
- [ ] The skill still runs the same detection/analysis logic it does today
- [ ] No audit reports appear in commit messages
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill-updater)**
  - Name: skill-updater
  - Role: Modify the do-docs-audit SKILL.md to implement threshold routing and accurate commit messages
  - Agent Type: builder
  - Resume: true

- **Validator (skill-validator)**
  - Name: skill-validator
  - Role: Verify the updated skill instructions match acceptance criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update SKILL.md with threshold routing
- **Task ID**: build-threshold-routing
- **Depends On**: none
- **Assigned To**: skill-updater
- **Agent Type**: builder
- **Parallel**: true
- Add Step 6.5 between current Steps 6 and 7: threshold router that counts UPDATE+DELETE verdicts
- For <=5 changes: proceed to commit with concise per-file message
- For >5 changes: create GitHub issue with full report, then commit with issue reference
- Rewrite Step 7 commit message template to list only files actually changed in the commit
- Ensure 0-change case (all KEEP) skips the commit entirely

### 2. Validate skill changes
- **Task ID**: validate-skill
- **Depends On**: build-threshold-routing
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Read the updated SKILL.md and verify threshold logic is present
- Verify commit message template references only actual changes
- Verify GitHub issue creation path exists for >5 findings
- Verify all acceptance criteria from issue #404 are addressed

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-skill
- **Assigned To**: skill-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/documentation-audit.md` with new threshold behavior
- Verify `docs/features/README.md` entry is accurate

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Run ruff format and lint checks
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Skill exists | `test -f .claude/skills/do-docs-audit/SKILL.md` | exit code 0 |
| Threshold logic present | `grep -c "threshold\|<=5\|>5\|issue.*create" .claude/skills/do-docs-audit/SKILL.md` | output > 0 |
| No full report in commit template | `grep -c "for each file.*VERDICT\|{for each file" .claude/skills/do-docs-audit/SKILL.md` | exit code 1 |
