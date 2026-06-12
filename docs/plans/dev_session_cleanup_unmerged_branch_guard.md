---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-12
tracking: https://github.com/tomcounsell/ai/issues/1646
last_comment_id:
---

# Dev-Session Completion Cleanup: Guard Against Destroying Unmerged Work

## Problem

A granite PTY dev session committed finished work, and the completion cleanup
force-deleted its branch before anything was merged, pushed, or PR'd. The work
became a dangling commit while the session, dashboard, and Telegram flow all
reported success. This is silent data loss on the production session runner.

**Current behavior:**
On successful completion of a bridge-originated dev session, `agent/session_executor.py`
runs two unconditional destruction steps with no merge precondition:

1. After `mark_work_done()` (which only archives plan files and checks out main —
   it never merges, `agent/branch_manager.py:387`), the executor runs
   `git branch -D {branch}` (`agent/session_executor.py:~2017`). `-D` force-deletes,
   ignoring unmerged commits. (`-d` would have refused.)
2. For synthetic-slug dev sessions (`dev-{8hex}`, issue #1272), the `finally` block
   calls `worktree_manager.cleanup_after_merge()` (`agent/session_executor.py:2080`)
   — a function whose own docstring says it runs "After `gh pr merge --squash
   --delete-branch`" — with **no verification that any merge occurred**. It removes
   the worktree and force-deletes the branch (`agent/worktree_manager.py:1226`, `-D`).

There is no landing path on this code path at all: the granite Dev persona commits
but never opens a PR or pushes, and the PM persona has no merge step. So work is
committed, then destroyed.

**Confirmed incident:** session `ec1e7c6ede0b4cc491247067dea676f6` (2026-06-12), commit
`95e1a39b` "feat(image_gen): make gpt-image-1 the default provider" became dangling;
recovered via `git fsck` onto `rescued/dev-ec1e7c6e`.

**Desired outcome:**
- Cleanup can **never** delete a branch holding commits that are not an ancestor of
  `main`. This is an unconditional safety floor regardless of any other decision.
- Branch/worktree cleanup stops being an automatic side effect of session completion.
  It runs only after the work has landed (merged to main, or pushed with an open PR)
  and the PM has authorized it.
- `cleanup_after_merge()` verifies its own precondition instead of trusting its caller.

## Freshness Check

**Baseline commit:** `c136101e` (`git rev-parse HEAD` at plan time, on `main`)
**Issue filed at:** 2026-06-12T11:04:35Z
**Disposition:** Unchanged

**File:line references re-verified (all still hold against `c136101e`):**
- `agent/branch_manager.py:387` `mark_work_done` — confirmed: archives ACTIVE plan
  docs, checks out main, deletes branch with `git branch -d` (line 445, the *safe*
  variant). It performs no merge. Matches issue claim.
- `agent/branch_manager.py:442-449` — confirmed: `mark_work_done` already uses
  `git branch -d` (safe). The executor's subsequent `-D` is what overrides this refusal.
- `agent/session_executor.py:~2010-2027` — confirmed: auto-mark block calls
  `mark_work_done()` then `git branch -D {branch}` with `capture_output=True`
  (failures swallowed). Matches issue claim.
- `agent/session_executor.py:2057-2089` — confirmed: synthetic-slug `finally` cleanup
  matches `^dev-[0-9a-f]{8}$` and calls `cleanup_after_merge()` with no merge check.
- `agent/worktree_manager.py:1150` `cleanup_after_merge` — confirmed: docstring states
  it is the post-PR-merge step; deletes worktree + branch via `git branch -D` (line 1226)
  with no `is-ancestor`/merge precondition.
- `agent/worktree_manager.py:882` `remove_worktree(delete_branch=True)` — additional
  force-delete site (`git branch -D`) found during recon, not cited in the issue but in
  the same blast radius.

**Cited sibling issues/PRs re-checked:**
- #1644 (PM→Dev relay drop) — OPEN. Same observed session; orthogonal defect (relay), not
  the cleanup path. No overlap with this plan's surface.
- #1572 (granite PTY production cutover) — CLOSED. Established the path this bug lives on.
- #1272 (synthetic slug isolation) — CLOSED. Introduced the synthetic-slug cleanup hook
  that this plan hardens.
- #887 (session isolation bypass) — CLOSED. Background on worktree lifecycle.
- #1643 (purge legacy PoC framing) — OPEN. Owns persona-prime rewrites.
- #1647 (PM never routes [/user]/[/complete]) — OPEN. Owns the PM completion-routing fix.

**Commits on main since issue was filed (touching referenced files):** none — issue filed
2026-06-12T11:04Z, baseline `c136101e` is the merge-gate plan revision (docs only). No code
changes to `session_executor.py`, `branch_manager.py`, or `worktree_manager.py` since filing.

**Active plans in `docs/plans/` overlapping this area:** none directly. `session-branch-checkout-guard.md`
and `session_lifecycle_stale_cleanup.md` touch session lifecycle but neither touches the
completion-cleanup deletion path. No overlap requiring coordination.

**Notes:** The issue's Open Question #1 ("what is the intended landing path?") was resolved by
Tom on 2026-06-12 (see Prior Art / memory `project-granite-landing-policy`). The plan adopts
that decision rather than re-asking it.

## Prior Art

- **Issue #1272 (CLOSED):** Introduced synthetic-slug worktree isolation and the
  `cleanup_after_merge()` call in the executor `finally` block. The cleanup hook was added
  so synthetic-slug worktrees do not linger — correct intent, but it reused a function whose
  contract assumes a prior PR merge, on a path where no merge happens. This plan fixes that
  misuse without removing the legitimate "don't leak worktrees" goal.
- **Issue #1357:** Added `blocked_by_session` handling to `cleanup_after_merge` —
  precedent that this function already guards against unsafe deletion in one dimension
  (live session references); we extend it with the unmerged-commits dimension.
- **Tom's landing-policy decision (2026-06-12), memory `project-granite-landing-policy`,
  recorded on #1646/#1644/#1647:** Resolves the upstream design question. **PM decides the
  landing path per task** (trivial → auto-merge when green; substantive → push + PR +
  `/do-merge`); **PM sign-off is the final step BEFORE any cleanup**; cleanup must never be
  an executor-automatic side effect; unconditional floor: never delete an unmerged branch.

No prior *fix* attempts for this specific data-loss path were found — `gh issue list --state
closed --search "unmerged branch data loss cleanup"` returned nothing. This is a first fix.

## Research

No relevant external findings — this is purely internal git-plumbing and session-lifecycle
work. The relevant external fact (git's `branch -d` vs `-D` semantics, and `git merge-base
--is-ancestor <branch> main` returning exit 0 iff `<branch>` is an ancestor of main) is
standard git and already confirmed by reading the code.

## Data Flow

Trace of a granite dev session from completion to (current) data loss, annotated with where
each guard lands:

1. **Entry point:** Dev PTY session commits work to `session/dev-{id}` inside worktree
   `.worktrees/dev-{id}`. No push, no PR. Session transitions `running → completed`.
2. **`session_executor.py` auto-mark block (~2010):** if `not task.error and not
   chat_state.defer_reaction` →
   - `mark_work_done(working_dir, branch_name)` — archives plan, `git checkout main`,
     `git branch -d` (safe; refuses unmerged — but only inside the worktree's own checkout).
   - `git branch -D {branch_name}` ← **DESTRUCTION SITE A.** Force-deletes regardless.
     *Guard: replace with `-d` + explicit `is-ancestor` check; skip + warn + persist a
     recovery marker when not merged.*
3. **`session_executor.py` synthetic-slug `finally` (2057):** if slug matches `^dev-[0-9a-f]{8}$`
   → `cleanup_after_merge(repo_root, slug)`.
   - `remove_worktree(..., delete_branch=False)` removes the worktree dir.
   - `git branch -D {branch}` ← **DESTRUCTION SITE B** (worktree_manager.py:1226).
     *Guard: `cleanup_after_merge` must verify `is-ancestor` before deleting; refuse + report
     when unmerged.*
4. **`worktree_manager.remove_worktree(delete_branch=True)` (882):** ← **DESTRUCTION SITE C.**
   Not on the incident path (callers pass `delete_branch=False` there), but the same force-delete
   primitive. *Guard: route all branch deletion through one safe helper.*
5. **Output:** session reported success; commit dangling. *Desired output: work landed (merged
   or PR-open) before any deletion, or branch preserved with an actionable recovery marker.*

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1272 cleanup hook | Added `cleanup_after_merge()` to executor `finally` to stop synthetic-slug worktrees leaking | Reused a function whose contract assumes a prior PR merge, on a path where no merge ever occurs. The "merge already happened" precondition was implicit and untrusted-but-trusted. |
| `mark_work_done` `-d` (existing) | Uses the safe `git branch -d` inside the worktree | Correct, but the executor immediately follows with `git branch -D` in the main repo, overriding the refusal. The safe delete was load-bearing and got bypassed. |

**Root cause pattern:** deletion is treated as an unconditional completion side effect rather
than a consequence of a verified landing. Each force-delete site trusts that "if we got here,
the work is safe to discard" — an invariant nothing establishes.

## Architectural Impact

- **New dependencies:** none. Pure git plumbing + existing subprocess patterns.
- **Interface changes:**
  - `cleanup_after_merge(repo_root, slug)` gains an internal precondition check and a new
    result key (e.g. `skipped_unmerged: bool` + `unmerged_branch` in `errors`). Signature
    unchanged; return dict grows additively (back-compatible per existing callers in
    `post_merge_cleanup.py`).
  - A new small helper `safe_delete_branch(repo_root, branch_name, *, base="main")` centralizes
    the `is-ancestor`-then-`-d` logic so all three destruction sites share one implementation.
- **Coupling:** *decreases* — three ad-hoc `git branch -D` call sites collapse to one guarded
  helper.
- **Data ownership:** the executor stops owning "delete the branch on completion." Branch
  lifecycle ownership moves to the landing step (PM-authorized), matching Tom's policy.
- **Reversibility:** trivial to revert (localized to two files plus tests). No data migration.

## Appetite

**Size:** Medium

**Team:** Solo dev, validator, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm scope boundary vs. #1643/#1647 persona work)
- Review rounds: 1 (this is a data-loss fix — one careful review round)

## Prerequisites

No prerequisites — this work has no external dependencies. It modifies existing Python files
and runs against the local git toolchain already present.

## Solution

### Key Elements

- **`safe_delete_branch` helper** (`agent/worktree_manager.py`): the single primitive for
  deleting a session branch. Runs `git merge-base --is-ancestor {branch} {base}`; deletes with
  `git branch -d` only when the branch is an ancestor of base (i.e. fully merged). When not
  merged, it refuses, logs a `[unmerged-branch-guard]` warning, and returns a structured
  "skipped" result. Surfaces git failures instead of swallowing them.
- **`cleanup_after_merge` precondition** (`agent/worktree_manager.py:1150`): before deleting
  the branch, verify the merged precondition via `safe_delete_branch`. If unmerged, remove
  nothing destructive to the commit (skip branch deletion), set `skipped_unmerged: true`, and
  record the branch name so callers/operators can find the work.
- **Executor stops force-deleting** (`agent/session_executor.py`): replace the
  `git branch -D` in the auto-mark block (Site A) with `safe_delete_branch`. The synthetic-slug
  `finally` path (Site B) inherits the guard automatically via `cleanup_after_merge`.
- **Recovery marker:** when a branch is preserved because it is unmerged, the path logs a clear,
  greppable warning and persists the branch name (and the worktree path, if still present) so a
  human or follow-up automation can recover the work — never a silent skip.
- **No new automatic landing on this PR.** Per Tom's policy, *who* lands the work (auto-merge vs
  push+PR) and *when* cleanup is authorized is the PM's per-task decision and belongs to the
  PM-routing work (#1647) and persona primes (#1643). This plan guarantees the safety floor:
  **nothing destroys unmerged commits**, so a missing landing path degrades to "branch and
  worktree are preserved," not "work is lost."

### Flow

Dev session completes → executor auto-mark runs → `safe_delete_branch(repo, "session/dev-{id}")`
→ **branch is ancestor of main?**
- **Yes (merged):** `git branch -d` succeeds → branch removed cleanly.
- **No (unmerged):** deletion skipped → `[unmerged-branch-guard]` warning logged with branch name
  → branch and (if synthetic) worktree preserved → work recoverable.

### Technical Approach

- Centralize: extract `safe_delete_branch(repo_root, branch_name, *, base="main") -> dict` and
  route Sites A, B, and C through it. No raw `git branch -D` remains in the cleanup paths.
- `is-ancestor` is the correctness oracle: `git merge-base --is-ancestor <branch> <base>` exits 0
  iff `<branch>`'s tip is reachable from `<base>` — i.e. fully merged. Exit 1 = unmerged → refuse.
  Treat any other exit (e.g. missing branch, 128) as "do not delete, surface the error."
- Determine `base` robustly: default `"main"`; if `main` is not present locally fall back to the
  repo's default branch (`git symbolic-ref refs/remotes/origin/HEAD`), and if even that is
  unavailable, refuse deletion (fail safe — never delete when we cannot prove merged).
- Keep `cleanup_after_merge`'s worktree removal (the directory) intact — removing the *worktree
  directory* does not destroy commits (they live in `.git`); only *branch deletion* destroys
  reachability. But when the branch is unmerged we keep the worktree too, so the work is easy to
  find and resume.
- Stop swallowing failures: the executor's current `capture_output=True` with no return check is
  replaced by inspecting `safe_delete_branch`'s result and logging on the skip/error branches.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The synthetic-slug `finally` block (`session_executor.py:2085`) keeps its
  `except Exception … non-fatal` swallow (cleanup must never fail a session) — but add a test
  asserting that when `cleanup_after_merge` *skips* an unmerged branch, the warning is logged and
  the branch survives (observable behavior, not a silent pass).
- [ ] The auto-mark block's `except Exception` (`session_executor.py:~2028`) — add a test that an
  unmerged branch is preserved and a `[unmerged-branch-guard]` warning is emitted, not swallowed.

### Empty/Invalid Input Handling
- [ ] `safe_delete_branch` with a non-existent branch name → returns a structured "not found"
  result, deletes nothing, does not raise.
- [ ] `safe_delete_branch` when `base` (main) cannot be resolved → refuses deletion (fail safe),
  returns an error result.

### Error State Rendering
- [ ] When work is preserved due to being unmerged, the log line is greppable
  (`[unmerged-branch-guard]`) and names the branch — verify in a test capturing log output.
- [ ] `cleanup_after_merge` result dict carries `skipped_unmerged: true` + branch name so
  `post_merge_cleanup.py` and any operator tooling can render the preserved state.

## Test Impact

- [ ] `tests/unit/test_worktree_manager.py::test_branch_deletion_fails` (line 144) — UPDATE:
  this currently exercises the `cleanup_after_merge` deletion path with mocked git; update its
  expectations for the new `is-ancestor` precondition (it should now assert the precondition is
  checked, and that an unmerged branch yields `skipped_unmerged`).
- [ ] `tests/unit/test_worktree_manager.py` (cleanup_after_merge happy-path cases) — UPDATE:
  add an `is-ancestor`-returns-0 setup so existing merged-branch deletion tests still pass under
  the new guard.
- [ ] `tests/unit/test_post_merge_cleanup.py::{test_clean_exits_0,test_success_exits_0,
  test_blocked_session_exits_2,test_generic_error_exits_1}` — UPDATE: extend the fake
  `cleanup_after_merge` result dicts with the new `skipped_unmerged` key (additive) and add a new
  case asserting the script's behavior when a branch is skipped as unmerged.
- [ ] NEW `tests/unit/test_worktree_manager.py::test_safe_delete_branch_*` — REPLACE/ADD: a focused
  set covering merged (deletes), unmerged (refuses + preserves), missing-branch, and
  unresolvable-base cases against a real temp git repo.
- [ ] NEW `tests/unit/test_session_executor_cleanup.py` — ADD: regression test reproducing the
  incident — a dev session branch with an unmerged commit goes through the auto-mark cleanup and
  the commit remains reachable afterward (the canonical "this bug never recurs" test).

## Rabbit Holes

- **Implementing the full PM-authorized landing handshake here.** The "PM decides per task /
  PM sign-off gates cleanup" ordering lives in PM routing (#1647) and persona primes (#1643).
  This plan delivers the safety floor only. Building the landing orchestration here would balloon
  scope and collide with those slugs.
- **Auto-merging dev work to main inside the executor.** Tempting ("just merge it"), but merge
  policy is a PM decision and merging from the executor would bypass review for substantive
  changes. Out of scope.
- **Rewriting worktree lifecycle / garbage collection.** `prune_worktrees`, stale-session
  cleanup, and worktree GC are adjacent but separate. Touch only the branch-deletion guard.
- **Auto-pushing every session branch to origin as a backup.** Network/credential surface, remote
  clutter, and policy questions. The `is-ancestor` guard + preserved local branch already prevents
  data loss; remote backup is a possible later enhancement, not this fix.

## Risks

### Risk 1: Worktrees accumulate when work is unmerged
**Impact:** Preserving the worktree for every unmerged completion could leak `.worktrees/dev-*`
directories over time (the original #1272 concern resurfaces, inverted).
**Mitigation:** Preserving the *worktree directory* is cheap and reversible (it holds no unique
commit data once the branch is safe). The follow-up landing work (#1647) authorizes cleanup after
landing, which reaps these. As a backstop, the existing stale-worktree GC can prune directories
whose branch has since merged. Document the preserved-worktree state clearly so it is observable.

### Risk 2: `is-ancestor` base resolution edge cases
**Impact:** If `main` cannot be resolved (detached state, unusual checkout), a naive check could
either wrongly delete or wrongly preserve.
**Mitigation:** Fail safe — when base cannot be resolved, refuse deletion. Test the
unresolvable-base path explicitly. Preserving too much is recoverable; deleting too much is not.

### Risk 3: Existing tests assume force-delete semantics
**Impact:** Tests that relied on `cleanup_after_merge` always deleting could break.
**Mitigation:** Test Impact section enumerates every affected case; updates set up the
`is-ancestor`-true condition so legitimately-merged branches still delete. Run the affected
worktree and cleanup-script unit suites in validation.

## Race Conditions

### Race 1: Worktree-local checkout vs. main-repo branch deletion
**Location:** `agent/session_executor.py` auto-mark block (~2010) — `mark_work_done` operates in
the worktree checkout, then `git branch -D` runs against the branch in the main repo.
**Trigger:** The branch is checked out in the worktree at the moment the main repo tries to delete
it.
**Data prerequisite:** The commit must remain reachable until the merged check completes.
**State prerequisite:** `safe_delete_branch` reads `is-ancestor` and decides atomically; it never
deletes a branch it just proved unmerged.
**Mitigation:** `is-ancestor` + `-d` is itself the guard — git's `-d` independently refuses to
delete a branch that is checked out or unmerged, so even a TOCTOU between the check and the delete
fails closed (git refuses), never destroying the commit. No lock needed; the operations are
single-process and synchronous within the executor.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1647] PM completion-routing changes that decide the landing path per task and
  authorize cleanup after sending the user summary. This plan only guarantees the safety floor;
  the PM-routing handshake is tracked on #1647.
- [SEPARATE-SLUG #1643] Persona-prime wording (PM prime stating its landing-decision duty and
  cleanup-authorization role; Dev prime stating Dev commits but never lands/merges/deletes). Owned
  by the persona rewrite on #1643.
- [SEPARATE-SLUG #1644] PM→Dev relay drop. Orthogonal defect on the same observed session.

## Update System

No update system changes required — this fix is purely internal to `agent/`. No new
dependencies, config files, or machine-propagated artifacts. The changed Python files ship to
every machine through the normal `git pull` in `/update`; the worker picks them up on its next
restart (the standard restart already in the update flow). No migration step for existing
installations — the guard is active the moment the new code runs.

## Agent Integration

No agent integration required — this is a worker-internal change to session-completion cleanup.
The agent does not invoke branch deletion through a tool or CLI; it happens inside the executor
(`agent/session_executor.py`) and `agent/worktree_manager.py` as part of session lifecycle. No new
CLI entry point in `pyproject.toml [project.scripts]`, no `mcp_servers/` change, no bridge import.
The behavior is exercised end-to-end by the worker running a dev session; the new
`test_session_executor_cleanup.py` regression test covers that path without needing a live agent.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — document the completion-cleanup safety
  floor: cleanup never deletes an unmerged branch; preserved branches/worktrees are recoverable;
  landing/cleanup-authorization is the PM's job (cross-link #1647/#1643).
- [ ] Update `docs/features/session-isolation.md` (or the worktree-lifecycle doc) — note that
  `cleanup_after_merge` now verifies the merged precondition and may skip deletion.

### Inline Documentation
- [ ] Docstring on new `safe_delete_branch` stating the `is-ancestor` contract and fail-safe
  behavior.
- [ ] Update `cleanup_after_merge` docstring to state it now verifies the merged precondition
  rather than trusting the caller (remove the "trust the caller already merged" implication).

### External Documentation Site
No external docs site changes — this repo has no Sphinx/MkDocs site for these internals.

## Success Criteria

- [ ] No `git branch -D` remains in the cleanup paths (`session_executor.py` auto-mark block,
  `worktree_manager.py` `cleanup_after_merge` / `remove_worktree`) — all branch deletion routes
  through `safe_delete_branch`. (`grep -n "branch.*-D" agent/session_executor.py
  agent/worktree_manager.py` returns no cleanup-path hits.)
- [ ] A dev session that commits unmerged work and completes leaves its branch (and synthetic
  worktree) intact, with a `[unmerged-branch-guard]` warning — verified by the regression test
  reproducing incident `ec1e7c6e`.
- [ ] A dev session whose branch *is* merged into main still has its branch cleaned up (no
  regression in the happy path).
- [ ] `cleanup_after_merge` returns `skipped_unmerged: true` + the branch name when it declines to
  delete an unmerged branch.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No raw `git branch -D` in cleanup code: `grep -rn "branch.*\"-D\"\|branch.*'-D'" agent/`
  shows only intentional, non-cleanup uses (or none).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER
builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (cleanup-guard)**
  - Name: `cleanup-guard-builder`
  - Role: Implement `safe_delete_branch`, route all cleanup-path deletions through it, add the
    `is-ancestor` precondition to `cleanup_after_merge`, replace the executor `-D`.
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: `cleanup-test-builder`
  - Role: Add `test_safe_delete_branch_*`, the executor regression test, and update affected
    worktree and cleanup-script tests per Test Impact.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (cleanup-guard)**
  - Name: `cleanup-validator`
  - Role: Verify no `git branch -D` remains in cleanup paths, the regression test fails on the
    pre-fix code and passes after, and all affected suites are green.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `cleanup-doc`
  - Role: Update `granite-pty-production.md` and session-isolation docs; update docstrings.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Implement the safe-delete guard
- **Task ID**: build-guard
- **Depends On**: none
- **Validates**: tests/unit/test_worktree_manager.py, tests/unit/test_post_merge_cleanup.py
- **Assigned To**: cleanup-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `safe_delete_branch(repo_root, branch_name, *, base="main") -> dict` to
  `agent/worktree_manager.py`: resolve base (main → origin default → refuse), run
  `git merge-base --is-ancestor`, delete with `git branch -d` only when merged, return a
  structured result (`deleted` / `skipped_unmerged` / `not_found` / `error`), log
  `[unmerged-branch-guard]` on skip.
- Route `cleanup_after_merge`'s branch deletion (worktree_manager.py:1226) and
  `remove_worktree(delete_branch=True)` (882) through `safe_delete_branch`; add
  `skipped_unmerged` + branch name to the `cleanup_after_merge` result dict.
- Replace `git branch -D {branch}` in `agent/session_executor.py` auto-mark block with
  `safe_delete_branch`; inspect the result and log instead of swallowing.

### 2. Tests
- **Task ID**: build-tests
- **Depends On**: build-guard
- **Validates**: tests/unit/test_worktree_manager.py, tests/unit/test_post_merge_cleanup.py, tests/unit/test_session_executor_cleanup.py (create)
- **Assigned To**: cleanup-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `test_safe_delete_branch_*` against a real temp git repo: merged→deletes,
  unmerged→preserves+`skipped_unmerged`, missing branch, unresolvable base.
- Add `tests/unit/test_session_executor_cleanup.py` reproducing incident `ec1e7c6e`: unmerged
  commit survives the auto-mark cleanup.
- Update `test_branch_deletion_fails` and `cleanup_after_merge` happy-path tests for the new
  precondition; extend `test_post_merge_cleanup.py` fakes with `skipped_unmerged`.

### 3. Validation
- **Task ID**: validate-guard
- **Depends On**: build-guard, build-tests
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm no `git branch -D` in cleanup paths (grep).
- Confirm the regression test reproduces the bug on pre-fix code (git stash the fix, run, expect
  fail) and passes with the fix.
- Run `pytest tests/unit/test_worktree_manager.py tests/unit/test_post_merge_cleanup.py
  tests/unit/test_session_executor_cleanup.py -q`.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-guard
- **Assigned To**: cleanup-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/granite-pty-production.md` and session-isolation/worktree docs.
- Update `safe_delete_branch` and `cleanup_after_merge` docstrings.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-guard, build-tests, validate-guard, document-feature
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run lint, format, and the full affected unit suites.
- Verify every Success Criterion, including the docs updates.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Affected unit tests pass | `pytest tests/unit/test_worktree_manager.py tests/unit/test_post_merge_cleanup.py tests/unit/test_session_executor_cleanup.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/` | exit code 0 |
| No force-delete in cleanup paths | `grep -n "branch\", \"-D\"\|branch', '-D'" agent/session_executor.py agent/worktree_manager.py` | exit code 1 |
| Guard helper exists | `grep -n "def safe_delete_branch" agent/worktree_manager.py` | output contains safe_delete_branch |

## Critique Results

**Verdict: NEEDS REVISION** (cycle 1, real plan — supersedes the pre-plan router-quirk verdict)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| MAJOR | Archaeologist / Structural | **Fourth force-delete site missed.** `agent/session_revival.py:230-238` force-deletes session branches by **age alone** (`age_hours > max_age_hours`) via `git branch -D` with **zero merge check** — and is invoked autonomously by the `stale-branch-cleanup` reflection (`cleanup_stale_branches_all_projects`, scheduler-driven across all projects). This is the exact root-cause pattern the plan names ("deletion as an unconditional side effect"), and is arguably a *higher-risk* data-loss vector than the completion path because it runs on a schedule, not just on session completion. The plan's Freshness Check and Data Flow enumerate only 3 sites (executor:2018, worktree:882, worktree:1226) and claim to enumerate "force-delete sites" — that claim is incomplete. | UNADDRESSED — needs plan revision | Route `session_revival.py:232` through `safe_delete_branch` (same helper, trivial), OR explicitly carve it out in No-Gos with a justification for why age-based force-delete of unmerged work is acceptable there. The former is strongly preferred: an age threshold is not a merged-ness proof, so the scheduler can silently destroy unmerged work the same way the incident did. Add a test for the revival path if routed. |
| MINOR | Skeptic / Structural | **Internal success-criteria contradiction.** Success Criterion line 394 (`grep -rn 'branch.*"-D"...' agent/`, expects "intentional non-cleanup only / none") will still return `session_revival.py:232` after the fix as scoped — and revival is a *cleanup* reflection, so it reads as a cleanup-path hit that fails the criterion. Verification-table line 503 is narrowly scoped to only `session_executor.py` + `worktree_manager.py` and will pass. The two acceptance gates disagree on whether revival.py is in scope, so the plan can be simultaneously "passing" (table) and "failing" (criterion). | UNADDRESSED — needs plan revision | Reconcile the two greps. If revival.py is routed through the helper (preferred per the MAJOR finding), both can be repo-wide and agree. If carved out, line 394's grep must explicitly allow the revival.py occurrence and the carve-out must be named. |

**Structural checks that PASSED:**
- All four required sections present (Documentation, Update System, Agent Integration, Test Impact).
- Task dependency graph valid — no gaps, no cycles; all `Depends On` references resolve.
- All cited `file:line` references hold against HEAD (executor:2018, worktree_manager:882 & :1226, branch_manager:387/445, worktree_manager:1150 docstring).
- All referenced existing tests exist (`test_branch_deletion_fails`, the four `test_post_merge_cleanup` cases); `post_merge_cleanup.py` import confirmed.
- `is-ancestor` correctness oracle, fail-safe base resolution, and TOCTOU/`-d` fail-closed reasoning are sound.
- Scope boundary vs. #1647 (PM landing handshake) and #1643 (persona wording) is cleanly drawn and justified.

**Why NEEDS REVISION rather than READY (with concerns):** The plan's central promise is "cleanup can **never** delete a branch holding unmerged commits" (Problem / Desired outcome). A scheduler-driven force-delete site that does exactly that remains untouched and unmentioned, while the plan asserts it enumerated the force-delete sites. That is a correctness gap in the stated invariant, not a stylistic concern — closing it is a small edit (one more call routed through the helper the plan already introduces). Fixing the two findings is low-cost and makes the data-loss class genuinely closed.

---

## Open Questions

The issue's three open questions are resolved as follows; no human input is blocking:

1. **Intended landing path for granite dev work?** — Resolved by Tom's 2026-06-12 decision
   (memory `project-granite-landing-policy`): PM decides per task; PM sign-off gates cleanup.
   This plan implements only the safety floor; the landing handshake is #1647 / #1643.
2. **Should `cleanup_after_merge` hard-fail/skip when the branch is not an ancestor of main?** —
   Yes. It **skips** branch deletion (preserving the work) and reports `skipped_unmerged`, rather
   than hard-failing the session (cleanup must never fail a session). Decided in this plan.
3. **Should `git branch -D` become `-d` everywhere with failures surfaced?** — Yes, routed through
   `safe_delete_branch` (`is-ancestor` + `-d`), with results inspected instead of swallowed.
   Decided in this plan.

One item for supervisor confirmation (not blocking):
- **Scope boundary:** This plan deliberately stops at the safety floor and defers the
  PM-authorized landing handshake to #1647 and persona-prime wording to #1643. Confirm that
  boundary is correct, or whether any part of the landing handshake should move into this slug.
