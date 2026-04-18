---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1027
last_comment_id:
---

# Consolidate Three Overlapping Steering Docs

## Problem

**Current behavior:** Three docs in `docs/features/` describe steering with overlapping but non-identical content:

| File | Lines | Scope |
|------|-------|-------|
| `session-steering.md` | 128 | Popoto ListField inbox (`queued_steering_messages`), CLI reference, PM→child section absent (lives in `steering-queue.md`) |
| `steering-queue.md` | 330 | Original design spec: Redis list mechanism, watchdog hook, SDK client registry; contains a parent-child section (lines 235–299) that conceptually belongs in `session-steering.md` |
| `mid-session-steering.md` | 101 | End-to-end Telegram reply-thread flow, references the other two but is not referenced by them |

A reader landing on any of the three docs is unsure which to trust, what the authoritative hierarchy is, and where to go for related information. Abort keywords, session status transitions, and race conditions are described in multiple places with no single canonical source.

**Desired outcome:** One primary doc (`session-steering.md`) is the canonical reference for how steering works. Two focused companions (`mid-session-steering.md` for the user-facing flow, `steering-queue.md` retaining or being deleted as historical spec) have a clear, explicit relationship to the primary. Cross-reference hygiene is symmetric and enforced. No duplicated content.

## Freshness Check

**Baseline commit:** `fe2a2dcb9b89560cec44812942bcbb3c42fc728e`
**Issue filed at:** 2026-04-17T08:44:05Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `docs/features/session-steering.md` — 128 lines (issue said 320; the issue was referencing a cached line count). Content claims still hold: Popoto inbox, CLI reference, output router — all present. No parent-child section in this file (it lives in `steering-queue.md` as the issue stated).
- `docs/features/steering-queue.md` — 330 lines, parent-child section lines 235–299 confirmed present and still in `steering-queue.md` only.
- `docs/features/mid-session-steering.md` — 101 lines, cross-links to `steering-queue.md` and `session-steering.md` confirmed. Still not referenced by the other two.

**Cited sibling issues/PRs re-checked:**
- #1022 (PM orchestration audit open questions) — still open; does not affect scope.
- #1018 (PM→Dev mid-execution steering silently fails on CLI-harness children) — closed 2026-04-17T06:37:14Z via PR #1020 (`fix(#1018): CLI-harness steering fix — steer_child uses turn-boundary inbox`). PR #1020 modified `docs/features/session-steering.md` to reflect the fix. Content is accurate post-merge.

**Commits on main since issue was filed (touching referenced files):**
- `c1e99197` (feat: SDLC router oscillation guards) — touched `session-steering.md` (added to index). Change is irrelevant to steering content accuracy.
- `8689daa3` (feat: close five SDLC blind spots) — touched `session-steering.md` (added to index). Also irrelevant to steering content.
- No commits have touched `steering-queue.md` or `mid-session-steering.md` since the issue was filed.

**Active plans in `docs/plans/` overlapping this area:** `parent-child-steering.md` (status: Complete), `summarizer-fallback-steering.md` (status: Shipped). Both are shipped — no active overlap.

**Notes:** The issue cited 320 lines for `session-steering.md`; the actual current count is 128. The file was edited heavily between the original filing context and now (PR #1020 and SDLC docs changes). The core problem statement and solution sketch in the issue remain accurate despite this drift.

## Prior Art

- **PR #1020 / Issue #1018**: `fix(#1018): eliminate silent drop in CLI-harness steering` — Fixed the functional bug where PM→Dev steering silently failed. Updated `session-steering.md` to document the fix. Did not consolidate the three docs. Directly relevant: confirmed `session-steering.md` is the right home for PM→child content.
- **PR #892** (Summarizer fallback via session steering) — Added summarizer fallback to `session-steering.md`. No docs consolidation scope.
- No prior attempt to consolidate the three steering docs was found.

## Research

No relevant external findings — this is a purely internal docs consolidation with no external libraries, APIs, or ecosystem patterns involved.

## Appetite

**Size:** Small

**Team:** Solo dev (documentarian)

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. All work is doc edits and commits.

## Solution

### Key Elements

- **`session-steering.md` becomes the primary doc.** It absorbs the parent-child section from `steering-queue.md` (lines 235–299) and gains "See also" links to its companions at the top.
- **`mid-session-steering.md` stays as-is** (user-facing Telegram reply-thread flow). Gains a "See also" link to `session-steering.md` at the top to close the asymmetric reference gap.
- **`steering-queue.md` is renamed to `steering-implementation-spec.md`** and marked clearly as historical design documentation. The parent-child section is removed from it (moved to `session-steering.md`). Remaining content (Redis key design, watchdog hook design, SDK client registry design) is preserved as historical context.
- **Duplicated content** on abort keywords, session status transitions, and race conditions is removed from `steering-queue.md`/`steering-implementation-spec.md` — canonical location is `mid-session-steering.md` for the Telegram flow; `session-steering.md` for the general inbox model.
- **`docs/features/README.md`** index is updated to reflect the new file name and hierarchy.
- **Broken link audit** — grep codebase for references to `steering-queue.md` and update them to point to `steering-implementation-spec.md`.

### Flow

Read all three docs → Identify duplicated sections → Move parent-child section to `session-steering.md` → Add cross-links → Rename `steering-queue.md` → Remove duplicates from renamed file → Update `README.md` index → Audit all codebase refs → Verify no broken links

### Technical Approach

This is a pure documentation edit — no code changes. The implementation order matters to avoid broken intermediate states:

1. Add cross-links to `session-steering.md` and `mid-session-steering.md` before moving content
2. Move parent-child section from `steering-queue.md` into `session-steering.md`
3. Strip duplicated content from `steering-queue.md`
4. Add historical-spec header to `steering-queue.md`, then rename it via `git mv`
5. Update `README.md` index row and all codebase refs in a single commit
6. Grep-verify no broken links remain

Files referenced as cross-links to `steering-queue.md` (must be updated to `steering-implementation-spec.md`):
- `docs/features/README.md` (line 133)
- `docs/features/mid-session-steering.md` (line 98)
- `docs/features/telegram-message-edit-handling.md` (two refs)
- `docs/features/bridge-workflow-gaps.md` (line 105)
- `docs/features/pm-dev-session-architecture.md` (line 405)
- `docs/plans/parent-child-steering.md` (multiple refs — completed plan, update is cosmetic)
- `docs/plans/redis-popoto-migration.md` (one ref)

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope — this is a pure documentation change with no code modifications.

### Empty/Invalid Input Handling
Not applicable — no functions created or modified.

### Error State Rendering
Not applicable — no user-visible output produced by this change.

## Test Impact

No existing tests affected — this is a pure documentation consolidation with no code modifications. No test file imports or references any of the three steering doc filenames.

## Rabbit Holes

- **Rewriting `steering-queue.md` content from scratch.** The file is a historical spec. Mark it as such and move on — rewriting costs time with no reader benefit.
- **Merging all three files into one.** The issue recommends three distinct docs with a clear hierarchy. A single monolithic doc would be harder to navigate than three focused ones with cross-links.
- **Fixing functional bugs discovered while reading.** Issue #1027 is docs-only. If steering behavior is found to be wrong, file a separate issue.
- **Updating CLAUDE.md.** CLAUDE.md already references `session-steering.md` (the primary). No change needed there.

## Risks

### Risk 1: Broken cross-links after rename
**Impact:** Any doc or code comment referencing `steering-queue.md` will become a broken link.
**Mitigation:** The Technical Approach section lists all known referencing files. Run a full `grep -r "steering-queue" docs/ .claude/` check after rename to catch any missed refs before commit.

### Risk 2: Accidentally losing content when stripping duplicates
**Impact:** Information that exists only in `steering-queue.md` could be lost if it's mistakenly identified as a duplicate.
**Mitigation:** Read each section carefully before removing. The parent-child section is clearly unique to `steering-queue.md` and moves to `session-steering.md`. Abort keyword / race condition content is duplicated in `mid-session-steering.md` — keep it there (canonical), remove from renamed spec.

## Race Conditions

No race conditions identified — all operations are synchronous doc edits with no concurrent access patterns.

## No-Gos (Out of Scope)

- Code changes of any kind
- Modifying `agent/steering.py` or any other Python module
- Filing additional issues for functional steering bugs discovered during review
- Consolidating docs beyond the three named steering files (e.g., `summarizer-format.md` reference)
- Deleting `steering-queue.md` instead of renaming it — the historical spec has value as context for future maintainers

## Update System

No update system changes required — this is a pure documentation change with no new dependencies, config files, or deployment steps.

## Agent Integration

No agent integration required — this is a documentation-only change. CLAUDE.md already correctly references `session-steering.md` as the primary steering doc (no change needed).

## Documentation

This plan IS the documentation task — the deliverables are all doc files.

- [ ] `docs/features/session-steering.md` — absorb parent-child section, add "See also" links at top
- [ ] `docs/features/mid-session-steering.md` — add "See also: session-steering.md" at top
- [ ] `docs/features/steering-queue.md` → rename to `docs/features/steering-implementation-spec.md`, add historical-spec header, remove parent-child section, strip content duplicated in `mid-session-steering.md`
- [ ] `docs/features/README.md` — update index row for renamed file, add hierarchy note
- [ ] All cross-referencing files — update `steering-queue.md` links to `steering-implementation-spec.md`

## Success Criteria

- [ ] `session-steering.md` contains the parent-child steering section (absorbed from `steering-queue.md`)
- [ ] `session-steering.md` has "See also" links to `mid-session-steering.md` and `steering-implementation-spec.md` at the top
- [ ] `mid-session-steering.md` has "See also: session-steering.md" at the top
- [ ] `steering-queue.md` is renamed to `steering-implementation-spec.md` and marked as historical
- [ ] Abort keyword / status transition / race condition content exists in exactly one location (not duplicated across primary and spec)
- [ ] `docs/features/README.md` reflects the final hierarchy with updated file name
- [ ] `grep -r "steering-queue" docs/ .claude/` returns zero results (no broken links)
- [ ] `grep -r "steering-implementation-spec" docs/features/README.md` confirms new entry exists

## Team Orchestration

### Team Members

- **Builder (docs-consolidator)**
  - Name: docs-consolidator
  - Role: Execute all doc edits, rename, and cross-link updates
  - Agent Type: documentarian
  - Resume: true

- **Validator (link-checker)**
  - Name: link-checker
  - Role: Verify no broken links remain; confirm all success criteria are met
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

#### 1. Consolidate and restructure steering docs
- **Task ID**: build-docs
- **Depends On**: none
- **Validates**: All success criteria in the Success Criteria section
- **Assigned To**: docs-consolidator
- **Agent Type**: documentarian
- **Parallel**: false
- Read `session-steering.md`, `steering-queue.md`, `mid-session-steering.md` in full
- Add "See also" block at top of `session-steering.md` linking to `mid-session-steering.md` and the soon-to-be-renamed spec
- Absorb lines 235–299 (parent-child section) from `steering-queue.md` into `session-steering.md` after the existing "Summarizer Fallback Steering" section
- Add "See also: session-steering.md" link at top of `mid-session-steering.md`
- Strip the parent-child section from `steering-queue.md`; remove content duplicated in `mid-session-steering.md` (abort keywords, status transition table, race conditions); add prominent "Historical Design Specification" header
- Rename `steering-queue.md` → `steering-implementation-spec.md` via `git mv`
- Update all cross-references: `README.md`, `telegram-message-edit-handling.md`, `bridge-workflow-gaps.md`, `pm-dev-session-architecture.md`, and both completed plan docs
- Commit all changes in a single commit

#### 2. Validate — verify no broken links
- **Task ID**: validate-links
- **Depends On**: build-docs
- **Assigned To**: link-checker
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -r "steering-queue" docs/ .claude/ CLAUDE.md` — expect zero results
- Run `grep -r "steering-implementation-spec" docs/features/README.md` — expect match
- Verify `session-steering.md` contains the parent-child section
- Verify "See also" blocks present in `session-steering.md` and `mid-session-steering.md`
- Verify `steering-implementation-spec.md` has historical header and no parent-child section
- Report pass/fail

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No broken steering-queue links | `grep -r "steering-queue" docs/ .claude/ CLAUDE.md` | exit code 1 (no matches) |
| New spec file exists | `test -f docs/features/steering-implementation-spec.md` | exit code 0 |
| Old file gone | `test ! -f docs/features/steering-queue.md` | exit code 0 |
| README updated | `grep "steering-implementation-spec" docs/features/README.md` | exit code 0 |
| Parent-child in primary | `grep "Parent-Child Steering" docs/features/session-steering.md` | exit code 0 |
| See-also in mid-session | `grep "session-steering" docs/features/mid-session-steering.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — the solution sketch from the issue is unambiguous and the freshness check confirmed no major drift. Ready to proceed to critique.
