---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/880
last_comment_id:
---

# Worktree Cleanup Path Guard

## Problem

On 2026-04-10 ~14:53 ICT, the entire working copy at `/Users/valorengels/src/ai` was wiped — `.git`, source files, and an uncommitted stash gone, only `logs/` surviving. Root cause: `agent/worktree_manager.py::_cleanup_stale_worktree` trusts any path `git worktree list --porcelain` hands it, and its fallback branch calls `shutil.rmtree(wt, ignore_errors=True)` on that path with no containment check. When the path happens to be the main working tree (because a session branch was checked out there directly), the helper recursively deletes the main repo — and `ignore_errors=True` silently swallows the `EBUSY` errors from open file handles, so only `logs/` remains.

The trigger path is fully verified in the issue (#880, lines 9–19 of the issue body) from the Claude Code transcript. Full narrative: a session branch got checked out in the main working tree; `/do-build` then called `get_or_create_worktree` which called `_find_worktree_for_branch` which reported the main repo as the worktree for that branch; `create_worktree` compared that to the expected `.worktrees/...` path, saw a mismatch, and called `_cleanup_stale_worktree(repo_root, branch_name, "/Users/valorengels/src/ai")`. `git worktree remove --force` refused (git correctly protects the main worktree), the `except` branch ran, and `shutil.rmtree(wt, ignore_errors=True)` at line 244 tore through the main repo.

**Current behavior:** `_cleanup_stale_worktree` has no path-containment invariant. The `shutil.rmtree` fallback at line 244 uses `ignore_errors=True`, hiding the EBUSY signal that would otherwise have surfaced the problem loudly. There is no `logger.critical` log before the destructive fallback runs, so the crash tracker had no correlation signal when this fired.

**Desired outcome:** The helper refuses to operate on any path outside `repo_root / WORKTREES_DIR` (including, critically, `repo_root` itself). The fallback no longer swallows errors. A `logger.critical` entry fires before any `rmtree` attempt, giving crash-tracker and log audits a correlation point. The unit test suite gains explicit coverage for the path-guard invariant so future refactors can't regress it.

## Freshness Check

**Baseline commit:** `226fbc1d59e15eacd4ecaea09f5e3eeb4663fc53`
**Issue filed at:** 2026-04-10T08:28:36Z (same day as this plan)
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/worktree_manager.py:16` — `WORKTREES_DIR = ".worktrees"` — still holds (verified by Read).
- `agent/worktree_manager.py:199` — `_cleanup_stale_worktree` function header — still holds.
- `agent/worktree_manager.py:229` — `subprocess.run(["git", "worktree", "remove", "--force", ...])` — still holds (actually line 230 in current file; one-line drift from an added comment, immaterial).
- `agent/worktree_manager.py:239` — `except subprocess.CalledProcessError` — still holds (line 239).
- `agent/worktree_manager.py:243-244` — `if wt.exists(): shutil.rmtree(wt, ignore_errors=True)` — still holds (lines 243–244).
- `agent/worktree_manager.py:293` — `_find_worktree_for_branch` call site inside `create_worktree` — still holds (line 293).
- `tests/unit/test_worktree_manager.py::TestCleanupStaleWorktree` — still present at lines 254–294, still uses only `/repo/.worktrees/...` paths, still has no `worktree_path == repo_root` coverage.

**Cited sibling issues/PRs re-checked:**
- #237 — closed (parent issue that introduced `_cleanup_stale_worktree`); no change to the helper since #238 landed on 2026-03-03.
- #301 — closed (symptom: shell CWD death); PR #304 addressed the CWD death side, orthogonal to this guard.
- #306 — closed (workspace safety invariants at session launch); `validate_workspace()` is a launch-time guard, does not defend the deletion path — gap confirmed.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since="2026-04-10" -- agent/worktree_manager.py tests/unit/test_worktree_manager.py` returned zero commits.

**Active plans in `docs/plans/` overlapping this area:** none (no existing plan slug matches `worktree*`).

**Notes:** Line numbers in the issue are accurate to within one line (a comment line accounts for the 229→230 drift on the `subprocess.run` call). All file:line references hold; the bug is fully present in the current main.

## Prior Art

- **PR #238** (2026-03-03, merged): Introduced `_cleanup_stale_worktree` as the fix for #237. Added the force-remove path and the `rmtree` fallback — but never bounded which paths the helper would operate on. This is the exact gap the current plan closes.
- **PR #304** (2026-03-08, merged): "Prevent shell CWD death when worktree is removed." Handled the symptom side — what happens to the dev session's shell after a worktree under it is removed. Does not defend against the main repo being the thing getting removed.
- **PR #315** (2026-03-08, merged): "Add git state guard to prevent dirty state blocking SDLC operations." A different guard layer (dirty-state detection). Does not touch `_cleanup_stale_worktree`.
- **PR #201** (2026-02-26, merged): "Fix worktree blocking branch deletion on PR merge." Different code path (merge cleanup); does not touch `_cleanup_stale_worktree`.

No prior PR has added path containment to the cleanup helper. The invariant in this plan is a net-new defense.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #238 | Introduced `_cleanup_stale_worktree` with a `shutil.rmtree` fallback using `ignore_errors=True`. | Added destructive fallback without bounding its target path. The helper trusts its caller; there is no invariant that the path lives under `.worktrees/`. `ignore_errors=True` was a defensive reflex (don't fail on partial state) that turned into a silent-failure footgun when the target was the main repo. |
| PR #304 | Made dev sessions survive their own worktree getting removed (shell CWD death fix). | Handled the session-side symptom, not the deletion-side cause. A session that deletes the wrong thing will still delete the wrong thing; PR #304 just makes the session's shell behave better afterward. |
| PR #306 | Added `validate_workspace()` pre-launch guard for session CWDs. | Validates before *spawning* a subprocess in a path; does not validate before *deleting* a path. The mental model is "guard subprocess launches" rather than "guard any destructive filesystem operation." This plan extends the same containment mental model to the deletion path. |

**Root cause pattern:** Each prior fix added a guard at one ingress point (subprocess launch, merge cleanup, etc.) without establishing a general invariant that destructive filesystem operations must validate their target. The cleanup helper is a rare case where the agent itself calls `rmtree`, so it was overlooked. The fix is not to enumerate every caller — it is to make the helper itself refuse bogus inputs.

## Architectural Impact

- **New dependencies:** None. Uses `Path.is_relative_to()` (Python 3.9+; repo pins `>=3.11`), `Path.resolve()`, `shutil.rmtree`, all already imported.
- **Interface changes:** `_cleanup_stale_worktree` now raises `RuntimeError` when handed a path outside `.worktrees/`. Existing callers (`create_worktree` at line 298) do not catch this — by design. A bogus path indicates either a bug in `_find_worktree_for_branch` or a genuinely dangerous repo state; either way we want the loud crash and the crash tracker correlation.
- **Coupling:** Unchanged. The helper's contract with `create_worktree` remains "given a stale worktree path, remove it" — the plan just adds a precondition.
- **Data ownership:** Unchanged.
- **Reversibility:** Fully reversible. The guard is a pre-condition check plus a flag removal. Reverting is a one-line reapplication of `ignore_errors=True` and a removal of the guard.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (hotfix — scope is surgical and fully specified by the issue)
- Review rounds: 1 (standard `/do-pr-review` before merge)

This is a hotfix pass as the issue explicitly directs: plan-lite → build → test → PR → merge. No design iteration, no spike work, no unknowns. The fix is already sketched in the issue body; this plan's job is to lock it in so the build agent executes it faithfully.

## Prerequisites

No prerequisites — this work has no external dependencies. It modifies one Python helper and adds unit tests; the standard repo virtualenv already provides everything needed (pytest, ruff).

## Solution

### Key Elements

- **Path-containment invariant**: A precondition at the top of `_cleanup_stale_worktree` that refuses to operate on any path not strictly under `repo_root / WORKTREES_DIR`. Includes an explicit check that the path is not `repo_root` itself (this is the exact path from the 2026-04-10 incident).
- **Fail-loud fallback**: Remove `ignore_errors=True` from the `shutil.rmtree` call in the `except` branch. We want any subsequent failure to surface as a real exception, not be silently swallowed.
- **Critical log before destructive fallback**: Add `logger.critical` immediately before the `rmtree` call in the fallback branch so crash-tracker and log audits have a correlation point if this ever fires.
- **Explicit test coverage**: Four new test cases in `TestCleanupStaleWorktree` that lock in the guard's behavior — two negative cases (reject `repo_root`, reject arbitrary outside path), two positive cases (legit force-remove under `.worktrees/`, legit fallback under `.worktrees/` without `ignore_errors`).

### Flow

Not a user-facing flow — this is a defensive helper that runs inside `create_worktree` recovery paths. The user journey is unchanged; only the failure mode changes.

**Today:** Bogus path hits helper → force-remove fails → `rmtree(wt, ignore_errors=True)` → silent partial destruction → crash tracker sees nothing correlatable.

**After:** Bogus path hits helper → guard raises `RuntimeError` → `create_worktree` surfaces the exception → crash tracker correlates via stacktrace and commit SHA. Legit stale worktree hits helper → force-remove succeeds → no fallback needed. Legit stale worktree where force-remove fails → `logger.critical` fires → `rmtree(wt)` (no `ignore_errors`) → if *that* fails the exception propagates (no more silent partial state).

### Technical Approach

Modify `agent/worktree_manager.py::_cleanup_stale_worktree` (lines 199–247) as follows. The exact shape follows the solution sketch in issue #880 with minor clarifications.

1. Resolve both paths at the top of the function:
   ```python
   wt = Path(worktree_path).resolve()
   worktrees_root = (repo_root / WORKTREES_DIR).resolve()
   ```
   Using `.resolve()` handles symlinks, `..` components, and relative paths consistently. Without resolving, a caller passing `/repo/.worktrees/../foo` would pass a naive substring check but still escape containment.

2. Immediately after resolving, add the guard:
   ```python
   if wt == repo_root.resolve() or not wt.is_relative_to(worktrees_root):
       raise RuntimeError(
           f"Refusing to clean up worktree at {wt}: path is not under "
           f"{worktrees_root}. Branch={branch_name}, repo_root={repo_root}."
       )
   ```
   The `wt == repo_root.resolve()` clause is redundant with `is_relative_to(worktrees_root)` in the strict-subpath sense (since `repo_root` is never under `repo_root/.worktrees/`), but we keep it explicit because the 2026-04-10 incident was precisely `wt == repo_root`. Making that path fail with a distinctive error message improves debuggability.

3. Leave the "directory missing → prune" branch (lines 215–222) unchanged — it only calls `prune_worktrees`, no filesystem destruction.

4. Leave the `git worktree remove --force` happy path (lines 229–238) unchanged.

5. In the `except subprocess.CalledProcessError` branch (lines 239–247):
   - Keep `logger.error(...)` on line 240 as-is.
   - Keep `prune_worktrees(repo_root)` on line 242 as-is.
   - Before the `if wt.exists(): shutil.rmtree(...)` block, add a `logger.critical` call describing the fallback attempt:
     ```python
     if wt.exists():
         logger.critical(
             f"Fallback rmtree for stale worktree {wt} after git worktree "
             f"remove failed. branch={branch_name} repo_root={repo_root}"
         )
         shutil.rmtree(wt)  # no ignore_errors — fail loud on partial destruction
         logger.info(f"Manually removed stale worktree directory: {worktree_path}")
         prune_worktrees(repo_root)
     ```
   - Remove `ignore_errors=True` from the `shutil.rmtree` call. Any failure here must propagate.

The existing `TestCleanupStaleWorktree` tests patch `Path.exists`. The new tests that exercise the guard cannot rely on that same mocking strategy for `.resolve()` — the guard resolves paths before checking anything. Approach: construct real paths using `tmp_path` (pytest fixture) or use `Path(...).resolve()` on absolute paths that exist on the test host. Since the guard check is purely computational (no filesystem I/O on the guard path itself), passing real absolute paths that pytest's `tmp_path` fixture created is the clean approach. For tests that hit the fallback `rmtree`, continue using `patch.object(Path, "exists", ...)` and `patch("agent.worktree_manager.shutil.rmtree")` to avoid actual deletion.

**Caveat on `.resolve()` behavior:** On systems where `repo_root` does not exist (e.g., CI with mocked paths), `.resolve()` is still well-defined — it performs lexical normalization and returns an absolute path. Tests using `Path("/repo")` or `Path("/fake/repo")` will resolve to themselves on Linux/macOS (no symlinks to follow). The existing test patterns use `Path("/repo")` and will continue to work.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_cleanup_stale_worktree`'s `except subprocess.CalledProcessError` block (currently lines 239–247) does NOT swallow the CalledProcessError silently — it logs at `error` level and then runs the fallback. After this change, the block still catches `CalledProcessError` from `git worktree remove --force`, but the subsequent `rmtree` call no longer hides its own errors. This is verified by test case **(d)** below: when `rmtree` succeeds, the test asserts the call was made; a follow-up assertion confirms `ignore_errors` was not passed (or was `False`).
- [ ] The new guard raises `RuntimeError` — not caught anywhere in `create_worktree` (verified by reading lines 293–298). The test for the caller-side path is out of scope for this hotfix (see No-Gos).

### Empty/Invalid Input Handling
- [ ] Empty-string `worktree_path`: `Path("").resolve()` returns the current working directory on most platforms. This is dangerous behavior (could equal `repo_root` if cwd is the repo). Test case **(a)** implicitly covers this if the test runs from `repo_root`; we add an explicit empty-string test to lock the guard rejection regardless of cwd.
- [ ] `None` `worktree_path`: Type signature says `str`; passing `None` would raise `TypeError` at `Path(None)`. Not in scope — upstream callers in `create_worktree` always pass a string from `git worktree list`.
- [ ] Whitespace-only `worktree_path`: Same as empty string, resolves to cwd. The guard rejects it on the "not under .worktrees/" branch.

### Error State Rendering
- [ ] This is an internal helper, no user-facing output. The `logger.critical` log is the observable signal for log audits and crash tracker. The test for the fallback path asserts `logger.critical` was called before `rmtree` (via `caplog` or `patch("agent.worktree_manager.logger")`).

## Test Impact

- [ ] `tests/unit/test_worktree_manager.py::TestCleanupStaleWorktree::test_prunes_when_directory_missing` — UPDATE: the test passes `/repo/.worktrees/feat` as the worktree path, which is valid under the new guard. Verify it still passes. No code change expected; listed here so the builder knows to re-run it.
- [ ] `tests/unit/test_worktree_manager.py::TestCleanupStaleWorktree::test_force_removes_existing_directory` — UPDATE: same as above, uses `/repo/.worktrees/old-feat`, valid under guard. No code change expected.
- [ ] `tests/unit/test_worktree_manager.py::TestCleanupStaleWorktree::test_fallback_rmtree_on_force_remove_failure` — UPDATE: passes `/repo/.worktrees/stuck`, valid under guard. The test currently asserts `mock_rmtree.assert_called_once()` — after the change, we also want to assert that `ignore_errors` was NOT passed (or was `False`). Tighten the assertion: `mock_rmtree.assert_called_once_with(Path("/repo/.worktrees/stuck").resolve())` or inspect `mock_rmtree.call_args.kwargs` to confirm `ignore_errors` is absent.
- [ ] `tests/unit/test_worktree_manager.py::TestCleanupStaleWorktree` — ADD four new test methods (detailed in Solution section):
  - `test_guard_rejects_repo_root_path` — calls `_cleanup_stale_worktree(Path("/repo"), "session/feat", "/repo")` and asserts `RuntimeError` raised with a message mentioning both the path and `.worktrees`.
  - `test_guard_rejects_path_outside_worktrees` — calls with `worktree_path="/tmp/foo"` and asserts `RuntimeError`.
  - `test_guard_rejects_sibling_dir_under_repo` — calls with `worktree_path="/repo/some-other-dir"` and asserts `RuntimeError` (this is the "same repo but not under .worktrees/" case).
  - `test_fallback_does_not_pass_ignore_errors` — mirrors `test_fallback_rmtree_on_force_remove_failure` but explicitly asserts the `rmtree` call does not pass `ignore_errors=True`, and asserts `logger.critical` was called before the `rmtree`.
- [ ] `tests/unit/test_worktree_manager.py::TestCreateWorktreeStaleRecovery` — No changes expected. The create-worktree tests mock `_cleanup_stale_worktree` directly, so they are insulated from the guard change.

All four new tests live in the existing `TestCleanupStaleWorktree` class; no new class needed.

## Rabbit Holes

- **Caller-side hardening in `create_worktree`** (pre-checking `existing_wt == repo_root` at line 294 before calling `_cleanup_stale_worktree`). Tempting because it catches the bad input one layer earlier, but **explicitly out of scope** per the issue's Recon Summary. The invariant inside the helper is the load-bearing fix. Adding caller-side checks means enumerating every call site; the guard-inside-the-helper approach defends against every future caller without that enumeration.
- **Replacing `shutil.rmtree` entirely with a bounded walker** (e.g., only deleting files matching a manifest). Over-engineering for this hotfix. The guard plus loud failure is sufficient defense.
- **Adding a project-wide `assert_inside(path, allowed_root)` utility** and refactoring other destructive filesystem operations to use it. Good idea, but scope creep for a hotfix. File a separate issue if the pattern recurs.
- **Testing the actual `git worktree remove` behavior against a real repo** (integration test instead of mocked unit test). The unit tests mock `subprocess.run` and the filesystem; real git behavior is already validated by the existing `TestCreateWorktreeStaleRecovery` tests. Don't add a git integration test for this hotfix.
- **Investigating why `_find_worktree_for_branch` returned the main repo path in the first place** (the root cause one layer up). Worth investigating but out of scope — the guard is the *defense in depth*, not the root-cause fix. The issue explicitly frames it this way.

## Risks

### Risk 1: The guard breaks a legitimate cleanup case we didn't anticipate
**Impact:** A stale worktree that genuinely lives under `.worktrees/` but has a symlink component or other path weirdness could theoretically fail `is_relative_to(worktrees_root)` after `.resolve()`, causing the helper to refuse cleanup and leaving stale state behind.
**Mitigation:** Use `.resolve()` on both sides of the comparison (`wt` and `worktrees_root`) so symlinks are followed consistently. The test suite verifies the happy path under `/repo/.worktrees/...` still works. If a real-world case surfaces where this blocks a legit cleanup, the fix is to investigate *why* the worktree lives somewhere unexpected — not to weaken the guard.

### Risk 2: Removing `ignore_errors=True` causes a real exception where before the system "recovered"
**Impact:** If a partial-state worktree genuinely has locked files during legit cleanup, the new fail-loud fallback will raise an exception and abort `create_worktree`. Before, it would have silently left debris and claimed success.
**Mitigation:** This is a desired change, not a regression. Silent partial success was the *primary bug class* that hid the 2026-04-10 incident. The `logger.critical` call gives us correlation; if this fires repeatedly in practice, we investigate and fix the underlying cause (probably a file lock we need to release first). Do not restore `ignore_errors=True` as a workaround.

### Risk 3: Tests that mock `Path.exists` interact oddly with `Path.resolve()`
**Impact:** The existing tests use `patch.object(Path, "exists", return_value=...)` and pass string paths like `/repo/.worktrees/feat`. The new guard calls `.resolve()` on these paths before any other check. On macOS/Linux, `.resolve()` on a non-existent absolute path returns the same absolute path (lexically normalized), so the existing tests will not break. On Windows (if anyone ever runs these tests there), `.resolve()` behavior has changed across Python versions — but this repo is macOS/Linux only.
**Mitigation:** Verify with a quick local run before committing. If any existing test breaks, the fix is to use `tmp_path` in the affected test rather than `Path("/repo")`.

## Race Conditions

No race conditions identified — `_cleanup_stale_worktree` is synchronous, single-threaded, and operates on a path the caller has exclusive handle on. The concurrency story for worktree creation (multiple sessions trying to create the same worktree) is handled elsewhere by `create_worktree`'s existing checks and is not touched by this plan.

## No-Gos (Out of Scope)

- **Caller-side hardening in `create_worktree`** (explicitly dropped per issue Recon Summary).
- **Investigating why `_find_worktree_for_branch` returns the main repo path** when a session branch is checked out there (root-cause fix one layer up — file as follow-up if needed).
- **PM Bash discipline fix** (the chain reaction where the PM session executed the dev session's "re-clone the repo" recovery instructions as shell commands) — tracked separately per the issue body.
- **Generalizing `assert_inside(path, allowed_root)` as a project-wide utility** and migrating other destructive helpers to use it.
- **Integration tests against a real git repo** — unit tests with mocked subprocess are sufficient for this hotfix.
- **Changing `validate_workspace()` in `agent/worktree_manager.py`** or any other surface; only `_cleanup_stale_worktree` changes.

## Update System

No update system changes required — this fix is purely internal to `agent/worktree_manager.py`. No new dependencies, no config files, no migration steps, no deployment changes. The `/update` skill and `scripts/remote-update.sh` do not need edits.

## Agent Integration

No agent integration required — `_cleanup_stale_worktree` is called only by `create_worktree` inside `agent/worktree_manager.py`, which is itself called by `get_or_create_worktree` from within the session infrastructure. It is never exposed to the agent as a tool and has no MCP surface. No changes to `.mcp.json` or `mcp_servers/`. No bridge-level imports affected.

## Documentation

- [ ] Update `docs/features/session-isolation.md` with a brief note on the worktree cleanup path-containment invariant. The file already documents the `session/{slug}` → `.worktrees/{slug}/` mapping; add one paragraph under the worktree-management section explaining that `_cleanup_stale_worktree` now enforces path containment and fails loudly, and cross-reference the incident (issue #880).
- [ ] Inline docstring update on `_cleanup_stale_worktree` to document the raise behavior: add a `Raises:` section mentioning `RuntimeError` on bogus paths.
- [ ] Inline comment above the `shutil.rmtree(wt)` call noting explicitly that `ignore_errors` is intentionally absent and why (correlates with the #880 incident).

No changes to `docs/features/README.md` index (the feature page already exists). No external docs site. No MkDocs/Sphinx.

## Success Criteria

- [ ] `_cleanup_stale_worktree` raises `RuntimeError` when `worktree_path` resolves to `repo_root` or any path outside `repo_root / WORKTREES_DIR` (acceptance criterion 1 from issue #880).
- [ ] `shutil.rmtree` fallback no longer uses `ignore_errors=True` (acceptance criterion 2).
- [ ] `logger.critical` fires before the fallback `rmtree` call (acceptance criterion 3).
- [ ] Four new unit tests in `TestCleanupStaleWorktree` cover cases (a) through (d) from the issue's acceptance criterion 4.
- [ ] Existing `TestCleanupStaleWorktree` and `TestCreateWorktreeStaleRecovery` tests still pass (acceptance criterion 5).
- [ ] `python -m ruff check .` exits 0 (acceptance criterion 6).
- [ ] `python -m ruff format --check .` exits 0 (acceptance criterion 6).
- [ ] `pytest tests/unit/test_worktree_manager.py -x -q` exits 0.
- [ ] `docs/features/session-isolation.md` updated with the invariant note.
- [ ] PR merges to main with the hotfix.

## Team Orchestration

This is a Small hotfix with a fully specified solution. One builder does the work end-to-end; no parallel decomposition needed.

### Team Members

- **Builder (worktree-guard)**
  - Name: worktree-guard-builder
  - Role: Implements the path-containment guard, updates the fallback branch, tightens the existing test, adds the four new tests, and updates documentation.
  - Agent Type: builder
  - Resume: true

- **Validator (worktree-guard)**
  - Name: worktree-guard-validator
  - Role: Runs the full test file, ruff check, ruff format check. Verifies all success criteria.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Apply guard and fallback changes to `_cleanup_stale_worktree`
- **Task ID**: build-guard
- **Depends On**: none
- **Validates**: tests/unit/test_worktree_manager.py::TestCleanupStaleWorktree
- **Informed By**: Issue #880 solution sketch, this plan's Technical Approach section
- **Assigned To**: worktree-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `agent/worktree_manager.py` lines 199–247:
  - Replace `wt = Path(worktree_path)` with `wt = Path(worktree_path).resolve()`
  - Add `worktrees_root = (repo_root / WORKTREES_DIR).resolve()` immediately after
  - Add the guard: `if wt == repo_root.resolve() or not wt.is_relative_to(worktrees_root): raise RuntimeError(...)` with a message mentioning the path, `worktrees_root`, `branch_name`, and `repo_root`
  - In the `except` branch, add `logger.critical(...)` before the `rmtree` call
  - Remove `ignore_errors=True` from the `shutil.rmtree(wt)` call
  - Add an inline comment on the `rmtree` line explaining `ignore_errors` is intentionally absent (ref #880)
  - Update the function docstring with a `Raises:` section for `RuntimeError`

### 2. Add four new tests and tighten existing fallback test
- **Task ID**: build-tests
- **Depends On**: build-guard
- **Validates**: tests/unit/test_worktree_manager.py::TestCleanupStaleWorktree (all tests)
- **Assigned To**: worktree-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `tests/unit/test_worktree_manager.py::TestCleanupStaleWorktree`:
  - Add `test_guard_rejects_repo_root_path`: call `_cleanup_stale_worktree(Path("/repo"), "session/feat", "/repo")`, assert `RuntimeError` with message containing `.worktrees`
  - Add `test_guard_rejects_path_outside_worktrees`: call with `worktree_path="/tmp/foo"`, assert `RuntimeError`
  - Add `test_guard_rejects_sibling_dir_under_repo`: call with `worktree_path="/repo/some-other-dir"`, assert `RuntimeError`
  - Add `test_fallback_does_not_pass_ignore_errors`: mirror `test_fallback_rmtree_on_force_remove_failure` structure. Patch `agent.worktree_manager.logger`, assert `logger.critical` was called before `shutil.rmtree`, and assert the `rmtree` call's kwargs do not include `ignore_errors=True`
  - Tighten `test_fallback_rmtree_on_force_remove_failure`: add assertion that `mock_rmtree.call_args.kwargs.get("ignore_errors")` is not `True` (i.e., `ignore_errors` either absent or explicitly `False`)
  - Ensure all new tests use `Path("/repo")` and `/repo/.worktrees/...` conventions consistent with existing tests (no `tmp_path` fixture needed — the guard computation is pure)

### 3. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-guard, build-tests
- **Assigned To**: worktree-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Open `docs/features/session-isolation.md`
- Locate the section on worktree management (search for `.worktrees/` or `worktree_manager`)
- Add a short subsection or paragraph titled "Path-containment invariant" describing: (a) `_cleanup_stale_worktree` will only operate on paths strictly under `repo_root/.worktrees/`, (b) bogus inputs raise `RuntimeError` rather than silently deleting, (c) the fallback `rmtree` is logged at CRITICAL and does not swallow errors, (d) cross-reference issue #880 as the incident that motivated the guard
- If `docs/features/session-isolation.md` does not exist, create it with a minimal stub and add the invariant section

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-guard, build-tests, document-feature
- **Assigned To**: worktree-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worktree_manager.py -x -q` and verify exit 0
- Run `python -m ruff check agent/worktree_manager.py tests/unit/test_worktree_manager.py` and verify exit 0
- Run `python -m ruff format --check agent/worktree_manager.py tests/unit/test_worktree_manager.py` and verify exit 0
- Read the updated `_cleanup_stale_worktree` and verify the guard literal `is_relative_to(worktrees_root)` is present
- Read the updated `TestCleanupStaleWorktree` class and verify all four new test method names exist
- Confirm `docs/features/session-isolation.md` has a section mentioning `#880` or "path-containment invariant"
- Report pass/fail with per-check status

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Worktree tests pass | `pytest tests/unit/test_worktree_manager.py -x -q` | exit code 0 |
| Lint clean on touched files | `python -m ruff check agent/worktree_manager.py tests/unit/test_worktree_manager.py` | exit code 0 |
| Format clean on touched files | `python -m ruff format --check agent/worktree_manager.py tests/unit/test_worktree_manager.py` | exit code 0 |
| Guard literal present | `grep -n 'is_relative_to' agent/worktree_manager.py` | output contains `worktrees_root` |
| `ignore_errors` removed | `grep -n 'ignore_errors' agent/worktree_manager.py` | exit code 1 |
| `logger.critical` added | `grep -n 'logger.critical' agent/worktree_manager.py` | output contains `rmtree` or `fallback` |
| New test methods present | `grep -n 'test_guard_rejects\|test_fallback_does_not_pass_ignore_errors' tests/unit/test_worktree_manager.py` | output contains 4 lines |
| Docs reference #880 | `grep -rn '880' docs/features/session-isolation.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

---

## Open Questions

None. The issue is fully specified, the Recon Summary confirmed all file:line references, and the solution sketch in the issue body is directly implementable. If the critique step surfaces concerns, they will be addressed in a revision pass.
