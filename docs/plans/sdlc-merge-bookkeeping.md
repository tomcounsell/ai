---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/884
last_comment_id:
---

# SDLC Merge Bookkeeping: Three Chained Bug Fixes

## Problem

Three latent bugs in the plan-lifecycle bookkeeping chain fire together at runtime during `/do-merge`. They were surfaced during the #881 merge flow (PR #883) and caused manual reconciliation commits (`be7685fd`, `e733344c`, `70a73295`).

**Current behavior:**
1. `migrate_completed_plan.py` uses `.title()` to reconstruct feature names from filenames, mangling acronyms (PM -> Pm, SDLC -> Sdlc), causing `validate_feature_index()` to fail.
2. `/do-merge` reads the plan document from the session worktree (cwd) instead of `origin/main`, getting a stale copy since plans are always committed on main.
3. `/do-merge` calls `AgentSession.get_by_slug()` which does not exist, causing a latent AttributeError.

**Desired outcome:**
All three bugs fixed in a single build pass. The merge-to-migration chain works end-to-end without manual intervention.

## Freshness Check

**Baseline commit:** `be7685fd`
**Issue filed at:** 2026-04-10T10:23:04Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/migrate_completed_plan.py:225` -- `.title()` call still present
- `.claude/commands/do-merge.md:20` -- `AgentSession.get_by_slug('$SLUG')` still present
- `.claude/commands/do-merge.md:84` -- second `AgentSession.get_by_slug('$SLUG')` still present
- `.claude/commands/do-merge.md:145` -- `plan_path = Path('$PLAN_PATH')` (cwd-relative) still present
- `.claude/commands/do-merge.md:248` -- `PLAN_PATH="docs/plans/${SLUG}.md"` (cwd-relative) still present
- `docs/features/README.md:28` -- `[Chat Dev Session Architecture](pm-dev-session-architecture.md)` still drifted

**Cited sibling issues/PRs re-checked:**
- #881 -- closed 2026-04-10, merged as PR #883
- #823 -- closed 2026-04-09, different concern (structured review comments)

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** All claims from the issue verified against current main. No drift.

## Prior Art

- **Issue #823**: Enforce structured review comment check in `/do-merge` -- different section of the same file, no overlap with these three bugs.
- **Issue #645**: Implicit pipeline stage tracking -- introduced `PipelineStateMachine` and the `get_by_slug` call sites, but the method was never implemented on `AgentSession`. The plan at `docs/plans/completed/implicit-pipeline-tracking.md` references `AgentSession.get_by_slug(slug)` at line 44 as if it existed.

No prior fixes attempted for any of these three bugs.

## Data Flow

The three bugs chain in a single runtime path:

1. **Entry point**: Agent runs `/do-merge {PR_NUMBER}`
2. **Pre-merge pipeline check** (do-merge.md:13-60): Embedded Python calls `AgentSession.get_by_slug('$SLUG')` (Bug 3, site 1). Wrapped in try/except, silently degrades to empty `states = {}`.
3. **Prerequisites check** (do-merge.md:80-103): Embedded Python calls `AgentSession.get_by_slug('$SLUG')` (Bug 3, site 2). NO try/except -- would crash with AttributeError. But this block has never fired because prior merges fell through to manual checks.
4. **Plan completion gate** (do-merge.md:133-189): Reads `plan_path = Path('$PLAN_PATH')` from cwd (Bug 2, site 1). In a worktree, this is the stale checkout-time copy, not the authoritative main copy with checked-off items.
5. **Post-merge migration** (do-merge.md:244-254): Reads `PLAN_PATH="docs/plans/${SLUG}.md"` from cwd (Bug 2, site 2). Passes stale file to `migrate_completed_plan.py`.
6. **Migration script** (migrate_completed_plan.py:225): Reconstructs feature name via `.title()` (Bug 1). Mangled name fails regex match against README index. Migration aborts.
7. **Output**: Manual reconciliation commits needed to clean up.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None -- the migration script's CLI interface stays the same
- **Coupling**: Slightly reduced -- removing the nonexistent `get_by_slug` call removes a coupling to a phantom API
- **Data ownership**: No change
- **Reversibility**: Trivial -- all changes are to internal bookkeeping, no external contracts

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

- **Bug 1 fix (title casing)**: Replace `.title()` with a lookup that reads the actual display text from the README index row, keyed by filename link. The README is already the source of truth for display names.
- **Bug 2 fix (cwd plan read)**: Both plan-reading sites in `/do-merge` use `git show origin/main:docs/plans/${SLUG}.md` instead of reading from the filesystem. Feed content via stdin or tempfile to downstream consumers.
- **Bug 3 fix (get_by_slug)**: Replace both call sites with a working Popoto query: iterate `AgentSession.query.all()` and filter by `slug` attribute. Wrap in a small inline helper within the embedded Python blocks.

### Flow

`/do-merge` invoked -> git show origin/main for plan content -> pipeline check uses working session lookup -> migration script reads display name from README -> success

### Technical Approach

**Bug 1 -- `scripts/migrate_completed_plan.py:225`:**
- Replace line 225 with logic that searches `docs/features/README.md` for a table row whose link target matches the feature doc filename (e.g., `pm-dev-session-architecture.md`).
- Extract the bracketed display text from that row (e.g., `[Chat Dev Session Architecture]` -> `Chat Dev Session Architecture`).
- Use that extracted text as `feature_name` for validation -- no casing transformation needed.
- This makes `validate_feature_index()` a filename-based lookup rather than a reconstructed-name match.
- Separately, fix the drifted README entry at line 28: change display text from `Chat Dev Session Architecture` to `PM/Dev Session Architecture` (matching the actual file's `# PM/Teammate/Dev session Architecture` heading, normalized to title case).

**Bug 2 -- `.claude/commands/do-merge.md` lines ~145 and ~248:**
- Plan completion gate (line 145): Replace `plan_path = Path('$PLAN_PATH')` with reading plan content via `git show origin/main:docs/plans/${SLUG}.md`. Pass content as a string variable into the Python block.
- Post-merge migration (line 248): Before calling `migrate_completed_plan.py`, check out the authoritative plan from main: `git show origin/main:docs/plans/${SLUG}.md > /tmp/plan_${SLUG}.md`, then pass the temp path to the migration script. Or refactor the `if [ -f "$PLAN_PATH" ]` check to use `git show` exit code.

**Bug 3 -- `.claude/commands/do-merge.md` lines 20 and 84:**
- Both call sites are load-bearing: site 1 displays pipeline progress, site 2 gates merge on stage completion.
- Replace `AgentSession.get_by_slug('$SLUG')` with an inline query: `next((s for s in AgentSession.query.all() if s.slug == '$SLUG'), None)`.
- Site 1 (line 20) already has try/except -- keep it as defense-in-depth.
- Site 2 (line 84) lacks try/except -- add one with a clear error message directing to manual checks.

**README drift fix:**
- `docs/features/README.md:28`: Change `[Chat Dev Session Architecture]` to `[PM/Dev Session Architecture]`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Bug 3 site 2 (line 84) currently has NO exception handling -- add try/except with informative error message and graceful fallback to manual checks
- [ ] Bug 3 site 1 (line 20) already has try/except Exception -- no change needed

### Empty/Invalid Input Handling
- [ ] Test `validate_feature_index()` with a feature doc whose filename has no matching README row -- should return (False, error message), not crash
- [ ] Test the new README-based name extraction when the README has no matching link -- should fail gracefully

### Error State Rendering
- [ ] Migration script error messages should name the feature doc and the README row it searched for, not just the mangled title

## Test Impact

No existing tests affected -- `validate_feature_index()`, `validate_feature_doc()`, and the migration script have zero test coverage today. All tests in this plan are new.

## Rabbit Holes

- **Adding `slug` as an IndexedField on AgentSession**: Tempting for query performance, but this is a Popoto schema migration affecting all sessions in Redis. Overkill for two call sites in a prompt template that run once per merge. A linear scan of active sessions is fine.
- **Refactoring `migrate_completed_plan.py` to accept plan content on stdin**: Nice-to-have but unnecessary -- the tempfile approach for Bug 2 is simpler and keeps the script's existing file-path interface.
- **Fixing the recon validator's bucket pattern matching**: The validator at `.claude/hooks/validators/validate_issue_recon.py` fails on issue #884 because it expects `**Confirmed:**` (colon) but the issue uses `**Confirmed.**` (period). Real bug, but out of scope for this plan.

## Risks

### Risk 1: README display text change breaks other automation
**Impact:** If any other script matches on "Chat Dev Session Architecture", it would break.
**Mitigation:** Grep the codebase for the old display text before changing it. The only consumer is `validate_feature_index()`, which we're fixing simultaneously.

### Risk 2: `AgentSession.query.all()` returns too many sessions
**Impact:** Slow scan if thousands of sessions exist in Redis.
**Mitigation:** The scan runs once per merge invocation. Even with 1000 sessions, iterating a Python list is sub-millisecond. Not a real concern for an interactive prompt skill.

## Race Conditions

No race conditions identified -- all operations are synchronous, single-threaded, and run within a single `/do-merge` invocation. The `git show origin/main:` read is a snapshot of a specific ref, immune to concurrent pushes.

## No-Gos (Out of Scope)

- Adding `slug` as an IndexedField on AgentSession (schema migration)
- Refactoring `migrate_completed_plan.py` to accept stdin (interface change)
- Fixing the recon validator bucket pattern matching (separate bug)
- Adding a `get_by_slug()` classmethod to AgentSession (unnecessary API surface)

## Update System

No update system changes required -- all changes are to internal SDLC tooling (a prompt template and a migration script). No new dependencies, no config files, no deployment changes.

## Agent Integration

No agent integration required -- the affected files are a slash command prompt template (`.claude/commands/do-merge.md`) and a standalone Python script (`scripts/migrate_completed_plan.py`). Neither is exposed through MCP servers or called by the bridge.

## Documentation

- [ ] Update `docs/features/README.md` line 28: fix drifted display text from `Chat Dev Session Architecture` to `PM/Dev Session Architecture`
- [ ] Update `docs/features/documentation-lifecycle.md` if it references the `.title()` pattern -- verify and correct if needed
- [ ] No new feature doc needed -- this is a bugfix to existing infrastructure

## Success Criteria

- [ ] `python scripts/migrate_completed_plan.py` succeeds against a feature doc whose filename contains acronyms (pm-, sdlc-, ai-)
- [ ] Unit test: `validate_feature_index()` matches features with acronym-heavy names without mangling
- [ ] Unit test: feature name extraction from README works when display text differs from filename
- [ ] `/do-merge` plan completion gate reads from `origin/main`, not cwd
- [ ] `/do-merge` post-merge migration reads from `origin/main`, not cwd
- [ ] `grep "get_by_slug" .claude/commands/do-merge.md` returns zero matches
- [ ] `docs/features/README.md:28` display text matches the actual feature doc title
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (merge-fixes)**
  - Name: merge-fix-builder
  - Role: Fix all three bugs across the three affected files
  - Agent Type: builder
  - Resume: true

- **Validator (merge-fixes)**
  - Name: merge-fix-validator
  - Role: Verify all fixes and run tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix Bug 1: Title Casing in Migration Script
- **Task ID**: build-bug1-title-casing
- **Depends On**: none
- **Validates**: tests/unit/test_migrate_completed_plan.py (create)
- **Assigned To**: merge-fix-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `feature_doc_path.stem.replace("-", " ").title()` at `scripts/migrate_completed_plan.py:225` with README-based display name extraction
- Add a helper function `extract_feature_name_from_index(feature_doc_filename: str) -> str | None` that parses the README table for a row with a matching link target and returns the bracketed display text
- Update `validate_feature_index()` to use the extracted name instead of the mangled one
- Fix `docs/features/README.md:28`: change `[Chat Dev Session Architecture]` to `[PM/Dev Session Architecture]`
- Create `tests/unit/test_migrate_completed_plan.py` with tests for:
  - Acronym-heavy filenames (pm-dev-session-architecture, sdlc-critique-stage, ai-evaluator)
  - Display text that differs from filename (the exact drifted case)
  - Missing README entry (graceful failure)

### 2. Fix Bug 2: CWD-Dependent Plan Read in do-merge
- **Task ID**: build-bug2-plan-read
- **Depends On**: none
- **Assigned To**: merge-fix-builder
- **Agent Type**: builder
- **Parallel**: true
- In `.claude/commands/do-merge.md` plan completion gate (~line 138-189): replace `plan_path = Path('$PLAN_PATH')` with reading from `git show origin/main:docs/plans/${SLUG}.md` and passing content as a Python string variable
- In `.claude/commands/do-merge.md` post-merge migration (~line 244-254): use `git show origin/main:docs/plans/${SLUG}.md` to write a temp file, then pass that temp file to `migrate_completed_plan.py`
- Ensure both sites handle the "plan not found on origin/main" case gracefully (skip with warning, matching current behavior)

### 3. Fix Bug 3: Nonexistent get_by_slug
- **Task ID**: build-bug3-get-by-slug
- **Depends On**: none
- **Assigned To**: merge-fix-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `AgentSession.get_by_slug('$SLUG')` at line 20 with `next((s for s in AgentSession.query.all() if s.slug == '$SLUG'), None)`
- Replace `AgentSession.get_by_slug('$SLUG')` at line 84 with the same pattern, wrapped in try/except with fallback to manual checks
- Verify `grep "get_by_slug" .claude/commands/do-merge.md` returns zero matches

### 4. Integration Test
- **Task ID**: build-integration-test
- **Depends On**: build-bug1-title-casing
- **Validates**: tests/unit/test_migrate_completed_plan.py
- **Assigned To**: merge-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Create a test that exercises the full migration chain: mock a README with acronym-heavy entries, create a temp feature doc, run `validate_feature_doc()` + `validate_feature_index()` end-to-end
- Test the specific scenario that triggered the bug: a feature named `pm-dev-session-architecture` with a README entry that says `PM/Dev Session Architecture`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-bug1-title-casing, build-bug2-plan-read, build-bug3-get-by-slug
- **Assigned To**: merge-fix-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Verify `docs/features/documentation-lifecycle.md` does not reference the `.title()` pattern
- Confirm README line 28 fix is in place

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-integration-test, document-feature
- **Assigned To**: merge-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_migrate_completed_plan.py -v`
- Run `python -m ruff check scripts/migrate_completed_plan.py .claude/commands/`
- Verify `grep "get_by_slug" .claude/commands/do-merge.md` returns exit code 1
- Verify `grep "\.title()" scripts/migrate_completed_plan.py` returns exit code 1
- Verify README line 28 contains `PM/Dev Session Architecture`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_migrate_completed_plan.py -v` | exit code 0 |
| Lint clean | `python -m ruff check scripts/migrate_completed_plan.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/migrate_completed_plan.py` | exit code 0 |
| No get_by_slug | `grep "get_by_slug" .claude/commands/do-merge.md` | exit code 1 |
| No naive title | `grep "\.title()" scripts/migrate_completed_plan.py` | exit code 1 |
| README fixed | `grep "PM/Dev Session Architecture" docs/features/README.md` | exit code 0 |
| Full test suite | `pytest tests/ -x -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions -- all three bugs are fully characterized with confirmed line numbers, root causes, and fix approaches. The only decision (Bug 3: add helper vs delete dead code) is resolved: use an inline query, do not add API surface.
