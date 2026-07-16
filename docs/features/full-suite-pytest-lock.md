# Full-suite pytest advisory lock

The default pytest config (`pyproject.toml`) runs the suite with
`-n auto --dist=loadfile` — one xdist worker per CPU core. When two *full-suite*
runs overlap (a manual run racing `/do-test`, `/do-docs`, or
`scripts/refresh_test_baseline.py`), total workers exceed cores and every worker
starves. During PR #1956 the load average reached 79-82 on a 10-core machine;
one baseline run accumulated 15 seconds of CPU across 90 minutes of wall-clock
before it was killed — not deadlocked, just almost never scheduled (issue #1967, F1).

## The guard

`scripts/pytest-clean.sh` acquires an advisory lock before launching a
full-suite run and releases it on exit. A second concurrent full-suite run
**waits** for the first to finish rather than piling on. The lock reuses the
`mkdir`-atomic lock-dir pattern already used by `scripts/remote-update.sh`.

The policy lives in `scripts/suite_lock.py`:

| Situation | Behavior |
|-----------|----------|
| No other full-suite run | Acquire instantly — **single-run behavior is unchanged** |
| Another full-suite run is active | Wait (poll every 2s) until it releases, then acquire |
| Owner process crashed (PID gone) | Reclaim the stale lock immediately |
| Owner alive but past a 1-hour backstop | Reclaim (guards against a wedged owner) |
| Waited past `--timeout` (default 30 min) | Proceed **unlocked** with a warning rather than deadlock |

The lock is **narrowly scoped to full-suite runs**. `scripts/suite_lock.py`
decides full-suite-ness from the pytest arguments:

- Full-suite: `pytest`, `pytest tests`, `pytest tests/` (with any flags).
- **Not** full-suite (never touches the lock): any narrower path (`tests/unit/`,
  a `.py` file, a `::` node id) or a serial / xdist-disabled run (`-n0`,
  `-n 0`, `--numprocesses=0`, `-p no:xdist`).

So quick focused runs keep their unchanged parallelism and never wait.

## Knobs

| Env var | Default | Effect |
|---------|---------|--------|
| `PYTEST_SUITE_LOCK` | `1` | Set to `0` to disable the guard entirely (e.g. nested runs) |
| `PYTEST_SUITE_LOCK_TIMEOUT` | `1800` | Max seconds to wait for another full-suite run before proceeding unlocked |

## Lock location — machine-global, shared across worktrees

The lock dir is **machine-global**, resolved by `suite_lock.default_lock_dir()`:

```
/tmp/valor-suite-lock-<sha1(git-common-dir)[:16]>/full-suite-running.lock/
```

(a directory containing `owner.pid`). The suffix is a hash of the repo's shared
`git rev-parse --git-common-dir`, which every worktree of one repo resolves to
the same absolute path — so **all worktrees of a repo contend on one lock**,
while unrelated clones on the same machine get distinct locks. The base is a
fixed `/tmp` (deliberately **not** `$TMPDIR`: a launchd worker has `TMPDIR`
unset → `/tmp` while an interactive shell has `TMPDIR=/var/folders/.../T`; using
`$TMPDIR` would let the two compute different lock dirs and never serialize).

This replaces the original per-checkout `data/full-suite-running.lock` (relative
to the pytest rootdir), which gave every `.worktrees/{slug}/` checkout its own
independent lock — so concurrent SDLC lanes ran full suites simultaneously,
oversubscribing cores and cross-reaping each other's xdist workers (issue #2064).
The `/tmp` location also survives post-merge worktree deletion, which previously
could remove a live lock from under a running suite.

`scripts/pytest-clean.sh` passes **no** `--lock-dir`, letting the Python default
govern so `acquire` and `release` always resolve the identical path.
`scripts/refresh_test_baseline.py` imports `suite_lock.default_lock_dir()` for
the same reason.

### `__pycache__` hardening

`scripts/pytest-clean.sh` exports `PYTHONDONTWRITEBYTECODE=1` before invoking
pytest. With serialization in force this is defense-in-depth (each worktree
already has its own `__pycache__`), guarding against any future cross-checkout
bytecode sharing that produced the 6727-CollectError junit observed in #2064.

## CLI

`scripts/suite_lock.py` is also usable directly:

```bash
python scripts/suite_lock.py is-full-suite -- tests          # exit 0 (yes)
python scripts/suite_lock.py is-full-suite -- tests/unit/     # exit 1 (no)
python scripts/suite_lock.py acquire --owner-pid $$ -- tests  # prints ACQUIRED / SKIPPED_NOT_FULL_SUITE / PROCEEDED_UNLOCKED
python scripts/suite_lock.py release --owner-pid $$
```

## Scope and follow-ups

This lock fixes F1 (CPU starvation) and the **cross-run** dimension of F2
(two full-suite runs racing on the same hardcoded Redis sentinel, e.g.
`test_sdlc_sessionless_e2e.py`'s `ISSUE_NUMBER = 999137`) — serialized runs can
no longer collide.

The **within-run** dimension of F2 (xdist workers inside a *single* run sharing
Redis `db=1`) is not addressed here; it needs per-worker namespacing of fixed
test identifiers and is tracked separately in issue #1967.

A distinct **cross-process test-DB collision** — two separate pytest processes
(e.g. this lock's *own* narrow-run exemptions: a single-test run and a
full-suite run) both deriving the same Redis test db and calling `flushdb()` on
each other mid-test — is handled outside this lock by the **per-process test-DB
claim** in `tests/conftest.py` (issue #2060): each pytest process holds an
`fcntl.flock` on a unique db number from the pool, so no two live processes
share a test db regardless of this lock's scope. See
[`docs/features/test-isolation-hardening.md`](test-isolation-hardening.md)
(root cause 3).

## Tests

`tests/unit/test_suite_lock.py` covers full-suite detection, the
take/wait/steal policy, and real filesystem acquire/release — including a second
waiter backing off when the owner is alive and a crashed owner's stale lock
being reclaimed.

## See also

[`docs/features/test-isolation-hardening.md`](test-isolation-hardening.md) —
the companion **single-run** isolation doc (issue #1897). This lock fixes
**cross-run** concurrency (two separate `pytest` invocations racing); that doc
covers phantom test failures caused by xdist worker composition *within* one
run (popoto db-cache staleness, `agent.hooks` cache corruption). Don't conflate
the two.
