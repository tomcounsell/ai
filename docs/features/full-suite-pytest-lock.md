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

Lock location: `data/full-suite-running.lock/` (a directory containing
`owner.pid`), relative to the pytest rootdir — so a worktree run coordinates
against its own worktree, not the primary checkout.

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

## Tests

`tests/unit/test_suite_lock.py` covers full-suite detection, the
take/wait/steal policy, and real filesystem acquire/release — including a second
waiter backing off when the owner is alive and a crashed owner's stale lock
being reclaimed.
