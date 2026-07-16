---
type: bug
slug: full-suite-lock-cross-worktree
issue: 2064
---

# Full-suite pytest lock: serialize across worktrees, stop cross-checkout poisoning

## Problem

The advisory full-suite lock (`data/full-suite-running.lock`) shipped in #1967 was
validated only for **same-checkout** concurrent runs. It does **not** serialize
across git worktrees: `scripts/pytest-clean.sh` and `scripts/suite_lock.py`
resolve the lock dir relative to the current checkout's `data/` directory, so
every `.worktrees/{slug}/` checkout gets its own independent lock. Concurrent
SDLC lanes (each in its own worktree) therefore run full `-n auto` suites
simultaneously, oversubscribing cores and cross-killing each other's xdist
workers. Confirmed on the 2026-07-13 parallel batch (issue #2064):

- xdist workers from one checkout get reaped as PPID-1 orphans by a peer
  checkout's wrapper (`[gw0] node down`), controllers wedge at 0% CPU for 25+ min.
- `__pycache__` / `.pytest_cache` cross-checkout poisoning produced a junitxml
  with 6727 CollectErrors out of 10861 tests.
- Synchronous lock-waiters were OOM-killed (exit 137) under the memory pressure
  the lock was supposed to prevent, with 2+ queued waiters.
- The MERGE-stage full-suite gate in `docs/sdlc/do-merge.md` shells out to **bare
  `pytest`**, never routing through `scripts/pytest-clean.sh`, so it never
  acquires the lock at all and never reaps its own workers — the lock provided
  zero protection even for same-checkout concurrent merge gates.

## Freshness Check

- `scripts/pytest-clean.sh` (HEAD) sets `SUITE_LOCK_DIR="$REPO_ROOT/data/full-suite-running.lock"` — repo-relative, per-worktree.
- `scripts/suite_lock.py::_default_lock_dir()` returns `Path.cwd() / "data" / "full-suite-running.lock"` — cwd-relative, per-worktree.
- `scripts/refresh_test_baseline.py` hardcodes `SUITE_LOCK_DIR = PROJECT_DIR / "data" / "full-suite-running.lock"` — PROJECT_DIR-relative, per-checkout.
- `docs/sdlc/do-merge.md` (Full Suite Gate, ~L286-296) invokes bare `pytest ... --junitxml=/tmp/pr_run.xml` in both the small-patch and full-suite branches.
- The in-wrapper reaper (`ours_or_orphan`) already scopes kills to own-ancestry or PPID-1 orphans — no machine-wide reaping bug; cross-reaping is a *consequence* of concurrent suites orphaning workers, not a reaper scoping bug. Serialization removes the cause.

## Prior Art

- #1967 shipped the advisory lock (`scripts/suite_lock.py`, `pytest-clean.sh`
  integration, `docs/features/full-suite-pytest-lock.md`).
- `docs/features/test-isolation-hardening.md` (#1897) covers within-run xdist
  isolation — orthogonal to this cross-run/cross-worktree fix.

## Research

All worktrees of one repo share a single `.git` common dir
(`git rev-parse --git-common-dir` → the same absolute path from every worktree).
Hashing that path yields one stable lock key **per repo**, shared across all its
checkouts, distinct from unrelated clones. A machine-global base
(`$TMPDIR`, falling back to `/tmp`) puts the lock outside any checkout's `data/`
so worktree deletion (post-merge cleanup) never removes a live lock.

## Data Flow

`scripts/pytest-clean.sh` → `python3 scripts/suite_lock.py acquire` (default
lock dir now machine-global, keyed by git-common-dir hash) → mkdir-atomic lock →
pytest → exit trap releases. `scripts/refresh_test_baseline.py` imports the same
`suite_lock.default_lock_dir()` helper so baseline runs coordinate on the exact
same lock. `docs/sdlc/do-merge.md` merge gate routed through `pytest-clean.sh`.

## Architectural Impact

Lock-dir resolution is centralized in one Python helper
(`suite_lock.default_lock_dir()`) so the shell wrapper, the baseline refresher,
and the CLI all agree by construction. No new dependencies, no schema changes.

## Appetite

Small. Contained to two scripts, one skill doc, one feature doc, and the unit
test file. ~1 day.

## Prerequisites

None.

## Solution

1. **Machine-global lock path keyed to repo identity.** Add
   `suite_lock.default_lock_dir()` (public) that resolves to
   `/tmp/valor-suite-lock-<sha1(git-common-dir)[:16]>/full-suite-running.lock`.
   The base is a **fixed `/tmp`**, deliberately NOT `$TMPDIR`: a launchd worker
   daemon typically has `TMPDIR` unset (→ `/tmp`) while an interactive shell has
   `TMPDIR=/var/folders/.../T` (see `project_launchd_plist_auth_source.md`);
   using `$TMPDIR` would make the worker-driven merge gate and a manual
   `pytest-clean.sh` compute different lock dirs and never serialize —
   reproducing the exact #1967 blind spot. Only the git-common-dir hash needs to
   be per-repo; the base only needs to be machine-global and stable across
   process types. Resolve the git common dir via `git rev-parse --git-common-dir`,
   made absolute against cwd; fall back to hashing cwd when the subprocess
   returns **non-zero OR empty output** (covers git-absent, corrupted repo,
   `GIT_DIR` override). `_default_lock_dir()` (the CLI default) delegates to it.
   All worktrees of one repo now contend on one lock; worktree deletion can't
   remove it (the lock lives outside every checkout's `data/`).
2. **Route `pytest-clean.sh` through the Python default.** Stop hardcoding
   `SUITE_LOCK_DIR` in the shell; pass no `--lock-dir` so both `acquire` and
   `release` resolve the identical machine-global default. (Release must resolve
   the same path acquire used — centralizing in Python guarantees it.)
3. **Disable bytecode writes for suite runs (defense-in-depth, NOT load-bearing).**
   Export `PYTHONDONTWRITEBYTECODE=1` in `pytest-clean.sh` before invoking pytest.
   Serialization (items 1-2) already removes the *concurrent* poisoning path and
   each worktree has its own `__pycache__` dir, so this is not the primary fix;
   it is cheap belt-and-suspenders against any future cross-checkout bytecode
   sharing (e.g. a stray shared `PYTHONPYCACHEPREFIX`). Contained to the pytest
   subprocess + its xdist workers.
4. **`refresh_test_baseline.py` uses the shared helper.** Replace the hardcoded
   `SUITE_LOCK_DIR` with `suite_lock.default_lock_dir()`.
5. **Fix the merge-gate bypass.** In `docs/sdlc/do-merge.md` Full Suite Gate,
   route both branches through `scripts/pytest-clean.sh` (acquires the machine-
   global lock for the full-suite branch, reaps workers for both). **Keep the
   junit path `/tmp/pr_run.xml` unchanged**: the `baseline_gate.py --pr-junitxml`
   read step is NOT co-located in this gate block (it is driven separately by the
   merge skill per `merge-gate-baseline.md`), so renaming the write target would
   silently break the read side. A per-run `$$` suffix is unsafe anyway — `$$`
   in two separate Bash blocks resolves to different PIDs. Concurrent-gate
   clobber of the shared path is now moot for full-suite runs because the
   machine-global lock serializes them (only one full suite writes at a time).
6. **OOM bounding — addressed by serialization.** With one machine-global lock
   per repo, only one full suite runs at a time; the concurrent-suite memory
   pressure that OOM-killed queued waiters is removed at the source. Waiters
   only poll-sleep (negligible RSS). No separate semaphore is added — that would
   be redundant mechanism for a cause serialization already eliminates.

## Failure Path Test Strategy

- Two acquires with **different** git-common-dir keys get **different** lock dirs
  (independent repos don't serialize against each other).
- Two acquires resolving the **same** key share one lock dir (worktree case):
  the second waits/proceeds-unlocked exactly as the same-checkout case does.
- `default_lock_dir()` falls back to a cwd-hash when `git` is absent (no crash).
- Release resolves the same path as acquire when neither passes `--lock-dir`.

## Test Impact

- [ ] `tests/unit/test_suite_lock.py` — UPDATE: add `TestDefaultLockDir` covering
  (a) machine-global base under `/tmp`, not repo `data/`, (b) same
  git-common-dir → same lock dir, (c) different keys → different dirs, (d)
  git-absent/erroring fallback to cwd hash, (e) **TMPDIR-independence**: the
  resolved lock dir is identical whether `TMPDIR` is set (interactive) or unset
  (launchd worker). Existing tests pass unchanged (they pass an explicit
  `--lock-dir`/`lock` path and are unaffected by the default change).

## Rabbit Holes

- Do **not** build a controller-side watchdog/timeout for wedged pytest in this
  PR — the wedge is caused by cross-reaping, which serialization eliminates.
  Track separately if it recurs post-fix.
- Do **not** rework the reaper scoping — it is already correct (`ours_or_orphan`).
- Do **not** add a machine-wide (all-repos) lock — key by repo to avoid
  serializing unrelated clones; the observed failure is worktrees of one repo.

## No-Gos

- No change to `is_full_suite` detection semantics.
- No change to lock take/wait/steal policy or timeouts.
- No new runtime dependencies.

## Update System

No update-system changes required. `scripts/pytest-clean.sh`,
`scripts/suite_lock.py`, and `scripts/refresh_test_baseline.py` are already
propagated by the normal repo sync; the lock path is computed at runtime from
git, needs no config file, and needs no migration (a stale per-checkout
`data/full-suite-running.lock` left by the old code is simply ignored — the new
default never looks there; it is gitignored and harmless).

## Agent Integration

No agent-integration changes required. `scripts/pytest-clean.sh` is already the
sanctioned test entry point the agent invokes via Bash; this change only alters
where it resolves its lock and that it disables bytecode writes. No new CLI
entry point, no bridge import. The `docs/sdlc/do-merge.md` edit changes the
command the merge-stage skill instructs the agent to run (bare `pytest` →
`scripts/pytest-clean.sh`).

## Documentation

- [ ] Update `docs/features/full-suite-pytest-lock.md`: replace the "Lock
  location: `data/full-suite-running.lock/` ... relative to the pytest rootdir"
  paragraph with the machine-global, git-common-dir-keyed path; add a
  "Cross-worktree serialization" note and the `PYTHONDONTWRITEBYTECODE=1`
  hardening; reference issue #2064.
- [ ] Update `docs/sdlc/do-merge.md` Full Suite Gate to route through
  `scripts/pytest-clean.sh` with a unique junitxml.
- [ ] `docs/sdlc/do-test.md` — the inline clause at L30 names the lock at
  `data/full-suite-running.lock`; update it to note the lock is now machine-global
  (a `/tmp` path keyed to the repo, shared across all worktrees).

## Step by Step Tasks

1. Add `default_lock_dir()` + git-common-dir helper to `scripts/suite_lock.py`;
   delegate `_default_lock_dir()` to it.
2. Update `scripts/pytest-clean.sh`: drop hardcoded `SUITE_LOCK_DIR`, stop
   passing `--lock-dir`, export `PYTHONDONTWRITEBYTECODE=1`.
3. Update `scripts/refresh_test_baseline.py` to use `suite_lock.default_lock_dir()`.
4. Update `docs/sdlc/do-merge.md` Full Suite Gate.
5. Update `docs/features/full-suite-pytest-lock.md` and `docs/sdlc/do-test.md`.
6. Add `TestDefaultLockDir` to `tests/unit/test_suite_lock.py`.
7. Run `scripts/pytest-clean.sh tests/unit/test_suite_lock.py`; ruff.

## Success Criteria

- `scripts/pytest-clean.sh tests/unit/test_suite_lock.py` passes.
- Two `python scripts/suite_lock.py acquire` calls from two worktrees of this
  repo resolve to the **same** lock dir (manual check: run from main checkout
  and from `.worktrees/*` and compare the printed default path).
- `docs/sdlc/do-merge.md` contains no bare `pytest` in the Full Suite Gate.
- `python -m ruff check scripts/ tests/unit/test_suite_lock.py` clean.

## Open Questions

None.
