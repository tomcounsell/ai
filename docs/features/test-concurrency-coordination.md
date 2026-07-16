# Test Concurrency Coordination

Companion to [Full-suite pytest advisory lock](full-suite-pytest-lock.md),
which documents the canonical lock module (`scripts/suite_lock.py`) and its
integration in `scripts/pytest-clean.sh`. This page covers the two
coordination surfaces that issue #1967's PR #1984 added on top of that lock:
the `refresh_test_baseline.py` per-run integration and the sentinel-ID
namespacing that eliminates cross-run Redis contention at the source.

## `scripts/refresh_test_baseline.py` integration

The baseline refresh tool runs the suite N times sequentially. Each per-run
iteration acquires the full-suite lock before launching pytest and releases
it in a `finally` block, so a second concurrent full-suite invocation (manual
or `/do-test`) waits rather than piling on. The lock is acquired in-process via
`scripts.suite_lock` (not the CLI):

```python
from scripts import suite_lock

SUITE_LOCK_DIR = suite_lock.default_lock_dir()
SUITE_LOCK_TIMEOUT = 1800  # provisional/tunable

suite_lock.acquire(lock_dir=SUITE_LOCK_DIR, owner_pid=os.getpid(), timeout=SUITE_LOCK_TIMEOUT)
try:
    ...
finally:
    suite_lock.release(lock_dir=SUITE_LOCK_DIR, owner_pid=os.getpid())
```

The lock dir comes from `suite_lock.default_lock_dir()` — the canonical
machine-global default (a `/tmp` path keyed to the repo's git common dir, shared
across all worktrees; see [Full-suite pytest advisory lock](full-suite-pytest-lock.md#lock-location--machine-global-shared-across-worktrees)),
so the refresh tool coordinates against the same lock as every other full-suite
entry point, including runs from separate worktrees (issue #2064).

The lock is skipped in `--dry-run` mode (`use_lock = not args.dry_run`) so
dry-runs never block on, or hold, the lock. Releasing in the `finally` lets the
next iteration acquire fresh, allowing interleaving with other waiting runs.

## Sentinel-ID namespacing (F2 defense-in-depth)

The full-suite lock (F1) serializes runs so two concurrent suites cannot race
on the same hardcoded Redis sentinel. Sentinel-ID namespacing is the F2
defense-in-depth: even if two suites *did* run simultaneously (lock disabled,
timeout-proceeded-unlocked, or operator error), their session records would
not collide because the sentinel issue number is randomized per run.

| Test file | Sentinel assignment |
|-----------|----------------------|
| `tests/integration/test_sdlc_sessionless_e2e.py` | `ISSUE_NUMBER = 1_000_000 + random.randint(0, 999)`, with `LOCAL_SESSION_ID = f"sdlc-local-{ISSUE_NUMBER}"`. The `1_000_000` base keeps the number out of any real issue range while the `0..999` suffix makes collisions between concurrent runs astronomically unlikely. |
| `tests/integration/test_stage_comment.py` | Keeps the fixed `TEST_ISSUE_NUMBER = 520` deliberately — the test posts through the real `gh` CLI, and a random non-existent issue number makes `gh` return rc=1. Its concurrency safety comes from the full-suite lock plus `--dist=loadfile`, as documented in the test's inline comment. |

Two concurrent suites will almost certainly draw different
`test_sdlc_sessionless_e2e.py` sentinel IDs, so even without the lock those
session records would not collide.

## See Also

| Resource | Purpose |
|----------|---------|
| [Full-suite pytest advisory lock](full-suite-pytest-lock.md) | Canonical lock module (`scripts/suite_lock.py`) and `pytest-clean.sh` integration |
| [`scripts/suite_lock.py`](../../scripts/suite_lock.py) | Lock module + CLI |
| [`scripts/refresh_test_baseline.py`](../../scripts/refresh_test_baseline.py) | Per-run acquire/release with `try/finally` |
| [do-test addendum](../sdlc/do-test.md) | Repo-specific test runner guidance |
