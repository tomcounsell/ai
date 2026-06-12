---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-12
tracking: https://github.com/tomcounsell/ai/issues/1646
last_comment_id:
revision_applied: false
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
- `agent/session_revival.py:230-238` `cleanup_stale_branches` — **fourth force-delete site**
  (surfaced by cycle-1 critique). Force-deletes any `session/*` branch by **age alone**
  (`age_hours > max_age_hours`, default 72h) via `git branch -D` with **no merge check**.
  Invoked autonomously by `cleanup_stale_branches_all_projects` (`session_revival.py:246`),
  the scheduler-driven `stale-branch-cleanup` reflection that runs across **all projects**.
  This is the same root-cause pattern (deletion as an unconditional side effect) and is a
  *higher-risk* vector than the completion path because it fires on a schedule, not just on
  completion. Routed through `safe_delete_branch` by this plan (see Solution / Site D).

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
5. **`session_revival.cleanup_stale_branches` (230-238):** ← **DESTRUCTION SITE D.** Off the
   completion path entirely — fires from the scheduler-driven `stale-branch-cleanup` reflection
   (`cleanup_stale_branches_all_projects`, 246) across *all* projects. Force-deletes any
   `session/*` branch whose tip commit is older than `max_age_hours` (default 72h) with `git
   branch -D` and **no merge check**. An age threshold is not a merged-ness proof, so this can
   silently destroy unmerged work the same way the incident did — autonomously, on a timer.
   *Guard: route the deletion through `safe_delete_branch`; the age filter only selects
   *candidates*, the merged check decides deletion.*
6. **Output:** session reported success; commit dangling. *Desired output: work landed (merged
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
  - `cleanup_after_merge(repo_root, slug)` gains an internal precondition check and a single new
    result key `skipped_unmerged: bool` (carrying the preserved branch name in the same dict).
    Signature unchanged; return dict grows additively (back-compatible per existing callers in
    `post_merge_cleanup.py`). **Exactly two representations of "branch preserved," no more:**
    (1) the structured result key `skipped_unmerged` (for callers/tooling), and (2) the greppable
    `[unmerged-branch-guard]` log line naming the branch (for humans). There is no separate
    `unmerged_branch` errors entry and no distinct "recovery marker" artifact — the log line *is*
    the recovery marker; it does not persist state beyond the log.
  - A new small helper `safe_delete_branch(repo_root, branch_name, *, base="main", predicate, force=False)`
    centralizes the refuse-then-delete / log / structured-result logic so all four destruction sites
    share one implementation, each supplying the merged-ness oracle correct for its context
    (`merged_via_ancestor` for the no-prior-merge sites, `merged_via_cherry` for the post-squash-merge sites).
- **Coupling:** *decreases* — four ad-hoc `git branch -D` call sites (executor auto-mark,
  `cleanup_after_merge`, `remove_worktree`, and the stale-branch reflection) collapse to one
  guarded helper. The only surviving `git branch -D` lives inside that helper, gated behind a
  proven-landed check.
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

The central correction over the prior revision: **a single `is-ancestor` oracle is correct for
two of the four sites and wrong for the other two.** Sites A (executor) and D (stale-branch
reflection) run where no PR merge has occurred and the landing model is a true merge to `main`,
so `git merge-base --is-ancestor` is the right merged-ness proof. Sites B/C run *inside*
`cleanup_after_merge`, which by definition executes after `gh pr merge --squash --delete-branch`
(this repo squash-merges — `do-merge/SKILL.md:100`). A squash-merge writes a brand-new commit to
`main` whose ancestry does **not** include the branch tip, so `is-ancestor` exits 1 for a
legitimately landed branch and `git branch -d` itself refuses it. The guard therefore needs a
**squash-aware merged oracle** at Sites B/C. (Reproduced: branch+commit → `git checkout main &&
git merge --squash branch && git commit` → `is-ancestor` exits 1 AND `git branch -d` refuses;
`git cherry main branch` prints every commit `-`-prefixed = already upstream.)

- **`safe_delete_branch` helper** (`agent/worktree_manager.py`): the single deletion primitive,
  **parameterized by a pluggable merged predicate** so each call context supplies the correct
  oracle. It resolves the base branch, runs the supplied predicate, deletes only when the
  predicate reports "landed," logs `[unmerged-branch-guard]` and returns a structured `skipped_unmerged`
  result otherwise, and surfaces git failures instead of swallowing them. Two predicates ship:
  - `merged_via_ancestor(branch, base)` → `git merge-base --is-ancestor branch base` (exit 0 = landed).
    Used by **Site A (executor)** and **Site D (revival)**, where a true merge to `main` is the model.
    Deletion uses `git branch -d` (which independently fails-closed on unmerged/checked-out branches).
  - `merged_via_cherry(branch, base)` → `git cherry base branch`; landed iff every output line is
    `-`-prefixed (or output is empty) — i.e. every branch commit is patch-id-equivalent to a commit
    already on `base`. This is squash-safe. Used by **Sites B/C (`cleanup_after_merge`,
    `remove_worktree`)**. Because `git branch -d` *also* refuses a squash-merged branch, deletion in
    this context uses a **guarded `git branch -D`** — reached only after `merged_via_cherry` proves
    the branch is fully upstream. This is the *only* `-D` in `agent/`, and it is gated, not raw.
  - NOTE: `git log base..branch` emptiness is the WRONG oracle — it stays non-empty after a squash-merge.
- **`cleanup_after_merge` precondition** (`agent/worktree_manager.py:1150`): before deleting
  the branch, verify the merged precondition via `safe_delete_branch(..., predicate=merged_via_cherry)`.
  If the branch is not fully upstream, skip branch deletion (preserving the work), set
  `skipped_unmerged: true`, and record the branch name so callers/operators can find the work.
- **Executor stops force-deleting** (`agent/session_executor.py`): replace the
  `git branch -D` in the auto-mark block (Site A) with `safe_delete_branch(..., predicate=merged_via_ancestor)`.
  The synthetic-slug `finally` path inherits the squash-aware guard via `cleanup_after_merge` (Site B).
- **Stale-branch reflection stops force-deleting** (`agent/session_revival.py:230-238`, Site D):
  replace its `git branch -D` with `safe_delete_branch(..., predicate=merged_via_ancestor)`. The age
  threshold continues to select *candidate* branches; the helper then deletes only the landed ones and
  preserves unmerged ones with the `[unmerged-branch-guard]` warning. This closes the scheduler-driven
  vector — the autonomous reflection can no longer destroy unmerged work on a timer.
- **Preserved-state observability:** when a branch is preserved because it is unmerged, the path logs
  one greppable `[unmerged-branch-guard]` warning naming the branch (and worktree path, if present).
  This is the single recovery signal — there is no separate "recovery marker" artifact; the log line
  *is* the marker. To bound accumulation before #1647 lands, `safe_delete_branch` also increments a
  preserved-branch counter exposed via a one-line periodic `[unmerged-branch-guard] preserved=N`
  summary in the worker log (greppable), so monotonic growth is visible before it bites. The existing
  stale-worktree GC (`scripts/worktree-gc.sh`) reaps a preserved worktree once its branch later merges
  via PR (it skips only open/recently-merged-PR branches), so preserved worktrees are not orphaned
  forever — they are reaped by the normal GC after the eventual landing.
- **No new automatic landing on this PR.** Per Tom's policy, *who* lands the work (auto-merge vs
  push+PR) and *when* cleanup is authorized is the PM's per-task decision and belongs to the
  PM-routing work (#1647) and persona primes (#1643). This plan guarantees the safety floor:
  **nothing destroys unmerged commits**, so a missing landing path degrades to "branch and
  worktree are preserved," not "work is lost."

### Flow

**Completion path (no PR merge — Site A):** Dev session completes → executor auto-mark runs →
`safe_delete_branch(repo, "session/dev-{id}", predicate=merged_via_ancestor)` → **branch is ancestor
of main?**
- **Yes (merged):** `git branch -d` succeeds → branch removed cleanly.
- **No (unmerged):** deletion skipped → `[unmerged-branch-guard]` warning logged with branch name
  → branch and (if synthetic) worktree preserved → work recoverable.

**Post-PR-merge path (squash-merge already happened — Sites B/C, via `cleanup_after_merge`):**
PR squash-merged → `cleanup_after_merge` → `safe_delete_branch(..., predicate=merged_via_cherry)` →
**all branch commits patch-equivalent already on main (`git cherry` all `-`)?**
- **Yes (landed):** guarded `git branch -D` succeeds → branch + worktree removed cleanly. (Plain `-d`
  would *wrongly refuse* here — squash creates a new commit, so the branch is "not fully merged" to
  `-d`'s ancestry test. The cherry check is what proves it is safe.)
- **No (work not upstream):** deletion skipped → `[unmerged-branch-guard]` warning → branch preserved.

### Technical Approach

- Centralize: extract
  `safe_delete_branch(repo_root, branch_name, *, base="main", predicate, force=False) -> dict`
  and route Sites A, B, C, and D through it. The `predicate` argument is the merged-ness oracle
  (one of the two shipped functions); `force` selects `git branch -D` (only set True at Sites B/C,
  reached only after `predicate` passes). No **unguarded** `git branch -D` remains anywhere in
  `agent/` — the single `-D` lives inside `safe_delete_branch`, behind the proven-landed check.
- **Two oracles for two contexts:**
  - `merged_via_ancestor` (Sites A/D, no prior PR merge): `git merge-base --is-ancestor <branch> <base>`
    exits 0 iff `<branch>`'s tip is reachable from `<base>` — fully merged. Exit 1 = unmerged → refuse.
    Deletion via `git branch -d` (fails-closed independently).
  - `merged_via_cherry` (Sites B/C, post-squash-merge): `git cherry <base> <branch>` prints one line
    per branch commit; `-` prefix = a patch-id-equivalent commit already exists on `<base>`, `+` =
    not upstream. Landed iff **no `+` lines** (all `-`, or empty). This is squash-safe — it is true
    immediately after `git merge --squash` + commit. Deletion via guarded `git branch -D` (because
    `-d` wrongly refuses a squash-merged branch — verified).
  - For both: treat any other git exit (missing branch, 128) as "do not delete, surface the error."
- Determine `base` robustly: default `"main"`; if `main` is not present locally fall back to the
  repo's default branch (`git symbolic-ref refs/remotes/origin/HEAD`), and if even that is
  unavailable, refuse deletion (fail safe — never delete when we cannot prove merged).
- Keep `cleanup_after_merge`'s worktree removal (the directory) intact — removing the *worktree
  directory* does not destroy commits (they live in `.git`); only *branch deletion* destroys
  reachability. But when the branch is unmerged we keep the worktree too, so the work is easy to
  find and resume.
- Stop swallowing failures: the executor's current `capture_output=True` with no return check is
  replaced by inspecting `safe_delete_branch`'s result and logging on the skip/error branches.
- **Site C is free safety, not a risk:** `grep -rn 'delete_branch=True' agent/ tools/ worker/ bridge/`
  returns **zero callers** — the only `remove_worktree` invocation (`worktree_manager.py:1197`) passes
  `delete_branch=False`. So routing the `delete_branch=True` path through the guard changes no live
  caller's behavior; it only hardens a primitive that is currently reachable but unused.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The synthetic-slug `finally` block (`session_executor.py:2085`) keeps its
  `except Exception … non-fatal` swallow (cleanup must never fail a session) — but add a test
  asserting that when `cleanup_after_merge` *skips* an unmerged branch, the warning is logged and
  the branch survives (observable behavior, not a silent pass).
- [ ] The auto-mark block's `except Exception` (`session_executor.py:~2028`) — add a test that an
  unmerged branch is preserved and a `[unmerged-branch-guard]` warning is emitted, not swallowed.
- [ ] The stale-branch reflection (`session_revival.py:cleanup_stale_branches`) keeps its
  `except Exception` swallow (a reflection must never crash the scheduler) — but add a test
  asserting that a stale-but-unmerged branch is preserved (the warning is logged, the branch
  survives), so the autonomous path's safety is observable, not silent.

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
  set these up with a **real squash-merge** (`git checkout main && git merge --squash branch &&
  git commit`) against a temp git repo, and assert the branch is **still deleted**. Do NOT use an
  `is-ancestor`-returns-0 setup — that encodes the wrong production model (the repo squash-merges)
  and would pass against an is-ancestor-only design while masking the squash-merge BLOCKER. Written
  honestly with a squash-merge, this test is what proves `merged_via_cherry` (not `is-ancestor`) is
  the correct oracle at Sites B/C.
- [ ] `tests/unit/test_post_merge_cleanup.py::{test_clean_exits_0,test_success_exits_0,
  test_blocked_session_exits_2,test_generic_error_exits_1}` — UPDATE: extend the fake
  `cleanup_after_merge` result dicts with the new `skipped_unmerged` key (additive) and add a new
  case asserting the script's behavior when a branch is skipped as unmerged.
- [ ] NEW `tests/unit/test_worktree_manager.py::test_safe_delete_branch_*` — REPLACE/ADD: a focused
  set against a real temp git repo covering **both oracles**:
  `merged_via_ancestor` — true-merged (deletes via `-d`), unmerged (refuses + preserves);
  `merged_via_cherry` — **squash-merged** (`git merge --squash` + commit; deletes via guarded `-D`),
  truly-unmerged (refuses + preserves); plus shared missing-branch and unresolvable-base cases.
  Critically include a test asserting `merged_via_ancestor` *refuses* a squash-merged branch (proving
  why Sites B/C cannot use it) and `merged_via_cherry` *accepts* it.
- [ ] NEW `tests/unit/test_session_executor_cleanup.py` — ADD: regression test reproducing the
  incident — a dev session branch with an unmerged commit goes through the auto-mark cleanup and
  the commit remains reachable afterward (the canonical "this bug never recurs" test).
- [ ] NEW `tests/unit/test_session_revival_cleanup.py` — ADD: no existing test exercises
  `cleanup_stale_branches`' deletion behavior (the only repo reference, `test_worker_entry.py:193`,
  is an import allowlist and is unaffected). Add a focused case against a real temp git repo: a
  stale-but-**unmerged** `session/*` branch is preserved (not force-deleted) while a
  stale-and-**merged** branch is still cleaned — proving the scheduler-driven vector is closed.

## Rabbit Holes

- **Implementing the full PM-authorized landing handshake here.** The "PM decides per task /
  PM sign-off gates cleanup" ordering lives in PM routing (#1647) and persona primes (#1643).
  This plan delivers the safety floor only. Building the landing orchestration here would balloon
  scope and collide with those slugs.
- **Auto-merging dev work to main inside the executor.** Tempting ("just merge it"), but merge
  policy is a PM decision and merging from the executor would bypass review for substantive
  changes. Out of scope.
- **Rewriting worktree lifecycle / garbage collection.** `prune_worktrees`, stale-session
  cleanup, and worktree GC are adjacent but separate. Touch only the branch-deletion guard —
  including in the stale-branch reflection (`cleanup_stale_branches`), where the *only* change is
  swapping the `git branch -D` for `safe_delete_branch`. Do not rework the reflection's age
  selection, scheduling, or all-projects iteration.
- **Auto-pushing every session branch to origin as a backup.** Network/credential surface, remote
  clutter, and policy questions. The `is-ancestor` guard + preserved local branch already prevents
  data loss; remote backup is a possible later enhancement, not this fix.

## Risks

### Risk 1: Worktrees accumulate when work is unmerged (until #1647 lands)
**Impact:** Per the incident, *all* current granite dev sessions complete unmerged (Dev never opens a
PR, PM has no merge step). The safety floor preserves a branch + worktree for every one until #1647
lands the PM-authorized landing step — on the 24/7 worker this is monotonic `.worktrees/dev-*` +
`session/dev-*` growth in the interim. This trades silent data loss for visible accumulation.
**Mitigation (in this slug):**
- **Observability hook:** `safe_delete_branch` increments a preserved-branch counter on every skip and
  the worker emits a greppable `[unmerged-branch-guard] preserved=N` summary line, so growth is visible
  in `logs/worker.log` before it bites. This is the bounded-accumulation mitigation the critique asked
  for, scoped to a log line rather than a dashboard surface (keeping the slug narrow).
- **Existing reaper confirmed:** `scripts/worktree-gc.sh` reaps a preserved worktree once its branch
  later merges via PR — it skips only branches that are on an open PR or merged within the protect
  window (`gh pr list --state merged`), and force-deletes the rest (line 208, in `scripts/`, outside
  this slug's `agent/` boundary, gated by its own merged-PR check). So a preserved worktree is *not*
  orphaned forever: after the eventual PR landing it is collected by the normal GC.
- Preserving the *worktree directory* is itself cheap and reversible — it holds no unique commit data
  once the branch is safe (commits live in `.git`).

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

- [ ] No **unguarded** `git branch -D` remains in any cleanup path — completion
  (`session_executor.py` auto-mark block, `worktree_manager.py` `cleanup_after_merge` /
  `remove_worktree`) **and** scheduler-driven (`session_revival.py` `cleanup_stale_branches`). All
  four sites route through `safe_delete_branch`. The **only** permitted `git branch -D` in `agent/`
  is the single occurrence *inside* `safe_delete_branch`, reached only after the merged-ness oracle
  passes (it exists because `git branch -d` wrongly refuses squash-merged branches).
- [ ] A dev session that commits unmerged work and completes leaves its branch (and synthetic
  worktree) intact, with a `[unmerged-branch-guard]` warning — verified by the regression test
  reproducing incident `ec1e7c6e`.
- [ ] A dev session whose branch **was squash-merged** into main still has its branch cleaned up
  (no regression in the SDLC happy path) — verified by a real-squash-merge test, NOT an
  is-ancestor-returns-0 stub.
- [ ] `cleanup_after_merge` returns `skipped_unmerged: true` + the branch name when it declines to
  delete an unmerged branch.
- [ ] `safe_delete_branch` emits a greppable `[unmerged-branch-guard] preserved=N` summary so
  unmerged-branch accumulation is observable in `logs/worker.log` before #1647 lands.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No **unguarded** `git branch -D` in `agent/`: every `git branch -D` call site outside the
  body of `safe_delete_branch` is eliminated. Verify the four original sites are gone and the sole
  remaining `-D` is inside `safe_delete_branch`:
  `grep -rn 'branch.*-D' agent/` returns **exactly one hit**, in `safe_delete_branch`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER
builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (cleanup-guard)**
  - Name: `cleanup-guard-builder`
  - Role: Implement `safe_delete_branch` with the two merged predicates (`merged_via_ancestor`,
    `merged_via_cherry`), route all cleanup-path deletions through it, wire the squash-aware
    `merged_via_cherry` precondition into `cleanup_after_merge`, replace the executor `-D`, and route
    the stale-branch reflection (`session_revival.py`) through the same helper with `merged_via_ancestor`.
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
  - Role: Verify no **unguarded** `git branch -D` remains (exactly one hit, inside `safe_delete_branch`),
    the unmerged-work regression test fails on the pre-fix code and passes after, the squash-merge
    happy-path test confirms a landed branch is still deleted, and all affected suites are green.
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
- Add `safe_delete_branch(repo_root, branch_name, *, base="main", predicate, force=False) -> dict`
  plus the two predicate functions `merged_via_ancestor` and `merged_via_cherry` to
  `agent/worktree_manager.py`. The helper resolves base (main → origin default → refuse), runs the
  supplied `predicate`, deletes when it reports landed (`git branch -d` when `force=False`,
  guarded `git branch -D` when `force=True` — the only `-D` in `agent/`), returns a structured
  result (`deleted` / `skipped_unmerged` / `not_found` / `error`), logs `[unmerged-branch-guard]`
  on skip, and increments a preserved-branch counter feeding a greppable
  `[unmerged-branch-guard] preserved=N` worker summary line.
- `merged_via_ancestor`: `git merge-base --is-ancestor branch base` (exit 0 = landed). Used at the
  no-prior-merge sites (A, D), paired with `force=False` (`-d`).
- `merged_via_cherry`: `git cherry base branch`, landed iff no `+`-prefixed line. Squash-safe. Used
  inside `cleanup_after_merge` (Sites B/C), paired with `force=True` (guarded `-D`, because `-d`
  wrongly refuses a squash-merged branch).
- Route `cleanup_after_merge`'s branch deletion (worktree_manager.py:1226) and
  `remove_worktree(delete_branch=True)` (882) through `safe_delete_branch(..., predicate=merged_via_cherry,
  force=True)`; add the single `skipped_unmerged` key (carrying branch name) to the
  `cleanup_after_merge` result dict.
- Replace `git branch -D {branch}` in `agent/session_executor.py` auto-mark block with
  `safe_delete_branch(..., predicate=merged_via_ancestor)`; inspect the result and log instead of swallowing.
- Replace `git branch -D {branch}` in `agent/session_revival.py:231` (`cleanup_stale_branches`,
  Site D) with `safe_delete_branch(..., predicate=merged_via_ancestor)`; the age threshold still selects
  candidates, the helper decides deletion. Only branches the helper reports `deleted` go into the
  `cleaned` list; preserved (unmerged) branches are logged via the helper, not appended as "cleaned."

### 2. Tests
- **Task ID**: build-tests
- **Depends On**: build-guard
- **Validates**: tests/unit/test_worktree_manager.py, tests/unit/test_post_merge_cleanup.py, tests/unit/test_session_executor_cleanup.py (create), tests/unit/test_session_revival_cleanup.py (create)
- **Assigned To**: cleanup-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `test_safe_delete_branch_*` against a real temp git repo covering both oracles:
  `merged_via_ancestor` true-merged→deletes / unmerged→preserves+`skipped_unmerged`;
  `merged_via_cherry` **squash-merged→deletes** (guarded `-D`) / truly-unmerged→preserves; plus
  missing branch, unresolvable base. Include the assertion that `merged_via_ancestor` *refuses* a
  squash-merged branch and `merged_via_cherry` *accepts* it (locks in the cycle-2 BLOCKER fix).
- The `cleanup_after_merge` happy-path test must use a **real squash-merge** (`git merge --squash`
  + commit on main), NOT an is-ancestor-returns-0 stub, and assert the branch is still deleted.
- Add `tests/unit/test_session_executor_cleanup.py` reproducing incident `ec1e7c6e`: unmerged
  commit survives the auto-mark cleanup.
- Add `tests/unit/test_session_revival_cleanup.py`: a stale-but-unmerged `session/*` branch is
  preserved by `cleanup_stale_branches` while a stale-and-merged branch is still cleaned —
  closing the scheduler-driven vector.
- Update `test_branch_deletion_fails` and `cleanup_after_merge` happy-path tests for the new
  precondition; extend `test_post_merge_cleanup.py` fakes with `skipped_unmerged`.

### 3. Validation
- **Task ID**: validate-guard
- **Depends On**: build-guard, build-tests
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm no **unguarded** `git branch -D` in `agent/`: `grep -rn 'branch.*-D' agent/` returns
  exactly one hit, inside `safe_delete_branch` (all four original sites — executor, cleanup_after_merge,
  remove_worktree, session_revival — routed through the helper).
- Confirm both predicates exist and a squash-merged branch is still deleted (squash-merge regression
  test), proving `merged_via_cherry` is wired at Sites B/C.
- Confirm the regression test reproduces the bug on pre-fix code (git stash the fix, run, expect
  fail) and passes with the fix.
- Run `pytest tests/unit/test_worktree_manager.py tests/unit/test_post_merge_cleanup.py
  tests/unit/test_session_executor_cleanup.py tests/unit/test_session_revival_cleanup.py -q`.

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
| Affected unit tests pass | `pytest tests/unit/test_worktree_manager.py tests/unit/test_post_merge_cleanup.py tests/unit/test_session_executor_cleanup.py tests/unit/test_session_revival_cleanup.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/` | exit code 0 |
| No unguarded force-delete in agent/ | `grep -rn 'branch.*-D' agent/` | exactly one hit, inside `safe_delete_branch` (the guarded squash-merge delete) — all four original sites gone |
| Guard helper exists | `grep -n "def safe_delete_branch" agent/worktree_manager.py` | output contains safe_delete_branch |
| Both oracles exist | `grep -n "def merged_via_ancestor\|def merged_via_cherry" agent/worktree_manager.py` | both predicate functions present |
| Worker restarted onto new code (post-deploy) | `./scripts/valor-service.sh worker-status` | PID start time is after the merge commit — confirms the always-on worker is no longer running the vulnerable old code in memory (CLAUDE.md "ALWAYS RESTART RUNNING SERVICES") |

## Critique Results

**Verdict: NEEDS REVISION** (cycle 3) — the cycle-2 BLOCKER is **not actually resolved**. `merged_via_cherry` is broken for multi-commit squash-merges (every real SDLC branch), and the cited accumulation reaper itself force-deletes unmerged work. See the cycle-3 block immediately below. The historical cycle-2 record follows it.

### Cycle 3 findings (NEEDS REVISION)

The cycle-3 revision swapped the single is-ancestor oracle for a two-oracle design (`merged_via_ancestor` for Sites A/D; `merged_via_cherry` for Sites B/C). The single-commit reproduction the revision relied on (line ~229) passes — but that is the *only* branch shape where the new oracle is correct, and it is unrepresentative of production. Three findings, two BLOCKER:

| Severity | Critic | Finding | Evidence (reproduced live this cycle) |
|----------|--------|---------|----------------------------------------|
| BLOCKER | Adversary | **`merged_via_cherry` false-negatives on every MULTI-commit squash-merge — i.e. essentially all real SDLC branches. The cycle-2 BLOCKER is not fixed, only patched against an unrepresentative single-commit fixture.** A `gh pr merge --squash` collapses N commits into one new commit whose patch-id matches none of the originals, so `git cherry base branch` prints **all N** with `+` → "landed iff no `+` lines" → verdict UNMERGED → refuse delete. Sites B/C (`cleanup_after_merge`, `remove_worktree`) would therefore preserve **every multi-commit squash-merged branch forever** — re-introducing the exact `.worktrees/dev-*` + `session/dev-*` leak #1272's hook existed to prevent, on the main pipeline. The plan's own mandated happy-path test (lines 354-360, 599) would PASS while masking this if the builder uses a single-commit fixture (the natural minimal setup, and the exact shape the plan itself reproduces with). | 3-commit branch, fully squash-merged to main (`git diff --quiet main branch` exits 0 = trees identical, work 100% landed): `git cherry main branch` printed **all 3 commits `+`-prefixed** → `merged_via_cherry` verdict UNMERGED (REFUSE DELETE). The single-commit case coincidentally yields all `-` only because squashing one commit preserves its patch-id. **Fix direction:** use tree-equality `git diff --quiet <base> <branch>` (exit 0 = landed) as the squash-safe oracle at Sites B/C — verified correct for the 3-commit squash and correctly divergent when the branch carries genuine unmerged work — AND mandate every squash-merge happy-path test fixture use a **≥2-commit** branch with an explicit assertion that `git cherry` returns `+` lines on it (so the cherry-insufficiency is a locked-in tested invariant, not a re-maskable assumption). |
| BLOCKER | Operator | **The cited accumulation reaper, `scripts/worktree-gc.sh`, itself force-deletes unmerged no-PR branches with no merged check — the plan's accumulation mitigation is a second data-loss vector, not a safe reaper.** The plan cites worktree-gc three times (lines 264-266, 411-415, 665) as the thing that safely reaps preserved worktrees "once its branch later merges via PR." But granite dev branches **never** open a PR (the incident), so they never merge; and worktree-gc's candidate selection skips only main/locked/keep-token/open-PR/recently-merged branches — an unmerged no-PR `session/dev-*` branch falls straight through to `git branch -D "$branch"` with NO merged guard. The plan manufactures the disk pressure (Risk 1: "monotonic `.worktrees/dev-*` growth") that prompts an operator to run the very tool that destroys the unmerged work. This is Site D resurrected in `scripts/`. | `scripts/worktree-gc.sh:207` runs `git branch -D "$branch"` on every prune candidate; selection logic at lines 145-160 has no merged-ness check for no-PR branches. **Mitigating factor (why blast radius is bounded, not why it's downgraded):** worktree-gc is dry-run by default, requires `--apply`, and is NOT scheduled by any reflection/cron — it fires only on manual operator invocation. **Fix direction (no scope expansion needed):** drop the false "worktree-gc reaps them safely" claim; state plainly that preserved worktrees accumulate until #1647 lands, with `preserved=N` as the only interim signal and manual operator action as the only safe reaping path. (Optionally, a separately-tracked task to guard worktree-gc line 207 — but that is a `scripts/` change outside this slug's `agent/` boundary and must be explicit, not hand-waved.) |
| CONCERN | Adversary | **Site D (revival) is mis-assigned `merged_via_ancestor`; it will stop reaping the most common class of stale ref — landed-but-undeleted squash-merged `session/*` branches — causing autonomous local-ref accumulation.** The plan assigns `merged_via_ancestor` to Site D (lines 237, 581) on the rationale that revival runs "where no PR merge has occurred." That rationale is wrong: the stale-branch reflection fires across all projects against ANY `session/*` branch >72h old, including branches that **were** squash-merged via a real PR and never got their local ref deleted. For those, `is-ancestor` returns 1 (reads UNMERGED) → Site D now preserves a safely-landed branch forever. Not data loss (work is on main), so CONCERN not BLOCKER — but it makes the stale-ref reaper stop reaping stale refs, on a timer. | `session/dev-old` squash-merged via PR (ref undeleted): `git merge-base --is-ancestor session/dev-old main` exit 1 → reads UNMERGED → preserved forever. **Fix direction:** Site D needs the squash-aware (tree-equality) oracle too, NOT `is-ancestor`. The two-oracle split mis-models Site D, which absolutely sees prior PR merges. |

**Adversary attacks that did NOT land (recorded so cycle 4 doesn't re-litigate them):**
- `git cherry` false-positive with live unmerged work (a branch where cherry shows all `-` yet real work is lost): does NOT exist. To lose work every divergent commit would have to be patch-id-equivalent to an upstream commit, at which point the content genuinely is upstream. The oracle's only failure mode is false-*negatives* (the BLOCKER) — safe for data, causes the leak.
- Empty-diff / identical-tree commit: `git cherry` prints it `+` → conservative preserve. Safe.
- Base-resolution wrong-branch: could not construct a state where a real unmerged branch reads as merged; a wrong base shows MORE divergence (errs toward preservation). The fail-safe ladder (main → origin/HEAD → refuse) is sound.

**Why NEEDS REVISION (cycle 3):** The fix's central correctness claim — that `merged_via_cherry` makes Sites B/C squash-safe — is false for multi-commit branches, which are the production norm. The cycle-2 BLOCKER (refuse-to-delete-merged-branches → unbounded leak) therefore survives the revision essentially intact, just relocated from `is-ancestor` to a `git cherry` oracle that fails the same way on real (multi-commit) inputs. Compounding it, the accumulation mitigation that was supposed to bound the leak points at a tool that force-deletes the preserved work. The remedy is well-scoped (tree-equality oracle at Sites B/C **and** D; ≥2-commit test fixtures; correct the worktree-gc claim) but must be made before build. **Two findings reproduced with live git this cycle; the third is a code read of `worktree-gc.sh:207`.**

---

### Cycle 2 (historical record — verdict was NEEDS REVISION, revision applied in cycle 3 but incompletely, see above)

**Verdict: NEEDS REVISION** (cycle 2) — **findings were ADDRESSED in revision cycle 3 (see Status column + notes below), but the cycle-3 fix is itself defective — see the Cycle 3 block above.**

**Cycle-1 findings — verified resolved:** Both cycle-1 findings were genuinely addressed by the revision. Site D (`session_revival.py:231`) is now enumerated everywhere and routed through `safe_delete_branch`, and both acceptance gates were reconciled to a single repo-wide grep over `agent/`. These two are CLOSED. However, cycle 2 surfaces a deeper correctness defect in the very mechanism the cycle-1 fix leaned on.

| Severity | Critic | Finding | Status | Implementation Note |
|----------|--------|---------|--------|---------------------|
| BLOCKER | Adversary + Archaeologist + Skeptic + Operator (4 independent critics) | **`is-ancestor` oracle is wrong for `cleanup_after_merge` — it would refuse to delete every squash-merged branch.** `cleanup_after_merge` (Sites B/C) runs after `gh pr merge --squash --delete-branch` (its own docstring, confirmed; the repo merges via `--squash` per `do-merge/SKILL.md:100`). A squash-merge creates a brand-new commit on `main` whose ancestry does NOT include the branch tip, so `git merge-base --is-ancestor session/{slug} main` exits **1 (unmerged)** for a legitimately landed branch. **Reproduced:** branch+commit → `git checkout main && git merge --squash branch && git commit` → `is-ancestor` exits 1, AND `git branch -d` itself refuses ("not fully merged"). Routing Site B/C through an is-ancestor-`-d`-only helper means every cleanly squash-merged SDLC branch is classified "unmerged," refused deletion, and preserved forever — local branches and `.worktrees/` accumulate without bound on the main pipeline. This directly violates the plan's own Success Criterion "A dev session whose branch *is* merged into main still has its branch cleaned up (no regression in the happy path)" (line 424). | **ADDRESSED (cycle 3)** | `safe_delete_branch` is now **parameterized by a pluggable merged predicate** with two oracles for two contexts: **`merged_via_ancestor`** (`git merge-base --is-ancestor`) for executor Site A + revival Site D (true-merge model, deletes via `-d`); **`merged_via_cherry`** (`git cherry base branch`, landed iff no `+` lines) for `cleanup_after_merge` Sites B/C (squash-safe, deletes via guarded `-D`). Reproduction re-run during this revision confirmed: squash-merged branch → `is-ancestor` exit 1, `branch -d` refuses, `git cherry` shows `-` prefix; truly-unmerged branch → `git cherry` shows `+`. The `git log base..branch`-emptiness WRONG-oracle warning is recorded in Technical Approach. See Solution / Key Elements (two-oracle design), Flow (both paths), Technical Approach (predicate signature). |
| CONCERN | Skeptic | **The "zero `git branch -D` in `agent/`" acceptance gate is self-contradictory with the squash-merge reality.** Because `git branch -d` uses the same is-ancestor logic and *also* refuses squash-merged branches, a correct `safe_delete_branch` for Sites B/C almost certainly needs a **guarded `-D` fallback** after the cherry-check proves merged-ness. The current hard gate (Success Criteria lines 417/420/430, Verification line 549: `grep -rn 'branch.*"-D"' agent/` expects exit 1 / no hits) would then fail the build on correct code. | **ADDRESSED (cycle 3)** | Gate rescoped from "no `-D` anywhere" to "no **unguarded** `-D`": the sole permitted `git branch -D` lives inside `safe_delete_branch`, reached only after the merged oracle passes. Success Criteria and the Verification table now assert `grep -rn 'branch.*-D' agent/` returns **exactly one hit, inside `safe_delete_branch`** (not zero). Squash-merge `-d`-refusal verified during this revision. |
| CONCERN | Operator | **Unbounded branch/worktree accumulation between ship and #1647.** Per the incident, *all* current granite dev sessions complete unmerged (Dev never opens a PR, PM has no merge step). The safety floor preserves a branch + worktree for every one of them, with no TTL, cap, or operator alert, and reaping is explicitly out of scope (Rabbit Holes). On the 24/7 worker this trades silent data loss for monotonic `.worktrees/dev-*` + `session/dev-*` growth until #1647 lands. | **ADDRESSED (cycle 3)** | Two-part mitigation added in THIS slug (Risk 1, Key Elements, Success Criteria): (1) **observability hook** — `safe_delete_branch` increments a preserved-branch counter and the worker emits a greppable `[unmerged-branch-guard] preserved=N` summary in `logs/worker.log`, so growth is visible before it bites (scoped to a log line, not a dashboard surface, to keep the slug narrow); (2) **interim reaper confirmed** — `scripts/worktree-gc.sh` reaps a preserved worktree once its branch later merges via PR (skips only open/recently-merged-PR branches), so preserved worktrees are not orphaned forever. |
| NIT | Operator | No post-merge operational verification that the long-lived worker restarted onto the new code. For a silent-data-loss fix on an always-on process, shipping without confirming the worker restarted leaves the vulnerable old code running in memory. | **ADDRESSED (cycle 3)** | Verification table gains a "Worker restarted onto new code (post-deploy)" row asserting `./scripts/valor-service.sh worker-status` shows a PID started after the merge commit (CLAUDE.md "ALWAYS RESTART RUNNING SERVICES"). |
| NIT | Simplifier | Four representations of one fact (`skipped_unmerged` key, `[unmerged-branch-guard]` log, `unmerged_branch` in errors, "recovery marker"). | **ADDRESSED (cycle 3)** | Consolidated to exactly two: the structured result key `skipped_unmerged` (carrying the branch name) + the greppable `[unmerged-branch-guard]` log line. The `unmerged_branch`-in-errors entry and the distinct "recovery marker" artifact are dropped — the log line *is* the marker and persists no state beyond the log (stated in Architectural Impact / interface changes). |
| NIT | Simplifier | Site C (`remove_worktree(delete_branch=True)`) is off the incident path; routing it through the guard is a behavior change to a path the incident never touched. | **ADDRESSED (cycle 3)** | Confirmed during this revision: `grep -rn 'delete_branch=True' agent/ tools/ worker/ bridge/` returns **zero callers** (the only `remove_worktree` call, `worktree_manager.py:1197`, passes `delete_branch=False`). Routing Site C through the guard changes no live caller's behavior — free safety. Stated in Technical Approach. |

**Structural checks that PASSED:**
- All four required sections present (Documentation, Update System, Agent Integration, Test Impact).
- Task dependency graph valid — no gaps, no cycles; all `Depends On` references resolve.
- All cited `file:line` references hold against HEAD (executor:2018, worktree_manager:882 & :1226 & :1150 docstring, branch_manager:387/445, session_revival:230-238).
- All referenced existing tests exist (`test_branch_deletion_fails`, the four `test_post_merge_cleanup` cases); `post_merge_cleanup.py` import confirmed.
- Cycle-1 findings (Site D enumeration; reconciled grep gates) genuinely resolved.
- Scope boundary vs. #1647 (PM landing handshake) and #1643 (persona wording) is cleanly drawn.
- Fail-safe base resolution and TOCTOU/`-d` fail-closed reasoning are sound **for the true-merge paths (A/D)** — the defect is specifically the misapplication of that oracle to the squash-merge path (B/C).

**Why NEEDS REVISION rather than READY (with concerns):** The plan's central mechanism — a single is-ancestor `safe_delete_branch` routed through all four sites — is *correct for two of the four sites and broken for the other two*. `cleanup_after_merge` is by definition the post-squash-merge cleanup; an is-ancestor-`-d`-only guard there refuses to delete legitimately merged branches, breaking the SDLC happy path and violating the plan's own no-regression criterion. Four critics raised this independently and it is reproducible in three git commands. This is not stylistic: the fix as written would replace one resource-leak (preserve-when-it-should-delete) for the very leak the original #1272 hook existed to prevent, on the main pipeline. The remedy is well-scoped (two oracles for two contexts; rescope the `-D` gate; add the squash-merge happy-path test honestly so it would catch this), but it must be made before build.

**Test-design correction (folded into the above):** Test Impact line 296 planned an "`is-ancestor`-returns-0 setup" to represent a merged branch. That encodes the wrong production model and would pass while masking the BLOCKER. **ADDRESSED in cycle 3:** the `cleanup_after_merge` happy-path Test Impact item now mandates a **real squash-merge** (`git merge --squash` + commit on main) and asserts the branch is still deleted; the new `test_safe_delete_branch_*` set explicitly asserts `merged_via_ancestor` *refuses* a squash-merged branch while `merged_via_cherry` *accepts* it — making the squash-merge BLOCKER a tested invariant rather than a masked assumption.

---

**Revision cycle 3 (revision_applied: true):** All one BLOCKER, two CONCERNs, and three NITs from cycle 2 are ADDRESSED above. The core change is the two-oracle `safe_delete_branch` (is-ancestor for the true-merge sites A/D; squash-aware `git cherry` for the post-PR-merge sites B/C), with a single guarded `-D` inside the helper, rescoped acceptance gates, an accumulation observability hook, confirmation that the worktree-GC reaps preserved worktrees, a worker-restart verification line, the two-representation consolidation, and the zero-`delete_branch=True`-callers confirmation. The squash-merge oracle behavior was re-reproduced with live git commands during this revision.

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
