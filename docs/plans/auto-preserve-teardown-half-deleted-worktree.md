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

- **Issue #2137 / PR #2150** — "Uncommitted work in session worktrees has no backstop" (closed 2026-07-17). Introduced `preserve_uncommitted_worktree_changes` and the `validate_no_destructive_git_in_worktree.py` PreToolUse hook as the two halves of one backstop. The design is sound and stays; it simply never anticipated that a *destroyed* tree and a *dirty* tree are indistinguishable to `git status --porcelain`'s emptiness test. Plan archived at `docs/archive/plans-completed/sdlc-2137.md`.
- **Issue #1646** — the unmerged-branch guard. Protects *committed* work from branch deletion. It is precisely why the wipe survived: once committed, the deletion looked like work worth keeping, and `safe_delete_branch`'s `merged_via_tree` predicate preserved the branch carrying it.
- **Issue #880** — the 2026-04-10 incident where a stale-worktree cleanup recursively destroyed the main repository. Produced the path-containment guard at `agent/worktree_manager.py:918` and the deliberate absence of error suppression around the fallback `shutil.rmtree`. Same failure family (teardown destroying more than it should), different layer. That guard is why the blast radius here stayed inside `.worktrees/`.
- **Issue #2517 / PR #2681** — scheduled disk reclaim replacing an unguarded worktree-gc. Adjacent worktree lifecycle work, no overlap with the preserve path.
- **Issue #1357** — the refuse-busy guard in `remove_worktree` (live `AgentSession` still references the worktree). Runs *before* preserve on that call path, so it does not shield the `_cleanup_stale_worktree` path at all.

No prior attempt has been made to fix *this* defect. #3167 is the first report.

## Research

No relevant external findings — proceeding with codebase context and direct empirical verification.

The work is entirely internal: `git` plumbing already vendored on every machine, called through `subprocess` from one module. The only external contract that matters is git's own porcelain output format, and that is better settled by running it than by reading about it, which is what spike-1 does below.

## Spike Results

Two spikes ran against a throwaway git repo with a linked worktree, simulating the incident exactly: tracked top-level directories deleted from disk, nothing else touched.

### spike-1: Does `git status --porcelain` alone distinguish a wipe from ordinary dirt?

- **Assumption**: "The deletion signal is available before `git add -A`, so the refusal can be computed without mutating the index."
- **Method**: prototype (throwaway repo, isolated scratch dir)
- **Finding**: **Yes.** With `tests/`, `bridge/`, `agent/` removed from disk, `git status --porcelain` reported ` D <path>` for all nine tracked files and nothing else. After `git add -A`, `git diff --cached --shortstat` reported `9 files changed, 9 deletions(-)` — zero insertions — and `git diff --cached --numstat` reported `0\t1\t<path>` per file. Both signals are present and cheap; the porcelain one is available *earlier*.
- **Confidence**: high
- **Impact on plan**: The check runs **before** `git add -A`, reading the existing `status` output the function already captures. This is a strict improvement on the issue's Next Steps bullet, which proposed computing `--numstat` *after* staging. Staging first means a refusal leaves a fully-staged wipe sitting in the worktree index — strictly worse than today whenever the subsequent force-remove fails and the directory survives. Reading `status` costs one already-executed subprocess and mutates nothing.

### spike-2: Is there a signal cheaper and less tunable than a ratio?

- **Assumption**: "Detecting missing tracked top-level directories requires hardcoding `tests/ bridge/ agent/ config/`, as the issue suggests."
- **Method**: prototype
- **Finding**: **No hardcoding needed.** `git ls-tree --name-only -d HEAD` enumerates the tracked top-level directories directly from the commit. Iterating them and testing `Path(worktree_dir / d).is_dir()` returned exactly `agent`, `bridge`, `tests` as missing — the three that were deleted, and nothing else. The signal is repo-agnostic, needs no configuration, and has no threshold to tune.
- **Confidence**: high
- **Impact on plan**: Both guards ship, and the structural one is primary. A worktree missing a directory that HEAD tracks is *definitionally* not "uncommitted work" — no ratio, no threshold, no env var, no false-positive story to argue about. The ratio guard becomes the secondary net for partial wipes that leave every top-level directory nominally present (a `rm -rf` interrupted inside one subtree, for instance), and it is the only one of the two that carries a tunable constant.

### spike-3: Would the issue's proposed insertions-ratio predicate have caught the reported incident?

- **Assumption**: "Refusing when staged deletions overwhelm staged insertions (the Next Steps bullet) is a sound predicate."
- **Method**: prototype — reproduce the incident's *shape*, not just a bare wipe: tracked directories deleted from disk **and** untracked build artifacts present, which is what a real lane worktree looks like.
- **Finding**: **No — it would not have fired.** With five untracked artifacts under `.venv/lib/` alongside nine deleted tracked files, `git add -A` followed by `git diff --cached --shortstat` reported `14 files changed, 5 insertions(+), 9 deletions(-)`. The insertions are real and come entirely from staging untracked junk. This is exactly the reported commit's signature: 902,840 deletions arrived **with 223,142 insertions**, which no "insertions are a negligible fraction" test treats as a wipe.
- **Confidence**: high — the incident's own numbers are the confirming evidence, and the reproduction shows the mechanism that produces them.
- **Impact on plan**: the insertions-ratio predicate is rejected outright. The proportional guard counts *files gone from disk* against *files tracked in the index* (`git ls-files --deleted -z` vs `git ls-files -z`), which is unaffected by whatever untracked artifacts the lane left behind. Verified in the same repo: 9 deleted against 16 tracked = 56%, over the 50% threshold, refuse. Control cases behave: restoring one directory dropped the count to 6, and a fully clean worktree reported 0 deleted and 0 dirty entries.

### spike-4: Is there a NUL-safe primitive for "tracked but missing from disk"?

- **Assumption**: "Counting deleted files requires parsing `git status --porcelain`, with its rename two-path entries and quoting rules."
- **Method**: prototype
- **Finding**: `git ls-files --deleted -z` returns exactly the tracked-and-absent set, and `git ls-files -z` the full tracked set. Both are plumbing, both NUL-delimited, neither mutates the index, and neither has porcelain's rename/quoting ambiguity.
- **Confidence**: high
- **Impact on plan**: the proportional guard is two `ls-files` calls and a division. No porcelain parsing anywhere in the guard.

## Data Flow

Two paths reach the producer. Both end in the same unguarded commit.

**Path A — stale worktree found while creating a new one** (the path the issue names)

1. **Entry point**: `create_worktree(repo_root, slug)` — `agent/worktree_manager.py:1295`. Called on session start / revival.
2. `worktree_dir.exists()` is False, so creation proceeds. `_find_worktree_for_branch(repo_root, "session/{slug}")` finds the branch checked out at some *other* `.worktrees/` path (line 1318).
3. `_cleanup_stale_worktree(repo_root, branch_name, existing_wt)` — line 1323.
4. Path-containment guard (line 918) passes: the path is under `.worktrees/`. Directory exists, so the force-remove branch is taken.
5. `stale_slug = wt.name`; `preserve_uncommitted_worktree_changes(repo_root, stale_slug, wt)` — line 948.
6. **The gap**: `git status --porcelain` in a gutted `wt` returns a long list of ` D` lines. Non-empty, so the clean-tree early return does not fire. `git add -A` stages every deletion. `git commit --no-verify --no-gpg-sign` writes it — **onto whatever branch `wt` has checked out**, which is `session/{slug}`, not `session/{stale_slug}`.
7. `git -C {repo_root} update-ref refs/session-wip/{stale_slug} {sha}` — the ref is filed under the *directory name*, the commit landed on a *different branch*. Recovery pointer and recovered content disagree.
8. **Output**: `git worktree remove --force` proceeds. The wipe is now the head of a live session branch, and the durable ref points at it.

**Path B — ordinary teardown** (unnamed in the issue, equally exposed)

1. **Entry point**: `remove_worktree(repo_root, slug)` — line 1551. Called from `post_merge_cleanup` (line 2010) and from session teardown.
2. Refuse-busy guard (#1357) and live-process guard pass, or `force=True` overrides them.
3. `preserve_uncommitted_worktree_changes(repo_root, slug, worktree_dir)` — line 1660. Same unguarded body, same outcome. Here slug and branch do agree.
4. **Output**: same wipe commit, same ref.

**Where the guard goes**: step 6 in Path A is step 3 in Path B — the identical function body. Putting the refusal inside `preserve_uncommitted_worktree_changes`, between the `status` read and `git add -A`, covers both paths with one change and leaves every caller's contract unchanged (the function already returns `{"preserved": False, "errors": [...]}` and is documented as never raising into teardown).

**Where the deletions came from** is deliberately *not* on this path. Whatever gutted the tree — a `shutil.rmtree` that raised partway through the `_cleanup_stale_worktree` fallback at line 978, a `git worktree remove --force` that deleted files and then failed, or two concurrent teardown passes racing on the same directory — the observable at step 6 is identical and the refusal is correct against all three.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2150 (#2137) | Added `preserve_uncommitted_worktree_changes`: auto-WIP-commit + `refs/session-wip/{slug}` before every force-remove. Added the destructive-git PreToolUse hook as the agent-facing half. | Treated "dirty" as a single condition. `git status --porcelain` being non-empty was taken as proof that work exists, when it is equally consistent with work having been destroyed. The one place a filter belonged — between reading status and staging everything — is the one place with no logic in it. |
| PR #2150 (#2137) | Used `--no-verify` on the WIP commit, to keep pre-commit hooks from hanging teardown. | Correct for the stated reason, and it removed the last check that could have caught a 902,840-deletion commit. Nothing else in the path inspects the diff. |
| #1646 (unmerged-branch guard) | Refuses to delete a session branch carrying unmerged commits. | Worked exactly as designed, in the wrong direction. Once the wipe was committed it *was* unmerged work, so the guard protected it. A safety mechanism for committed work cannot distinguish a commit worth keeping from a commit that should never have been made. |

**Root cause pattern:** every guard in this subsystem answers "is there something here?" and none answers "is what's here plausible?". `status --porcelain` non-empty, `merge-base --is-ancestor` passing, `merged_via_tree` false — each is a presence check, and a wipe satisfies all three. The fix is the first shape-of-the-change check on the path.

**A correction to the issue's own diagnosis.** The Diagnostic Output section attributes the `bridge boot → WIP commit` correlation to `cleanup_stale_branches`. That function (`agent/session_revival.py:193`) lists `session/*` branches, checks their age against `max_age_hours`, and calls `safe_delete_branch`. It never touches a worktree and never reaches preserve. The correlation is real; the mechanism named for it is not. The only caller of `_cleanup_stale_worktree` is `create_worktree` at line 1323 — so what a bridge boot does is *create* worktrees, and it is the stale-worktree recovery inside creation (Path A above) that reaches the producer. Anyone building from the issue's stated mechanism would have instrumented the wrong function.

## Architectural Impact

- **New dependencies**: none. Two additional `git` reads (`ls-tree`, and reuse of the `status` output already captured) inside a function that already shells out four times.
- **Interface changes**: none breaking. `preserve_uncommitted_worktree_changes` keeps its signature and its documented return shape. The refusal reuses the existing failure branch — `{"preserved": False, "was_clean": False, "errors": [...]}` — and adds one key, `"refused": <reason>`, for callers and tests that want to distinguish a refusal from a git failure. Every existing caller ignores the return value entirely, so nothing downstream changes.
- **Coupling**: unchanged. The guard is local to one function; no new imports across module boundaries. One named constant lands in `config/settings.py` alongside the other tunables.
- **Data ownership**: unchanged.
- **Reversibility**: trivial. Deleting the guard block restores today's behavior exactly. No migration, no persisted state, no schema.

The one thing that genuinely changes is the function's *contract in the failure case*: it can now decline to preserve a tree it previously would have committed. That is the point, and the No-Gos and Risks sections below bound it.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (the shape is settled by spikes 1–3; nothing needs alignment)
- Review rounds: 1

One guard block inside one function, one constant, and a test class in a file that already has the fixtures for it. The investigation that would justify a larger appetite — root-causing what gutted the tree — is explicitly not in scope, because the guard is correct against every candidate mechanism.

## Prerequisites

No prerequisites — this work has no external dependencies. Everything it touches is `git` plumbing already required by the module and a pydantic field in `config/settings.py`.

## Solution

### Key Elements

- **Wipe detector (structural, primary)**: answers "is this worktree missing directories that HEAD says it tracks?" A worktree in that state is definitionally not uncommitted work. No threshold, no configuration, no false-positive story to argue about.
- **Wipe detector (proportional, secondary)**: answers "have a majority of the tracked files vanished from disk?" Catches a partial wipe that leaves every top-level directory nominally present — an interrupted `rm -rf` inside one subtree, say. This is the only piece carrying a tunable.
- **Refusal path**: returns the function's existing failure shape plus a `refused` reason, logs at ERROR with the counts, and mutates neither index nor branch nor ref. Teardown proceeds unchanged.
- **Guard placement**: inside `preserve_uncommitted_worktree_changes`, between the `status --porcelain` read and `git add -A`. Both producers (`_cleanup_stale_worktree` and `remove_worktree`) inherit it from one edit.

### Flow

Teardown begins → preserve called → `git status --porcelain` non-empty (dirty) → **wipe check** → *not a wipe*: `git add -A` → WIP commit → `refs/session-wip/{slug}` → force-remove (today's behavior, unchanged)

Teardown begins → preserve called → `git status --porcelain` non-empty (dirty) → **wipe check** → *wipe*: ERROR log naming slug, missing directories, and deleted/tracked counts → return `{"preserved": False, "refused": "<reason>"}` → force-remove proceeds → **no commit, no ref, session branch untouched**

### Technical Approach

**The two primitives, both verified by spike (see Spike Results):**

| Signal | Command | Refuse when |
|---|---|---|
| Missing tracked directory | `git ls-tree --name-only -d HEAD` | any listed name is not a directory on disk in the worktree |
| Majority of tracked files gone | `git ls-files --deleted -z` and `git ls-files -z` | `deleted >= wipe_refusal_min_deleted_files` **and** `deleted / tracked >= wipe_refusal_deleted_fraction` |

`git ls-files --deleted` is the exact primitive for "tracked in the index, absent from the working tree." It needs no porcelain parsing, is NUL-safe under `-z`, survives paths with spaces and quoting, and has no rename-entry ambiguity. It mutates nothing.

**Why not the insertions-ratio predicate the issue proposes.** The issue's Next Steps suggest computing `git diff --cached --numstat` after `git add -A` and refusing when deletions dwarf insertions. Spike-3 shows this **would not have fired on the reported incident.** That commit carried 223,142 insertions alongside its 902,840 deletions — because `git add -A` also stages untracked build artifacts (`.venv/`, `.pyc` files, whatever the lane left behind), and those count as insertions. Reproducing the incident's shape in a throwaway repo (tracked directories deleted, untracked artifacts present) produced `14 files changed, 5 insertions(+), 9 deletions(-)` — a real insertions figure that defeats any "insertions are a negligible fraction" test. Counting *files gone from disk* against *files tracked in the index* is immune to whatever junk happens to be sitting in the worktree.

**Why the check runs before `git add -A`.** Both signals are available without staging, so nothing is gained by staging first and everything is risked: a refusal computed after `add -A` leaves a fully-staged wipe in the worktree index, which is worse than today's behavior in exactly the case that matters (the subsequent force-remove fails and the directory survives with a staged wipe waiting for the next process to commit it).

**Constants.** Both live on `PerformanceSettings` in `config/settings.py`, which already hosts a provisional non-timing tunable (`max_content_filename_bytes`) with the same "Provisional/tunable" idiom and the working `PERFORMANCE__` nested env prefix:

- `wipe_refusal_min_deleted_files: int = 50` — absolute floor. Below this, no refusal on the proportional signal regardless of fraction, so a small worktree with a handful of tracked files never trips it.
- `wipe_refusal_deleted_fraction: float = 0.5` — a majority of tracked files must be gone.

Both marked provisional in their `description`. Env: `PERFORMANCE__WIPE_REFUSAL_MIN_DELETED_FILES`, `PERFORMANCE__WIPE_REFUSAL_DELETED_FRACTION`.

**Return shape.** The refusal reuses the documented failure branch and adds one key:

```
{"preserved": False, "was_clean": False, "refused": "missing-tracked-dirs" | "majority-deleted",
 "ref": ref, "errors": ["<human-readable detail with counts>"]}
```

Every current caller discards the return value, so nothing downstream changes. The key exists for the regression tests and for any future caller that wants to distinguish a refusal from a git failure.

**Failure of the guard itself fails open.** If `ls-tree` or `ls-files` errors or times out, the function logs at WARNING and proceeds as it does today. Rationale: this function's hard contract is that it never raises into teardown and never hangs it. A guard-computation failure is not evidence of a wipe, and treating it as one would convert a git hiccup into a silent loss of the backstop for legitimate work. The two guards are a net, not a gate.

**The slug/branch mismatch (found during recon) is fixed in the same edit.** `_cleanup_stale_worktree` passes `stale_slug = wt.name` — the foreign directory name — while the WIP commit lands on whatever branch that worktree has checked out. The ref then names a slug the commit was never made on. Fix: resolve the worktree's actual branch with `git -C {wt} rev-parse --abbrev-ref HEAD` and derive the ref slug from it, falling back to the directory name when HEAD is detached or the branch is not `session/*`. This is two lines in the preserve function and it makes the docstring's `git checkout refs/session-wip/{slug}` promise true on the Path A producer, which today it is not.

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
