---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3167
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-09-05T13:16:40Z
---

# Auto-preserve refuses a half-deleted worktree

## Problem

The one function whose entire purpose is preventing data loss committed a wipe. On 2026-09-05, `preserve_uncommitted_worktree_changes` ran against a session worktree whose tracked files had already been removed from disk, read the resulting maximally-dirty tree as "uncommitted work," and committed it: 3,914 files changed, 223,142 insertions, **902,840 deletions**, landing on `session/dev-a4e15370` under the subject `WIP: auto-preserved before teardown`. `tests/`, `bridge/`, `agent/`, `config/` and `docs/` were gone from the branch head. Three further preserve passes then stacked no-op WIP commits on top, because relative to the committed wipe the tree was now clean, burying the destructive commit under innocuous ones.

Nothing catches this. `git merge-base --is-ancestor origin/main HEAD` still passes: the branch is strictly *ahead* of main, it just deletes most of it. `git status` is clean. `--no-verify` skips every commit hook. The commit subject reads like a routine safety net. The reporter noticed only because `sed` could not find a test file they had just been reading, and `git ls-files | wc -l` returned 1,491 against main's several thousand.

The same wipe is written to `refs/session-wip/{slug}`, so the advertised recovery path preserves the destruction rather than the work. The docstring tells a human to run `git reset --soft HEAD~1` to restore the dirty tree; doing that on a wipe commit makes the deletion the working state.

**Current behavior:**

`preserve_uncommitted_worktree_changes` (`agent/worktree_manager.py:1508` at `ffda9fc86`) tests only whether `git status --porcelain` is non-empty, then unconditionally runs `git add -A` and commits. It never asks *what* is dirty. A worktree caught mid-teardown is maximally dirty, so the guard meant to skip a clean tree instead waves a total deletion straight through.

**Desired outcome:**

`preserve_uncommitted_worktree_changes` never commits a deletion it detects as a wipe. When the worktree is missing a directory that HEAD tracks, it stages only the additions and edits that coexist with the wipe, commits those, and leaves the deletions in the working tree for the force-remove to discard. When there is nothing but the wipe, it writes no commit and no ref and leaves the index untouched. Either way it logs loudly at ERROR with enough detail to reconstruct the event after the worktree is gone, and lets teardown proceed. Legitimate dirty trees, including refactors that delete files inside directories that survive on disk, are preserved exactly as they are today.

## Freshness Check

**Re-verified at:** `ffda9fc86` (`origin/main`, 2026-09-05, during the post-critique revision pass). Every anchor below was re-derived by symbol against this commit, not copied forward.
**Issue filed at:** 2026-09-05T03:25:53Z
**Disposition:** Minor drift — one commit landed on the module after filing, shifting every line citation by roughly 100 lines and adding a third worktree-removal entry point. The defect and the fix shape are unchanged.

**The drift: #3162 closed after this plan's first draft.** Issue #3162 ("worktree-gc: reap stale `nightly-triage-*` branches and worktrees") closed at 2026-09-05T12:52:41Z via `55ad9ac89`, which is an ancestor of the current head. It touches both files this plan reads (`agent/worktree_manager.py` +102 lines, `agent/session_revival.py` +41) and adds `reap_idle_worktree()` — a third worktree-removal entry point in the same module. The first draft of this plan asserted "#3162 still open, no code overlap" and "no commits since filing"; both statements were false at its own head. Corrected here.

**`reap_idle_worktree` needs no guard, and this is deliberate, not an oversight.** It lives at `agent/worktree_manager.py:1007` and refuses on a non-empty `git status --porcelain` before doing anything else, so it never calls `preserve_uncommitted_worktree_changes` at all. A half-deleted tree reads as maximally dirty there and the lane is kept. Its docstring already reasons about #3167 explicitly ("The clean-tree guard is also what keeps this path clear of issue #3167"), and `tests/unit/worktree_manager/test_worktree_manager_cleanup.py::TestReapIdleWorktree::test_half_deleted_tree_is_kept_and_never_preserved` pins that behavior. This plan touches neither the function nor that test.

**Anchors re-derived at `ffda9fc86`** (cited here for orientation; task bodies below cite by *symbol*, because line numbers in this module have now drifted twice):

| Symbol | Line | Note |
|---|---|---|
| `preserve_uncommitted_worktree_changes` (def) | 1508 | the producer |
| its `_cleanup_stale_worktree` call site | 972 | Path A, immediately before `git worktree remove --force` |
| its `remove_worktree` call site | 1762 | Path B |
| `_cleanup_stale_worktree` (def) | 903 | |
| path-containment guard (#880) | 942 | |
| fallback `shutil.rmtree` | 1001 | |
| `reap_idle_worktree` (def) | 1007 | new in `55ad9ac89`; never reaches preserve |
| `create_worktree` (def) | 1370 | |
| its `_cleanup_stale_worktree(...)` call | 1425 | |
| `cleanup_stale_branches` (`agent/session_revival.py`) | 198 | |
| `@patch(...preserve_uncommitted_worktree_changes)` in `test_worktree_manager_cleanup.py` | 466, 544 | |
| `monkeypatch.setattr(wm, "preserve_uncommitted_worktree_changes", ...)` in the same file | 101, 122 | inside `TestReapIdleWorktree`, added by `55ad9ac89` |
| `VALID_SLUG_RE` | 21 | `^[a-zA-Z0-9][a-zA-Z0-9._-]*$` |
| `PerformanceSettings` / `max_content_filename_bytes` / its `model_post_init` | 133 / 146 / 160 | `config/settings.py` |
| `**Why a WIP commit + named ref, not `git stash`.**` paragraph | 263 | `docs/features/session-isolation.md` |
| the docstring's counter-statement on `refs/stash` | 1518–1527 | `agent/worktree_manager.py` |

**Claims re-checked and still true:**

- The producer still tests only `git status --porcelain` non-emptiness and then runs `git add -A` unconditionally. There is no shape check anywhere between them. The defect is present at `ffda9fc86`.
- Commits `ba3ea72e9`, `805983e36`, `c004ff37d`, `35c76c461` — **gone.** Not resolvable in this checkout; the reporter's `git reset --hard origin/main` plus `--force-with-lease` recovery removed them. The defect is confirmed by code reading and by the spikes below, not by re-inspecting those objects.
- `refs/session-wip/*` — **empty.** `git for-each-ref refs/session-wip/` returns zero refs on this machine.
- **Second call site the issue did not name:** `remove_worktree` also calls preserve before its own force-remove. A fix scoped to `_cleanup_stale_worktree` alone would leave that path exposed, which is why the guard belongs inside the preserve function itself.
- #2137 — closed 2026-07-17; its PR #2150 introduced this function. Still the governing design; this plan hardens it.
- #3166 (bridge SIGTERM restart loop) — **still open.** The amplifier, independent of this fix.
- **Active plans overlapping this area:** none. `grep -l "worktree_manager\|preserve_uncommitted" docs/plans/*.md` matches only this plan.

**Notes:** The one substantive premise correction to the issue itself is in its "Diagnostic Output" timing paragraph — see **Why Previous Fixes Failed**. `cleanup_stale_branches` is not the mechanism. The bridge-boot correlation is real; the branch sweep is not what reaches preserve.

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
- **Impact on plan**: The check runs **before** `git add -A`. (Spike-4 later replaced porcelain parsing with the `ls-files` plumbing pair, but the ordering conclusion this spike established is what carried into the design.) This is a strict improvement on the issue's Next Steps bullet, which proposed computing `--numstat` *after* staging. Staging first means a refusal leaves a fully-staged wipe sitting in the worktree index — strictly worse than today whenever the subsequent force-remove fails and the directory survives. Reading `status` costs one already-executed subprocess and mutates nothing.

### spike-2: Is there a signal cheaper and less tunable than a ratio?

- **Assumption**: "Detecting missing tracked top-level directories requires hardcoding `tests/ bridge/ agent/ config/`, as the issue suggests."
- **Method**: prototype
- **Finding**: **No hardcoding needed.** `git ls-tree --name-only -d HEAD` enumerates the tracked top-level directories directly from the commit. Iterating them and testing `Path(worktree_dir / d).is_dir()` returned exactly `agent`, `bridge`, `tests` as missing — the three that were deleted, and nothing else. The signal is repo-agnostic, needs no configuration, and has no threshold to tune.
- **Confidence**: high
- **Impact on plan**: this is the signal the change ships, and after the revision pass it is the *only* one. A worktree missing a directory that HEAD tracks is definitionally not a tree whose deletions should be committed — no ratio, no threshold, no env var, no false-positive story to argue about. (The first draft paired it with a proportional ratio as a secondary net; spike-6 and the critique's scope finding cut that.)

### spike-3: Would the issue's proposed insertions-ratio predicate have caught the reported incident?

- **Assumption**: "Refusing when staged deletions overwhelm staged insertions (the Next Steps bullet) is a sound predicate."
- **Method**: prototype — reproduce the incident's *shape*, not just a bare wipe: tracked directories deleted from disk **and** untracked build artifacts present, which is what a real lane worktree looks like.
- **Finding**: **No — it would not have fired.** With five untracked artifacts under `.venv/lib/` alongside nine deleted tracked files, `git add -A` followed by `git diff --cached --shortstat` reported `14 files changed, 5 insertions(+), 9 deletions(-)`. The insertions are real and come entirely from staging untracked junk. This is exactly the reported commit's signature: 902,840 deletions arrived **with 223,142 insertions**, which no "insertions are a negligible fraction" test treats as a wipe.
- **Confidence**: high — the incident's own numbers are the confirming evidence, and the reproduction shows the mechanism that produces them.
- **Impact on plan**: the insertions-ratio predicate is rejected outright, and the rejection is pinned by a test so nobody reintroduces it. Any predicate that reasons about *content* volume is defeated by untracked build artifacts; the signal has to be about *which paths exist*, which is what the structural check answers.

### spike-4: Is there a NUL-safe primitive for "tracked but missing from disk"?

- **Assumption**: "Counting deleted files requires parsing `git status --porcelain`, with its rename two-path entries and quoting rules."
- **Method**: prototype
- **Finding**: `git ls-files --deleted -z` returns exactly the tracked-and-absent set, and `git ls-files -z` the full tracked set. Both are plumbing, both NUL-delimited, neither mutates the index, and neither has porcelain's rename/quoting ambiguity.
- **Confidence**: high
- **Impact on plan**: the deleted-file set is available as plumbing, with no porcelain parsing anywhere. (The proportional guard this spike was run for is no longer shipping — see spike-6 — but the same `ls-files --deleted -z` set is what the additive-only preserve filters against.)

### spike-5: Can preserve keep coexisting real work while refusing to commit the deletions?

- **Assumption**: "A wipe detection has to be all-or-nothing: either commit the tree as-is or commit nothing." (The first draft of this plan assumed exactly this, and it is what the critique's first blocker rejected.)
- **Method**: prototype — the *mixed* shape the first draft never modelled: one tracked top-level directory gone from disk **plus** genuine edits elsewhere.
- **Finding**: **No, it does not have to be all-or-nothing, and git has the exact primitive.** Fixture: `agent/ bridge/ docs/ tests/` tracked at HEAD, `docs/` removed from disk, `tests/f1.py` edited, `tests/f4.py` added untracked. First, the critique's reproduction confirmed: `ls-tree --name-only -d HEAD` lists `docs`, which is absent on disk, so the structural check fires; deleted=3 against tracked=12 is 25%, so a proportional check stays silent; and under the first draft's plain refusal the edit and the new file are destroyed by the force-remove that follows. Then the fix: `git ls-files --modified --others --exclude-standard -z` minus the `git ls-files --deleted -z` set yields exactly `{tests/f1.py, tests/f4.py}`. Staging that set and committing produced `2 files changed, 2 insertions(+)` with **zero deletions**, `docs/` still tracked at the new HEAD, and the three ` D docs/*` entries still sitting unstaged in the working tree where the force-remove discards them.
- **Confidence**: high — run end to end on git 2.50.1 (Apple Git-155), including the commit and the resulting diffstat.
- **Impact on plan**: this replaces the refusal with a **downgrade to additive-only preserve**, and it is the central design change of this revision. Two mechanical corrections the run surfaced, both of which would have cost a builder a debugging round:
  - **`git add` has no `-z`.** The critique's suggested `git add --pathspec-from-file=- -z --` exits non-zero with ``error: unknown switch `z` ``. The correct flag is `--pathspec-file-nul`. Verified both ways.
  - **An empty candidate set must short-circuit.** `git add --pathspec-from-file=<empty> --pathspec-file-nul --` exits 0 and stages nothing (printing an `addEmptyPathspec` hint), after which `git commit` fails with "nothing to commit" and the outer handler would log a spurious `[worktree-wip-preserve-failed]`. On a pure wipe the candidate set *is* empty, which is the common case, so the code must test the set and return before staging.

### spike-6: Does the proportional guard survive a partially-staged wipe?

- **Assumption**: "`git ls-files --deleted` is a stable signal for 'tracked files gone from disk'." (Raised as a concern by the critique.)
- **Method**: prototype
- **Finding**: **No — the index blinds it.** With `docs/` (3 of 12 tracked files) removed from disk, `ls-files --deleted`=3 and `ls-files`=12. After a bare `git add -A` — reachable today, because `add -A` succeeds and the subsequent `commit` can fail — the same tree gives `ls-files --deleted`=**0** and `ls-files`=**9**. The proportional guard cannot fire. The structural signal is unaffected: `ls-tree --name-only -d HEAD` still lists all four directories, because it reads HEAD rather than the index. The staged deletions are recoverable via `git diff --cached --diff-filter=D --name-only HEAD` (returns 3) and the true tracked count via `git ls-tree -r --name-only HEAD` (returns 12).
- **Confidence**: high
- **Impact on plan**: the proportional guard is **dropped from this change** rather than repaired. It was declared secondary yet carried the change's only two tunables, its only new config and env surface, and two of its eight tests, while its target case (a partial wipe leaving every tracked top-level directory present) has never been observed and the reported incident is caught by the structural check alone. This spike is recorded so that if a real partial wipe ever justifies adding it, whoever does so reads HEAD (`ls-tree -r`, `diff --cached --diff-filter=D`) instead of the index — see **Rabbit Holes**.

## Data Flow

Two paths reach the producer. Both end in the same unguarded commit.

**Path A — stale worktree found while creating a new one** (the path the issue names)

1. **Entry point**: `create_worktree(repo_root, slug)` — `agent/worktree_manager.py:1370`. Called on session start / revival.
2. `worktree_dir.exists()` is False, so creation proceeds. `_find_worktree_for_branch(repo_root, "session/{slug}")` finds the branch checked out at some *other* `.worktrees/` path.
3. `_cleanup_stale_worktree(repo_root, branch_name, existing_wt)` — line 1425.
4. Path-containment guard (line 942) passes: the path is under `.worktrees/`. Directory exists, so the force-remove branch is taken.
5. `stale_slug = wt.name`; `preserve_uncommitted_worktree_changes(repo_root, stale_slug, wt)` — line 972.
6. **The gap**: `git status --porcelain` in a gutted `wt` returns a long list of ` D` lines. Non-empty, so the clean-tree early return does not fire. `git add -A` stages every deletion. `git commit --no-verify --no-gpg-sign` writes it — **onto whatever branch `wt` has checked out**, which is `session/{slug}`, not `session/{stale_slug}`.
7. `git -C {repo_root} update-ref refs/session-wip/{stale_slug} {sha}` — the ref is filed under the *directory name*, the commit landed on a *different branch*. Recovery pointer and recovered content disagree.
8. **Output**: `git worktree remove --force` proceeds. The wipe is now the head of a live session branch, and the durable ref points at it.

**Path B — ordinary teardown** (unnamed in the issue, equally exposed)

1. **Entry point**: `remove_worktree(repo_root, slug)` — line 1653. Called from `cleanup_after_merge` and from session teardown.
2. Refuse-busy guard (#1357) and live-process guard pass, or `force=True` overrides them.
3. `preserve_uncommitted_worktree_changes(repo_root, slug, worktree_dir)` — line 1762. Same unguarded body, same outcome. Here slug and branch do agree.
4. **Output**: same wipe commit, same ref.

**Path C — the idle-lane reaper** (`reap_idle_worktree`, `agent/worktree_manager.py:1007`, added by `55ad9ac89` for #3162) **does not reach the producer and needs no change.** It refuses on a non-empty `git status --porcelain` before any other work, so a half-deleted tree reads as dirty, the lane is kept, and preserve is never called. Its docstring says so explicitly and `TestReapIdleWorktree::test_half_deleted_tree_is_kept_and_never_preserved` pins it. Named here so a reader auditing the module's teardown surface does not have to rediscover it.

**Where the guard goes**: step 6 in Path A is step 3 in Path B — the identical function body. Putting the check inside `preserve_uncommitted_worktree_changes`, between the `status` read and the staging step, covers both live paths with one change and leaves every caller's contract unchanged (the function already returns `{"preserved": False, "errors": [...]}` and is documented as never raising into teardown).

**Where the deletions came from** is deliberately *not* on this path. Whatever gutted the tree — a `shutil.rmtree` that raised partway through the `_cleanup_stale_worktree` fallback at line 1001, a `git worktree remove --force` that deleted files and then failed, or two concurrent teardown passes racing on the same directory — the observable at step 6 is identical and the response is correct against all three.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2150 (#2137) | Added `preserve_uncommitted_worktree_changes`: auto-WIP-commit + `refs/session-wip/{slug}` before every force-remove. Added the destructive-git PreToolUse hook as the agent-facing half. | Treated "dirty" as a single condition. `git status --porcelain` being non-empty was taken as proof that work exists, when it is equally consistent with work having been destroyed. The one place a filter belonged — between reading status and staging everything — is the one place with no logic in it. |
| PR #2150 (#2137) | Used `--no-verify` on the WIP commit, to keep pre-commit hooks from hanging teardown. | Correct for the stated reason, and it removed the last check that could have caught a 902,840-deletion commit. Nothing else in the path inspects the diff. |
| #1646 (unmerged-branch guard) | Refuses to delete a session branch carrying unmerged commits. | Worked exactly as designed, in the wrong direction. Once the wipe was committed it *was* unmerged work, so the guard protected it. A safety mechanism for committed work cannot distinguish a commit worth keeping from a commit that should never have been made. |

**Root cause pattern:** every guard in this subsystem answers "is there something here?" and none answers "is what's here plausible?". `status --porcelain` non-empty, `merge-base --is-ancestor` passing, `merged_via_tree` false — each is a presence check, and a wipe satisfies all three. The fix is the first shape-of-the-change check on the path.

**A correction to the issue's own diagnosis.** The Diagnostic Output section attributes the `bridge boot → WIP commit` correlation to `cleanup_stale_branches`. That function (`agent/session_revival.py:198`) lists `session/*` branches, checks their age against `max_age_hours`, and calls `safe_delete_branch`. It never touches a worktree and never reaches preserve. The correlation is real; the mechanism named for it is not. The only caller of `_cleanup_stale_worktree` is `create_worktree` at line 1425 — so what a bridge boot does is *create* worktrees, and it is the stale-worktree recovery inside creation (Path A above) that reaches the producer. Anyone building from the issue's stated mechanism would have instrumented the wrong function.

## Architectural Impact

- **New dependencies**: none. One additional `git` read on the common path (`ls-tree -d HEAD`), plus two more and a temporary file only on the wipe path, inside a function that already shells out five times.
- **Interface changes**: none breaking. `preserve_uncommitted_worktree_changes` keeps its signature and its documented return shape, and adds one key, `"refused": <reason>`, for callers and tests that want to distinguish a declined wipe from a git failure. Every existing caller ignores the return value entirely, so nothing downstream changes.
- **Coupling**: unchanged. The check is local to one function; no new imports across module boundaries, and **no new configuration surface at all** — no settings field, no env key.
- **Data ownership**: unchanged.
- **Reversibility**: trivial. Deleting the guard block restores today's behavior exactly. No migration, no persisted state, no schema.

The one thing that genuinely changes is the function's *contract on a half-deleted tree*: it now commits the additions and edits without the deletions, where it previously committed the deletions too. On a pure wipe it commits nothing at all. That is the point, and the No-Gos and Risks sections below bound it.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (the shape is settled by spikes 1–3; nothing needs alignment)
- Review rounds: 1

One check plus one additive-staging branch inside one function, no new configuration, and a set of tests in a file that already has the fixtures for them. The revision pass *removed* scope (the proportional guard and both tunables) at the same time as it added the additive-preserve branch, so the appetite holds. The investigation that would justify a larger appetite — root-causing what gutted the tree — is explicitly not in scope, because the response is correct against every candidate mechanism.

## Prerequisites

No prerequisites — this work has no external dependencies. Everything it touches is `git` plumbing already required by the module.

## Solution

### Key Elements

- **Wipe detector (structural, and the only one shipping)**: answers "is this worktree missing a directory that HEAD says it tracks?" A worktree in that state is definitionally not a tree whose deletions should be committed. No threshold, no configuration, no env key, no false-positive story to argue about. `git ls-tree --name-only -d HEAD` supplies the list; the check is `Path(worktree_dir / name).is_dir()`.
- **Additive-only preserve on detection**: the response is *not* "commit nothing." It is "commit the additions and the edits, and leave the deletions in the working tree where the force-remove discards them." Coexisting real work is still preserved; the wipe is not. This is the change the critique's first blocker forced, and spike-5 verifies it end to end.
- **Enriched refusal record**: an ERROR log under `[worktree-wip-refused-wipe]` carrying slug, branch, worktree HEAD sha, the sorted missing directory names, and the counts of what was preserved versus skipped — captured *before* the force-remove destroys the evidence.
- **Guard placement**: inside `preserve_uncommitted_worktree_changes`, between the `status --porcelain` read and the staging step. Both live producers (`_cleanup_stale_worktree` and `remove_worktree`) inherit it from one edit; `reap_idle_worktree` never reaches this code.

### Flow

Teardown begins → preserve called → `git status --porcelain` non-empty → **wipe check** → *no missing tracked directory*: `git add -A` → WIP commit → `refs/session-wip/{slug}` → force-remove (today's behavior, unchanged)

Teardown begins → preserve called → `git status --porcelain` non-empty → **wipe check** → *a tracked directory is missing from disk* → compute the additive-only pathspec (`ls-files --modified --others --exclude-standard` minus `ls-files --deleted`) →

- **set non-empty**: stage exactly those paths → WIP commit carrying additions and edits and **zero deletions** → `refs/session-wip/{branch}` → ERROR log with the full record → force-remove proceeds. Real work preserved, wipe not committed.
- **set empty** (the pure-wipe case, and the reported incident's shape): **no staging, no commit, no ref** → ERROR log with the full record → force-remove proceeds. Branch head never moves.

### Technical Approach

**The structural primitive:**

| Signal | Command | Fires when |
|---|---|---|
| Missing tracked directory | `git -C <wt> ls-tree --name-only -d HEAD` | any listed name is not a directory on disk in the worktree |

It reads HEAD, not the index, which is what makes it survive the partially-staged case spike-6 found (a prior `git add -A` erases `ls-files --deleted` but leaves `ls-tree -d HEAD` untouched). It mutates nothing.

**The additive-only pathspec, when the structural signal fires.** Three plumbing reads and a set difference:

```
candidates = split_nul(git -C <wt> ls-files --modified --others --exclude-standard -z)
deleted    = set(split_nul(git -C <wt> ls-files --deleted -z))
additive   = [p for p in candidates if p not in deleted]
```

If `additive` is empty, return without staging or committing. Otherwise write the NUL-joined paths to a temporary file and stage exactly them:

```
git -C <wt> add --pathspec-from-file=<tmpfile> --pathspec-file-nul --
```

**`git add` takes `--pathspec-file-nul`, not `-z`.** `git add --pathspec-from-file=- -z --` exits non-zero with ``error: unknown switch `z` `` — `-z` is a `git ls-files` / `git diff` flag, and `git add` spells the same thing differently. Verified both ways in spike-5. `--pathspec-from-file=-` reading stdin is also available, but a temporary file keeps the call inside the module's existing `subprocess.run(capture_output=True)` idiom rather than introducing an `input=` variant; either is acceptable, the flag name is the part that is not negotiable.

Never `git add -A` on this path: it restages the very deletions the check just declined.

**Why not simply refuse and commit nothing.** That was the first draft's answer and it is a regression against today's behavior in the mixed case. With `docs/` gone from disk but `tests/f1.py` edited and `tests/f4.py` newly written, today's unguarded code commits everything — destructive, but the additions are recoverable by cherry-picking them out of the WIP commit. A bare refusal lets `git worktree remove --force` proceed and the edit and the new file are gone with no backstop at all, which is precisely the loss #2137 exists to prevent, reintroduced from the other side. The additive-only path is strictly better than both: the wipe is never committed *and* the real work is.

**Why the check runs before any staging.** The signal is available without staging, so nothing is gained by staging first and everything is risked: a check computed after `git add -A` leaves a fully-staged wipe in the worktree index, which is worse than today's behavior in exactly the case that matters (the subsequent force-remove fails and the directory survives with a staged wipe waiting for the next process to commit it). Spike-6 adds a second reason: after `add -A` the deletion signal is partly erased from the index anyway.

**Why the insertions-ratio predicate the issue proposes is rejected.** The issue's Next Steps suggest computing `git diff --cached --numstat` after `git add -A` and refusing when deletions dwarf insertions. Spike-3 shows this **would not have fired on the reported incident.** That commit carried 223,142 insertions alongside its 902,840 deletions — because `git add -A` also stages untracked build artifacts (`.venv/`, `.pyc` files, whatever the lane left behind), and those count as insertions. Reproducing the incident's shape in a throwaway repo produced `14 files changed, 5 insertions(+), 9 deletions(-)` — a real insertions figure that defeats any "insertions are a negligible fraction" test.

**No new configuration.** The proportional guard and its two `PerformanceSettings` fields are dropped (spike-6, and the critique's scope finding). This change adds no settings field, no `PERFORMANCE__` env key, no `.env.example` entry, and no tunable number of any kind. The structural signal is exact.

**Return shape.** Both wipe branches reuse the documented result dict and add one key:

```
# additive work preserved, deletions declined
{"preserved": True, "was_clean": False, "refused": "missing-tracked-dirs",
 "sha": <sha>, "ref": "refs/session-wip/{name}", "errors": []}

# pure wipe, nothing to preserve
{"preserved": False, "was_clean": False, "refused": "missing-tracked-dirs",
 "ref": "refs/session-wip/{name}", "errors": ["<detail with counts>"]}
```

`refused` names *what was declined* (the deletions), independently of whether additive work was preserved alongside. Every current caller discards the return value, so nothing downstream changes; the key exists for the regression tests and any future caller that wants to distinguish this from a git failure.

**The refusal record, captured before the evidence is destroyed.** A refusal is followed within milliseconds by `git worktree remove --force`, so anything not logged at that moment is unrecoverable. The `[worktree-wip-refused-wipe]` ERROR record carries: `slug`, `branch` (from `rev-parse --abbrev-ref HEAD`, already read for the ref-slug fix below, so it costs nothing), `head` (`rev-parse HEAD`), `missing` (the sorted missing directory names), `preserved_paths` (count of additive paths staged, 0 on a pure wipe), and `deleted_paths` (count of tracked files absent from disk). The tag is distinct from `[worktree-wip-preserve-failed]` so an operator can tell "we declined to commit a wipe" from "git broke."

**Failure of the check itself fails open.** If `ls-tree` or `ls-files` errors or times out, the function logs at WARNING under `[worktree-wip-guard-failed]` and proceeds as it does today. Rationale: this function's hard contract is that it never raises into teardown and never hangs it. A read failure is not evidence of a wipe, and treating it as one would convert a git hiccup into a silent loss of the backstop for legitimate work. The check is a net, not a gate.

**The slug/branch mismatch (found during recon) is fixed in the same edit.** `_cleanup_stale_worktree` passes `stale_slug = wt.name` — the foreign directory name — while the WIP commit lands on whatever branch that worktree has checked out. The ref then names a slug the commit was never made on. Fix: resolve the worktree's branch with `git -C <wt> rev-parse --abbrev-ref HEAD`; when it matches `session/<name>`, use `<name>`, otherwise fall back to the `slug` argument. **The `session/` prefix match is the gate, not `VALID_SLUG_RE`** — on a detached HEAD `rev-parse --abbrev-ref HEAD` returns the literal string `HEAD`, which matches `VALID_SLUG_RE` (`^[a-zA-Z0-9][a-zA-Z0-9._-]*$`, `agent/worktree_manager.py:21`) and would produce `refs/session-wip/HEAD`. Verified: a detached worktree returns exactly `HEAD`. The regex stays as a second gate on the extracted `<name>`, never as the only one.

## Failure Path Test Strategy

### Exception Handling Coverage

`preserve_uncommitted_worktree_changes` has one broad `except Exception` at the bottom of its body, which is the documented non-blocking contract (it logs at ERROR with the `[worktree-wip-preserve-failed]` tag and returns the error in the result dict). `test_git_failure_returns_error_dict_and_never_raises` already asserts that observable behavior by patching `subprocess.run` to raise.

- [ ] The new code adds no new `except Exception: pass`. Its own failure mode is a narrow catch around the `ls-tree` / `ls-files` reads that logs at WARNING under `[worktree-wip-guard-failed]` and falls through to today's behavior.
- [ ] Add `test_guard_computation_failure_falls_open_and_warns` — patch the `ls-tree` read to fail, assert the function still preserves a legitimately dirty tree and that a WARNING naming the guard was logged. This is the test that proves fail-open is deliberate rather than accidental.

### Empty/Invalid Input Handling

- [ ] **Unborn HEAD** (a worktree whose branch has no commits): `git ls-tree -d HEAD` fails. Covered by the fail-open path above; add an explicit case so the behavior is pinned rather than incidental.
- [ ] **Empty additive set on a wipe** (the pure-wipe case): the code must return *before* staging. Spike-5 showed that `git add --pathspec-from-file=<empty> --pathspec-file-nul --` exits 0 having staged nothing, after which `git commit` fails with "nothing to commit" and the outer handler logs a misleading `[worktree-wip-preserve-failed]`. `test_pure_wipe_writes_no_commit_and_no_ref` pins the short-circuit.
- [ ] **Repository with no tracked directories at HEAD** (all files at top level): `ls-tree -d HEAD` returns empty, so the check never fires and today's behavior runs. Add a case.
- [ ] **Detached HEAD in the stale worktree**: the branch-resolution half of the fix must fall back to the `slug` argument rather than writing `refs/session-wip/HEAD`. `rev-parse --abbrev-ref HEAD` returns the literal `HEAD`, which passes `VALID_SLUG_RE`, so the `session/` prefix match is what must gate it. Test it.
- [ ] **Untracked-only dirty tree**: no directory missing, so the check does not fire and preservation proceeds. Already covered by `test_untracked_only_is_preserved`; re-run it as a non-regression.

### Error State Rendering

There is no user-visible surface here; the observable is the log line and the returned dict.

- [ ] The refusal log must be ERROR (not WARNING) and must carry slug, branch, worktree HEAD sha, the sorted missing directory names, and the preserved/deleted path counts. A silent refusal is the same class of bug as the silent commit, just in the other direction — and the worktree is destroyed within milliseconds of the log line, so anything omitted is unrecoverable. Assert on `caplog.records[-1]` in the refusal tests, the way `test_dirty_tree_preserved_in_named_ref_and_wip_commit` asserts on `[worktree-wip-preserved]`.
- [ ] Give it its own greppable tag, `[worktree-wip-refused-wipe]`, distinct from both `[worktree-wip-preserve-failed]` and `[worktree-wip-guard-failed]`.

## Test Impact

All existing tests for this function live in `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py`. Its fixtures (`_init_git_repo`, `_add_linked_worktree`, `_dirty`, `_git`) build real git repos with real linked worktrees, so the regression fixture is an addition to that file, not new scaffolding.

- [ ] `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py::TestPreserveUncommittedChanges::test_dirty_tree_preserved_in_named_ref_and_wip_commit` — **no change expected.** `_dirty()` modifies one tracked file, stages a second, and adds an untracked one: no tracked directory goes missing, so the check does not fire. Re-run as the primary non-regression proof that legitimate work is still preserved by the unchanged `add -A` path.
- [ ] `...::test_clean_tree_is_noop_and_creates_no_ref` — **no change expected.** The clean-tree early return runs before the check.
- [ ] `...::test_untracked_only_is_preserved` — **no change expected.**
- [ ] `...::test_git_failure_returns_error_dict_and_never_raises` — **UPDATE.** It patches `subprocess.run` to raise on *every* call, which now includes the `ls-tree` read. Confirm the assertion still holds (`preserved: False`, error dict, `[worktree-wip-preserve-failed]` logged) and that the narrow guard catch does not swallow the failure before the outer handler sees it. Adjust the assertion if the error message changes shape.
- [ ] `...::test_remove_worktree_preserves_dirty_tree_before_force_remove` — **no change expected.** Proves the change did not break Path B.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_cleanup.py` `@patch("agent.worktree_manager.preserve_uncommitted_worktree_changes")` at lines 466 and 544 — **REVIEW, likely no change.** Both patch the function wholesale, so they are insulated from its internals. Confirm neither asserts on a return-value shape that changed.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_cleanup.py::TestReapIdleWorktree` (the `monkeypatch.setattr` sites at lines 101 and 122, added by `55ad9ac89` for #3162) — **no change expected, and re-run deliberately.** `test_half_deleted_tree_is_kept_and_never_preserved` asserts the reaper never calls preserve on a #3167-shaped tree. It must stay green: this plan does not touch `reap_idle_worktree`, and a break there would mean the change leaked outside its intended surface.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_creation.py:38` — **no change expected.** Patches `_cleanup_stale_worktree` entirely.
- [ ] `tests/unit/test_settings.py` — **no change.** The revision dropped both `PerformanceSettings` fields, so this change adds no settings surface for it to cover.

**New tests**, all in `test_worktree_manager_uncommitted.py`:

- [ ] `test_pure_wipe_writes_no_commit_and_no_ref` — fixture worktree with tracked top-level directories deleted on disk and nothing else dirty; assert `preserved is False`, `refused == "missing-tracked-dirs"`, the branch head sha is unchanged, `refs/session-wip/{slug}` does not exist, and the `[worktree-wip-refused-wipe]` ERROR record carries the missing directory names.
- [ ] `test_mixed_wipe_preserves_additions_and_drops_deletions` — **the test this revision exists for.** Spike-5's fixture: one tracked top-level directory removed from disk, one tracked file edited, one untracked file added. Assert `preserved is True`, `refused == "missing-tracked-dirs"`, and that the resulting commit's diff against its parent contains **zero deletions** while containing both the edit and the new file. Assert the removed directory is still tracked at the new HEAD.
- [ ] `test_wipe_with_untracked_artifacts_still_refuses_deletions` — the incident's shape (spike-3): tracked directories deleted **and** untracked build artifacts present, so `add -A` would report real insertions. Assert the deletions are not committed. This is the test that would fail under the issue's proposed insertions-ratio predicate, and it exists to keep anyone from "simplifying" the check back to that shape.
- [ ] `test_partially_staged_wipe_still_detected` — run `git add -A` on a half-deleted tree *before* calling preserve, then assert detection still fires. Pins spike-6: the structural signal reads HEAD, so the index cannot blind it. This is the test that would have failed under the proportional guard the revision dropped.
- [ ] `test_deletions_inside_a_surviving_directory_are_preserved_normally` — files deleted from within `tests/` while `tests/` itself survives on disk, alongside genuine edits; assert `preserved is True`, `refused` absent, and the deletions **are** in the commit. The false-positive boundary: a real refactor that deletes files is untouched by this change.
- [ ] `test_index_untouched_on_pure_wipe` — after the pure-wipe case, assert `git diff --cached --name-only` is empty. Pins the "check before staging" decision so a later refactor cannot quietly move it after `git add -A`.
- [ ] `test_guard_computation_failure_falls_open_and_warns` — see Failure Path Test Strategy.
- [ ] `test_ref_slug_follows_checked_out_branch` — a worktree directory named `foo` with `session/bar` checked out; assert the WIP ref is `refs/session-wip/bar`, matching the branch that received the commit. Pins the recon-found mismatch fix.
- [ ] `test_detached_head_falls_back_to_slug_argument` — assert the ref is `refs/session-wip/{slug}` and specifically **not** `refs/session-wip/HEAD`.

## Rabbit Holes

- **Root-causing what gutted the worktree.** Three mechanisms fit the evidence equally well: a `shutil.rmtree` that raised partway through the `_cleanup_stale_worktree` fallback (`agent/worktree_manager.py:1001`), a `git worktree remove --force` that deleted files and then failed, or two concurrent teardown passes racing on the same directory. Distinguishing them means instrumenting a path that fires only under a bridge restart storm, and the answer changes nothing: the response is correct against all three. Filed as a No-Go, not attempted here.
- **Fixing the bridge restart loop.** #3166 is the amplifier that turned one latent race into nine teardown passes. It is a separate issue with a separate investigation, and this fix does not wait on it.
- **Serializing teardown with a lock.** Tempting once you notice the race, and a large change to a module three subsystems call. If the guard proves insufficient in practice, that is the follow-up; it is not the first move.
- **Building recovery tooling for `refs/session-wip/*`.** There are zero such refs on this machine. Writing a resurrect/inspect CLI for a namespace that is currently empty is speculative work.
- **The proportional "majority of tracked files gone" guard.** Cut from this change during the revision pass. It was declared secondary yet carried the only two tunables, the only new config and env surface, and two of the tests, while its target case — a partial wipe leaving every tracked top-level directory nominally present — has never been observed, and the reported incident is caught by the structural signal alone. If a real partial wipe ever justifies adding it, **read HEAD, not the index**: spike-6 showed a prior `git add -A` drops `ls-files --deleted` from 4 to 0 and shrinks `ls-files` correspondingly, blinding any index-based ratio. The HEAD-based equivalents are `git ls-files --deleted -z` unioned with `git diff --cached --diff-filter=D --name-only -z HEAD` for the numerator and `git ls-tree -r --name-only -z HEAD` for the denominator, with a `tracked > 0` short-circuit before the division.
- **Broadening the `validate_no_destructive_git_in_worktree.py` hook.** Different half of the #2137 backstop, agent-facing rather than teardown-facing, and its blocked-shape set was deliberately scoped in that plan's No-Gos. Out of scope.
- **Auditing `refs/session-wip/*` across the fleet.** The audit is a one-line `git for-each-ref` an operator can run; here it returns zero. It is not a feature and there is nothing to ship for it.

## Risks

### Risk 1: The check fires on a legitimate refactor that deletes a whole top-level directory

**Impact:** A lane deleting an entire tracked top-level package while the replacement lives elsewhere trips the structural signal. Its deletions are then not committed into the WIP commit.

**Mitigation:** Bounded, and no longer a data-loss risk after the revision. The additive-only path still preserves every addition and every edit that lane made, so nothing it *wrote* is lost — only the record that it deleted a directory, which the branch's own committed history and `origin/main` still hold. The trigger is narrow: a directory tracked at HEAD must be **absent from disk entirely**, which a refactor deleting files inside a package does not produce (the directory survives with its remaining files) — `test_deletions_inside_a_surviving_directory_are_preserved_normally` pins that boundary. And the case is logged at ERROR with a dedicated tag, the branch, the head sha, and the missing names, so it is visible rather than silent.

### Risk 1b: A refusal destroys coexisting uncommitted work

**Impact:** This was the shape the first draft of this plan actually shipped, and the critique's first blocker. With `docs/` gone from disk but `tests/f1.py` edited and `tests/f4.py` newly written, a bare refusal let `git worktree remove --force` proceed and both were destroyed with no backstop — strictly worse than today's unguarded behavior, where they at least land in the WIP commit and can be cherry-picked out.

**Mitigation:** Eliminated by design rather than accepted. The additive-only preserve (spike-5) commits exactly those paths and no deletions. `test_mixed_wipe_preserves_additions_and_drops_deletions` asserts both halves: the edit and the new file are in the commit, and the commit's diff against its parent contains zero deletions.

### Risk 2: Fail-open on check-computation failure means a wipe slips through

**Impact:** If `git ls-tree` or `git ls-files` errors or times out at exactly the wrong moment, the check is bypassed and today's behavior resumes — including the possibility of committing a wipe.

**Mitigation:** Accepted deliberately, with reasoning recorded. This function's hardest contract is that it never raises into teardown and never hangs it; converting a git hiccup into a refusal would trade a rare wipe for a routine loss of the backstop on legitimate work. The failure is logged at WARNING under `[worktree-wip-guard-failed]` so a recurring pattern is visible in the logs, and `test_guard_computation_failure_falls_open_and_warns` makes the choice explicit rather than incidental. Revisit only if the WARNING actually appears in production logs.

### Risk 3: A future refactor moves the guard after `git add -A`

**Impact:** A check computed after staging leaves a fully-staged wipe in the worktree index — worse than the status quo, because the next process to touch that worktree commits it without ever running preserve. Spike-6 adds a second failure mode: after `add -A` the deletion signal is partly erased from the index, so a moved check also reads degraded input.

**Mitigation:** `test_index_untouched_on_pure_wipe` asserts `git diff --cached --name-only` is empty after the pure-wipe case, which fails immediately if the check moves. A comment at the check site states the ordering constraint and why it exists.

### Risk 4: Someone "simplifies" the guard back to the insertions-ratio predicate

**Impact:** The predicate the issue originally proposed does not fire on the incident it was written for (spike-3). Reintroducing it would leave the bug fixed on paper and open in fact.

**Mitigation:** `test_wipe_with_untracked_artifacts_still_refuses_deletions` reproduces the incident's exact shape — deletions plus untracked artifacts producing real insertions — and fails under that predicate. The Solution section records why it was rejected, with the incident's own numbers.

## Race Conditions

### Race 1: A concurrent teardown pass gutting the worktree while preserve reads it

**Location:** `preserve_uncommitted_worktree_changes` (`agent/worktree_manager.py:1508`), against its two call sites at `:972` and `:1762`, plus the fallback `shutil.rmtree` at `:1001`.

**Trigger:** Two teardown passes target the same `.worktrees/{slug}` concurrently. Pass A's `git worktree remove --force` (or the fallback `rmtree`) begins deleting files; pass B's preserve reads a tree that is now partially gone. The bridge restart loop in #3166 supplied nine such passes in seventeen minutes. This is the leading candidate for what produced the reported commit, though the plan does not depend on it being the right one.

**Data prerequisite:** The check needs `git ls-tree -d HEAD` (reads the commit object) and, on the wipe path, `git ls-files` (reads the index at `.git/worktrees/{name}/index`, untouched by a filesystem delete of the working tree). Both remain readable while the working tree is being destroyed, which is what makes the check work at all under this race.

**State prerequisite:** None. The guard makes no assumption about who else is operating on the directory.

**Mitigation:** Not prevented — *detected*. The check converts an unwinnable ordering problem into a question about observable state: whatever the interleaving, a tree missing directories that HEAD tracks does not get its deletions committed. There is a residual window (the check reads a still-intact tree, then the deletion completes, then `add -A` runs) which it cannot close; closing that needs the lock the Rabbit Holes section defers. The window is milliseconds against a teardown that is otherwise unguarded for its whole duration, and the check eliminates the case where preserve is invoked against an *already* gutted tree, which is what the stacked no-op commits in the report demonstrate actually happened.

### Race 2: Repeated preserve passes stacking commits on an already-committed wipe

**Location:** Same.

**Trigger:** Once a wipe is committed, every subsequent preserve pass sees a tree that is clean relative to the new HEAD and either no-ops or commits trivia — which is precisely how the report's three follow-on commits (`805983e36`, `c004ff37d`, `35c76c461`) buried the destructive one.

**Data prerequisite:** None.

**State prerequisite:** The wipe must have been committed for this to be reachable.

**Mitigation:** Eliminated at the source. If the first wipe is never committed, HEAD never moves, and later passes see the same gutted tree and respond identically. On a pure wipe the response is idempotent by construction — it reads state and writes nothing — so N passes produce N ERROR logs and zero commits. On the mixed shape the first pass commits the additive work and the tree afterwards has no additive work left, so later passes fall through to the pure-wipe branch.

## No-Gos (Out of Scope)

Three of the issue's five Next Steps bullets were completed during planning rather than deferred, and are recorded where they belong:

- The **ordering question** in `_cleanup_stale_worktree` is answered, not deferred. Within a single pass the ordering is already correct: preserve runs at `agent/worktree_manager.py:972`, before that pass's own `git worktree remove --force`. The "preserve running against a tree a previous pass already gutted" hypothesis is a *cross-pass* concern, which is Race 1 in the Race Conditions section and is what the check detects. No code change is needed for ordering.
- The **`refs/session-wip/*` audit** is done: `git for-each-ref refs/session-wip/` returns zero refs on this machine. Nothing to audit, nothing to ship.
- The **sharper "missing tracked directory" variant** is not an alternative to evaluate later — it is the *only* signal this plan ships, and spike-2 removed the need for the hardcoded directory list the issue proposed.

Genuinely out of scope:

- The **proportional "majority of tracked files gone" guard** and its two `PerformanceSettings` tunables, cut during the revision pass. Rationale and the HEAD-based recipe for whoever adds it later are in **Rabbit Holes**.
- [SEPARATE-SLUG #3166] The bridge SIGTERM restart loop that amplified one latent race into nine teardown passes in seventeen minutes. Filed and open as its own reliability investigation with its own evidence. This plan's guard is correct with or without it, and touching bridge restart or watchdog code from this lane would put an unrelated subsystem into a worktree-teardown PR.

## Update System

No update system changes required. This is a code-path change inside a module every machine already runs; `/update`'s `git pull` plus `uv sync` propagates it with no new dependency, no new config file, and no migration step.

No `.env.example` entry is needed either. The revision pass dropped the two `PerformanceSettings` tunables, so this change introduces **no configuration surface at all** — no settings field, no env key, nothing for an installation to set or fail to set.

## Agent Integration

No agent integration required — this is a bridge-internal change.

`preserve_uncommitted_worktree_changes` is called only from within `agent/worktree_manager.py` (lines 972 and 1762) on teardown paths the bridge and worker drive automatically. There is no CLI entry point to add to `pyproject.toml [project.scripts]`, no MCP tool to register, and nothing for the agent to invoke. The agent's relationship to this code is as a *subject* of it, not a caller: its worktree is what gets torn down.

The one agent-facing surface in this subsystem is the sibling `validate_no_destructive_git_in_worktree.py` PreToolUse hook, and it is explicitly out of scope (see Rabbit Holes).

## Documentation

### Feature Documentation

- [ ] Update `docs/features/session-isolation.md` — the "**1. Auto-WIP-commit before teardown.**" subsection at line 254 describes the mechanism as an unconditional four-step sequence. Add the wipe check as step 2, the additive-only branch, the `[worktree-wip-refused-wipe]` log tag, and the corrected statement of what `refs/session-wip/{slug}` is guaranteed to contain (never a commit that deletes tracked directories). The `agent/worktree_manager.py` row in the file-map table at line 307 also needs it named.
- [ ] **Fix the `git stash` contradiction between the two surfaces this task edits — they currently disagree, and a documentarian editing both in one pass will otherwise propagate the false version.** The paragraph beginning "**Why a WIP commit + named ref, not `git stash`.**" at `docs/features/session-isolation.md:263` claims a stash "writes to the *per-worktree* `refs/stash`, which is destroyed with the worktree." That is false, and the function's own docstring (`agent/worktree_manager.py:1518-1527`) says the opposite and is correct: `refs/stash` lives in the **common** ref store, so a stash pushed from a worktree is visible as `stash@{0}` from the main checkout and survives the worktree's removal (verified on git 2.50.1). Replace the doc's sentence with the docstring's actual reasoning — that shared stack is precisely the problem, because every lane on this machine pushes onto the same one so an entry's position is meaningless and a teardown backstop keyed on it would race every peer (issue #2650, shape 1) — and note that `git stash` declines untracked files by default while a WIP commit captures them.
- [ ] Correct the recovery promise in the same document. The docstring and the feature doc both tell a human to run `git checkout refs/session-wip/{slug}` or `git reset --soft HEAD~1`; with the check in place that promise is sound, and the doc should say so explicitly rather than leaving the reader to infer it.
- [ ] Note in the same subsection that `reap_idle_worktree` is the third worktree-removal entry point and deliberately never reaches preserve, so a reader auditing the teardown surface does not have to rediscover it.
- [ ] `docs/features/README.md` — no new row needed (`session-isolation.md` is already indexed); verify its one-line description still reads correctly after the edit.

### External Documentation Site

Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation

- [ ] Rewrite the `preserve_uncommitted_worktree_changes` docstring's numbered mechanism list to include the wipe check as step 2, between the status read and staging, describe both wipe branches (additive-only commit vs. no commit at all), and state the ordering constraint (before staging) with the reason.
- [ ] Update the docstring's Returns block for the `refused` key and the two wipe shapes.
- [ ] Add a comment at the check site recording why the insertions-ratio predicate was rejected, citing the incident's 223,142 insertions. This is the single most likely thing for a future reader to "simplify."
- [ ] Add a comment on the staging call recording that `git add` spells NUL-separated pathspecs `--pathspec-file-nul` and rejects `-z`, and that `git add -A` must never be used on the wipe path because it restages the deletions.
- [ ] No `config/settings.py` docstring work — the revision dropped both fields.

## Success Criteria

- [ ] On a worktree missing a directory tracked at HEAD **with no other uncommitted work**, `preserve_uncommitted_worktree_changes` writes no commit and no ref, leaves the branch head unmoved, and returns `{"preserved": False, "refused": "missing-tracked-dirs", ...}`.
- [ ] On the same worktree **with coexisting edits or new files**, it commits exactly those paths — the resulting commit's diff against its parent has zero deletions — and returns `preserved: True` alongside the `refused` key. The removed directory is still tracked at the new HEAD.
- [ ] Detection survives a partially-staged wipe: calling `git add -A` before preserve does not blind it (the signal reads HEAD, not the index).
- [ ] It still preserves every legitimately dirty tree the existing suite covers — tracked edits, staged edits, untracked-only — through the unchanged `git add -A` path, and still commits deletions made *inside* a surviving directory.
- [ ] The worktree index is unmodified after the pure-wipe case (`git diff --cached --name-only` empty), proving the check runs before staging.
- [ ] Both live producers are covered by the one change: `_cleanup_stale_worktree` (`:972`) and `remove_worktree` (`:1762`). `reap_idle_worktree` is untouched and `TestReapIdleWorktree` stays green.
- [ ] `refs/session-wip/{slug}` names the branch the WIP commit actually landed on, falling back to the `slug` argument on detached HEAD — never `refs/session-wip/HEAD`.
- [ ] A wipe response logs at ERROR under `[worktree-wip-refused-wipe]` with slug, branch, worktree HEAD sha, sorted missing directory names, and preserved/deleted path counts; a check-computation failure logs at WARNING under `[worktree-wip-guard-failed]` and falls through to today's behavior.
- [ ] The change adds no settings field and no env key.
- [ ] Tests pass (`/do-test` — `scripts/pytest-clean.sh tests/unit/worktree_manager/`)
- [ ] Documentation updated (`/do-docs`), including the `refs/stash` correction in `docs/features/session-isolation.md`.
- [ ] No agent integration wiring needed — asserted by the absence of a new `[project.scripts]` entry in the diff.
- [ ] No xfail conversions required — `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/unit/worktree_manager/` returns nothing, so this bug has no expected-failure marker to convert.

## Team Orchestration

The lead agent orchestrates and never builds directly.

### Team Members

- **Builder (guard)**
  - Name: `guard-builder`
  - Role: the wipe check, the additive-only preserve branch, the enriched refusal record, and the ref-slug fix — all inside `preserve_uncommitted_worktree_changes`
  - Agent Type: builder
  - Domain: concurrency (paste the async/concurrency rules from `DOMAIN_FRAMING.md` — this code operates on a directory another process may be actively deleting)
  - Resume: true

- **Test engineer (regression)**
  - Name: `wipe-test-engineer`
  - Role: the nine new tests in `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py`, including the `_gut` fixture helper and the mixed-shape fixture
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `guard-validator`
  - Role: verifies the check cannot be bypassed, that the index is untouched on a pure wipe, that the additive commit carries zero deletions, and that every pre-existing test in `tests/unit/worktree_manager/` still passes unmodified except the one flagged UPDATE
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `session-isolation-doc`
  - Role: `docs/features/session-isolation.md` (including the `refs/stash` correction) and the docstring rewrite
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

**Cite by symbol, not by line.** Every line number in this module has now drifted twice (once between the issue and the first plan draft, once between that draft and this revision, via `55ad9ac89`). Locate `preserve_uncommitted_worktree_changes` by name; the anchors in **Freshness Check** are for orientation only.

### 1. Implement the wipe check and the additive-only preserve

- **Task ID**: build-guard
- **Depends On**: none
- **Validates**: `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py` (extended in task 3)
- **Informed By**: spike-1 (the signal precedes staging), spike-2 (`ls-tree -d HEAD` removes the hardcoded directory list), spike-3 (insertions-ratio rejected), spike-5 (the additive-only pathspec, and the two mechanical traps in it), spike-6 (read HEAD, not the index)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Domain**: concurrency
- **Parallel**: false
- In `preserve_uncommitted_worktree_changes`, insert the check **between** the `status --porcelain` non-empty branch and `git add -A`. Add a comment stating the ordering constraint and why staging first is unsafe.
- Structural check: `git -C <worktree_dir> ls-tree --name-only -d HEAD`, split on newlines; a listed name that is not a directory on disk in the worktree means a wipe. Collect the missing names sorted.
- On no missing directory, fall through to today's `git add -A` path unchanged.
- On a wipe, compute the additive-only pathspec: `git -C <wt> ls-files --modified --others --exclude-standard -z` minus the set from `git -C <wt> ls-files --deleted -z`, both split on NUL with empty trailing segments dropped.
  - **Empty set** → return `{"preserved": False, "was_clean": False, "refused": "missing-tracked-dirs", "ref": ref, "errors": [<detail>]}` **without staging or committing.** Do not call `git add` with an empty pathspec file: it exits 0 having staged nothing, and the following `git commit` then fails with "nothing to commit" and the outer handler logs a misleading `[worktree-wip-preserve-failed]` (spike-5).
  - **Non-empty set** → write the NUL-joined paths to a temporary file and run `git -C <wt> add --pathspec-from-file=<tmpfile> --pathspec-file-nul --`, then the existing commit / `rev-parse` / `update-ref` path, returning `preserved: True` alongside the `refused` key.
- **`git add` has no `-z`.** It is `--pathspec-file-nul`; `-z` exits non-zero with ``error: unknown switch `z` ``. Never use `git add -A` on the wipe path — it restages the deletions the check just declined.
- Emit the ERROR record under `[worktree-wip-refused-wipe]` with `slug`, `branch`, `head` (from `git -C <wt> rev-parse HEAD`), `missing` (sorted names), `preserved_paths` (count staged), and `deleted_paths` (count from `ls-files --deleted`). Capture all of it before returning — the force-remove that follows destroys the evidence.
- Wrap the check's own git reads in a narrow `try/except` that logs at WARNING under `[worktree-wip-guard-failed]` and falls through to today's behavior. Do not let it swallow errors the outer handler should see.
- Add the comment recording why the insertions-ratio predicate was rejected, citing the incident's 223,142 insertions.
- **Add no settings field and no env key.** The proportional guard and its two tunables were cut in the revision pass; do not reintroduce them.

### 2. Fix the ref-slug / branch mismatch

- **Task ID**: build-ref-slug
- **Depends On**: build-guard
- **Validates**: `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py::test_ref_slug_follows_checked_out_branch`
- **Informed By**: recon (found while tracing Path A; the issue does not mention it)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Resolve the worktree's checked-out branch with `git -C <worktree_dir> rev-parse --abbrev-ref HEAD`. **Gate on the `session/` prefix**: only when the output matches `session/<name>` use `<name>` for the `refs/session-wip/` ref; otherwise (detached HEAD, non-session branch, or a failed read) fall back to the `slug` argument as today.
- The `session/` prefix is the load-bearing gate, not `VALID_SLUG_RE`. On a detached HEAD `rev-parse --abbrev-ref HEAD` returns the literal string `HEAD`, which *passes* `VALID_SLUG_RE` (`agent/worktree_manager.py:21`), so a builder implementing the regex leg alone writes `refs/session-wip/HEAD`. Keep the regex as a second gate on the extracted `<name>`, never as the only one.
- The branch string is also one of the fields in the `[worktree-wip-refused-wipe]` record, so read it once and reuse it.
- This makes the docstring's `git checkout refs/session-wip/{slug}` recovery instruction true on the `_cleanup_stale_worktree` producer, where today the ref can name a different slug than the branch that received the commit.

### 3. Regression tests

- **Task ID**: build-tests
- **Depends On**: build-guard, build-ref-slug
- **Validates**: `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py`
- **Informed By**: spike-5 (the mixed-shape fixture is the one that matters), spike-6 (the partially-staged case)
- **Assigned To**: wipe-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add a `_gut(wt, *dirs)` fixture helper alongside the existing `_dirty`, deleting the named tracked directories from disk.
- Write the nine tests enumerated in **Test Impact**, reusing `_init_git_repo` / `_add_linked_worktree` / `_git`.
- `test_mixed_wipe_preserves_additions_and_drops_deletions` asserts on the commit's diff against its parent: both the edit and the new file present, **zero deletions**, and the removed directory still tracked at the new HEAD.
- `test_partially_staged_wipe_still_detected` runs `git add -A` before calling preserve.
- `test_wipe_with_untracked_artifacts_still_refuses_deletions` must create untracked files so `git add -A` would report insertions — this pins spike-3's finding.
- `test_index_untouched_on_pure_wipe` asserts `git diff --cached --name-only` is empty afterwards.
- Update `test_git_failure_returns_error_dict_and_never_raises` if the new reads change the error shape; do not weaken its assertions.

### 4. Validate

- **Task ID**: validate-guard
- **Depends On**: build-tests
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `scripts/pytest-clean.sh tests/unit/worktree_manager/ -q` and confirm the whole directory is green, not just the new file. `TestReapIdleWorktree` in `test_worktree_manager_cleanup.py` must stay green untouched — a break there means the change leaked outside its surface.
- **Mutation-check each assertion separately**: remove the structural check and confirm `test_pure_wipe_writes_no_commit_and_no_ref` fails; replace the additive-only staging with `git add -A` and confirm `test_mixed_wipe_preserves_additions_and_drops_deletions` fails on the zero-deletions assertion; move the check to after `git add -A` and confirm both `test_index_untouched_on_pure_wipe` and `test_partially_staged_wipe_still_detected` fail; drop the `session/` prefix gate and confirm `test_detached_head_falls_back_to_slug_argument` fails. A test that stays green under removal of the thing it names is not testing it.
- Confirm no pre-existing test in the file was weakened: diff the test file and check that only additions and the one flagged UPDATE appear.
- Run the Verification table commands and report each result.

### 5. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-guard
- **Assigned To**: session-isolation-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md` per the Documentation section, **including the `refs/stash` correction at line 263** — the doc and the docstring currently contradict each other and the doc is the wrong one.
- Rewrite the `preserve_uncommitted_worktree_changes` docstring's mechanism list and Returns block to cover both wipe branches and the before-staging ordering constraint.

### 6. Final validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run every Verification row against the final head and confirm all Success Criteria.
- Confirm the diff touches no bridge, watchdog, or restart code (the #3166 anti-criterion), and adds no `config/settings.py` field.

## Verification

Every row is read the same way: **the command must exit 0.** Anti-criteria are written as `! grep -q …`, which exits 0 exactly when the forbidden pattern is absent. Pipes inside the table cells are escaped as `\|` so the Markdown table renders; unescape them to `|` before pasting into a shell, or the ERE alternation matches a literal backslash-pipe and the row certifies nothing. This is deliberate — `grep -c` prints `0` while *exiting 1* on no match, so a table mixing "output > 0" rows with "count == 0" rows is read by two contradictory rules. Run each row from the lane worktree.

| Check | Command | Expected |
|-------|---------|----------|
| Worktree-manager tests pass | `scripts/pytest-clean.sh tests/unit/worktree_manager/ -q` | exit 0 |
| Lint clean | `python -m ruff check .` | exit 0 |
| Format clean | `python -m ruff format --check .` | exit 0 |
| Refusal tag exists in the producer | `grep -q 'worktree-wip-refused-wipe' agent/worktree_manager.py` | exit 0 |
| Fail-open tag exists | `grep -q 'worktree-wip-guard-failed' agent/worktree_manager.py` | exit 0 |
| Structural signal is `ls-tree -d HEAD`, not a hardcoded list | `grep -q 'ls-tree --name-only -d HEAD' agent/worktree_manager.py` | exit 0 |
| Additive staging uses the correct NUL flag | `grep -q -- '--pathspec-file-nul' agent/worktree_manager.py` | exit 0 |
| Regression tests exist | `grep -q 'def test_pure_wipe_writes_no_commit_and_no_ref' tests/unit/worktree_manager/test_worktree_manager_uncommitted.py && grep -q 'def test_mixed_wipe_preserves_additions_and_drops_deletions' tests/unit/worktree_manager/test_worktree_manager_uncommitted.py && grep -q 'def test_partially_staged_wipe_still_detected' tests/unit/worktree_manager/test_worktree_manager_uncommitted.py && grep -q 'def test_index_untouched_on_pure_wipe' tests/unit/worktree_manager/test_worktree_manager_uncommitted.py` | exit 0 |
| Anti-criterion — no hardcoded top-level directory list | `! grep -qE '\["?(tests\|bridge\|agent\|config)/?"?,' agent/worktree_manager.py` | exit 0 |
| Anti-criterion — the rejected insertions-ratio predicate is absent | `! grep -qE -- '--(numstat\|shortstat)' agent/worktree_manager.py` | exit 0 |
| Anti-criterion — the dropped tunables were not reintroduced | `! grep -qE 'wipe_refusal_(min_deleted_files\|deleted_fraction)' config/settings.py` | exit 0 |
| Anti-criterion (#3166 No-Go) — no bridge/watchdog code in the diff | `test -z "$(git diff --name-only origin/main...HEAD -- bridge/ monitoring/)"` | exit 0 |
| No stale xfails in scope | `! grep -rq 'xfail' tests/unit/worktree_manager/` | exit 0 |

The three `! grep -qE` rows were mutation-checked during the revision pass: each was run against a seeded file containing the forbidden shape (`TOP = ["tests", "bridge", "agent", "config"]`, a plain unslashed list; and `x = "git diff --cached --numstat"`) and each exited 1 as required, so they bite rather than certifying vacuously. The first draft's `grep -c '"tests/", *"bridge/"'` passed against both seeds.

## Critique Results

War-room verdict **NEEDS REVISION** (2 blockers, 5 concerns, 2 nits), recorded at `22ad11c63`. All nine were re-verified independently against `origin/main` at `ffda9fc86` during the revision pass — line numbers re-derived by symbol rather than trusted, and every empirical claim re-run — then addressed. Two of the critique's own suggestions were themselves wrong and are corrected below.

| Severity | Critics | Finding | Addressed By | Disposition |
|----------|---------|---------|--------------|-------------|
| BLOCKER | Risk & Robustness | On refusal the plan let `git worktree remove --force` proceed, destroying coexisting legitimate uncommitted work in the surviving directories. A regression against today's behavior for the mixed shape (one tracked top-level directory gone plus real edits elsewhere), which the structural check refuses unconditionally. | **Solution → Key Elements / Technical Approach**, spike-5, Risk 1b, `test_mixed_wipe_preserves_additions_and_drops_deletions`, task 1 | **Accepted, design changed.** Reproduction confirmed independently: `docs/` removed, `tests/f1.py` edited, `tests/f3.py` added → structural check fires, proportional check silent at 3/12, work lost. The bare refusal is replaced by an **additive-only preserve**: stage `ls-files --modified --others --exclude-standard` minus `ls-files --deleted`, commit that, leave the deletions in the working tree. Verified end to end: `2 files changed, 2 insertions(+)`, zero deletions, `docs/` still tracked at the new HEAD. **Two corrections to the critique's own recipe**, both found by running it: (1) `git add` has no `-z` — `git add --pathspec-from-file=- -z --` exits non-zero with ``error: unknown switch `z` ``; the flag is `--pathspec-file-nul`. (2) An empty candidate set must short-circuit before staging, or `git commit` fails with "nothing to commit" and the outer handler logs a spurious `[worktree-wip-preserve-failed]`. Both are written into task 1. |
| BLOCKER | History & Consistency | The Freshness Check was false at the plan's own head: #3162 closed via `55ad9ac89`, an ancestor of the plan commit, touching both named files, adding `reap_idle_worktree()` as a third worktree-removal entry point, and shifting every citation ~100 lines. | **Freshness Check** (rewritten), **Data Flow → Path C**, every line citation in the document, task preamble | **Accepted.** Re-verified: #3162 CLOSED 2026-09-05T12:52:41Z; `55ad9ac89` is an ancestor of `ffda9fc86` and adds 102 lines to `agent/worktree_manager.py` plus 41 to `agent/session_revival.py`. Every anchor was re-derived by symbol at `ffda9fc86` rather than copied from the critique, and all fifteen agree with the offered values. `reap_idle_worktree` (`:1007`) is now named in Data Flow as Path C with the reason it needs no change (it refuses on a non-empty `git status --porcelain` and never reaches preserve), and `TestReapIdleWorktree` is in Test Impact as a must-stay-green non-regression. Task bodies now cite by symbol, with a preamble saying why. |
| CONCERN | Risk & Robustness (converged with Scope & Value) | The proportional guard reads the index, so a prior partial staging blinds it: after a bare `git add -A`, `ls-files --deleted` drops to 0. | **spike-6**, **Rabbit Holes**, `test_partially_staged_wipe_still_detected` | **Accepted, resolved by removal.** Reproduced: 3 of 12 tracked files removed gives `ls-files --deleted`=3, `ls-files`=12; after `git add -A` the same tree gives 0 and 9. `ls-tree --name-only -d HEAD` is unchanged, because it reads HEAD. Rather than patch the ratio, the proportional guard is cut (see the next row) and the structural signal — immune by construction — is what ships. A test now pins that a partially-staged wipe is still detected. The HEAD-based recipe (`diff --cached --diff-filter=D` union, `ls-tree -r` denominator) is preserved in Rabbit Holes for whoever adds the ratio later. |
| CONCERN | Scope & Value (converged with Risk & Robustness) | The proportional guard was the plan arguing with itself: declared secondary yet carrying both tunables, the only new config and env surface, two of eight tests and two extra subprocess calls, while Rabbit Holes ruled tuning it out of bounds and Open Question 1 asked whether it should ship at all. Its target case has never been observed. | **Solution**, **No-Gos**, **Rabbit Holes**, **Update System**, **Architectural Impact**, Open Question 1 | **Accepted, dropped.** The proportional guard, both `PerformanceSettings` fields, both `PERFORMANCE__` env keys, the old task 1, and the two proportional tests are gone. The change now adds **no configuration surface at all**. This also moots the concern's fallback advice about not copying the `max_content_filename_bytes` `model_post_init` idiom. Net scope: the revision removed more than the additive-preserve branch added, so the Small appetite holds. |
| CONCERN | Risk & Robustness | A refusal's only observable was an ERROR log emitted immediately before the worktree is force-removed — evidence destroyed in the same breath, and the record named only slug, reason and counts. | **Solution → the refusal record**, **Failure Path Test Strategy → Error State Rendering**, task 1 | **Accepted.** The `[worktree-wip-refused-wipe]` record now carries `slug`, `branch`, `head` (worktree HEAD sha), `missing` (sorted directory names), `preserved_paths` and `deleted_paths`, all captured before returning. The branch string is read once and shared with the ref-slug fix, so it costs nothing. Tests assert on `caplog.records[-1]`, matching the existing `[worktree-wip-preserved]` precedent. |
| CONCERN | History & Consistency | `docs/features/session-isolation.md` and the function docstring contradict each other on whether `refs/stash` is worktree-local, and the plan scheduled a documentarian to edit both in one task without noticing. | **Documentation → Feature Documentation** (a dedicated checkbox), task 5 | **Accepted.** Confirmed at `ffda9fc86`: the doc's claim at line 263 ("writes to the *per-worktree* `refs/stash`, which is destroyed with the worktree") contradicts the docstring at `agent/worktree_manager.py:1518-1527`, and the docstring is the correct one. The Documentation section now names the exact paragraph, states which side is false, and supplies the replacement reasoning (common-store but a single machine-wide stack every lane pushes onto, so position is meaningless — issue #2650 shape 1 — plus `git stash` declining untracked files by default). Task 5 flags it as load-bearing. |
| CONCERN | Scope & Value | Two Verification anti-criterion rows were unfalsifiable, and the table mixed exit-code and stdout semantics under two unstated rules. | **Verification** (table rewritten) | **Accepted.** Every row now reads as "exit 0", anti-criteria are `! grep -q …`, and the table says so in a preamble. The three anti-criterion predicates were **mutation-checked**: run against seeded files containing the forbidden shapes (`TOP = ["tests", "bridge", "agent", "config"]` and `x = "git diff --cached --numstat"`) each exited 1 as required. The first draft's `grep -c '"tests/", *"bridge/"'` passed against both seeds, confirming the concern. A note warns that the table's `\|` escapes must be unescaped before pasting into a shell. |
| NIT | History & Consistency | Detached HEAD makes `rev-parse --abbrev-ref HEAD` return the literal `HEAD`, which passes `VALID_SLUG_RE`, so a builder implementing the regex leg alone writes `refs/session-wip/HEAD`. | **Solution → slug/branch mismatch**, task 2, `test_detached_head_falls_back_to_slug_argument` | **Accepted.** Verified both halves: a detached worktree returns exactly `HEAD`, and `HEAD` matches `^[a-zA-Z0-9][a-zA-Z0-9._-]*$` (`agent/worktree_manager.py:21`). Task 2 now makes the `session/` prefix the load-bearing gate and demotes the regex to a second gate on the extracted name. The test asserts the ref is specifically not `refs/session-wip/HEAD`. |
| NIT | Scope & Value | Task 1's `Validates` hedged on whether `tests/unit/test_settings.py` exists. | **Step by Step Tasks**, **Test Impact** | **Accepted, resolved by removal.** The file does exist (confirmed). The task that hedged is gone with the settings fields, and Test Impact now records `tests/unit/test_settings.py` as explicitly unaffected because the change adds no settings surface. |

## Open Questions

All three are resolved. Kept here with their resolutions rather than deleted, because two of them were settled by the critique's evidence and the reasoning is worth carrying into build and review.

1. **Is a 50%-of-tracked-files threshold the right proportional trigger, or should the structural check stand alone at first?** **Resolved: the structural check stands alone.** Two critique lenses converged on the proportional guard as unearned scope, and spike-6 then showed the index blinds it in exactly the interleaving it was meant to cover. It is cut, along with both tunables and both env keys. Rabbit Holes records the HEAD-based recipe for whoever adds it after a real partial wipe is observed.
2. **Should a refusal escalate beyond an ERROR log?** **Resolved: no, and the urgency dropped.** With the additive-only preserve in place, a detection no longer means work was lost — the real work is committed and only the deletions are declined — so the log is a diagnostic rather than a last-chance alarm. The record is now rich enough to reconstruct the event after the worktree is gone (slug, branch, head sha, missing directories, counts). Pulling `monitoring/` into a one-module change stays out of scope.
3. **Does the fail-open-on-check-failure choice sit right?** **Resolved: yes, deliberately.** A `git ls-tree` failure is not evidence of a wipe, and this function's hardest contract is that it never blocks or hangs teardown. Failing closed would trade a rare committed wipe for a routine loss of the backstop on legitimate work. Recorded as Risk 2, tagged `[worktree-wip-guard-failed]` at WARNING so a recurring pattern is visible, and pinned by `test_guard_computation_failure_falls_open_and_warns`. Revisit only if that WARNING appears in production logs.
