---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2060
last_comment_id:
---

# Test-isolation flake: cross-process Redis test-db collision (#2060)

## Problem

`tests/integration/test_tool_budget_enforcement.py::test_cli_hook_denies_over_budget_exit_2`
fails intermittently — the CLI PreToolUse hook subprocess exits `0` instead of the
expected `2` (`assert 0 == 2`), with empty stdout/stderr. Filed under umbrella
#1897 as a separate, unproven root cause from the two #1897 already fixed.

**Current behavior:**
The test creates an over-budget `AgentSession` in the per-worker Redis test DB,
writes a sidecar, then spawns a **real** CLI-hook subprocess
(`.claude/hooks/pre_tool_use.py`) which resolves the session via
`AgentSession.get_by_id` and must `sys.exit(2)` (deny). Intermittently the
subprocess finds **no session**, so `_resolve_cli_session` returns `None`, the
budget backstop takes its genuine-no-session path, silently fails open, and the
hook exits `0`.

**Root cause (proven empirically):** `tests/conftest.py::redis_test_db` and
`_redis_test_db_num` map the test DB from the xdist worker id:
`gw{N} → db{N+1}`, and **master / non-xdist → db1**. This is unique *within* a
single pytest run, but **not across concurrent pytest processes**: a background
full-suite run's `gw0` also owns **db1**, and a standalone `pytest ::test`
(master) also owns **db1**. The `redis_test_db` fixture calls `flushdb()` at
every test's setup. So when two pytest processes run at once, one process's
per-test `flushdb()` **wipes the other's just-written `AgentSession` mid-test**.
The target test is uniquely exposed because it writes state, then reads it back
from a *fresh subprocess* a beat later — if db1 was flushed in that window, the
subprocess reads an empty DB → `get_by_id` → `None` → fail-open → exit 0.

This is cross-**process** contention, not xdist ordering — which is exactly why
it reproduces "standalone, single test" (a background full-suite `gw0` is the
concurrent flusher) and why it is a *separate* root cause from #1897's
within-run xdist mechanisms.

**Desired outcome:**
Each concurrent pytest process claims a **unique** Redis test DB, so no two
processes ever share a DB and cannot `flushdb()` each other's data. The target
test then deterministically resolves its over-budget session and exits 2.

## Freshness Check

**Baseline commit:** c4e1a1368
**Issue filed at:** 2026-07-16 (author: valorengels)
**Disposition:** Unchanged

**File:line references re-verified:**
- `tests/conftest.py:306-310` (`redis_test_db` db derivation) — still `gw{N}→db{N+1}`, master→`db1`. Holds.
- `tests/conftest.py:356-361` (`_redis_test_db_num`) — identical derivation. Holds.
- `.claude/hooks/pre_tool_use.py:196-219` (`_resolve_cli_session` → `get_by_id`, returns None as genuine-no-session) — holds.
- `tests/integration/test_tool_budget_enforcement.py:205-246` (`_run_cli_hook` derives subprocess db from `POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"]`; target test) — holds; subprocess automatically follows whatever db the fixture picked.

**Cited sibling issues/PRs re-checked:**
- #1897 — parent umbrella (xdist isolation flakes). This plan is a follow-up instance under it, as the issue directs. Not superseded.
- #2037 — resolved by #1897 Fix 1 (popoto db-cache split-brain). Distinct mechanism; not this bug.

**Commits on main since issue was filed (touching referenced files):** none touching `tests/conftest.py`.

**Active plans in `docs/plans/` overlapping this area:** `xdist-test-isolation-flakes.md` (#1897 umbrella) — this plan is the residual instance it explicitly defers here; coordinate, do not merge.

**Notes:** Root cause reproduced against current main: with a concurrent
`redis-cli -n 1 flushdb` loop the target test fails **5/10**; with no contention
**0/12**. Confirmed the subprocess observes `db=1 dbsize=0` (an emptied DB) at
failure time, and a live concurrent `pytest tests/` (`scripts/pytest-clean.sh`,
gw0→db1) was the natural flusher during investigation.

## Prior Art

- **#1897** *(umbrella)*: xdist test-isolation flakes. Fixed two within-run
  mechanisms (popoto db-cache split-brain; agent-hooks hooks-less-parent
  corruption). Its deterministic acceptance is
  `tests/unit/test_conftest_isolation_guards.py`. It explicitly downgraded THIS
  test's poisoning-ordering re-run to "best-effort, may pass vacuously" and
  routed the residual here — this plan.
- **#2037**: create-then-`query.filter` miss under `--dist=loadfile` — resolved
  by #1897 Fix 1. Different mechanism (import-time db binding), not cross-process.
- **`data/full-suite-running.lock`** (`docs/features/full-suite-pytest-lock.md`):
  an existing advisory lock that serializes concurrent **full-suite** runs. It
  reduces — but does not eliminate — the collision (a full run vs. a single-test
  run, or vs. a manual script, still collide on db1). This plan complements it.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR for #1897 (Fix 1) | Rebuilt popoto db-cache on module identity change | Addressed *within-run* import-time db split-brain, not cross-*process* db sharing. A second pytest process still flushes the same db1. |
| `full-suite-running.lock` | Serializes two full-suite runs | Does not cover full-suite-vs-single-test, or manual-script-vs-pytest; those still share db1. |

**Root cause pattern:** the test DB is partitioned by *xdist worker id within a
run*, never by *process*. Any second pytest process on the machine re-derives
the same small db numbers and flushes them.

## Data Flow

1. **Entry point:** `redis_test_db` (autouse, function-scoped) runs at each test
   setup → derives `test_db` → `redis.Redis(db=test_db)` → **`flushdb()`** →
   rebinds `POPOTO_REDIS_DB` to the test client.
2. **Target test parent:** `make_session()` → `AgentSession.create(...).save()`
   writes the over-budget session into `db=test_db`.
3. **Concurrent process (the bug):** a *different* pytest process whose fixture
   also derived `db=test_db` runs ITS setup → `flushdb()` on the shared db →
   the target test's session is deleted.
4. **Target test subprocess:** `_run_cli_hook` spawns
   `.claude/hooks/pre_tool_use.py` with `REDIS_URL=…/{test_db}` →
   `AgentSession.get_by_id` scans an **empty** DB → `None`.
5. **Output:** `_resolve_cli_session` → `None` → genuine-no-session → silent
   allow → `exit 0`. Assertion `0 == 2` fails.

The fix inserts, before step 1's derivation, a **per-process unique-DB claim**
so step 3's process can never derive the same `test_db`.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0-1 (root cause already proven; scope is contained)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Local Redis reachable | `redis-cli ping` | Test DBs live on localhost:6379 |
| Redis has ≥16 logical DBs | `redis-cli config get databases` | Pool [1..15] usable for tests (db0 = prod) |

## Solution

### Key Elements

- **Per-process test-DB claim**: a process-lifetime, filesystem-atomic claim of
  a unique DB number from the pool `[1..15]`, so each live pytest process (master
  or xdist worker) owns a distinct test DB no other process will `flushdb()`.
- **Stale-claim reclamation**: a claim whose owner PID is dead (or whose record
  is implausibly old) is reclaimable, so a crashed/`SIGKILL`ed run never
  permanently strands a DB.
- **Graceful fallback**: if the pool is exhausted or the claim registry is
  unreachable, fall back to the current `worker_id+1` / master=`1` derivation —
  never worse than today.
- **Single source of truth**: both `redis_test_db` and `_redis_test_db_num` read
  the *same* claimed number, so `redis_test_url` and the fixture agree (the
  subprocess in `_run_cli_hook` already derives its db from the live
  `POPOTO_REDIS_DB` client, so it inherits the claim automatically — no change).

### Flow

pytest process starts → first `redis_test_db`/`_redis_test_db_num` call →
`_claim_test_db()` atomically claims first free DB in `[1..15]` (reclaiming
dead-PID slots) → number cached for the process's lifetime → every test in the
process uses that DB (flush + patch unchanged) → session end → claim released.

### Technical Approach

- **Claim registry (filesystem, mirrors `data/full-suite-running.lock`):** a
  directory `data/test-db-claims/` with one lock file per claimed DB, e.g.
  `data/test-db-claims/{n}.claim` containing `"{pid}\n{ts}"`. Atomic create via
  `os.open(path, O_CREAT|O_EXCL|O_WRONLY)`. Filesystem is the right substrate:
  all pytest processes/workers share one machine, and it keeps test bookkeeping
  out of the production Redis db0.
- **`_claim_test_db()`** (module-level in `tests/conftest.py`, memoized in a
  process global `_CLAIMED_TEST_DB`):
  1. If already claimed this process, return the cached number.
  2. For `n` in `1..15`: try `O_EXCL` create of `{n}.claim`. On success, write
     `{pid,ts}`, cache `n`, return `n`. On `FileExistsError`, read the file; if
     its PID is not alive (`os.kill(pid, 0)` raises `ProcessLookupError`) OR the
     record is malformed/older than `CLAIM_STALE_SECONDS`, atomically reclaim
     (remove + retry `O_EXCL`). Guard the read/reclaim so a race between two
     reclaimers resolves to exactly one winner (re-`O_EXCL` after unlink; if that
     fails, the other process won — continue to next `n`).
  3. If no slot claimable, log a warning and return the **legacy** derivation
     (`gw{N}→N+1`, master→1). Fallback keeps today's behavior exactly.
- **Release:** a session-scoped autouse finalizer (and an `atexit` backstop)
  removes this process's `{n}.claim` file. Stale files from hard-killed runs are
  reclaimed lazily by the next claimant via the PID-liveness check, so no
  separate reaper is required.
- **Wire-in:** `redis_test_db` replaces its inline `worker_id`→`test_db` block
  with `test_db = _claim_test_db(request)`; `_redis_test_db_num(request)` returns
  `_claim_test_db(request)`. The legacy derivation is preserved *inside*
  `_claim_test_db` as the fallback branch (single definition, no drift).
- **Determinism knobs (named constants, env-overridable per repo convention):**
  `TEST_DB_POOL = range(1, 16)`, `CLAIM_STALE_SECONDS` (default e.g. 7200,
  provisional/tunable), claim dir path. Grain-of-salt comment marks the stale
  window provisional.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_claim_test_db` fallback branch (registry unreachable / pool exhausted /
  FS error) must be covered by a test asserting it returns the legacy number and
  logs a warning (observable behavior, not a silent swallow).
- [ ] Reclaim-race guard: assert exactly one of two contending reclaimers wins
  a stale slot (no double-claim).

### Empty/Invalid Input Handling
- [ ] Malformed / empty `{n}.claim` file (partial write, empty, non-integer PID)
  must be treated as stale/reclaimable, not crash the fixture. Add a test.
- [ ] Missing `data/test-db-claims/` directory is created on demand (test the
  cold-start path).

### Error State Rendering
- [ ] Fallback path logs a WARNING naming the reason (exhausted vs. FS error) so
  a degraded run is visible in test output, not silent. Assert the log.

## Test Impact

- [ ] `tests/unit/test_conftest_isolation_guards.py` — UPDATE: add a new class
  covering the per-process DB claim (distinct claims, dead-PID reclaim,
  malformed-file reclaim, fallback-on-exhaustion). This file is the established
  home for conftest-isolation regression locks (#1897), so the new deterministic
  acceptance for #2060 belongs here.
- [ ] `tests/integration/test_tool_budget_enforcement.py::test_cli_hook_denies_over_budget_exit_2`
  — UPDATE (no code change to the test body): it must now pass under a concurrent
  `flushdb` loop. Verified by the plan's Verification section (repeated runs with
  a deliberate concurrent flusher on the *legacy* db1 must no longer flake,
  because the process now owns a claimed db ≠ 1). No assertion changes.
- Existing `redis_test_db` behavior (per-test `flushdb`, popoto-module patching,
  async-db rebind, teardown restore) is **unchanged** — only the DB *number*
  source changes. No other test should observe a difference beyond running on a
  higher DB number.

## Rabbit Holes

- **Do NOT rework `.claude/hooks/pre_tool_use.py` resolution semantics.** The
  `get_by_id`→None conflation (infra-wipe vs. genuine-no-session) is only
  reachable *because* the DB was wiped; eliminating the cross-process wipe
  removes the trigger entirely. Refactoring the hook's fail-open split is a
  separate concern and out of scope.
- **Do NOT try to give two concurrent full `-n auto` runs disjoint DBs.** With
  15 test DBs that is impossible (each run wants ~n-workers DBs). The
  `full-suite-running.lock` already serializes full-vs-full; the fallback branch
  covers the (already-serialized) exhaustion case. Chasing per-DB namespacing /
  key-prefix isolation is a large rewrite for no additional real-world coverage.
- **Do NOT touch Popoto's `scan_keys`/`get_by_id` or the redis-py connection
  pool.** Early investigation chased a "cold-connection SCAN" red herring; the
  real cause is the flush, not the scan. Leave the ORM alone.
- **Do NOT add a retry/sleep to the target test.** That masks, not fixes; the
  claim removes the nondeterminism at its source.

## Risks

### Risk 1: Filesystem claim leaks under hard-kill
**Impact:** A `SIGKILL`ed pytest process leaves a `{n}.claim` file; if not
reclaimed, that DB is permanently removed from the pool, shrinking capacity.
**Mitigation:** Reclamation is PID-liveness based (`os.kill(pid, 0)`) plus an
age backstop (`CLAIM_STALE_SECONDS`). The next claimant reclaims dead-PID slots
lazily — no reaper needed. Worst case the pool shrinks by leaked slots until an
age-based reclaim; fallback still prevents a hard failure.

### Risk 2: Higher/variable DB numbers surprise a test that hard-codes db1
**Impact:** Any test or helper assuming `db=1` breaks when the process claims a
different DB.
**Mitigation:** Grep confirms all test-db consumers go through `redis_test_db` /
`_redis_test_db_num` / `redis_test_url`; the `_run_cli_hook` subprocess derives
its db from the live client. The build step re-greps for hard-coded `db=1` / `/1`
in tests and routes any through the claim.

### Risk 3: PID reuse causes a live-looking stale claim
**Impact:** A recycled PID could make a genuinely-stale slot look alive (blocking
reclaim) or vice-versa.
**Mitigation:** The age backstop (`CLAIM_STALE_SECONDS`) bounds this; and a
false "alive" only means the claimant skips to the next free slot (correctness
preserved, capacity slightly reduced). No incorrect double-claim results.

## Race Conditions

### Race 1: Two processes claim the same free DB simultaneously
**Location:** `tests/conftest.py::_claim_test_db` (new)
**Trigger:** Two pytest processes start at the same instant and both scan the
pool.
**Data prerequisite:** The `{n}.claim` file must be the single arbiter of
ownership.
**State prerequisite:** Claim creation must be atomic.
**Mitigation:** `os.open(..., O_CREAT|O_EXCL)` is atomic on POSIX — exactly one
creator wins per slot; the loser moves to the next `n`. The reclaim path
re-`O_EXCL`s after `unlink` so a stale-slot race also has a single winner.

### Race 2: Concurrent `flushdb` on the target test's DB (the bug itself)
**Location:** cross-process, `redis_test_db` `flushdb()` calls
**Trigger:** Two processes share a DB number and one flushes mid-test.
**Data prerequisite:** The over-budget `AgentSession` must survive from
`make_session()` until the subprocess reads it.
**State prerequisite:** No other process may own the same DB.
**Mitigation:** The per-process claim guarantees DB-number disjointness among
live processes — the core fix. This race is eliminated, not merely narrowed
(within the 15-DB capacity; beyond it the already-serialized full-suite lock
applies).

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The
`get_by_id`→None conflation in `.claude/hooks/pre_tool_use.py` is intentionally
NOT changed: it is only reachable via the DB-wipe this plan removes, so touching
it would be speculative scope creep (see Rabbit Holes).

## Update System

No update system changes required — this is purely internal test infrastructure
(`tests/conftest.py`). No new runtime dependencies, no `scripts/update/run.py`
or `migrations.py` changes, no config to propagate. No Popoto model changes, so
no schema migration.

## Agent Integration

No agent integration required — this is a test-only change. No MCP server,
`.mcp.json`, CLI entry point, or bridge import is affected. The agent never
invokes the test-DB claim; it exists solely for the pytest fixture layer.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/full-suite-pytest-lock.md` (or add a short sibling
  section / note) describing the per-process test-DB claim: the pool `[1..15]`,
  the `data/test-db-claims/` registry, PID-liveness + age-based reclamation, and
  the legacy fallback. This is the natural home since it already documents
  cross-process test-run coordination.
- [ ] Add a one-line pointer in `tests/README.md` (test-isolation section) to
  the per-process DB claim, so contributors know test DBs are now
  process-unique, not just worker-unique.

## Success Criteria

| # | Acceptance criterion | How verified |
|---|----------------------|--------------|
| 1 | Two live processes never share a test DB | Unit test: two simulated claims (distinct PIDs) return distinct DB numbers |
| 2 | A dead-PID claim is reclaimed | Unit test: pre-write a `{n}.claim` with a dead PID; next claim reclaims `n` |
| 3 | A malformed/empty claim file is treated as stale | Unit test: pre-write empty/garbage file; claim reclaims it, no crash |
| 4 | Pool exhaustion falls back to legacy derivation + WARNING | Unit test: fill `[1..15]` with live claims; claim returns legacy number and logs warning |
| 5 | `redis_test_db` and `_redis_test_db_num` agree | Unit test: both return the same claimed number for the same process |
| 6 | Target test no longer flakes under concurrent legacy-db1 flush | Run `test_cli_hook_denies_over_budget_exit_2` ≥20× while a `redis-cli -n 1 flushdb` loop runs; 0 failures (process owns a claimed db ≠ 1) |
| 7 | #1897 isolation guards still pass | `tests/unit/test_conftest_isolation_guards.py` green |
| 8 | Hook resolution semantics unchanged (anti-criterion) | `git diff` shows no change to `.claude/hooks/pre_tool_use.py` |

## Step by Step Tasks

1. Add `_claim_test_db(request)` + release finalizer + module constants
   (`TEST_DB_POOL`, `CLAIM_STALE_SECONDS`, claim dir) to `tests/conftest.py`,
   with the legacy derivation preserved as the fallback branch.
2. Wire `redis_test_db` and `_redis_test_db_num` to `_claim_test_db`.
3. Add a session-scoped autouse release finalizer + `atexit` backstop.
4. Re-grep tests for hard-coded `db=1` / `redis://…/1` and route any stragglers
   through the claim/url helpers.
5. Add the regression class to `tests/unit/test_conftest_isolation_guards.py`
   (criteria 1-5) with `finally`-block state restoration (per that file's rule).
6. Update `docs/features/full-suite-pytest-lock.md` and `tests/README.md`.
7. Verify: run the unit guards (`-n0`), then run criterion 6 (concurrent-flusher
   loop) on the target test; confirm 0 failures.

## Open Questions

None blocking. The design mirrors the existing `data/full-suite-running.lock`
cross-process coordination pattern and stays within the proven root cause.
