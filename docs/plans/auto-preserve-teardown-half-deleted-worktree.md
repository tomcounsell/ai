---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3167
last_comment_id: none
---

# Auto-preserve refuses a half-deleted worktree

## Problem

The one function whose entire purpose is preventing data loss committed a wipe. On 2026-09-05, `preserve_uncommitted_worktree_changes` ran against a session worktree whose tracked files had already been removed from disk, read the resulting maximally-dirty tree as "uncommitted work," and committed it: 3,914 files changed, 223,142 insertions, **902,840 deletions**, landing on `session/dev-a4e15370` under the subject `WIP: auto-preserved before teardown`. `tests/`, `bridge/`, `agent/`, `config/` and `docs/` were gone from the branch head. Three further preserve passes then stacked no-op WIP commits on top, because relative to the committed wipe the tree was now clean, burying the destructive commit under innocuous ones.

Nothing catches this. `git merge-base --is-ancestor origin/main HEAD` still passes: the branch is strictly *ahead* of main, it just deletes most of it. `git status` is clean. `--no-verify` skips every commit hook. The commit subject reads like a routine safety net. The reporter noticed only because `sed` could not find a test file they had just been reading, and `git ls-files | wc -l` returned 1,491 against main's several thousand.

The same wipe is written to `refs/session-wip/{slug}`, so the advertised recovery path preserves the destruction rather than the work. The docstring tells a human to run `git reset --soft HEAD~1` to restore the dirty tree; doing that on a wipe commit makes the deletion the working state.

**Current behavior:**

`preserve_uncommitted_worktree_changes` (`agent/worktree_manager.py:1406`) tests only whether `git status --porcelain` is non-empty, then unconditionally runs `git add -A` and commits. It never asks *what* is dirty. A worktree caught mid-teardown is maximally dirty, so the guard meant to skip a clean tree instead waves a total deletion straight through.

**Desired outcome:**

`preserve_uncommitted_worktree_changes` refuses to commit when the dirty state is a wipe rather than work. It returns `{"preserved": False, ...}` with an explanatory error, logs loudly at ERROR, mutates neither the index nor the branch, and lets teardown proceed. Legitimate dirty trees — including large refactors that delete many files while adding real content — are preserved exactly as they are today.

## Freshness Check

**Baseline commit:** `bc42055a4` (`origin/main` at recon time; plan committed on `fd73c0ad1` after a sync)
**Issue filed at:** 2026-09-05T03:25:53Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `agent/worktree_manager.py:1406-1500` — the producer, with no check between the `status --porcelain` non-empty test and `git add -A` — **still holds, exact.** `def preserve_uncommitted_worktree_changes` is at line 1406.
- `agent/worktree_manager.py:940-948` — the `_cleanup_stale_worktree` caller — **still holds, exact.** The `preserve_uncommitted_worktree_changes(repo_root, stale_slug, wt)` call is at line 948, immediately before `git worktree remove --force`.
- Commits `ba3ea72e9`, `805983e36`, `c004ff37d`, `35c76c461` — **gone.** Not resolvable in this checkout; the reporter's `git reset --hard origin/main` plus `--force-with-lease` recovery removed them. The defect is confirmed by code reading and by spike-1 below, not by re-inspecting those objects. No content was lost in that recovery (the wipe commits held only deletions).
- `refs/session-wip/*` — **empty.** `git for-each-ref refs/session-wip/` returns zero refs on this machine, so the audit bullet in the issue's Next Steps has nothing to audit here.

**Second call site the issue did not name (drift found during recon):** `remove_worktree` also calls preserve, at `agent/worktree_manager.py:1660`, before its own `git worktree remove --force`. A fix scoped to `_cleanup_stale_worktree` alone would leave that path exposed. This is why the guard belongs inside the preserve function itself.

**Cited sibling issues/PRs re-checked:**

- #2137 — closed 2026-07-17. The issue that introduced this function. Its PR #2150 ("Preserve uncommitted worktree work + destructive-git guard") merged the same day. Still the governing design; this plan hardens it rather than replacing it.
- #3166 (bridge SIGTERM restart loop) — **still open.** Named in the issue as the companion investigation and the amplifier: nine bridge boots in seventeen minutes meant nine teardown passes against live worktrees. Independent of this fix; the guard is correct with or without it.
- #3162 (worktree-gc: reap stale `nightly-triage-*` branches) — **still open.** Adjacent worktree hygiene, no code overlap with `preserve_uncommitted_worktree_changes`.

**Commits on main since issue was filed (touching referenced files):**

`git log --since=2026-09-05T03:25:53Z origin/main -- agent/worktree_manager.py agent/session_revival.py` returns nothing. Neither file has moved since filing.

**Active plans in `docs/plans/` overlapping this area:** none. `grep -l "worktree_manager\|preserve_uncommitted" docs/plans/*.md` returns no matches across the fifteen active plans.

**Notes:** The one substantive premise correction is in the issue's "Diagnostic Output" timing paragraph — see **Why Previous Fixes Failed** and the Recon Summary on the issue. `cleanup_stale_branches` is not the mechanism. The bridge-boot correlation is real; the branch sweep is not what reaches preserve.

## Prior Art

_placeholder_

## Research

_placeholder_

## Spike Results

_placeholder_

## Data Flow

_placeholder_

## Why Previous Fixes Failed

_placeholder_

## Architectural Impact

_placeholder_

## Appetite

_placeholder_

## Prerequisites

_placeholder_

## Solution

_placeholder_

## Failure Path Test Strategy

_placeholder_

## Test Impact

_placeholder_

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

_placeholder_

## Success Criteria

_placeholder_

## Team Orchestration

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

## Open Questions

_placeholder_
