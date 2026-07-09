# Test Concurrency Coordination

A file-based coordination lock prevents concurrent full-suite pytest invocations from oversubscribing CPU and causing cross-run Redis contention via hardcoded sentinel IDs.

## The Problem

Two failure modes could collide when full-suite pytest runs were launched concurrently on the same machine:

1. **CPU oversubscription.** A 10-core machine left to run two full suites in parallel drove load averages to 79-82. xdist workers from both runs competed for the same cores, thrashing the scheduler and slowing both runs below their serial baseline.
2. **Cross-run Redis contention.** Integration tests reused hardcoded sentinel GitHub issue numbers (e.g. `ISSUE_NUMBER = 12345`). Two concurrent suites would both write SDLC stage comments and session records against the same issue number, producing state collisions and flaky failures that looked like genuine regressions.

## The Solution

A single-file coordination lock at `data/full-suite-running.lock` serializes full-suite runs. The first invocation acquires the lock; a second invocation waits (up to a timeout) and only proceeds once the holder releases the lock or dies. Targeted test runs (a specific file or subdirectory) skip the lock entirely so they are never blocked by a concurrent full suite.

## How the Lock Works

The lock lives in [`scripts/full_suite_lock.py`](../../scripts/full_suite_lock.py).

**Atomic acquisition.** `acquire()` attempts `os.open(path, O_CREAT | O_EXCL | O_WRONLY)`. `O_CREAT | O_EXCL` is atomic at the syscall level: exactly one caller gets the file descriptor, everyone else gets `FileExistsError`. On success the holder writes a JSON metadata file:

```json
{"pid": <PID>, "started_at": <unix_ts>, "host": "<hostname>"}
```

The hostname (`socket.gethostname()`) disambiguates holders on shared filesystems (e.g. an NFS-mounted `data/`) so a lock left by a machine that no longer runs is treated as stale, not blocking.

**Stale lock detection.** If the file already exists, `wait_for_lock()` polls every 2 seconds. A lock is treated as stale (and removed) when:

- the stored `pid` is no longer alive (`os.kill(pid, 0)` raises `ProcessLookupError`), or
- the JSON is corrupt or unreadable, or
- the stored `host` does not match the current hostname.

**PID-guarded release.** `release()` only removes the lock if the stored `pid` matches `os.getpid()`. A caller that timed out and proceeded without the lock never clobbers the original holder's lock. The CLI `release` subcommand force-removes the file because it runs as a separate process whose PID never matches the acquire-time PID — the shell wrapper (`pytest-clean.sh`) tracks `LOCK_ACQUIRED` and only invokes CLI release when it actually acquired.

**Timeout fallback.** If the timeout expires, `acquire()` returns `False` and the caller proceeds anyway with a reduced worker count via `recommended_workers()` rather than blocking indefinitely.

## `recommended_workers()`

When a caller times out waiting for the lock, it falls back to a load-aware worker count instead of `cpu_count`:

```python
def recommended_workers() -> int:
    cpu = os.cpu_count() or 1
    load = os.getloadavg()[0]
    return max(1, min(cpu, cpu - int(load)))
```

Formula: `max(1, min(cpu_count, cpu_count - int(load_average_1min)))`. On a 10-core machine already carrying a load of 8, this yields `min(10, 10 - 8) = 2` workers instead of 10. It never returns less than 1.

## Timeout Configuration

- Default timeout: **30 minutes (1800 seconds)**, hardcoded as `SUITE_LOCK_TIMEOUT` in `scripts/refresh_test_baseline.py` and passed as `--timeout 1800` from `scripts/pytest-clean.sh`.
- CLI override: `python scripts/full_suite_lock.py acquire --timeout <seconds>`.

On timeout, `acquire()` logs a warning with the current load average and the computed worker count, then returns `False` so the caller proceeds without the lock.

## Integration Points

### `scripts/pytest-clean.sh`

The shell wrapper is the primary entry point for full-suite runs. It:

1. Detects a full-suite run via `is_full_suite_run()`: true when an argument is exactly `tests` or `tests/`, or when there are no positional file/dir arguments at all. A specific path deeper than `tests/` (e.g. `tests/unit/test_foo.py`) is a targeted run and skips the lock.
2. Calls `python scripts/full_suite_lock.py acquire --timeout 1800` and records the result in `LOCK_ACQUIRED`.
3. Registers a `trap cleanup EXIT INT TERM HUP PIPE` that reaps xdist workers **and**, only if `LOCK_ACQUIRED=1`, releases the lock. A targeted run that never acquired never releases someone else's lock.

Targeted runs are never blocked by a concurrent full-suite run.

### `scripts/refresh_test_baseline.py`

The baseline refresh tool runs the suite N times sequentially. Each per-run iteration:

1. Acquires the lock via `full_suite_lock.acquire(timeout=SUITE_LOCK_TIMEOUT)` (in-process import, not the CLI).
2. Runs pytest once inside a `try` block.
3. Releases the lock in the `finally` so the next run can acquire fresh, allowing interleaving with other waiting runs.

The lock is skipped in `--dry-run` mode (`use_lock = not args.dry_run`) so dry-runs never block on, or hold, the lock.

## Sentinel Namespacing

To eliminate cross-run Redis contention at the source, integration tests now randomize the sentinel GitHub issue numbers they use:

- **`tests/integration/test_sdlc_sessionless_e2e.py`** — `ISSUE_NUMBER = 1_000_000 + random.randint(0, 999)`, and `LOCAL_SESSION_ID = f"sdlc-local-{ISSUE_NUMBER}"`. The `1_000_000` base keeps the number out of any real issue range while the `0..999` suffix makes collisions between concurrent runs astronomically unlikely.
- **`tests/integration/test_stage_comment.py`** — `TEST_ISSUE_NUMBER = random.randint(900000, 999999)`.

Two concurrent suites will almost certainly draw different sentinel IDs, so even if they did run simultaneously their SDLC stage comments and session records would not collide.

## How to Use

No change to existing workflows. Just run the suite as before:

```bash
scripts/pytest-clean.sh tests/            # full suite — lock acquired
scripts/pytest-clean.sh tests/unit/test_foo.py  # targeted — lock skipped
```

The lock is transparent for full-suite runs (acquire on start, release on exit via trap). Targeted test runs skip the lock entirely and are never blocked by a concurrent full suite.

If a full-suite run is already in progress, the second invocation waits up to 30 minutes. On timeout it proceeds with a load-aware reduced worker count rather than failing.

## CLI Interface

The lock module exposes a CLI for operator use:

```bash
# Acquire (blocks up to 30 min by default; exit 0 if acquired, 1 on timeout, 2 on error)
python scripts/full_suite_lock.py acquire --timeout 1800

# Release (force-removes the lock file; safe if already gone)
python scripts/full_suite_lock.py release

# Custom lock path (rarely needed)
python scripts/full_suite_lock.py acquire --lock-path /tmp/custom.lock
```

Exit codes for `acquire`: `0` if the lock was acquired (caller should release later), `1` if the timeout expired (proceed without the lock), `2` on unexpected errors. Permission errors on a read-only filesystem are caught and logged — the caller proceeds without the lock rather than crashing.

## See Also

| Resource | Purpose |
|----------|---------|
| [`scripts/full_suite_lock.py`](../../scripts/full_suite_lock.py) | Lock module + CLI |
| [`scripts/pytest-clean.sh`](../../scripts/pytest-clean.sh) | Full-suite detection + trap-based release |
| [`scripts/refresh_test_baseline.py`](../../scripts/refresh_test_baseline.py) | Per-run acquire/release with `try/finally` |
| [do-test addendum](../sdlc/do-test.md) | Repo-specific test runner guidance |