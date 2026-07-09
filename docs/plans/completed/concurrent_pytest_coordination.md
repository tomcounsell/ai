---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1967
last_comment_id:
---

# Concurrent Full-Suite pytest Coordination

## Problem

On a 10-core machine, 3+ full-suite pytest invocations (manual + `/do-test` + `refresh_test_baseline.py`) can overlap, each spawning ~10 xdist workers. No entry point checks whether another full-suite run is in progress. System load average reached 79-82 (8x oversubscribed). One `refresh_test_baseline.py` run accumulated only 15 seconds of CPU across 90 minutes of wall-clock before being killed.

Concurrent runs also cause spurious test failures: subprocess-bound tests like `test_sdlc_sessionless_e2e.py` write to production Redis db=0 with a hardcoded sentinel `ISSUE_NUMBER = 999137`. Two concurrent runs race on the same Redis keys, nondeterministically failing or self-skipping.

**Current behavior:**
- `-n auto` always spawns one worker per core regardless of existing load
- No lock or coordination mechanism prevents concurrent full-suite invocations
- Hardcoded sentinel IDs in subprocess tests collide cross-run on production Redis db=0

**Desired outcome:**
- Full-suite pytest invocations detect an already-running full-suite and wait/queue rather than launching a competitor
- Tests with hardcoded sentinels are isolated per-run so targeted single-file test runs don't collide with a full-suite run in progress

## Freshness Check

**Baseline commit:** `509a412ad572368e98b7c504b413c847431a2449`
**Issue filed at:** 2026-07-09T05:40:57Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `pyproject.toml:149` — `-n auto --dist=loadfile` — still holds
- `scripts/refresh_test_baseline.py:21` — "3 runs, 60s per test" default — still holds
- `tests/integration/test_sdlc_sessionless_e2e.py:38` — `ISSUE_NUMBER = 999137` — still holds
- `tests/conftest.py:222-226` — per-worker db allocation (gw0->db1, gw1->db2) — already exists, partially addresses F2 within-run dimension
- `tests/integration/test_stage_comment.py:23` — `TEST_ISSUE_NUMBER = 520` — additional hardcoded sentinel found during recon, same pattern

**Cited sibling issues/PRs re-checked:**
- #1966 — closed as duplicate of #1967 (this issue)
- #1954 — closed, SDLC pipeline issue-locking (related but different concern)
- #476 — closed, flaky filter + deterministic baseline parsing (related but addresses a different layer — retry/filtering, not concurrency coordination)

**Commits on main since issue was filed (touching referenced files):**
- None — issue was filed today, no commits touched the referenced files since

**Active plans in `docs/plans/` overlapping this area:** None — recent plans cover unrelated areas (impact finder rerank, config magic literals, delivery paths, message drafter)

**Notes:** The issue's claim that `/do-docs` runs tests is inaccurate — `/do-docs` is a documentation cascade skill with no pytest invocation. The actual full-suite entry points are: manual `pytest tests/`, `/do-test` skill, `scripts/refresh_test_baseline.py`, and `scripts/pytest-clean.sh`. The issue's F2 "within-run" dimension is partially stale: `conftest.py:222-226` already allocates per-worker Redis dbs, so Popoto-backed tests within a single run are isolated. The remaining F2 problem is cross-run (subprocess tests writing to production db=0 with shared sentinels) and targeted single-file runs that bypass the full-suite lock.

## Prior Art

- **Issue #476 (closed)**: Test reliability flaky filter + deterministic baseline parsing. Added a retry step for flaky tests and junitxml-based baseline parsing. Addresses a different layer (post-failure classification/retry, not concurrency prevention). The flaky filter can absorb residual contention failures that slip through the coordination guard.
- **Issue #1954 (closed)**: SDLC pipeline issue-level locking. Added a lock to prevent two sessions from working the same GitHub issue. Different scope (SDLC session coordination, not pytest run coordination) but same conceptual pattern (lock file preventing concurrent access).
- **Issue #1966 (closed)**: Closed as duplicate of #1967. Originally filed for the Redis-state contention dimension; consolidated into this issue.
- **PR #1956**: The merge-gate investigation that surfaced the problem. 3+ overlapping full-suite runs caused load average 79-82 and spurious failures.

## Research

No relevant external findings — proceeding with codebase context and training data. This is an internal infrastructure problem with well-understood solutions (file-based locks, load-aware scheduling).

## Data Flow

1. **Entry point**: Developer or agent invokes `pytest tests/`, `scripts/pytest-clean.sh`, `scripts/refresh_test_baseline.py`, or `/do-test` skill
2. **pytest-xdist**: `-n auto` spawns one worker per CPU core (e.g., 10 workers on a 10-core machine)
3. **Worker isolation**: `conftest.py:redis_test_db` (autouse) assigns each worker its own Redis db (gw0->db1, gw1->db2, etc.)
4. **Subprocess tests**: Tests like `test_sdlc_sessionless_e2e.py` spawn `sdlc-tool` as subprocesses — these connect to Redis **independently**, bypassing the test fixture's per-worker db allocation and writing to production db=0
5. **Cross-run collision**: A second concurrent full-suite run's workers also get gw0->db1, gw1->db2, etc. — their subprocess tests write to the same db=0 with the same sentinel `ISSUE_NUMBER = 999137`
6. **CPU oversubscription**: 2 concurrent runs x 10 workers = 20 processes competing for 10 cores. 3 runs = 30 processes. Load average climbs to 8x normal.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on lock behavior: wait vs. refuse, timeout)
- Review rounds: 1 (code review, concurrency edge cases)

## Prerequisites

No prerequisites — this work has no external dependencies.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Test fixture isolation verification |

## Solution

### Key Elements

- **Full-suite lock** (`scripts/full_suite_lock.py`): A Python module providing `acquire()` / `release()` / `wait_for_lock()` operations on a PID-based lock file at `data/full-suite-running.lock`. Checks PID liveness to handle stale locks. Configurable timeout (default: wait up to 30 minutes, then proceed with reduced workers).
- **Load-aware worker count** (`scripts/full_suite_lock.py`): A helper function `recommended_workers()` that computes worker count from current load average rather than total CPU count. Used as a fallback when the lock is held by another run and the timeout expires.
- **pytest-clean.sh integration**: `scripts/pytest-clean.sh` calls `full_suite_lock.py acquire` before launching pytest and `release` on exit via its existing trap mechanism.
- **refresh_test_baseline.py integration**: `scripts/refresh_test_baseline.py` calls `full_suite_lock.py acquire` before each of its N sequential runs.
- **do-test integration**: Update `/do-test` PYTHON.md (repo context) to document the lock and recommend `scripts/pytest-clean.sh` as the entry point that handles coordination.
- **Hardcoded sentinel namespacing**: Make `ISSUE_NUMBER` in `test_sdlc_sessionless_e2e.py` and `TEST_ISSUE_NUMBER` in `test_stage_comment.py` unique per-invocation using a random suffix, so targeted single-file runs don't collide with a full-suite run's subprocess tests on production db=0.

### Flow

**Starting point** (developer runs `pytest tests/`) → `pytest-clean.sh` calls `full_suite_lock.py acquire` → **Lock acquired** (PID + timestamp written to `data/full-suite-running.lock`) → `pytest` runs with `-n auto` workers → **Tests complete** → `pytest-clean.sh` trap calls `full_suite_lock.py release` → **Lock released** → back to clean state

**Concurrent invocation**: Second `pytest-clean.sh` calls `acquire` → Lock held by first run → Second run waits (polling PID liveness, up to timeout) → First run completes, releases lock → Second run acquires lock and proceeds → normal execution

**Timeout fallback**: If timeout expires while waiting, second run proceeds with `recommended_workers()` (load-aware reduced worker count) and logs a warning.

### Technical Approach

- **Lock file format**: JSON `{"pid": <PID>, "started_at": <unix_ts>, "host": <hostname>}`. Hostname prevents cross-machine false positives if `data/` is on a shared filesystem (unlikely but defensive).
- **Stale lock detection**: Read lock file, check if PID is alive (`os.kill(pid, 0)`). If dead, remove lock and acquire. If alive and same host, wait.
- **Lock acquisition**: Write atomically using `O_EXCL` flag on a temp file then rename, or use `fcntl.flock` for race-free acquisition. PID-based check handles stale locks.
- **Release**: Only release if the lock file's PID matches the current process. Handles edge case where a timeout caused us to proceed without the lock and the original holder already released.
- **`recommended_workers()`**: `max(1, min(cpu_count, cpu_count - int(load_average_1min)))` — never more than free cores, never less than 1.
- **Sentinel namespacing**: Replace `ISSUE_NUMBER = 999137` with `ISSUE_NUMBER = 999137 + random.randint(0, 999)` in `test_sdlc_sessionless_e2e.py`. The number just needs to be high enough to avoid colliding with real issue numbers; the random suffix ensures uniqueness across concurrent invocations. Same pattern for `test_stage_comment.py`'s `TEST_ISSUE_NUMBER = 520` (though this one posts to a real GitHub issue, so it needs a different approach — use the issue's own number or create a dedicated test issue).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `full_suite_lock.py acquire()` — stale lock removal must not raise if the PID check fails (process already dead). Test: write lock with a dead PID, call acquire, verify it succeeds.
- [ ] `full_suite_lock.py release()` — must not raise if the lock file is already gone. Test: acquire, delete lock file manually, call release, verify no exception.
- [ ] `full_suite_lock.py` — must not raise on permission errors (read-only filesystem). Test: acquire on a read-only directory, verify it degrades gracefully (logs warning, proceeds without lock).

### Empty/Invalid Input Handling
- [ ] `recommended_workers()` with load average > cpu_count (all cores busy) — must return 1, not 0 or negative.
- [ ] `recommended_workers()` with load average = 0 (idle system) — must return cpu_count.
- [ ] Lock file with corrupt JSON — must be treated as a stale lock (remove and acquire).

### Error State Rendering
- [ ] When a concurrent run is detected, the waiting run must log a clear message: "Waiting for full-suite run PID=X (started Ym ago)..." so a human/agent watching understands the delay.
- [ ] When timeout expires, the run must log a warning: "Timed out waiting for lock; proceeding with N workers (load average: M)".

## Test Impact

- [ ] `tests/integration/test_sdlc_sessionless_e2e.py` — UPDATE: replace hardcoded `ISSUE_NUMBER = 999137` with a per-invocation unique sentinel
- [ ] `tests/integration/test_stage_comment.py` — UPDATE: replace hardcoded `TEST_ISSUE_NUMBER = 520` with a per-invocation unique sentinel or a dedicated test issue
- [ ] `tests/unit/test_full_suite_lock.py` — CREATE: unit tests for lock acquire/release/stale detection/recommended_workers
- [ ] No other existing tests affected — the lock file is additive and transparent to existing test behavior when no concurrent run is in progress

## Rabbit Holes

- **Cross-process locking via Redis instead of file**: Redis-based distributed lock is more robust across machines but adds a Redis dependency to the test runner. File-based lock is simpler and sufficient — `data/` is local to each machine.
- **pytest plugin instead of a script wrapper**: A conftest.py hook could acquire the lock at session start. But `refresh_test_baseline.py` runs pytest via subprocess, not via the conftest path, so it would bypass a conftest-only lock. The Python module approach is entry-point agnostic.
- **Full CI integration**: This is a local development coordination problem, not a CI problem. CI runs are serialized. Don't build CI-specific coordination.
- **Per-test locking**: The lock is for full-suite runs only, not individual test files. Targeted test runs (`pytest tests/unit/test_foo.py`) should not be blocked by the lock.

## Risks

### Risk 1: Lock file left behind by a crashed process
**Impact:** Subsequent full-suite runs hang waiting for a dead process's lock.
**Mitigation:** PID liveness check in `acquire()` — if the PID in the lock file is no longer alive, remove the stale lock and proceed. The existing `pytest-clean.sh` trap mechanism already handles cleanup on exit signals.

### Risk 2: Targeted test runs bypass the lock and still collide
**Impact:** A developer running `pytest tests/integration/test_sdlc_sessionless_e2e.py` while a full-suite run is in progress could collide on the same Redis keys.
**Mitigation:** Hardcoded sentinel namespacing (random suffix) ensures uniqueness even without the lock. The lock is the primary defense for full-suite runs; sentinel namespacing is defense in depth for targeted runs.

### Risk 3: Lock timeout too aggressive (aborts long-running baseline refresh)
**Impact:** `refresh_test_baseline.py` runs 3 sequential full-suite passes; each could take 2+ minutes. A 30-minute timeout should be generous enough, but on very slow machines it could expire.
**Mitigation:** Default timeout is 30 minutes (configurable via `--lock-timeout`). On timeout, proceed with `recommended_workers()` rather than aborting — degraded but functional.

## Race Conditions

### Race 1: Two processes acquire the lock simultaneously
**Location:** `scripts/full_suite_lock.py` acquire path
**Trigger:** Two `pytest-clean.sh` invocations start within milliseconds of each other, both check the lock file, both find it absent, both try to write.
**Data prerequisite:** Lock file must not exist.
**State prerequisite:** No other full-suite run is in progress.
**Mitigation:** Atomic file creation using `os.open(path, O_CREAT | O_EXCL)` — only one process succeeds, the other gets an `FileExistsError` and falls through to the wait/retry path.

### Race 2: Stale lock removal races with lock holder exiting
**Location:** `scripts/full_suite_lock.py` stale detection path
**Trigger:** Process A holds the lock and is about to release it. Process B detects A's PID as alive, waits. Process A exits and releases the lock. Process B's next poll cycle reads the lock file but it's gone.
**Data prerequisite:** Lock file exists with Process A's PID.
**State prerequisite:** Process A is exiting.
**Mitigation:** If the lock file disappears during polling, treat it as "lock released" and acquire immediately. The `release()` function only removes the lock if the PID matches, so there's no risk of removing someone else's lock.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Redis-based distributed locking across machines — file-based lock is sufficient for single-machine coordination. Cross-machine CI coordination is a separate concern.
- [EXTERNAL] Replacing `-n auto` with a custom pytest plugin that integrates load-aware scheduling — `pyproject.toml` configuration is simpler and sufficient. A plugin would be over-engineering for this appetite.
- [SEPARATE-SLUG] Adding `pytest-rerunfailures` or `pytest-flaky` as a retry plugin — issue #476 already addressed flaky test handling via a retry step in the `/do-test` pipeline. This plan focuses on preventing concurrent-run contention, not post-failure retry.

## Update System

No update system changes required — this feature is purely internal (test infrastructure). The lock file lives in `data/` which is already gitignored. No new dependencies, no config files to propagate, no migration steps. `scripts/pytest-clean.sh` and `scripts/refresh_test_baseline.py` are already on every machine via the repo.

## Agent Integration

No agent integration required for the lock mechanism itself — the lock is transparent to the agent. The agent invokes `/do-test` or `scripts/pytest-clean.sh` as before; the lock acquisition happens inside the script wrapper.

However, `/do-test`'s PYTHON.md should be updated to document the lock behavior so the agent understands why a test run might wait at the start. The agent should not interpret a lock-wait as a hang.

- [ ] Update `/do-test` PYTHON.md (repo context at `docs/sdlc/do-test.md` if it exists, or the global skill's PYTHON.md) with a note about the full-suite lock
- [ ] No new MCP server or CLI entry point required — the lock module is called internally by existing scripts

## Documentation

### Feature Documentation
- [ ] Create `docs/features/test-concurrency-coordination.md` describing the lock mechanism, how it prevents CPU starvation and cross-run Redis contention, and how to configure the timeout
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Module docstring in `scripts/full_suite_lock.py` explaining the lock protocol
- [ ] Comments in `scripts/pytest-clean.sh` at the lock acquisition point
- [ ] Comments in `scripts/refresh_test_baseline.py` at the lock acquisition point

## Success Criteria

- [ ] Concurrent full-suite invocations no longer oversubscribe CPU — the second invocation waits for the first to complete
- [ ] `scripts/pytest-clean.sh` acquires the lock before running pytest and releases it on exit (including signal-based exit)
- [ ] `scripts/refresh_test_baseline.py` acquires the lock before each sequential run
- [ ] Stale lock (dead PID) is detected and removed automatically
- [ ] Lock timeout logs a clear warning and proceeds with load-aware reduced workers
- [ ] `test_sdlc_sessionless_e2e.py` uses a per-invocation unique sentinel, not a hardcoded `ISSUE_NUMBER = 999137`
- [ ] Unit tests for lock acquire/release/stale detection/recommended_workers pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (lock module + script integration)**
  - Name: lock-builder
  - Role: Implement `scripts/full_suite_lock.py`, integrate into `pytest-clean.sh` and `refresh_test_baseline.py`
  - Agent Type: builder
  - Resume: true

- **Builder (sentinel namespacing)**
  - Name: sentinel-builder
  - Role: Update hardcoded sentinels in `test_sdlc_sessionless_e2e.py` and `test_stage_comment.py`
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: lock-validator
  - Role: Verify lock behavior, stale detection, timeout fallback, sentinel isolation
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create `docs/features/test-concurrency-coordination.md`, update index
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation (default for most work)
- `validator` - Read-only verification (no Write/Edit tools)
- `documentarian` - Documentation updates

## Step by Step Tasks

### 1. Build lock module
- **Task ID**: build-lock-module
- **Depends On**: none
- **Validates**: `tests/unit/test_full_suite_lock.py` (create)
- **Assigned To**: lock-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/full_suite_lock.py` with `acquire()`, `release()`, `wait_for_lock()`, `recommended_workers()` functions
- Lock file at `data/full-suite-running.lock` with JSON format: `{"pid", "started_at", "host"}`
- Atomic acquisition via `os.open(O_CREAT | O_EXCL)`; `FileExistsError` falls through to wait path
- Stale lock detection: read PID, check liveness via `os.kill(pid, 0)`, remove if dead
- `recommended_workers()`: `max(1, min(cpu_count, cpu_count - int(load_average_1min)))`
- CLI interface: `python scripts/full_suite_lock.py acquire --timeout 1800` / `release`
- Log clear messages: "Waiting for full-suite run PID=X..." / "Timed out, proceeding with N workers"

### 2. Build sentinel namespacing
- **Task ID**: build-sentinel-namespacing
- **Depends On**: none
- **Validates**: `tests/integration/test_sdlc_sessionless_e2e.py`, `tests/integration/test_stage_comment.py`
- **Assigned To**: sentinel-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `ISSUE_NUMBER = 999137` in `test_sdlc_sessionless_e2e.py` with `ISSUE_NUMBER = 999137 + random.randint(0, 999)` (module-level, set once per process)
- Replace `TEST_ISSUE_NUMBER = 520` in `test_stage_comment.py` — either use a dedicated test issue number (file an issue for test use) or use a random high number to avoid collision with real issues
- Ensure `_delete_local_session()` still cleans up the random session ID
- Verify the test still round-trips correctly with the randomized sentinel

### 3. Integrate lock into pytest-clean.sh
- **Task ID**: integrate-pytest-clean
- **Depends On**: build-lock-module
- **Validates**: manual verification — run `scripts/pytest-clean.sh tests/unit/` in two terminals, second should wait
- **Assigned To**: lock-builder
- **Agent Type**: builder
- **Parallel**: false
- Add lock acquisition before `pytest "$@"` line in `scripts/pytest-clean.sh`
- Add lock release to the existing `trap reap_workers EXIT INT TERM HUP PIPE` trap (extend it to also release the lock)
- Only acquire lock when running the full suite (detect via argument: if args contain `tests/` or no specific file, it's a full-suite run; if a specific test file is passed, skip the lock)

### 4. Integrate lock into refresh_test_baseline.py
- **Task ID**: integrate-refresh-baseline
- **Depends On**: build-lock-module
- **Validates**: `scripts/refresh_test_baseline.py --dry-run` still works
- **Assigned To**: lock-builder
- **Agent Type**: builder
- **Parallel**: false
- Call `full_suite_lock.py acquire` before each of the N sequential pytest runs
- Call `release` after each run (so the next run can acquire fresh)
- Or acquire once for all N runs (simpler, but blocks other runs for 3x longer — decide during build)

### 5. Validate lock behavior
- **Task ID**: validate-lock
- **Depends On**: integrate-pytest-clean, integrate-refresh-baseline, build-sentinel-namespacing
- **Assigned To**: lock-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `tests/unit/test_full_suite_lock.py` — verify all lock tests pass
- Verify stale lock detection: write a lock file with a dead PID, run acquire, verify it succeeds
- Verify concurrent acquisition: start two `acquire` calls, verify only one succeeds
- Verify sentinel isolation: run `test_sdlc_sessionless_e2e.py` twice concurrently, verify no collision
- Report pass/fail status

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-lock
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/test-concurrency-coordination.md` describing the lock mechanism, how it prevents CPU starvation and cross-run Redis contention, timeout configuration, and how `recommended_workers()` works
- Add entry to `docs/features/README.md` index table
- Update `/do-test` PYTHON.md with a note about the full-suite lock

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-lock, document-feature
- **Assigned To**: lock-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lock unit tests pass | `python -m pytest tests/unit/test_full_suite_lock.py -v` | exit code 0 |
| Stale lock auto-removed | `echo '{"pid":99999,"started_at":0,"host":"test"}' > data/full-suite-running.lock && python scripts/full_suite_lock.py acquire --timeout 1` | exit code 0 |
| Lock release is safe | `python scripts/full_suite_lock.py release` | exit code 0 |
| Recommended workers sane | `python -c "from scripts.full_suite_lock import recommended_workers; w = recommended_workers(); assert 1 <= w, w; print(w)"` | output contains a number >= 1 |
| No hardcoded 999137 sentinel | `grep -c '999137' tests/integration/test_sdlc_sessionless_e2e.py` | match count == 0 |
| Lint clean | `python -m ruff check scripts/full_suite_lock.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/full_suite_lock.py` | exit code 0 |
| Feature doc exists | `test -f docs/features/test-concurrency-coordination.md` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Lock timeout default**: 30 minutes is generous for a single full-suite run (~2-5 min on a fast machine, ~10-20 min on a slow one). Is 30 minutes the right default, or should it be shorter (e.g., 15 minutes)?
2. **Lock scope for `refresh_test_baseline.py`**: Should the lock be acquired once for all N sequential runs (blocks others for 3x longer but simpler), or acquired/released per-run (allows interleaving but adds complexity)? Plan assumes per-run for fairness; confirm during critique.
3. **`test_stage_comment.py` sentinel**: This test posts real comments to GitHub issue #520. Replacing the hardcoded issue number with a random one would post comments to a random issue. Should we create a dedicated test issue, or accept that this test is inherently not concurrency-safe and leave it as-is (the full-suite lock already prevents concurrent full-suite runs)?