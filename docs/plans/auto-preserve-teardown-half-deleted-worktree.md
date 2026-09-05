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

### Exception Handling Coverage

`preserve_uncommitted_worktree_changes` has one broad `except Exception` at the bottom of its body, which is the documented non-blocking contract (it logs at ERROR with the `[worktree-wip-preserve-failed]` tag and returns the error in the result dict). `test_git_failure_returns_error_dict_and_never_raises` already asserts that observable behavior by patching `subprocess.run` to raise.

- [ ] The new guard adds no new `except Exception: pass`. Its own failure mode is a narrow catch around the two `ls-files`/`ls-tree` reads that logs at WARNING and falls through to today's behavior.
- [ ] Add `test_guard_computation_failure_falls_open_and_warns` — patch the `ls-files` calls to fail, assert the function still preserves a legitimately dirty tree and that a WARNING naming the guard was logged. This is the test that proves fail-open is deliberate rather than accidental.

### Empty/Invalid Input Handling

- [ ] **Unborn HEAD** (a worktree whose branch has no commits): `git ls-tree -d HEAD` fails. Covered by the fail-open path above; add an explicit case so the behavior is pinned rather than incidental.
- [ ] **Empty repository / zero tracked files**: `deleted / tracked` would divide by zero. The absolute floor (`wipe_refusal_min_deleted_files`) is evaluated first and short-circuits, but add a guard test with a repo whose index is empty to pin it.
- [ ] **Detached HEAD in the stale worktree**: the branch-resolution half of the fix must fall back to the directory name rather than writing `refs/session-wip/HEAD`. Test it.
- [ ] **Untracked-only dirty tree**: zero deleted files, so neither guard fires and preservation proceeds. Already covered by `test_untracked_only_is_preserved`; re-run it as a non-regression.

### Error State Rendering

There is no user-visible surface here; the observable is the log line and the returned dict.

- [ ] The refusal log must be ERROR (not WARNING) and must name the slug, the refusal reason, and the counts — a silent refusal is the same class of bug as the silent commit, just in the other direction. Assert on the log record in the refusal tests, the way the existing `worktree-wip-preserved` test does.
- [ ] Give the refusal its own greppable tag, `[worktree-wip-refused-wipe]`, distinct from `[worktree-wip-preserve-failed]`, so an operator scanning logs can tell "we declined to save a wipe" from "git broke."

## Test Impact

All existing tests for this function live in `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py`. Its fixtures (`_init_git_repo`, `_add_linked_worktree`, `_dirty`, `_git`) build real git repos with real linked worktrees, so the regression fixture is an addition to that file, not new scaffolding.

- [ ] `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py::TestPreserveUncommittedChanges::test_dirty_tree_preserved_in_named_ref_and_wip_commit` — **no change expected.** `_dirty()` modifies one tracked file, stages a second, and adds an untracked one: zero deletions, so neither guard fires. Re-run as the primary non-regression proof that legitimate work is still preserved.
- [ ] `...::test_clean_tree_is_noop_and_creates_no_ref` — **no change expected.** The clean-tree early return runs before the guard.
- [ ] `...::test_untracked_only_is_preserved` — **no change expected.** Zero deletions.
- [ ] `...::test_git_failure_returns_error_dict_and_never_raises` — **UPDATE.** It patches `subprocess.run` to raise on *every* call, which now includes the guard's `ls-files` reads. Confirm the assertion still holds (`preserved: False`, error dict, `[worktree-wip-preserve-failed]` logged) and that the guard's own narrow catch does not swallow the failure before the outer handler sees it. Adjust the assertion if the error message changes shape.
- [ ] `...::test_remove_worktree_preserves_dirty_tree_before_force_remove` — **no change expected.** `_dirty()` again; asserts the `remove_worktree` path still preserves. This is the test that proves the guard did not break Path B.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_cleanup.py::…` (lines 298, 376) — **REVIEW, likely no change.** Both patch `preserve_uncommitted_worktree_changes` wholesale, so they are insulated from its internals. Confirm neither asserts on a return value whose shape changed.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_creation.py:38` — **no change expected.** Patches `_cleanup_stale_worktree` entirely.

**New tests** (the regression suite the issue asks for), all in `test_worktree_manager_uncommitted.py`:

- [ ] `test_missing_tracked_directory_refuses` — fixture worktree with tracked top-level directories deleted on disk; assert `preserved is False`, `refused == "missing-tracked-dirs"`, no new commit on the branch, no `refs/session-wip/{slug}` created, and the `[worktree-wip-refused-wipe]` ERROR logged with the missing directory names.
- [ ] `test_majority_of_tracked_files_deleted_refuses` — every top-level directory still present but most tracked files removed; assert the proportional guard fires with `refused == "majority-deleted"`.
- [ ] `test_wipe_with_untracked_artifacts_still_refuses` — the incident's actual shape (spike-3): tracked files deleted **and** untracked artifacts present so `add -A` would report insertions. Assert refusal. This test is the one that would have failed under the issue's proposed insertions-ratio predicate, and it exists to keep anyone from "simplifying" the guard back to that shape.
- [ ] `test_large_deletion_with_real_work_is_preserved` — deletions above the floor but below the fraction, with genuine edits present; assert `preserved is True`. The false-positive boundary.
- [ ] `test_index_untouched_on_refusal` — after a refusal, assert `git diff --cached --name-only` is empty. Pins the "check before `git add -A`" decision so a later refactor cannot quietly move the guard after staging.
- [ ] `test_guard_computation_failure_falls_open_and_warns` — see Failure Path Test Strategy.
- [ ] `test_ref_slug_follows_checked_out_branch` — a worktree directory named `foo` with `session/bar` checked out; assert the WIP ref is `refs/session-wip/bar`, matching the branch that received the commit. Pins the recon-found mismatch fix.
- [ ] `test_detached_head_falls_back_to_directory_name` — the edge case of the previous test.

## Rabbit Holes

- **Root-causing what gutted the worktree.** Three mechanisms fit the evidence equally well: a `shutil.rmtree` that raised partway through the `_cleanup_stale_worktree` fallback (`agent/worktree_manager.py:978`), a `git worktree remove --force` that deleted files and then failed, or two concurrent teardown passes racing on the same directory. Distinguishing them means instrumenting a path that fires only under a bridge restart storm, and the answer changes nothing: the guard is correct against all three. Filed as a No-Go, not attempted here.
- **Fixing the bridge restart loop.** #3166 is the amplifier that turned one latent race into nine teardown passes. It is a separate issue with a separate investigation, and this fix does not wait on it.
- **Serializing teardown with a lock.** Tempting once you notice the race, and a large change to a module three subsystems call. If the guard proves insufficient in practice, that is the follow-up; it is not the first move.
- **Building recovery tooling for `refs/session-wip/*`.** There are zero such refs on this machine. Writing a resurrect/inspect CLI for a namespace that is currently empty is speculative work.
- **Tuning the threshold constants.** They are provisional by declaration and marked so in their descriptions. The structural guard is primary precisely so the numbers do not have to be right on the first try. Arguing about 0.5 versus 0.6 before a single real refusal has been observed is the trap.
- **Broadening the `validate_no_destructive_git_in_worktree.py` hook.** Different half of the #2137 backstop, agent-facing rather than teardown-facing, and its blocked-shape set was deliberately scoped in that plan's No-Gos. Out of scope.
- **Auditing `refs/session-wip/*` across the fleet.** The audit is a one-line `git for-each-ref` an operator can run; here it returns zero. It is not a feature and there is nothing to ship for it.

## Risks

### Risk 1: The guard refuses a legitimate large deletion, losing the backstop for real work

**Impact:** A lane doing a genuine mass-deletion refactor — deleting a whole package while the replacement lives in a sibling directory — gets torn down without its uncommitted work preserved. That is the exact loss #2137 was built to prevent, reintroduced from the other side.

**Mitigation:** Three layers. The structural guard fires only when a directory tracked at HEAD is *absent from disk entirely*, which a refactor deleting files inside a package does not produce (the directory survives with its remaining files). The proportional guard requires **both** an absolute floor and a majority of all tracked files gone — no plausible refactor deletes half the repository's tracked files without committing anything. And the refusal is logged at ERROR with a dedicated tag and the full counts, so the case is visible rather than silent. `test_large_deletion_with_real_work_is_preserved` pins the boundary.

### Risk 2: Fail-open on guard-computation failure means a wipe slips through

**Impact:** If `git ls-files` errors or times out at exactly the wrong moment, the guard is bypassed and today's behavior resumes — including the possibility of committing a wipe.

**Mitigation:** Accepted deliberately, with reasoning recorded. This function's hardest contract is that it never raises into teardown and never hangs it; converting a git hiccup into a refusal would trade a rare wipe for a routine loss of the backstop on legitimate work. The failure is logged at WARNING with its own tag so a recurring pattern is visible in the logs, and `test_guard_computation_failure_falls_open_and_warns` makes the choice explicit rather than incidental. Revisit only if the WARNING actually appears in production logs.

### Risk 3: A future refactor moves the guard after `git add -A`

**Impact:** A refusal computed after staging leaves a fully-staged wipe in the worktree index — worse than the status quo, because the next process to touch that worktree commits it without ever running preserve.

**Mitigation:** `test_index_untouched_on_refusal` asserts `git diff --cached --name-only` is empty after a refusal, which fails immediately if the guard moves. A comment at the guard site states the ordering constraint and why it exists.

### Risk 4: Someone "simplifies" the guard back to the insertions-ratio predicate

**Impact:** The predicate the issue originally proposed does not fire on the incident it was written for (spike-3). Reintroducing it would leave the bug fixed on paper and open in fact.

**Mitigation:** `test_wipe_with_untracked_artifacts_still_refuses` reproduces the incident's exact shape — deletions plus untracked artifacts producing real insertions — and fails under that predicate. The Solution section records why it was rejected, with the incident's own numbers.

## Race Conditions

### Race 1: A concurrent teardown pass gutting the worktree while preserve reads it

**Location:** `agent/worktree_manager.py:1406-1500` (preserve), against `:948` and `:1660` (the two force-remove call sites), plus the fallback `shutil.rmtree` at `:978`.

**Trigger:** Two teardown passes target the same `.worktrees/{slug}` concurrently. Pass A's `git worktree remove --force` (or the fallback `rmtree`) begins deleting files; pass B's preserve reads a tree that is now partially gone. The bridge restart loop in #3166 supplied nine such passes in seventeen minutes. This is the leading candidate for what produced the reported commit, though the plan does not depend on it being the right one.

**Data prerequisite:** The guard needs `git ls-files` (reads the index, which lives in `.git/worktrees/{name}/index` and is not touched by a filesystem delete of the working tree) and `git ls-tree HEAD`. Both remain readable while the working tree is being destroyed, which is what makes the guard work at all under this race.

**State prerequisite:** None. The guard makes no assumption about who else is operating on the directory.

**Mitigation:** Not prevented — *detected*. The guard converts an unwinnable ordering problem into a check on observable state: whatever the interleaving, a tree missing directories that HEAD tracks is refused. There is a residual window (the guard reads a still-intact tree, then the deletion completes, then `add -A` runs) which the guard cannot close; closing it needs the lock that the Rabbit Holes section defers. The window is milliseconds against a teardown that is otherwise unguarded for its whole duration, and the guard eliminates the case where preserve is invoked against an *already* gutted tree, which is what the stacked no-op commits in the report demonstrate actually happened.

### Race 2: Repeated preserve passes stacking commits on an already-committed wipe

**Location:** Same.

**Trigger:** Once a wipe is committed, every subsequent preserve pass sees a tree that is clean relative to the new HEAD and either no-ops or commits trivia — which is precisely how the report's three follow-on commits (`805983e36`, `c004ff37d`, `35c76c461`) buried the destructive one.

**Data prerequisite:** None.

**State prerequisite:** The wipe must have been committed for this to be reachable.

**Mitigation:** Eliminated at the source. If the first wipe is never committed, HEAD never moves, and later passes see the same gutted tree and refuse identically. The refusal is idempotent by construction — it reads state and writes nothing — so N passes produce N ERROR logs and zero commits.

## No-Gos (Out of Scope)

Three of the issue's five Next Steps bullets were completed during planning rather than deferred, and are recorded where they belong:

- The **ordering question** in `_cleanup_stale_worktree` is answered, not deferred. Within a single pass the ordering is already correct: preserve runs at `agent/worktree_manager.py:948`, before that pass's own `git worktree remove --force`. The "preserve running against a tree a previous pass already gutted" hypothesis is a *cross-pass* concern, which is Race 1 in the Race Conditions section and is what the guard detects. No code change is needed for ordering.
- The **`refs/session-wip/*` audit** is done: `git for-each-ref refs/session-wip/` returns zero refs on this machine. Nothing to audit, nothing to ship.
- The **sharper "missing tracked directory" variant** is not an alternative to evaluate later — it is the primary guard in this plan, and spike-2 removed the need for the hardcoded directory list the issue proposed.

Genuinely out of scope:

- [SEPARATE-SLUG #3166] The bridge SIGTERM restart loop that amplified one latent race into nine teardown passes in seventeen minutes. Filed and open as its own reliability investigation with its own evidence. This plan's guard is correct with or without it, and touching bridge restart or watchdog code from this lane would put an unrelated subsystem into a worktree-teardown PR.

## Update System

No update system changes required. The guard is a code-path change inside a module every machine already runs; `/update`'s `git pull` plus `uv sync` propagates it with no new dependency, no new config file, and no migration step. The two settings fields have working defaults, so an installation that never sets `PERFORMANCE__WIPE_REFUSAL_*` gets the intended behavior.

No `.env.example` entry is needed: these are tunables with in-code defaults, not credentials, and they live on a pydantic settings group whose nested `PERFORMANCE__` prefix is already wired.

## Agent Integration

No agent integration required — this is a bridge-internal change.

`preserve_uncommitted_worktree_changes` is called only from within `agent/worktree_manager.py` (lines 948 and 1660) on teardown paths the bridge and worker drive automatically. There is no CLI entry point to add to `pyproject.toml [project.scripts]`, no MCP tool to register, and nothing for the agent to invoke. The agent's relationship to this code is as a *subject* of it, not a caller: its worktree is what gets torn down.

The one agent-facing surface in this subsystem is the sibling `validate_no_destructive_git_in_worktree.py` PreToolUse hook, and it is explicitly out of scope (see Rabbit Holes).

## Documentation

### Feature Documentation

- [ ] Update `docs/features/session-isolation.md` — the "Auto-WIP-commit before teardown" subsection at line 254 describes the mechanism as an unconditional four-step sequence. Add the refusal conditions, the `[worktree-wip-refused-wipe]` log tag, and the corrected statement of what `refs/session-wip/{slug}` is guaranteed to contain. The line-307 table row for `agent/worktree_manager.py` also needs the guard named.
- [ ] Correct the recovery promise in the same document. The docstring and the feature doc both tell a human to run `git checkout refs/session-wip/{slug}` or `git reset --soft HEAD~1`; with the guard in place that promise is sound, and the doc should say so explicitly rather than leaving the reader to infer it.
- [ ] `docs/features/README.md` — no new row needed (`session-isolation.md` is already indexed); verify its one-line description still reads correctly after the edit.

### External Documentation Site

Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation

- [ ] Rewrite the `preserve_uncommitted_worktree_changes` docstring's numbered mechanism list to include the wipe check as step 2, between the status read and `git add -A`, and state the ordering constraint (before staging) with the reason.
- [ ] Add a comment at the guard site recording why the insertions-ratio predicate was rejected, citing the incident's 223,142 insertions. This is the single most likely thing for a future reader to "simplify."
- [ ] Docstring the two new `PerformanceSettings` fields as provisional, matching the `max_content_filename_bytes` precedent immediately above them.

## Success Criteria

- [ ] `preserve_uncommitted_worktree_changes` refuses and returns `{"preserved": False, "refused": ...}` for a worktree missing a directory tracked at HEAD, writing no commit and no ref.
- [ ] It refuses when a majority of tracked files are absent from disk and the absolute floor is met, even when untracked artifacts would have produced insertions under `git add -A` (the incident's actual shape).
- [ ] It still preserves every legitimately dirty tree the existing suite covers: tracked edits, staged edits, untracked-only, and large deletions accompanied by real work.
- [ ] The worktree index is unmodified after a refusal (`git diff --cached --name-only` empty), proving the guard runs before `git add -A`.
- [ ] Both producers are covered by the one change: `_cleanup_stale_worktree` (`:948`) and `remove_worktree` (`:1660`).
- [ ] `refs/session-wip/{slug}` names the branch the WIP commit actually landed on, with a fallback to the directory name on detached HEAD.
- [ ] A refusal logs at ERROR under `[worktree-wip-refused-wipe]` with slug, reason, and counts; a guard-computation failure logs at WARNING and falls through to today's behavior.
- [ ] Tests pass (`/do-test` — `scripts/pytest-clean.sh tests/unit/worktree_manager/`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No agent integration wiring needed — asserted by the absence of a new `[project.scripts]` entry in the diff.
- [ ] No xfail conversions required — `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/unit/worktree_manager/` returns nothing, so this bug has no expected-failure marker to convert.

## Team Orchestration

The lead agent orchestrates and never builds directly.

### Team Members

- **Builder (guard)**
  - Name: `guard-builder`
  - Role: the wipe-refusal guard and the ref-slug fix inside `preserve_uncommitted_worktree_changes`, plus the two `PerformanceSettings` fields
  - Agent Type: builder
  - Domain: concurrency (paste the async/concurrency rules from `DOMAIN_FRAMING.md` — the guard operates on a directory another process may be actively deleting)
  - Resume: true

- **Test engineer (regression)**
  - Name: `wipe-test-engineer`
  - Role: the eight new tests in `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py`, including the fixture worktree whose tracked files are deleted on disk
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `guard-validator`
  - Role: verifies the guard cannot be bypassed, that the index is untouched on refusal, and that every pre-existing test in the file still passes unmodified except the one flagged UPDATE
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `session-isolation-doc`
  - Role: `docs/features/session-isolation.md` and the docstring rewrite
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

## Open Questions

_placeholder_
