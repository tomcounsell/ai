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

### 1. Add the two provisional tunables to settings

- **Task ID**: build-settings
- **Depends On**: none
- **Validates**: `tests/unit/test_settings.py` (if a settings test module exists; otherwise the fields are covered indirectly by the guard tests)
- **Informed By**: spike-3 (the insertions-ratio predicate is rejected, so no insertion-related field is added)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `wipe_refusal_min_deleted_files: int = 50` (ge=1) and `wipe_refusal_deleted_fraction: float = 0.5` (gt=0.0, le=1.0) to `PerformanceSettings` in `config/settings.py`, directly below `max_content_filename_bytes`.
- Mark both "Provisional/tunable" in their `description`, matching the neighbouring field's idiom, and name the `PERFORMANCE__`-prefixed env keys explicitly.
- Do NOT add a `model_post_init` flat-name override. The nested `PERFORMANCE__` prefix works natively; the flat-override hack on `max_content_filename_bytes` exists only because that key was already documented under a bare name.

### 2. Implement the wipe-refusal guard

- **Task ID**: build-guard
- **Depends On**: build-settings
- **Validates**: `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py` (extended in task 3)
- **Informed By**: spike-1 (the signal precedes staging), spike-2 (`ls-tree -d HEAD` removes the hardcoded directory list), spike-3 (insertions-ratio rejected), spike-4 (`ls-files --deleted -z` is the NUL-safe primitive)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Domain**: concurrency
- **Parallel**: false
- In `preserve_uncommitted_worktree_changes` (`agent/worktree_manager.py:1406`), insert the guard **between** the `status --porcelain` non-empty check and `git add -A`. Add a comment stating the ordering constraint and why staging first is unsafe.
- Structural check: `git -C {worktree_dir} ls-tree --name-only -d HEAD`; refuse with `refused="missing-tracked-dirs"` if any listed name is not a directory on disk. Include the missing names in the error string.
- Proportional check: `git -C {worktree_dir} ls-files --deleted -z` and `git -C {worktree_dir} ls-files -z`, both split on NUL with empty trailing segments dropped. Refuse with `refused="majority-deleted"` when `deleted >= settings.performance.wipe_refusal_min_deleted_files` **and** `tracked > 0` and `deleted / tracked >= settings.performance.wipe_refusal_deleted_fraction`. Evaluate the floor first so an empty index cannot divide by zero.
- Refusal returns `{"preserved": False, "was_clean": False, "refused": <reason>, "ref": ref, "errors": [<detail with counts>]}` and logs at ERROR under the tag `[worktree-wip-refused-wipe]` with slug, reason, and counts.
- Wrap the guard's own git reads in a narrow `try/except` that logs at WARNING under `[worktree-wip-guard-failed]` and falls through to today's behavior. Do not let it swallow errors the outer handler should see.
- Add the comment recording why the insertions-ratio predicate was rejected, citing the incident's 223,142 insertions.

### 3. Fix the ref-slug / branch mismatch

- **Task ID**: build-ref-slug
- **Depends On**: build-guard
- **Validates**: `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py::test_ref_slug_follows_checked_out_branch`
- **Informed By**: recon (found while tracing Path A; the issue does not mention it)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Resolve the worktree's checked-out branch with `git -C {worktree_dir} rev-parse --abbrev-ref HEAD`. When it matches `session/<name>`, use `<name>` for the `refs/session-wip/` ref; otherwise (detached HEAD, non-session branch, or a failed read) fall back to the `slug` argument as today.
- Keep the `_validate_slug` guarantee: validate the derived name against `VALID_SLUG_RE` before it reaches a ref path, and fall back if it does not match.
- This makes the docstring's `git checkout refs/session-wip/{slug}` recovery instruction true on the `_cleanup_stale_worktree` producer, where today the ref can name a different slug than the branch that received the commit.

### 4. Regression tests

- **Task ID**: build-tests
- **Depends On**: build-guard, build-ref-slug
- **Validates**: `tests/unit/worktree_manager/test_worktree_manager_uncommitted.py`
- **Informed By**: spike-3 (the untracked-artifacts fixture shape is the one that matters)
- **Assigned To**: wipe-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add a `_gut(wt, *dirs)` fixture helper alongside the existing `_dirty`, deleting the named tracked directories from disk.
- Write the eight tests enumerated in **Test Impact**, reusing `_init_git_repo` / `_add_linked_worktree` / `_git`.
- `test_wipe_with_untracked_artifacts_still_refuses` must create untracked files so `git add -A` would report insertions — this is the test that pins spike-3's finding.
- `test_index_untouched_on_refusal` asserts `git diff --cached --name-only` is empty after a refusal.
- Update `test_git_failure_returns_error_dict_and_never_raises` if the guard's reads change the error shape; do not weaken its assertions.

### 5. Validate

- **Task ID**: validate-guard
- **Depends On**: build-tests
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `scripts/pytest-clean.sh tests/unit/worktree_manager/ -q` and confirm the whole directory is green, not just the new file.
- **Mutation-check each guard separately**: disable the structural check and confirm `test_missing_tracked_directory_refuses` fails; disable the proportional check and confirm `test_majority_of_tracked_files_deleted_refuses` fails; move the guard to after `git add -A` and confirm `test_index_untouched_on_refusal` fails. A test that stays green under its own guard's removal is not testing it.
- Confirm no pre-existing test in the file was weakened: diff the test file and check that only additions and the one flagged UPDATE appear.
- Run the Verification table commands and report each result.

### 6. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-guard
- **Assigned To**: session-isolation-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md` (the line-254 subsection and the line-307 table row) per the Documentation section.
- Rewrite the `preserve_uncommitted_worktree_changes` docstring's mechanism list to include the wipe check as step 2 and state the before-staging ordering constraint.

### 7. Final validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run every Verification row against the final head and confirm all Success Criteria.
- Confirm the diff touches no bridge, watchdog, or restart code (the #3166 anti-criterion).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Worktree-manager tests pass | `scripts/pytest-clean.sh tests/unit/worktree_manager/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Guard exists in the producer | `grep -c 'worktree-wip-refused-wipe' agent/worktree_manager.py` | output > 0 |
| Structural signal uses ls-tree, not a hardcoded directory list | `grep -c 'ls-tree' agent/worktree_manager.py` | output > 0 |
| Proportional signal uses the ls-files primitive | `grep -c 'ls-files' agent/worktree_manager.py` | output > 0 |
| Both tunables landed as provisional | `grep -c 'wipe_refusal_min_deleted_files\|wipe_refusal_deleted_fraction' config/settings.py` | output > 0 |
| Regression tests exist | `grep -c 'def test_missing_tracked_directory_refuses\|def test_majority_of_tracked_files_deleted_refuses\|def test_wipe_with_untracked_artifacts_still_refuses\|def test_index_untouched_on_refusal' tests/unit/worktree_manager/test_worktree_manager_uncommitted.py` | output > 0 |
| Anti-criterion — no hardcoded top-level directory list in the guard | `grep -c '"tests/", *"bridge/"\|tests/.*bridge/.*agent/.*config/' agent/worktree_manager.py` | match count == 0 |
| Anti-criterion — the rejected insertions-ratio predicate is absent | `grep -c 'diff --cached --numstat\|--shortstat' agent/worktree_manager.py` | match count == 0 |
| Anti-criterion (#3166 No-Go) — no bridge/watchdog code in the diff | `git diff --name-only origin/main...HEAD -- bridge/ monitoring/ \| grep -c .` | match count == 0 |
| No stale xfails in scope | `grep -rn 'xfail' tests/unit/worktree_manager/` | exit code 1 |

## Critique Results

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | On refusal the plan lets `git worktree remove --force` proceed, so coexisting legitimate uncommitted work in the surviving directories is destroyed with no backstop. Today's unguarded behavior at least commits that work into the WIP commit, where it is recoverable by cherry-picking the additions out; under the guard it is gone permanently. Risk 1 covers only a large-deletion refactor, never the mixed shape (one tracked top-level directory gone plus real edits elsewhere), which the primary structural guard refuses unconditionally with no floor and no coexistence check. | pending | Reproduced in a throwaway repo: with `docs/` removed from disk, `tests/f1.py` edited and `tests/f3.py` added, `git ls-tree --name-only -d HEAD` lists `agent bridge docs tests`, `docs` is absent on disk so the structural guard refuses, while deleted=2 tracked=9 leaves the proportional guard silent. Additive-only preserve: `git -C <wt> ls-files --modified --others --exclude-standard -z` filtered against the `ls-files --deleted -z` set, piped to `git -C <wt> add --pathspec-from-file=- -z --` (never `add -A`, which restages the deletions), then the existing commit/update-ref path. Alternative: state explicitly in Risks that a refusal destroys coexisting work and why that is accepted. |
| BLOCKER | History & Consistency | The Freshness Check is already false at the plan's own head commit. #3162 closed at 2026-09-05T12:52:41Z via `55ad9ac89`, which is an ancestor of the plan commit `9e07e3f1a`, so the plan asserts "#3162 still open, no code overlap" and "`git log --since=... -- agent/worktree_manager.py agent/session_revival.py` returns nothing" about a commit already in its own history. That commit touches both named files, adds `reap_idle_worktree()` (a third worktree-removal entry point in the same module, whose docstring explicitly reasons about #3167), and shifts every file:line citation in the plan by roughly 100 lines. Data Flow's "Two paths reach the producer" is no longer the module's full teardown surface. | pending | Corrected anchors at `768b87627`: `preserve_uncommitted_worktree_changes` def 1508 (not 1406); its `_cleanup_stale_worktree` call site 972 (not 948); its `remove_worktree` call site 1762 (not 1660); path-containment guard 942 (not 918); fallback `shutil.rmtree` 1001 (not 978); `create_worktree` 1370 (not 1295); the `_cleanup_stale_worktree(...)` call 1425 (not 1323); `cleanup_stale_branches` 198 (not 193); and the `test_worktree_manager_cleanup.py` `@patch("...preserve_uncommitted_worktree_changes")` decorators 466 and 544 (not 298 and 376, which were correct at the `bc42055a4` baseline). `reap_idle_worktree` (worktree_manager.py:1007) deliberately never calls preserve and refuses on non-empty `git status --porcelain`, so it needs no guard: say so rather than leaving a reader to rediscover it. Cite by symbol, not line, in task bodies. |
| CONCERN | Risk & Robustness (converges with Scope & Value on the same component) | The proportional guard reads the index, not HEAD, so it is blinded by a prior partially-staged wipe. Once any earlier process ran `git add -A` and failed before committing (the interleaving Risk 3 worries about, reachable today because `add -A` succeeds and `commit` can fail), the staged deletions leave the index, `git ls-files --deleted` returns 0, and the guard cannot fire. Only the structural guard still sees the wipe, so the guard added specifically for partial wipes that keep every top-level directory present is exactly the one that silently degrades. | pending | Verified empirically: 4 of 9 tracked files removed from disk gives `ls-files --deleted`=4 and `ls-files`=9; after a bare `git add -A` the same tree gives `ls-files --deleted`=0 and `ls-files`=5, while `ls-tree --name-only -d HEAD` is unchanged. Fix: `deleted = set(ls-files --deleted -z) \| set(git diff --cached --diff-filter=D --name-only -z HEAD)` and `tracked = len(git ls-tree -r --name-only -z HEAD)`; keep the `tracked > 0` short-circuit before the division. Add a test that stages the wipe first and asserts `refused == "majority-deleted"` still fires. |
| CONCERN | Scope & Value (converges with Risk & Robustness on the same component) | The proportional guard is the plan arguing with itself: declared secondary, it carries the change's only two tunables, the only new config surface, the only new env keys, two of the eight new tests and an extra pair of subprocess calls per teardown, while Rabbit Holes rules tuning those numbers out of bounds and Open Question 1 asks whether it should ship at all. Its target case (a partial wipe leaving every tracked top-level directory nominally present) has never been observed; the reported incident is caught by the structural guard alone. | pending | Ship the structural guard alone in this Small-appetite change and drop task 1, both `PerformanceSettings` fields, and the two proportional tests; add the proportional guard when a real partial wipe is observed. If it is kept, the two fields must not copy the `max_content_filename_bytes` idiom wholesale: that field's `model_post_init` flat-name override (config/settings.py:160-181) exists only for a pre-existing documented bare env name and must not be replicated, as task 1 already states. |
| CONCERN | Risk & Robustness | A refusal's only observable is an ERROR log line, emitted immediately before the worktree is force-removed, so the evidence is destroyed in the same breath. The Problem section identifies a log line as the reason the original incident went unnoticed and was found by accident, yet the refusal records slug, reason and counts only: not the branch, not the worktree HEAD sha, not the missing paths. After the force-remove there is nothing left to inspect and no pointer to what was lost. | pending | Capture before returning: `git -C <wt> rev-parse HEAD` (sha), `git -C <wt> rev-parse --abbrev-ref HEAD` (branch, already read for the task-3 ref-slug fix so it is free), the sorted missing-directory names, and deleted/tracked. Emit all of them in the `[worktree-wip-refused-wipe]` record and assert on the fields in `test_missing_tracked_directory_refuses` via `caplog.records[-1]`, the way `test_dirty_tree_preserved_in_named_ref_and_wip_commit` asserts on `[worktree-wip-preserved]`. |
| CONCERN | History & Consistency | The two documentation surfaces the plan schedules for edit already contradict each other on why a WIP commit was chosen over `git stash`, and the plan does not notice. `docs/features/session-isolation.md` states a stash "writes to the per-worktree `refs/stash`, which is destroyed with the worktree"; the function docstring states the opposite, that `refs/stash` lives in the common ref store (verified on git 2.50.1). A documentarian editing both in one task will either propagate the false version or leave the two further apart. | pending | The false sentence is the paragraph beginning "**Why a WIP commit + named ref, not `git stash`.**" in `docs/features/session-isolation.md`, immediately after the four-step mechanism list at line 254. Replace its body with the docstring's reasoning at `agent/worktree_manager.py:1518-1527`: `refs/stash` is common-store and does survive worktree removal, but it is a single machine-wide stack every lane pushes onto so an entry's position is meaningless (issue #2650), and `git stash` declines untracked files by default. Add it as a Documentation checkbox. |
| CONCERN | Scope & Value | Two of the three Verification anti-criterion rows are not falsifiable as written, and the table mixes exit-code and stdout semantics inconsistently. The hardcoded-directory-list grep passes against any list that does not use trailing slashes in that exact order (a plain `TOP_LEVEL = ["tests", "bridge", ...]` sails through), so it certifies nothing. And `grep -c` prints 0 while exiting 1 on no match, so rows expecting "output > 0" and rows expecting "match count == 0" are read by two different rules the table never states. | pending | Use `grep -q 'ls-tree --name-only -d HEAD' agent/worktree_manager.py` (exit 0 required) as the positive structural-signal check; `! grep -qE '\["?(tests\|bridge\|agent\|config)/?"?,' agent/worktree_manager.py` for the hardcoded-list anti-criterion; `! grep -qE -- '--(numstat\|shortstat)' agent/worktree_manager.py` for the rejected-predicate row. Every row then reads as exit code 0, matching the Lint clean and Format clean rows already in the table. |
| NIT | History & Consistency | Task 3 gives two gates for the derived ref slug, and only the `session/` prefix match works for the detached-HEAD case the task names. `git rev-parse --abbrev-ref HEAD` returns the literal string `HEAD` on a detached head, and `HEAD` matches `VALID_SLUG_RE`, so a builder implementing the regex leg alone writes `refs/session-wip/HEAD`. | pending | - |
| NIT | Scope & Value | Task 1's Validates field hedges: "`tests/unit/test_settings.py` (if a settings test module exists; otherwise the fields are covered indirectly)". The file exists; the hedge leaves a question for the builder to answer at build time. | pending | - |


## Open Questions

1. **Is a 50%-of-tracked-files threshold the right proportional trigger, or should the structural guard stand alone at first?** The structural check (a tracked directory missing from disk) is unambiguous and needs no tuning. The proportional check adds coverage for a partial wipe that leaves every top-level directory present, at the cost of the only two tunable numbers in the change. Shipping both is the plan's position; shipping only the structural guard and adding the proportional one after a real partial-wipe is observed is a defensible alternative.
2. **Should a refusal escalate beyond an ERROR log?** Today the only observable is a log line, which is how the original incident went unnoticed for hours. A refusal means a worktree was found in a state that should not occur, which is arguably worth a bridge notification or a crash-tracker entry rather than a log grep. That would pull `monitoring/` into a change that currently touches one module, so it is not in the plan.
3. **Does the fail-open-on-guard-failure choice sit right?** A `git ls-files` failure means the guard cannot tell wipe from work, and the plan chooses to preserve (today's behavior) rather than refuse, on the grounds that this function must never block teardown. The opposite choice — refuse when uncertain — trades a rare committed wipe for a routine loss of the backstop. Recorded as Risk 2 with the reasoning; worth a second opinion before build.
