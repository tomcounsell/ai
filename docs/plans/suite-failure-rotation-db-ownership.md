---
status: Planning
type: bug
revision_applied: true
revision_applied_at: 2026-08-07T07:26:00Z
appetite: Medium
owner: Valor Engels
created: 2026-08-07
tracking: https://github.com/tomcounsell/ai/issues/2628
last_comment_id: 5212938640
---

# Stop the unit suite's failure set from rotating: enforce test-DB ownership

## Problem

Two `tests/unit/` runs on the same commit, same machine, produce different failure sets —
3 failures then 5, with only one node in common. Every failing node passes in isolation.

**Current behavior:** Two tests call `flushdb()` on a Redis database that a *different live
pytest process* owns, wiping it mid-test. The victim is whichever process happens to hold
that slot, at whatever moment the flusher runs, so the damage lands on an arbitrary handful
of tests and rotates every run.

Since PR #2606, a pytest process's test database is assigned by an `flock` claim over the
pool `[1..15]` (`tests/db_claim.py:120-153`). Two call sites never got the memo and still
compute a database the pre-#2606 way:

| Call site | What it flushes | Why it is wrong |
|---|---|---|
| `tests/unit/test_redis_flush_guard.py:21-24,53-57` | `_own_test_db(request)` = `gw{N}` → `db{N+1}` | Its docstring says "matches redis_test_db()". It has not since #2606. |
| `tests/unit/test_conftest_isolation_guards.py:359-366,420` | hardcoded db **15**, flushed twice | The comment calls 15 "scratch space", but `tests/db_claim.py:48` sets `_TEST_DB_POOL_MAX = 15` — db 15 **is inside the claim pool**. |

Nothing stops either one. The flush guard installed at `tests/conftest.py:103-150` blocks
only `db == 0`; every `db >= 1` flush is permitted regardless of who owns it.

Reproduced on current main (`e6d0e2bc7`):

```
$ PYTEST_XDIST_WORKER=gw3 .venv/bin/python -c \
    "from tests.db_claim import claim_test_db, _legacy_test_db_num; print(claim_test_db(), _legacy_test_db_num())"
claimed slot     : 11
legacy derivation: 4    # what test_redis_flush_guard._own_test_db computes for gw3
MISMATCH -> cross-process flush
```

This is a **measuring-instrument defect**, not an ordinary flake. A suite whose failure set
changes between identical runs cannot answer "did my change break something." Two agents this
session mistook ambient rotation for a regression in the branch under review, and one mistook a
real regression for ambient rotation. Issue #2628 is load-bearing for the #2494 cutover: phases
2-3 are hot-intake-path releases and cannot be certified against a rotating baseline.

**Desired outcome:** Ownership of a test database is an *enforced invariant*, not a convention
that each call site is trusted to re-derive correctly. A test that flushes a database this
process has not claimed fails loudly at the offending line, in that test, every time — proven
by a ~1 s deterministic test that spawns a real competing flock holder, red on `main` and green
on the branch.

**A second, independent writer.** Cross-db `flushdb` is not the whole story. Critique found a
module-reload writer that rotates by the same mechanism and is entirely unrelated to Redis:
`tests/unit/test_index_drift.py:207-208` calls `importlib.reload(agent.index_drift)` with no
`try/finally` and no restore, so the reloaded module stays in `sys.modules` for the rest of that
worker's life. The reload rebinds `DRIFT_COVERED_MODELS` (`agent/index_drift.py:144`) to a *new*
dict and re-runs the three `register_drift_model` calls into it, while
`tests/unit/test_index_drift_coverage.py:26-32` holds the orphaned old dict bound at collection
time. Its `restore_registry` fixture (`:37-42`) then snapshots and restores the *stale* dict while
`register_drift_model` writes `FakeCoveredModel` into the *live* one — the cleanup silently
no-ops and a fake in-memory model stays registered for the rest of the worker. `--dist=loadfile`
assigns whole files to workers dynamically, so reloader and victim co-land only sometimes: the
same rotate-run-to-run signature, on a path `flushdb` ownership cannot touch. The existing reload
guard cannot see it — `tests/conftest.py:384` scopes `_SHARED_EXCEPTION_MODULES` to
`("models.session_lifecycle",)` and `_snapshot_shared_exceptions` (`:391-401`) snapshots only
`BaseException` subclasses; registry dicts, dataclasses, and enums fall outside it.

**A third writer, and the highest-frequency one: popoto's own pytest plugin flushes db 15 before
every test.** Round-2 investigation found that `popoto` ships a `pytest11` entry point
(`popoto.pytest_plugin`) which this repo **loads on every run** — `pyproject.toml:195` addopts
disable only `postgresql` (`-p no:postgresql`), never `popoto`, and the repo sets neither
`POPOTO_TEST_DB` nor the `popoto_test_db` ini option. The plugin therefore runs on its default of
**db 15**, which is the top slot of this repo's claim pool `[1..15]` (`tests/db_claim.py:48`,
`_TEST_DB_POOL_MAX = 15`). It installs a **function-scoped autouse** fixture `_popoto_flush_db` that
calls `redis_db.POPOTO_REDIS_DB.flushdb()` before *every* test, plus a session fixture that swaps to
db 15 at start and flushes it at teardown.

`--setup-plan` confirms the ordering is the damaging one — `_popoto_flush_db` sets up *before*
`redis_test_db` and tears down *after* it:

```
SETUP    S _popoto_test_db
    SETUP    F _popoto_flush_db (fixtures used: _popoto_test_db)
    SETUP    F redis_test_db
    TEARDOWN F redis_test_db
    TEARDOWN F _popoto_flush_db
```

So by the time `_popoto_flush_db` runs, the previous test's `redis_test_db` teardown has already
restored `rdb.POPOTO_REDIS_DB` to the plugin's db-15 client. `current_db == 15` matches the plugin's
target, no re-swap happens, and the `flushdb()` lands on **db 15**. Proven directly — a sentinel key
written to db 15 in one test does not survive into the next:

```
$ .venv/bin/python -m pytest <two-test probe> -n0 -s -q
PROBE sentinel_survived=False
```

This fires **before every test in every pytest process this repo runs**, so any process holding claim
slot 15 has its entire dataset wiped continuously by every sibling run. It is by a wide margin the
most frequent of the three writers, it is invisible to the plan's ownership guard (the flush is
issued from installed library code, not from `tests/`), and it is the mechanism a peer lane
independently hit from the popoto side (`/Users/valorengels/src/popoto/tests/test_pytest_plugin.py`
asserts `db == 15` in four places — that file is in the **popoto** repo, not this one, and is
evidence of the collision rather than a test this plan edits).

This plan fixes all three writers: Tasks 1-5 (unowned `flushdb`), Task 6 (unrestored module reload),
and Task 7 (the popoto plugin's db-15 flush). Anything that still rotates afterward is a fourth cause
and gets its own issue.

## Freshness Check

**Baseline commit:** `e6d0e2bc7`
**Issue filed at:** 2026-08-07T04:01:19Z (against `acf1f3129`, ~8 merges stale)
**Disposition:** Minor drift — two of the three reported symptoms were fixed by commits that
landed after filing; the third (the rotation itself) is unchanged and now has a proven mechanism.

**File:line references re-verified:**
- `tests/db_claim.py:90-117` (`_try_claim_db_slot`) — the issue's suspected racy reclaim path —
  **still present, and the suspicion is disproven.** Ownership is decided by a single
  `fcntl.flock(LOCK_EX|LOCK_NB)` at `:105`, a single-winner kernel primitive. The pid/timestamp
  written at `:113` is explicitly commented "for human debugging only (NOT correctness)" and is
  never read back anywhere in the module, so no TOCTOU exists. Dead-holder reclaim is a kernel
  side effect (the OS drops flocks on process death), not code that can race.
- `tests/db_claim.py:144-153` (pool-exhaustion fallback) — still present, still returns a
  colliding database with only a WARNING.
- `tests/conftest.py:103-150` (`_install_redis_db0_flush_guard`) — still present, still `db == 0`
  only.
- `tests/conftest.py:617,636` — the autouse `redis_test_db` fixture still flushes at both setup
  and teardown of every test, which is what makes a colliding claim destructive.
- `tests/unit/test_youtube_search.py:231-245` — `TestSearchIntegration` still lives in
  `tests/unit/`, marked `integration` + `slow`, and still runs because no `-m` filter is applied.

**Cited sibling issues/PRs re-checked:**
- **#2624** — MERGED 2026-08-07T04:08:46Z (`09444fbc4`), seven minutes after this issue was filed.
  Fixes `TestPerProcessDbClaim::test_dead_holder_slot_is_reclaimed`, the one node that failed in
  both runs, by clearing `PYTEST_XDIST_WORKER` in the tests' reset helper. **Out of scope here** —
  the reporter scoped it out in the first issue comment.
- **#2469** (serial lease leak, `TestFinalize` → `test_sdlc_stage_marker`) — the 2026-08-07 issue
  comment records it as "re-verified still failing on current main". It was fixed by `e6d0e2bc7`
  ("Bind the real lease helpers before TestFinalize freezes a mock into them"), which landed after
  that comment. Out of scope.
- **#2606** (`452eadc59`) — merged; it introduced `claim_test_db` and is the commit that turned two
  previously harmless flushes into cross-process wipes.
- **#2622 / #2631** (durability M2/M3) — merged; audited as a possible cause and cleared (see
  Prior Art).

**Commits on main since issue was filed:**
- `09444fbc4` (#2624) — **already fixes** the `test_dead_holder_slot_is_reclaimed` symptom.
- `e6d0e2bc7` — **already fixes** #2469's serial lease leak.
- `a8677a108`, `f11b083a3`, `2b7b5f905`, `0a53ad22f`, `956496ca6` — irrelevant to the flush path.

The issue's own "fix direction" step 1 (start with the reclaim race) is therefore stale in two
ways: the named test is fixed, and the reclaim path it points at is not racy. The remaining and
still-unaddressed part of the issue — the rotation — is what this plan fixes.

**Active plans in `docs/plans/` overlapping this area:** none. No plan touches `tests/db_claim.py`,
`tests/conftest.py`, or the test-isolation machinery.

**Bug still reproducible?** Yes — the mismatch spike above runs green-to-red on current main, and
the two offending call sites are unchanged.

## Prior Art

This is the fifth pass over the same substrate. Each prior fix was correct and none of them was
sufficient, because every one of them fixed *call sites* while leaving the invariant unenforced.

- **PR #2061** (#2093): Fixed popoto's db-cache split-brain and agent-hooks corruption under
  `-n auto`. Introduced the practice of repointing every popoto module binding. Succeeded at its
  scope.
- **PR #2097**: Fixed a cluster of 5 unit tests flaky under `-n auto`. Per-test fixes.
- **PR #2117** (#2060): First recognition that the collision is **cross-process**, not
  cross-worker. Fixed one victim test.
- **PR #1984**: "Concurrent full-suite pytest coordination" — a machine-global advisory lock
  serialising full-suite runs. **Deliberately deleted since**; the reasoning is preserved verbatim
  in `scripts/pytest-clean.sh`: *"Real isolation belongs in key namespacing, not in serializing the
  machine."* That verdict stands and this plan does not revisit it.
- **PR #2163** (#2147): Test-suite notify isolation — db-scoped pub/sub channel plus a live-worker
  guard. The closest structural precedent: it moved a correctness property out of per-test
  discipline and into production code (`notify_channel_for`).
- **PR #2606** (#2603, #2605): Replaced the `gw{N}→db{N+1}` derivation with the `flock` claim. The
  right fix. It updated `tests/conftest.py` and `tests/db_claim.py::subprocess_env` but did not
  sweep every consumer, and nothing existed to catch the ones it missed.
- **PR #2624**: Swept one more consumer (the reset helper). Same shape, one file later.
- **PR #2568** (#2552, #2553, #2556, #2557, #2523): "Repair the test suite's measuring instrument"
  — the umbrella this work belongs under.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2117 | Fixed one victim of cross-process db collision | Treated one symptom; the derivation rule was still wrong everywhere else |
| PR #2606 | Replaced the derivation with an `flock` claim; updated conftest and `subprocess_env` | Changed the *meaning* of the test db without sweeping every consumer, and added no enforcement — the two stragglers kept computing the old answer and silently began wiping strangers' data |
| PR #2624 | Cleared `PYTEST_XDIST_WORKER` in one test helper | Another one-file sweep of the same class. Found by a failing test, not by a guard |

**Root cause pattern:** *db ownership is a convention that every call site is trusted to re-derive
correctly, and re-deriving it wrong is silent.* Each fix corrects one derivation; none makes a wrong
derivation detectable. The fix that ends the series is to make ownership enforced at the point of
damage — the `flushdb()` call — so a stale derivation fails at its own line instead of corrupting a
stranger three files away. This is exactly what the `db == 0` guard already does for production, and
exactly what #2163 did for pub/sub: promote the invariant from discipline to mechanism.

## Research

**Queries used:**
- `pytest-xdist loadfile deterministic worker assignment reproducible test distribution`

**Key findings:**
- `--dist=loadfile` guarantees **co-location** (all tests in a file run in one worker) but not
  **assignment** determinism (file X is not always `gw2`). Work units are handed out by a
  low-watermark scheduler as workers drain, so the file→worker mapping follows wall-clock
  completion order and differs between runs. Confirmed against the installed source
  (`xdist/scheduler/loadscope.py:263,316,332`).
  ([pytest-xdist distribution docs](https://pytest-xdist.readthedocs.io/en/stable/distribution.html),
  [loadfile.py](https://github.com/pytest-dev/pytest-xdist/blob/master/src/xdist/scheduler/loadfile.py))
  → **Informs the plan:** the *variation* is unfixable and should not be fixed. Nondeterministic
  scheduling is only a bug amplifier; the bug is the unowned flush. Trying to pin assignment is a
  named rabbit hole.
- xdist's sanctioned pattern for per-worker resources is the `worker_id` fixture, e.g.
  `f"test_db_{worker_id}"`. This repo deliberately went further — a machine-global `flock` claim —
  because the contention is cross-*process*, not cross-worker, and `worker_id` is not unique across
  two concurrent pytest invocations. → **Informs the plan:** the existing claim design is correct
  and stays; the fix is enforcement, not redesign. It also explains the failure mode: `worker_id`
  is the "obvious" answer and is what both stale call sites reach for.
- The only way to get byte-identical worker assignment is deterministic sharding outside xdist
  (hash-modulo file splitting across separate invocations) or a custom scheduler. → **Informs the
  plan:** explicitly out of scope; noted as a rabbit hole.

Sources: [pytest-xdist distribution docs](https://pytest-xdist.readthedocs.io/en/stable/distribution.html),
[pytest-xdist loadfile.py](https://github.com/pytest-dev/pytest-xdist/blob/master/src/xdist/scheduler/loadfile.py)

## Spike Results

### spike-1: Is the claimed db different from the legacy derivation in practice?
- **Assumption**: "`_own_test_db()`'s `gw{N}→db{N+1}` still matches the process's real db, so the
  flush is harmless."
- **Method**: prototype (direct interpreter call on current main)
- **Finding**: **False.** Under `PYTEST_XDIST_WORKER=gw3` the process claimed slot **11** while the
  legacy derivation returned **4**. The flush would have wiped db 4, held by a sibling agent's
  suite. Reproduced above.
- **Confidence**: high
- **Impact on plan**: Establishes the root cause and makes the ownership guard (Task 1) the
  centrepiece rather than a defensive extra.

### spike-2: Is the dead-holder reclaim path race-prone, as the issue suspects?
- **Assumption**: "The reclaim races when several workers detect the same dead holder."
- **Method**: code-read (`tests/db_claim.py:90-153`)
- **Finding**: **False.** There is no detection code at all — reclaim is a kernel side effect of
  `flock` release on process death. Ownership is decided by one `LOCK_EX|LOCK_NB` call; N racers
  produce exactly one winner and N-1 `OSError`s. The file contents are never read, so the
  pid/timestamp cannot participate in a TOCTOU.
- **Confidence**: high
- **Impact on plan**: Removes the issue's step 1 from scope entirely and redirects the whole plan.
  Without this spike the build would have spent its budget hardening a path that is already correct.

### spike-3: Is the claim pool actually under pressure on this machine?
- **Assumption**: "Pool exhaustion is a theoretical edge case."
- **Method**: prototype (observed which slot spike-1 was granted)
- **Finding**: **False.** spike-1 was granted slot **11**, meaning slots 1-10 were already held by
  live sibling processes. `hw.ncpu = 10`, so one `-n auto` run claims 10 of 15 slots; two
  concurrent runs need 20. The pool routinely runs near its ceiling, and on exhaustion
  `tests/db_claim.py:144-153` silently returns a *colliding* database.
- **Confidence**: high
- **Impact on plan**: Adds Task 4 (make exhaustion loud) and, critically, makes "quiesce the machine
  first" a hard precondition of the verification protocol — any re-baseline taken while other agents
  test is measuring noise.

### spike-4: Is popoto's bundled pytest plugin active in this repo, and does it touch a pool slot?
- **Assumption**: "The only code that flushes a test db during a run lives in this repo's `tests/`."
- **Method**: prototype (entry-point enumeration, `--trace-config`, `--setup-plan`, sentinel-key probe)
- **Finding**: **False, decisively.** `popoto` registers `pytest11 = popoto.pytest_plugin`, and
  `--trace-config` shows it registered on a normal run of this repo. It is not disabled
  (`pyproject.toml:195` addopts carry `-p no:postgresql` only) and neither `POPOTO_TEST_DB` nor the
  `popoto_test_db` ini option is set, so it runs on its default **db 15** — inside the claim pool.
  Its `_popoto_flush_db` autouse fixture calls `flushdb()` before every test, and `--setup-plan`
  confirms it sets up before `redis_test_db` and tears down after it, so the client it flushes is the
  restored db-15 one, not the claimed one. A sentinel key written to db 15 in test A is gone by test
  B (`PROBE sentinel_survived=False`).
- **Confidence**: high
- **Impact on plan**: Adds Task 7. This is the highest-frequency writer of the three and the only one
  that fires from installed library code, where neither the ownership guard nor the AST recurrence
  guard can see it. Without this task the plan would have shipped, the suite would have kept
  rotating, and both new guards would have reported clean.

### spike-5: Can the `redis_test_db` fast-path skip leave a test on an unpatched default db?
- **Assumption**: "`tests/conftest.py:595`'s `if "popoto.redis_db" not in sys.modules: yield; return`
  can fire, and a test that imports popoto inside its own body afterwards lands on the unpatched
  default db — a candidate third mechanism."
- **Method**: code-read + prototype (`sys.modules` check after plugin import)
- **Finding**: **Ruled out on current main.** `popoto/pytest_plugin.py` does `from popoto import
  redis_db` at *module* level, and the plugin is loaded from its `pytest11` entry point during pytest
  startup — before conftest collection and therefore before any fixture runs. `popoto.redis_db` is
  consequently in `sys.modules` for the entire life of every pytest process this repo starts, so the
  fast-path branch is **unreachable dead code**, not a live hazard.
- **Confidence**: high
- **Impact on plan**: No task of its own, but it creates a **hard coupling with Task 7**: the branch
  is dead *only because the popoto plugin is loaded*. If Task 7 is implemented by disabling the
  plugin (`-p no:popoto`) rather than by repointing it, this branch becomes live in the same commit
  and the hypothesis stops being ruled out. Task 7 therefore carries an explicit rule: whichever
  option is chosen, delete the fast path in the same change. Recorded here so a future reader does
  not re-open it as an unexamined possibility.

## Data Flow

How a single stray `flushdb()` becomes a rotating failure set:

1. **Entry point**: `scripts/pytest-clean.sh` starts pytest; `pyproject.toml:194` addopts supply
   `-n auto --dist=loadfile`. Ten worker processes start.
2. **Claim**: each worker's first test triggers the autouse `redis_test_db` fixture
   (`tests/conftest.py:570-641`), which calls `claim_test_db()` → an `flock` over `[1..15]`. Worker
   `gw3` may claim db 11.
3. **Repoint**: the fixture points `POPOTO_REDIS_DB` and every cached popoto submodule binding at
   `redis.Redis(db=11)` and flushes it at setup and teardown of every test.
4. **The stray write**: `test_redis_flush_guard.py` lands on some worker. `_own_test_db()` ignores
   the claim and returns `worker_id + 1`. `test_flushdb_on_own_test_db_is_allowed` flushes **that**
   database. `tests/conftest.py:117-129` waves it through because it is not db 0.
5. **The victim**: whichever process claimed that slot loses its entire dataset at an arbitrary
   moment. `--dist=loadfile` guarantees the flusher and the victim are on different workers, so
   there is no in-process trace to find.
6. **Output**: a small set of tests fails — always ones holding Redis state across a long window and
   then asserting on it (`test_session_archive.py:723` loops delete→restore→assert five times;
   `test_session_lifecycle.py:1640` is the only test in its class asserting a lock is *still held*
   across two cold interpreter starts). Because xdist scheduling reshuffles which files sit where,
   the victim set differs every run, and every node passes in isolation.

After the fix, step 4 raises at its own line and steps 5-6 never happen.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `tests/db_claim.py` gains `claimed_test_dbs()`, `claim_scratch_test_db()`,
  and `_TEST_DB_CLAIM_WAIT_S`; `tests/conftest.py` gains a `scratch_test_db` fixture and a widened
  module-reload restoration guard, sets `POPOTO_TEST_DB` from `pytest_configure`, and loses its dead
  fast-path branch. Almost entirely test-infrastructure surface. **One production
  line**: `monitoring/bridge_watchdog.py:87-93` gains the `handlers.clear()` that
  `monitoring/worker_watchdog.py:150-151` already has, so a module reload stops stacking
  `RotatingFileHandler`s. That is idempotence hardening, not behavior change.
- **Coupling**: *reduces* it. The guard removes every test's licence to compute a db number for
  itself; `tests/db_claim.py` becomes the single authority, matching the "ONE authoritative signal
  owned by the module that owns the resource" principle already applied to session liveness.
- **Data ownership**: unchanged in production. Within tests, db ownership moves from convention to
  mechanism.
- **Reversibility**: high. The guard is one function in `tests/conftest.py`; reverting restores the
  db0-only behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirming the exhaustion policy, and the verification result)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable on the test port | `redis-cli -p ${REDIS_PORT:-6379} ping` | The claim pool and every guard test need a live server |
| Interpreter on the committed pin | `bash scripts/check-interpreter-pin.sh` | `scripts/pytest-clean.sh` aborts on an off-pin venv (#2617) |

Run via `python scripts/check_prerequisites.py docs/plans/suite-failure-rotation-db-ownership.md`.

**No quiescence precondition.** An earlier draft required the machine free of other pytest processes
for the verification run. That is now explicitly *not* a precondition — see Risk 4. The binding
proof is an adversarial single-process test that spawns its own competing flock holder, so it is
deterministic under any ambient load, and the optional soak is more informative *with* siblings
running than without.

## Solution

### Key Elements

- **Ownership-enforcing flush guard**: extends the existing db0 guard so `flushdb()` succeeds only
  on a database this process has claimed. Every other target raises, naming the offending db and
  the claimed set. `flushall()` stays unconditionally blocked.
- **A legitimate second database**: `claim_scratch_test_db()` plus a `scratch_test_db` fixture, for
  the one test that genuinely needs a *different* db than its own. Claimed through the same `flock`
  pool, so it is owned and therefore flushable.
- **Two corrected call sites**: the flush-guard test and the split-brain test stop deriving and
  start asking.
- **Loud exhaustion**: `claim_test_db()` waits briefly for a slot, then fails with an actionable
  message, instead of silently returning a colliding database.
- **A structural recurrence guard**: an AST walk over `tests/**/*.py` asserting that no
  `redis.Redis(db=...)` construction takes its `db=` value from anywhere but the claim API. This
  catches *both* documented offenders — the worker-id derivation *and* the hardcoded literal — and
  is what ends the #2117 → #2606 → #2624 series.
- **A restored module registry**: the `importlib.reload` writer in `test_index_drift.py` is removed
  and the conftest reload guard is widened past `BaseException`-in-one-module, closing the second
  rotation mechanism.
- **A repointed popoto plugin**: the bundled `popoto.pytest_plugin` stops flushing db 15 (a pool
  slot) before every test and is pointed at this process's *claimed* db instead, closing the third
  and most frequent rotation mechanism. The `redis_test_db` fast path — dead only because that plugin
  is loaded (spike-5) — is deleted in the same change.
- **A live-network test out of the unit suite**: `TestSearchIntegration` moves to
  `tests/integration/`.
- **Run provenance**: each worker reports its id and claimed db in the pytest header, so the next
  rotation is diagnosable from the log instead of unreproducible.

### Flow

**A test calls `flushdb(db=N)`** → guard consults `claimed_test_dbs()` → **N is claimed** → flush
proceeds → **N is not claimed** → `RuntimeError` naming N, the claimed set, and `scratch_test_db`
→ that test fails, at its own line, deterministically, in isolation and under load alike.

**A test needs a second database** → requests the `scratch_test_db` fixture → a second pool slot is
`flock`-claimed for this process → the test owns it and may flush it → released at session end.
Pool exhausted → `pytest.skip` with an explicit reason (an honest skip, never a colliding fallback).

### Technical Approach

- **Enforce at the point of damage.** The guard wraps `flushdb` on both `redis.Redis` and
  `redis.asyncio.Redis` (the existing hook at `tests/conftest.py:103-150`) and consults
  `tests/db_claim.py` for the authoritative claimed set. Damage and enforcement are then at the same
  line, so a stale derivation can no longer be silent.
- **Ownership is a set of numbers, and the set is populated on *every* path that returns a db.**
  `tests/db_claim.py` currently memoizes a single `_CLAIMED_TEST_DB` and keeps fds in
  `_CLAIM_LOCK_FDS`. Add a module-level `_CLAIMED_DB_NUMS: set[int]` and expose
  `claimed_test_dbs() -> frozenset[int]`. **Critical composition rule:** the fd list is
  flock-path-only, but the *number* set must also be populated on the registry-unreachable
  fallback (`tests/db_claim.py:130-139`), which returns a db without holding any lock. If it is
  not, `claimed_test_dbs()` is empty on that path, the fail-closed-on-empty guard denies the
  autouse fixture's own `flushdb()` at `tests/conftest.py:617`, and every test in the process
  errors in setup — a documented graceful degradation turned into a total outage. Concretely:
  `_CLAIMED_DB_NUMS.add(_CLAIMED_TEST_DB)` immediately before the `return` in the `except OSError`
  branch, on the same line-of-reasoning as the flock path's `_CLAIM_LOCK_FDS.append(fd)`.
  `claim_test_db()` keeps returning the primary slot; `claim_scratch_test_db()` adds another.
  `release_test_db_claim()` (`tests/db_claim.py:156-175`) clears the number set with the fds.
- **Do not fight xdist.** Worker assignment stays nondeterministic (Research). The plan makes the
  suite's *result* deterministic, not its scheduling.
- **Exhaustion: wait briefly, then fail.** Replace the `_legacy_test_db_num()` fallback at
  `tests/db_claim.py:144-153` with a bounded poll of the pool, then a `RuntimeError` naming the
  contention and pointing at `scripts/reap-xdist.sh --apply`. A colliding database is strictly worse
  than a clear failure: it produces exactly the corruption this plan exists to remove.
  **The wait window is 30 s, not 300 s** — spike-3 measured that two concurrent `-n auto` runs on a
  10-core box demand 20 of 15 slots and each takes ~20 minutes, so in the exact contention state a
  wait targets, *no slot frees inside any tolerable window*. A 300 s wait would only convert an
  instant actionable error into a five-minute stall before the identical error. 30 s is long enough
  to absorb a sibling run that is already tearing down, short enough to stay well inside
  `--timeout=420`, and cheap to raise if aborts prove premature. Poll on a ~1 s interval (not one
  long sleep) so a freed slot is picked up promptly.
- **The wait constant must be monkeypatchable.** Name it `_TEST_DB_CLAIM_WAIT_S`
  (`int(os.environ.get("TEST_DB_CLAIM_WAIT_S", "30"))`) and read it as a **module attribute at call
  time inside the retry loop** — never as a default argument, never bound to an import-time local.
  Otherwise `monkeypatch.setattr(_db_claim, "_TEST_DB_CLAIM_WAIT_S", 0)` silently no-ops and every
  exhaustion test blocks for the full window. This is the same shape that already makes
  `_TEST_DB_POOL_MAX` patchable via `_reset_claim_state`
  (`tests/unit/test_conftest_isolation_guards.py:486-487`); add a `wait_s` parameter to
  `_reset_claim_state` alongside its existing `pool_max`.
- **Hold-and-wait is a real deadlock shape** — see Race 1. The bounded timeout is what converts a
  deadlock into a loud, diagnosable failure; the all-or-nothing controller allocation is the
  escalation if one is ever observed, and is deliberately not built now.
- **Recurrence guard by structure, not by discipline — and it must catch both offender shapes.**
  A grep for `PYTEST_XDIST_WORKER` catches `_own_test_db` but *not*
  `tests/unit/test_conftest_isolation_guards.py:362`'s `divergent_db = 15 if base_test_db != 15
  else 14`, which is a bare literal that *invented* ownership rather than re-derived it. The
  primary leg is therefore an AST walk: visit every `ast.Call` whose func resolves to `Redis` or
  `Redis.from_url` and inspect the `db=` keyword, flagging any value that is not a `Call` to
  `claim_test_db`/`claim_scratch_test_db` or a `Name` bound from the `scratch_test_db`/
  `redis_test_db` fixture, against an explicit `{path: reason}` allowlist. Scoping to the `db=`
  argument specifically keeps this inside the Rabbit Hole boundary — it is emphatically *not* the
  rejected blanket "no raw `redis.Redis()` in tests" lint, which would churn dozens of files. Keep
  the `PYTEST_XDIST_WORKER`/`workerinput` grep as a cheaper second leg.
- **The guard must read the claimed set at call time.** `_install_redis_db0_flush_guard()` runs at
  conftest import (`tests/conftest.py:150`), long before any claim exists. The wrapper closure must
  call `db_claim.claimed_test_dbs()` on each invocation, never capture a snapshot.
- **Scope of the ownership guard: `flushdb` only** (Open Question 2, decided). Rationale: `flushdb`
  is the only operation whose blast radius is another process's *entire* dataset, and it is the
  operation the reproduced bug actually uses. A general "no write on an unclaimed db" guard would
  have to wrap every mutating command on both sync and async clients — a per-command hot-path cost
  on every test in the suite, and a much larger surface of legitimate call sites to audit, for a
  quieter class of bug that has never been observed here. The AST recurrence guard already prevents
  *construction* of a client on an unowned db, which closes the same hole one layer earlier and at
  zero runtime cost. Revisit only if a cross-db non-flush write is ever actually observed.
- **Second writer: remove the reload, then widen the guard.** `tests/unit/test_index_drift.py:207-208`
  reloads `agent.index_drift` purely to observe a module constant's default. Replace it with a
  direct assertion plus `monkeypatch` where a non-default value is needed — no reload, no restore
  problem. Then widen `tests/conftest.py`'s reload guard (`:384`, `:391-401`) from
  "`BaseException` subclasses in `models.session_lifecycle`" to also snapshot module-level
  *registry* objects (dicts/classes/enums) for `agent.index_drift` and `monitoring.bridge_watchdog`,
  so the next unrestored reload is caught rather than silently leaked.
- **Third writer: repoint the popoto plugin at the claimed db; do not simply silence it.** The
  preferred option is to set `os.environ["POPOTO_TEST_DB"] = str(claim_test_db())` from
  `tests/conftest.py`'s `pytest_configure`, which runs before the plugin's session fixture resolves
  its target (that fixture reads the env var first, ahead of the ini option and the default). The
  plugin then swaps and flushes *this process's own claimed db* — correct by construction — and it
  keeps the plugin's genuinely useful `_popoto_reset_async` per-test event-loop reset, which this
  repo's own fixture does not replicate. The alternative, `-p no:popoto` in addopts, is simpler but
  drops that async reset and must be validated against the async suite before being chosen.
  **Whichever option is taken, delete the `redis_test_db` fast path at `tests/conftest.py:592-597`
  in the same change** — spike-5 shows it is dead code today *only* because the plugin's module-level
  `from popoto import redis_db` guarantees the import, so disabling the plugin would silently revive
  it as a live hazard in the same commit. Do not leave the plugin on its db-15 default under any
  option: db 15 is a claimable slot and every flush of it is a cross-process wipe.
- **The `scratch_test_db` fixture is session-scoped and memoized.** `claim_scratch_test_db()` takes an
  *additional* pool slot on every call and there is no per-test release, so a function-scoped,
  non-memoized fixture would consume one slot per requesting test and walk the 15-slot pool
  monotonically into Task 4's new fail-hard `RuntimeError` — the fixture would create the exhaustion
  the same PR makes fatal. Session scope matches `claim_test_db()`'s own per-process memoization: one
  scratch slot per process, held for the session, released by `release_test_db_claim()` at session
  end.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tests/db_claim.py:102-103,106-109,114-115` — three `except OSError` blocks in
  `_try_claim_db_slot`. Two are load-bearing (`os.open` failure → not ours; `flock` failure → held
  by another live process) and gain assertions that the claim is *not* granted. The third (the
  `os.ftruncate`/`os.write` of debug metadata) is correctly best-effort; document why with an
  inline comment rather than testing it.
- [ ] `tests/db_claim.py:130-139` — the registry-unreachable `except OSError`: assert it still logs
  a WARNING, still returns the legacy number (this path is retained deliberately), **and that the
  returned number is in `claimed_test_dbs()` so a flush on it is permitted.** This last assertion
  is the regression test for the composition blocker: without it, the fallback silently converts
  into a whole-process setup outage under the fail-closed guard. Method: monkeypatch
  `_db_claim._test_db_claim_dir` to raise `OSError`, then assert
  `claim_test_db() in claimed_test_dbs()` and that `redis.Redis(db=that).flushdb()` does not raise.
- [ ] `tests/conftest.py:110-113` `_db_of` — the `except Exception: return 0` that assumes the
  dangerous db when it cannot determine one. Under the new guard this must still deny, not allow:
  add a test with a client whose `connection_pool` raises, asserting `RuntimeError`.
- [ ] No exception handlers are added by this change beyond those listed.

### Empty/Invalid Input Handling
- [ ] `claimed_test_dbs()` before any claim: must return an empty frozenset, and the guard must then
  deny every `flushdb` rather than allowing all of them. Fail-closed on empty is the whole point —
  test it explicitly. Note the corollary that makes this safe in practice: *every* return path of
  `claim_test_db()` populates the set (flock path and registry-unreachable fallback alike), so
  "empty" means "nothing has claimed yet", never "the claim degraded".
- [ ] `claim_scratch_test_db()` with an exhausted pool: returns `None`; the fixture converts that to
  `pytest.skip` with a reason string. Test both the `None` return and the skip.
- [ ] `flushdb` on a db outside `[0..TEST_DB_POOL_MAX]` (e.g. 99): denied, with the same message
  shape.

### Error State Rendering
- [ ] The denial message must name the attempted db, the claimed set, and the remedy
  (`scratch_test_db`). Assert on all three substrings — a guard whose message does not point at the
  fix will simply be worked around by the next agent.
- [ ] The exhaustion `RuntimeError` must name the pool size and `scripts/reap-xdist.sh --apply`.
  Assert both.

## Test Impact

- [ ] `tests/unit/test_redis_flush_guard.py::test_flushdb_on_own_test_db_is_allowed` — UPDATE: drop
  `_own_test_db`, flush `claim_test_db()`. This is the primary offender.
- [ ] `tests/unit/test_redis_flush_guard.py::test_flushall_is_blocked_even_on_test_db` — UPDATE:
  same helper removal (harmless today because `flushall` is blocked before the db matters, but the
  helper must go entirely).
- [ ] `tests/unit/test_redis_flush_guard.py::_own_test_db` — DELETE: the stale derivation itself.
- [ ] `tests/unit/test_conftest_isolation_guards.py` split-brain test (`:359-366`, `:420`) — UPDATE:
  take the divergent db from the `scratch_test_db` fixture instead of hardcoding 15.
- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim::test_pool_exhaustion_falls_back_with_warning`
  (`:592-605`) — REPLACE: it asserts the exact behavior being removed
  (`assert db == 4, "exhausted pool must fall back to legacy gw3->db4"`). Rewrite as
  `test_pool_exhaustion_raises_rather_than_colliding`.
- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim::test_dead_holder_slot_is_reclaimed`
  (`:570-591`) — REPLACE, not a one-line assertion edit. Its current shape claims with the pool
  fully held (`assert _db_claim.claim_test_db() == 1  # legacy master fallback` at `:576-577`),
  kills the holder, then re-claims. Under wait-then-fail that first call blocks for the whole wait
  window and then raises, so the test cannot survive an assertion edit. Rewrite as: spawn holder →
  terminate and `wait()` → reset `_CLAIMED_TEST_DB` → claim → assert the slot is reclaimed. The
  pre-kill claim is dropped entirely (it was never what the test was about; the property is
  "the kernel frees a dead holder's flock").
- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim::_reset_claim_state`
  (`:472-489`) — UPDATE: add a `wait_s` parameter mirroring the existing `pool_max`, so every
  exhaustion-path test sets `_TEST_DB_CLAIM_WAIT_S` to 0 and completes in milliseconds instead of
  eating 30 s (or, if the constant is ever read wrong, the full `--timeout=420` budget).
- [ ] `tests/unit/test_index_drift.py::TestToleranceConstant::test_default_tolerance_is_zero`
  (`:203-208`) — REPLACE: drop `importlib.reload(index_drift)`; assert the constant directly, and
  use `monkeypatch.setattr` for any non-default value. This is the second rotation writer.
- [ ] `tests/unit/test_index_drift_coverage.py:26-42` — UPDATE: `restore_registry` must snapshot and
  restore via the *module* (`index_drift.DRIFT_COVERED_MODELS`) rather than the collection-time
  `from ... import DRIFT_COVERED_MODELS` binding, so the fixture cannot be silently defeated by a
  future reload. Same for `register_drift_model`/`covered_model_names` call sites in that file.
- [ ] `tests/unit/test_worker_watchdog.py:47,52,58,1050-1058` — UPDATE: reloads
  `monitoring.worker_watchdog` and leaves `HEARTBEAT_THRESHOLD=90` in place across a span of tests.
  Bring under the widened reload guard (or `monkeypatch`) so it restores deterministically.
- [ ] `monitoring/bridge_watchdog.py:87-93` — UPDATE (production, one line): each reload appends a
  new `RotatingFileHandler` with no `handlers.clear()`. `monitoring/worker_watchdog.py:150-151`
  already clears; bridge_watchdog does not. Add the matching clear so reloads are idempotent.
- [ ] `tests/unit/test_youtube_search.py::TestSearchIntegration` (`:231-245`) — REPLACE: relocate to
  `tests/integration/test_youtube_search_live.py`. Leaves the unit-suite file with only offline
  tests.
- [ ] `tests/integration/test_agent_catchup_recovery.py:61` — UPDATE: `redis.Redis(db=1).ping()` is a
  hardcoded pool slot. Harmless in effect (a `ping`, never a flush) but it is exactly the shape the
  Task 5 AST guard flags, and the surrounding docstring already claims the test writes only to the
  per-process db. Convert to `claim_test_db()`; do not allowlist — an allowlist entry here would
  teach the next author that hardcoding is negotiable.
- [ ] `tests/unit/test_email_bridge.py:1399-1406` — UPDATE: derives the db by reading
  `POPOTO_REDIS_DB.connection_pool.connection_kwargs.get("db", 1)`. This is a *third* derivation
  route — not a worker-id derivation and not a literal — and it reads a value the popoto plugin can
  and does mutate (Task 7), so it is unsound for the same reason the other two are. Convert to
  `claim_test_db()` and extend the Task 5 AST guard to reject `connection_kwargs`-sourced `db=`
  values, not just literals and worker-id arithmetic.
- [ ] **Not a test in this repo:** `/Users/valorengels/src/popoto/tests/test_pytest_plugin.py` asserts
  `db == 15` in four places (`:66`, `:171`, `:293`, `:357`). Those belong to the **popoto** repo and
  are correct there — db 15 is that project's own test db. This plan does not edit them and must not.
  They are recorded here as corroboration for spike-4: popoto genuinely targets db 15, which is why
  its bundled plugin collides with this repo's claim pool. If the fix is ever taken upstream instead
  of downstream, those four tests become that PR's problem, not this one's.
- [ ] Sweep for other tests that build a raw `redis.Redis(db=...)` from anything but
  `claim_test_db()` — the recurrence guard (Task 5) is authored to fail against exactly these, so
  the sweep is mechanical rather than judgemental.

## Rabbit Holes

- **Chasing the four rotating nodes individually.** `test_session_archive.py`'s three and the
  `run_identity` node are *victims*, not causes — recon found no shared in-process writer, and
  `--dist=loadfile` puts them on different workers from the flusher. Fix the flush; re-measure;
  only then look at anything still failing.
- **Making xdist worker assignment deterministic.** It is not natively possible (Research). Custom
  schedulers or hash-modulo sharding across separate invocations is a project, not a fix, and it
  would only hide unowned-flush bugs rather than remove them.
- **Reinstating the machine-global full-suite advisory lock (#1984).** Deleted deliberately, with
  the reasoning preserved in `scripts/pytest-clean.sh`. Serialising the machine trades a correctness
  bug for a throughput bug and would block the concurrent-lane workflow this session depends on.
- **Raising the Redis `databases` setting to widen the pool.** `databases` is not runtime-settable,
  so it needs a Redis restart — disruptive to the live bridge, worker, and production db 0, for a
  problem the wait-then-fail policy already handles. Revisit only if exhaustion becomes routine
  after the fix.
- **Hardening the flock reclaim path.** Disproven by spike-2. It is already correct.
- **A blanket "no raw `redis.Redis()` in tests" lint.** Tempting adjacent cleanup; far wider than
  this bug and would churn dozens of files. The recurrence guard targets the *derivation*, which is
  the actual defect.

## Risks

### Risk 1: The guard denies a legitimate flush that some test depends on
**Impact:** New failures appear in the fix's own PR and could be mistaken for the ambient rotation
this plan exists to remove — the exact confusion the issue documents.
**Mitigation:** Land the guard and the two call-site corrections in one commit, then run the suite
serially (`-n0`) on the touched files first, where no cross-process claim exists and any denial is
unambiguous. Every denial is a real bug by construction: the message names the db and the claimed
set, so triage is reading one line. `tests/integration/test_session_archive_cold_boot.py:100,194`
flushes `rdb.POPOTO_REDIS_DB` — its own claimed client — and is expected to pass unchanged; verify
that explicitly rather than assuming.

### Risk 2: Wait-then-fail on exhaustion turns a silent corruption into a blocked run
**Impact:** Two agents running full suites concurrently could see a run abort after the wait window
instead of completing with subtly wrong results.
**Mitigation:** This is the intended trade — a blocked run is recoverable, a corrupted baseline is
not, and the abort message names the remedy. The wait is deliberately short (30 s, env-overridable
`TEST_DB_CLAIM_WAIT_S`, polled at ~1 s): spike-3 shows that in genuine two-run contention no slot
frees inside any tolerable window, so a long wait buys nothing but delay before the same error. If
aborts prove common, the escalation is all-or-nothing controller allocation (Race 1), not a return
to colliding fallbacks.

### Risk 3: The fix is correct but the rotation persists from a third, independent cause
**Impact:** The plan ships and the suite still rotates; confidence in the instrument stays broken.
**Mitigation:** Three writers are now in scope and each gets its own deterministic red-state proof
(Task 2's flock-holder test, Task 6's registry-restoration test, Task 7's db-15 sentinel test), so
"did we fix the thing we claimed" is answered in seconds, not inferred from a suite-wide diff. The
third was found only because round 2 looked outside `tests/` at installed plugins — a reminder that
"a fourth cause" is a live possibility, not a formality. If a residual rotation
survives, the provenance line added in Task 8 (worker id + claimed db) makes the next investigation
start with evidence instead of a fresh recon. File any residue as a new issue rather than widening
this one.

### Risk 4: The pass condition is unfalsifiable
**Impact:** The plan ships against a criterion that could not have failed, and the rotation returns.
**Mitigation:** This risk was live in the previous draft and is now designed out. A *quiesced*
double-run is the one configuration in which the bug provably cannot fire: the Problem statement
requires a **different live pytest process** to own the flushed db, and under quiescence no such
victim exists — the measurement would have certified "the failure set is stable when nothing
contends", which is vacuous. Quiescence also contradicts how this machine operates (`CLAUDE.md`:
several agents test at once; `scripts/reap-xdist.sh` and `scripts/pytest-clean.sh` are both built
to *spare* sibling runs; spike-3 found slots 1-10 already held), and a `pgrep` before/after cannot
detect a sibling that starts and finishes inside a 40-minute window. The binding criterion is
therefore the ~1 s adversarial holder test (Task 2), which reproduces a real competing owner
deterministically. The double-run is demoted to an optional post-merge soak and, if run at all, is
run **without** quiescence — a concurrent sibling is the only configuration in which a stable
failure set means anything.

## Race Conditions

### Race 1: Hold-and-wait deadlock between two concurrent suite runs
**Location:** `tests/db_claim.py:120-153` (`claim_test_db`), as modified by Task 4.
**Trigger:** xdist workers claim lazily, at their first test, not all at once. Run A acquires 8 of
15 slots, run B acquires the remaining 7; each still needs more, and each waits while holding what
it has. Neither can proceed until the other exits.
**Data prerequisite:** none — the deadlock is on the slot pool itself.
**State prerequisite:** total demand (workers × concurrent runs) exceeds `TEST_DB_POOL_MAX`. With
`hw.ncpu = 10` this is any two concurrent full-suite runs, which spike-3 shows is the normal
operating state of this machine.
**Mitigation:** The wait is bounded (`_TEST_DB_CLAIM_WAIT_S`, default 30 s, polled at ~1 s) and
expires into a `RuntimeError` naming the contention — a deadlock becomes a loud, diagnosable failure rather than a
hang, and the operator's remedy (`scripts/reap-xdist.sh --apply`, or waiting) is in the message.
The structural fix, deliberately deferred, is all-or-nothing allocation: the xdist *controller*
claims all N slots in `pytest_configure` before any worker starts and passes them down via
`workerinput`, so a run either gets its full allocation or waits holding nothing. Build that only
if a deadlock is actually observed.

### Race 2: The claim itself
**Location:** `tests/db_claim.py:90-117`.
**Trigger:** N processes race for the same freed slot.
**Data prerequisite:** none.
**State prerequisite:** none.
**Mitigation:** Already correct and unchanged by this plan — `fcntl.flock(LOCK_EX|LOCK_NB)` is a
single-winner kernel primitive and the lock file's contents are never read (spike-2). Recorded here
so a reviewer does not re-litigate the issue's original suspicion.

### Race 3: Guard consults the claimed set while a scratch claim is in flight
**Location:** the new guard in `tests/conftest.py`, against `claimed_test_dbs()`.
**Trigger:** a scratch slot is claimed on one thread while a `flushdb` is evaluated on another.
**Data prerequisite:** the scratch slot number must be registered in the claimed set *before*
`claim_scratch_test_db()` returns, or the very first flush of a freshly claimed scratch db is denied.
**State prerequisite:** the claimed set is process-local, mutated only by claim/release.
**Mitigation:** Register the slot number in the same critical section that appends the fd (mirroring
`_CLAIM_LOCK_FDS.append(fd)` at `tests/db_claim.py:116`), so registration strictly precedes the
return. Pytest fixtures are single-threaded per worker, so this is ordering discipline rather than
locking — but writing it as one step is what makes it true.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2628] A **fourth** rotation writer, if the optional post-merge soak surfaces one,
  gets its own issue rather than expanding this plan's scope. Three writers are in scope (unowned
  `flushdb`, unrestored module reload, popoto plugin's db-15 flush) and each has a deterministic
  proof; the soak is diagnostic, not a merge gate.
- [ORDERED] Fixing the db-15 default **upstream in the popoto repo** (so no downstream consumer can
  collide with a claim pool) is the more durable fix, but it needs a popoto release and a floor bump
  here. This plan takes the downstream repoint (Task 7) because it lands in one commit and is fully
  under this repo's control. File the upstream change separately if the collision recurs for another
  consumer. Note that `/Users/valorengels/src/popoto/tests/test_pytest_plugin.py`'s four `db == 15`
  assertions belong to that upstream change, not to this one.
- [ORDERED] A general "no write on an unclaimed db" guard covering every mutating command rather
  than just `flushdb` (Resolved Question 2). Deferred: it costs a per-command wrapper on both sync
  and async clients paid by every test, and the AST recurrence guard already blocks *construction*
  of a client on an unowned db at zero runtime cost. Build only if a cross-db non-flush write is
  actually observed.
- [ORDERED] The all-or-nothing controller slot allocation (Race 1) is built only if a deadlock is
  observed after the bounded-wait policy ships. Building it pre-emptively would add a
  `pytest_configure`/`workerinput` handshake to solve a condition that the timeout already converts
  into a clear failure.
- [DESTRUCTIVE] Raising Redis's `databases` setting to widen the pool. It is not runtime-settable,
  so it requires restarting the Redis instance that backs the live bridge, worker, and production
  db 0. Not worth it for a condition wait-then-fail already handles safely.
- [SEPARATE-SLUG #2628] Reinstating any machine-global full-suite serialisation lock (#1984). The
  deletion rationale in `scripts/pytest-clean.sh` stands: isolation belongs in namespacing, not in
  serializing the machine.

## Update System

No update-system changes required. Nearly every file touched is test infrastructure (`tests/`) plus
`pyproject.toml` marker/addopts hygiene; no new dependency or config file needs propagating, and no
migration is needed for existing checkouts. `/update` picks the changes up as ordinary source on the
next sync.

The single production edit — `handlers.clear()` in `monitoring/bridge_watchdog.py:87-93` — ships to
a running service, so the watchdog must be restarted after merge per `CLAUDE.md`'s restart rule
(`./scripts/valor-service.sh restart`). It is idempotence-only and carries no config or migration.

The one operational note worth recording in the docs: `scripts/reap-xdist.sh --apply` frees orphaned
workers and therefore frees claim slots. It becomes the named remedy in the exhaustion error
message, so its behavior is now load-bearing for a user-visible failure path.

## Agent Integration

No agent integration required — this is entirely test-infrastructure. Nothing new is reachable from
Telegram, no CLI entry point is added to `pyproject.toml [project.scripts]`, and the bridge imports
nothing from `tests/`.

The indirect effect is real but needs no wiring: `/do-test`, `/do-build`, and the merge gate all
consume `tests/unit/` results, so a suite that stops rotating makes those gates trustworthy again.
No code change in those paths.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/test-db-ownership.md` — the claim pool, the ownership invariant, what
  the guard denies and why, how to get a scratch db, what the exhaustion error means and how to
  clear it. Include the `#2117 → #2606 → #2624 → #2628` history so the next agent understands why
  the invariant is enforced rather than documented.
  Document all three rotation writers, not just the flush one: the module-reload registry leak is the
  same class of bug (a stale binding silently defeating a restore), and the popoto plugin's db-15
  flush is the case that proves the pool's owner is not only this repo's own code. The doc is the only
  place a future agent will find them connected. Include the rule that any installed pytest plugin
  touching Redis must be pointed at the claimed db, and record why `POPOTO_TEST_DB` is set from
  `pytest_configure` rather than the plugin being disabled.
- [ ] Add the entry to the `docs/features/README.md` index table.

### Inline Documentation
- [ ] `tests/README.md` — update the isolation section: tests must never construct a
  `redis.Redis(db=N)` from a self-derived number; `claim_test_db()` and the `scratch_test_db`
  fixture are the only sources. Note that `--dist=loadfile` gives co-location but not assignment
  determinism, so cross-file leaks surface differently every run. Add the module-reload rule: a
  test that `importlib.reload`s a module owning a registry must restore it, and the conftest guard
  now covers `agent.index_drift` and `monitoring.bridge_watchdog`.
- [ ] `tests/README.md:9` — fix the stale "~40s parallel" runtime claim. `CLAUDE.md` and
  `pyproject.toml`'s `--timeout=420` put a full `tests/unit/` run at roughly 20 minutes; the
  existing figure is ~30x off and misleads every agent budgeting a run.
- [ ] `tests/db_claim.py` module docstring — document `claimed_test_dbs()`,
  `claim_scratch_test_db()`, and the wait-then-fail exhaustion policy, replacing the current
  "graceful legacy fallback" description.
- [ ] `tests/conftest.py:103-150` — rewrite the guard's docstring: it is now an *ownership* guard
  of which the db0 rule is one case, and it still carries the 2026-06-03 production-wipe rationale.

### External Documentation Site
Not applicable — this repo has no external docs site.

## Success Criteria

- [ ] `flushdb()` against an unclaimed db raises, with a message naming the db, the claimed set, and
  `scratch_test_db`; `flushall()` remains unconditionally blocked; `db == 0` remains blocked.
- [ ] No `redis.Redis(db=...)` under `tests/` takes its `db=` value from anything but the claim API
  — asserted by an AST walk, not by grep and not by review. Both documented offenders (the
  `PYTEST_XDIST_WORKER` derivation *and* the hardcoded `15`) are caught.
- [ ] `claim_test_db()` never returns a db number absent from `claimed_test_dbs()` — on **every**
  return path, flock and registry-unreachable fallback alike. On exhaustion it polls briefly, then
  raises.
- [ ] `TestSearchIntegration` no longer runs as part of `tests/unit/`.
- [ ] **The binding measurement (writer 1):** with a real child process holding `flock` on slot 1,
  `flushdb()` on db 1 raises and `flushdb()` on the claimed db is permitted. ~1 s, deterministic,
  red on `main`. Both outputs pasted into the PR body.
- [ ] **The binding measurement (writer 2):** reloading `agent.index_drift` leaves
  `covered_model_names()` unchanged and strands no `FakeCoveredModel`. Red on `main`. Output pasted
  into the PR body.
- [ ] **The binding measurement (writer 3):** a sentinel key written to db 15 survives across a test
  boundary, i.e. the popoto plugin no longer flushes a claim-pool slot. Red on `main` (current
  observed result: `PROBE sentinel_survived=False`). Output pasted into the PR body.
- [ ] `POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"] == claim_test_db()` inside any test —
  the popoto plugin and this repo's fixture agree on one db, so a future popoto upgrade that changes
  the plugin's resolution order fails a test instead of silently rotating the suite.
- [ ] The `redis_test_db` fast path at `tests/conftest.py:592-597` is deleted, so spike-5's
  disposition holds regardless of which Task 7 option was chosen.
- [ ] Every regression test added fails on `main` and passes on the branch — red-state output pasted
  into the PR body.
- [ ] Exhaustion-path tests complete in seconds, not the wait window — proving
  `_TEST_DB_CLAIM_WAIT_S` is read as a module attribute at call time and is genuinely patchable.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail markers are added; the replaced exhaustion test is a hard assertion.

## Team Orchestration

### Team Members

- **Builder (db-ownership)**
  - Name: `db-ownership-builder`
  - Role: `tests/db_claim.py` + `tests/conftest.py` — claimed-set accessor, scratch claim, ownership
    guard, exhaustion policy
  - Agent Type: builder
  - Domain: Redis/Popoto data, async/concurrency
  - Resume: true

- **Builder (call-sites)**
  - Name: `callsite-builder`
  - Role: the two stale call sites, the replaced exhaustion tests, the youtube relocation, the AST
    recurrence guard, and the second rotation writer (module-reload registry restoration)
  - Agent Type: test-engineer
  - Resume: true

- **Validator (isolation)**
  - Name: `isolation-validator`
  - Role: red-state proofs, then the quiesced double-run measurement and failure-set diff
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `test-db-documentarian`
  - Role: `docs/features/test-db-ownership.md`, `tests/README.md`, docstrings
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Claimed-set accessor and scratch claim
- **Task ID**: build-claim-api
- **Depends On**: none
- **Validates**: tests/unit/test_conftest_isolation_guards.py (create cases in `TestPerProcessDbClaim`)
- **Informed By**: spike-2 (the flock claim is correct — extend it, do not redesign it), spike-3
  (the pool runs near its ceiling)
- **Assigned To**: db-ownership-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_CLAIMED_DB_NUMS: set[int]` in `tests/db_claim.py`; register the number in the same step that
  appends the fd (Race 3).
- **Populate the number set on EVERY path that returns a db, not just the flock path.** In the
  registry-unreachable `except OSError` branch (`tests/db_claim.py:132-139`), add
  `_CLAIMED_DB_NUMS.add(_CLAIMED_TEST_DB)` immediately before `return _CLAIMED_TEST_DB`. The fd list
  is legitimately flock-only; the number set is not. Skipping this composes with Task 2's
  fail-closed-on-empty rule into a whole-process setup outage (every test errors at
  `tests/conftest.py:617`).
- Add `claimed_test_dbs() -> frozenset[int]`, empty before any claim.
- Add `claim_scratch_test_db() -> int | None`: claims an additional pool slot, returns `None` when
  exhausted. Never falls back to a derived number. **Memoize it per process** exactly as
  `claim_test_db()` memoizes `_CLAIMED_TEST_DB` (add a `_CLAIMED_SCRATCH_DB` module global), so
  repeated calls return the same slot instead of consuming a new one each time.
- Extend `release_test_db_claim()` to clear the number set with the fds.
- Add `_TEST_DB_CLAIM_WAIT_S = int(os.environ.get("TEST_DB_CLAIM_WAIT_S", "30"))` next to
  `_TEST_DB_POOL_MAX` with the same provisional/tunable comment style
  (`tests/db_claim.py:43-48`). Task 4 consumes it; defining it here keeps the constants together.
- Add `wait_s` to `_reset_claim_state` (`tests/unit/test_conftest_isolation_guards.py:472-489`),
  mirroring `pool_max`.
- **`_reset_claim_state` must also rebind the number set, not just the fd list.** Add
  `monkeypatch.setattr(_db_claim, "_CLAIMED_DB_NUMS", set(), raising=False)` — and the same for the
  new `_CLAIMED_SCRATCH_DB` — alongside the existing
  `fresh_fds` swap (`:486`), and return it beside `fresh_fds` so assertions can read it. Without
  this the claim tests mutate the live process-wide set: `test_claim_is_in_pool_idempotent_and_releasable`
  (`:503-517`) calls `release_test_db_claim()` at `:513`, and `monkeypatch` cannot undo an in-place
  `.clear()` — so the real set is permanently emptied and every later test in that worker has its
  autouse `redis_test_db` flush (`tests/conftest.py:617`) denied by Task 2's fail-closed-on-empty
  rule, erroring in setup. The converse leak matters too: the other claim tests would otherwise
  push `tmp_path` slot numbers into the real set, making the guard *permit* flushes on dbs this
  process does not own. Which worker is hit varies per run under `--dist=loadfile`, so skipping
  this ships a rotating failure set of its own — precisely the bug this plan exists to kill.
- Add tests: empty-before-claim; scratch is a different number and appears in the set; release
  clears both; scratch returns `None` under a monkeypatched tiny pool; **registry-unreachable
  fallback returns a db that IS in `claimed_test_dbs()` and IS flushable** (monkeypatch
  `_test_db_claim_dir` to raise `OSError`); **a claim/release cycle performed inside
  `_reset_claim_state` leaves the real `_db_claim._CLAIMED_DB_NUMS` untouched** (read it before and
  after, assert equal — this is the regression test for the rebind above); **N sequential
  `claim_scratch_test_db()` calls consume one slot, not N** (the memoization regression test).
- **Land this task first.** Task 2's denial message and Task 5's allowlist both name
  `scratch_test_db`/`claim_scratch_test_db`, and neither symbol exists in `tests/` today.

### 2. Ownership-enforcing flush guard
- **Task ID**: build-flush-guard
- **Depends On**: build-claim-api
- **Validates**: tests/unit/test_redis_flush_guard.py
- **Assigned To**: db-ownership-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `_install_redis_db0_flush_guard` (`tests/conftest.py:103-150`) as an ownership guard:
  permit `flushdb` only when `_db_of(client)` is in `claimed_test_dbs()`.
- Keep `flushall` unconditionally blocked and `db == 0` blocked (an unclaimable db is denied by the
  same rule, but keep the explicit db0 branch and its 2026-06-03 rationale message).
- Keep `_db_of`'s fail-closed `except Exception: return 0`; add a test that a client whose
  `connection_pool` raises is denied.
- Deny when the claimed set is empty — fail closed.
- Message must name: attempted db, claimed set, and `scratch_test_db`.
- **Read the claimed set as a module attribute inside the wrapper, on every call.**
  `_install_redis_db0_flush_guard()` runs at conftest import time (`tests/conftest.py:150`), long
  before any claim exists; a captured snapshot would be permanently empty.
- Add the `scratch_test_db` fixture: yields `claim_scratch_test_db()`, or `pytest.skip`s with an
  explicit reason when the pool is exhausted. **Declare it `scope="session"`** and rely on Task 1's
  memoization of `claim_scratch_test_db()`. A function-scoped, non-memoized fixture would claim a
  fresh slot for every requesting test with no release path, walking the 15-slot pool monotonically
  into Task 4's new fail-hard `RuntimeError` — the fixture would manufacture the exact exhaustion the
  same PR makes fatal. One scratch slot per process, held for the session, released by
  `release_test_db_claim()` at session end.
- **The binding red-state proof — the adversarial holder test.** Add to `TestPerProcessDbClaim`,
  using helpers that already exist in that file: `_spawn_flock_holder(claim_dir, [1])`
  (`:445-470`) spawns a real child holding `flock(LOCK_EX|LOCK_NB)` on slot 1; `_reset_claim_state`
  (`:472-489`) redirects the registry at `tmp_path`; `_close_fds` (`:492-503`) is teardown;
  `test_claim_skips_slot_held_by_live_process` (`:556-568`) is the structural precedent. The case:
  hold slot 1 → `claim_test_db()` (returns something != 1) → assert `redis.Redis(db=1).flushdb()`
  raises → assert `redis.Redis(db=<mine>).flushdb()` is permitted. Runs in ~1 s; **red on `main`**
  (the existing guard at `tests/conftest.py:117-129` rejects only `db == 0`), green on the branch.
  This is the plan's pass condition — capture both outputs into the PR body.

### 3. Correct the two stale call sites
- **Task ID**: build-callsites
- **Depends On**: build-flush-guard
- **Validates**: tests/unit/test_redis_flush_guard.py, tests/unit/test_conftest_isolation_guards.py
- **Informed By**: spike-1 (gw3 claims 11, derives 4 — the mismatch is the bug)
- **Assigned To**: callsite-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete `_own_test_db` from `tests/unit/test_redis_flush_guard.py:21-24`; both call sites
  (`:45-50`, `:53-57`) use `claim_test_db()`.
- `tests/unit/test_conftest_isolation_guards.py:359-366,420`: take the divergent db from the
  `scratch_test_db` fixture; delete the `divergent_db = 15 if ... else 14` derivation and its
  now-wrong comment.
- Verify `tests/integration/test_session_archive_cold_boot.py:100,194` still passes unchanged (it
  flushes its own claimed client) — Risk 1.

### 4. Wait-then-fail on pool exhaustion
- **Task ID**: build-exhaustion-policy
- **Depends On**: build-claim-api
- **Validates**: tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim
- **Informed By**: spike-3 (exhaustion is routine, not theoretical)
- **Assigned To**: db-ownership-builder
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: false
- Replace the exhaustion fallback at `tests/db_claim.py:144-153` with a bounded poll over the pool
  (~1 s interval, ceiling `_TEST_DB_CLAIM_WAIT_S`), then `RuntimeError` naming the pool size, the
  contention, and `scripts/reap-xdist.sh --apply`.
- **Read `_TEST_DB_CLAIM_WAIT_S` as a module attribute inside the loop** — not a default argument,
  not an import-time local — so `monkeypatch.setattr(_db_claim, "_TEST_DB_CLAIM_WAIT_S", 0)` in
  tests actually takes effect. If this is got wrong the exhaustion tests do not fail, they *hang*,
  and at a 300 s-era value would blow the `--timeout=420` ceiling. Default is **30**, not 300
  (spike-3: in real contention no slot frees inside any tolerable window, so a long wait only
  delays an identical error).
- Keep the registry-unreachable fallback at `:130-139` as a fallback, but with Task 1's number-set
  registration added; add a test pinning that it still warns, still returns the legacy number, and
  that the number is claimed-and-flushable, so the two paths cannot be conflated later.
- REPLACE `test_pool_exhaustion_falls_back_with_warning` (`:592-605`) with
  `test_pool_exhaustion_raises_rather_than_colliding`, using `_reset_claim_state(..., wait_s=0)`.
- REPLACE `test_dead_holder_slot_is_reclaimed` (`:570-591`). Its current shape claims while the
  pool is fully held and expects a prompt return (`:576-577`); under wait-then-fail that blocks the
  whole window and then raises, so an assertion edit cannot save it. New shape: spawn holder →
  `terminate()` + `wait()` → reset `_CLAIMED_TEST_DB` → `claim_test_db()` → assert slot 1 reclaimed
  and one fd held. Drop the pre-kill claim entirely.
- Record Race 1 (hold-and-wait) as a comment at the retry loop, naming the deferred all-or-nothing
  escalation.

### 5. Recurrence guard and live-test relocation
- **Task ID**: build-recurrence-guard
- **Depends On**: build-callsites
- **Validates**: tests/unit/test_redis_flush_guard.py, tests/integration/test_youtube_search_live.py (create)
- **Assigned To**: callsite-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- **Primary leg — AST walk.** Parse every `tests/**/*.py`; for each `ast.Call` whose func resolves
  to `Redis` or `Redis.from_url`, inspect the `db=` keyword. Permit only: a `Call` to
  `claim_test_db`/`claim_scratch_test_db`, or a `Name` bound from the `scratch_test_db`/
  `redis_test_db` fixture. Everything else fails, against an explicit `{path: reason}` allowlist so
  an addition is a conscious act. Scoping to the `db=` argument is what keeps this inside the
  Rabbit Hole boundary — it is not the rejected blanket no-raw-client lint.
- **Second leg — the cheap grep.** Keep the `PYTEST_XDIST_WORKER`/`workerinput` derivation check as
  a fast secondary assertion.
- **Why both legs.** The grep catches `_own_test_db` but *not*
  `tests/unit/test_conftest_isolation_guards.py:362`'s `divergent_db = 15 if base_test_db != 15
  else 14` — a bare literal, no worker id. Only the AST leg catches that one, and it is the offender
  the one-off `grep -c 'divergent_db = 15'` Verification row would miss the moment someone writes
  `13`. Without the AST leg the "ends the series" claim is unearned.
- **Red-state proof**: it must fail on `main` on *both* offenders. Capture that output — a
  recurrence guard that has never been seen red is not known to work.
- Move `TestSearchIntegration` (`tests/unit/test_youtube_search.py:231-245`) to
  `tests/integration/test_youtube_search_live.py`.

### 6. Close the second rotation writer: module-reload registry leak
- **Task ID**: build-reload-restore
- **Depends On**: none
- **Validates**: tests/unit/test_index_drift.py, tests/unit/test_index_drift_coverage.py, tests/unit/test_conftest_isolation_guards.py
- **Informed By**: critique BLOCKER 3 (independent of `flushdb`; same rotate-run-to-run signature)
- **Assigned To**: callsite-builder
- **Agent Type**: test-engineer
- **Parallel**: true (touches no file Tasks 1-5 touch)
- Replace `importlib.reload(agent.index_drift)` at `tests/unit/test_index_drift.py:207-208` with a
  direct assertion on `AGENTSESSION_INDEX_DRIFT_TOLERANCE`, plus `monkeypatch.setattr` wherever a
  non-default value is wanted. The reload existed only to observe a module constant; it leaves a
  freshly-rebound `DRIFT_COVERED_MODELS` in `sys.modules` for the rest of the worker.
- Rebind `tests/unit/test_index_drift_coverage.py`'s `restore_registry` fixture (`:37-42`) and its
  `register_drift_model` call sites to go through the *module* object, not the collection-time
  `from agent.index_drift import ...` names, so a fixture can never restore an orphaned dict while
  the test writes to the live one.
- Widen the conftest reload guard: `tests/conftest.py:384` currently scopes
  `_SHARED_EXCEPTION_MODULES` to `("models.session_lifecycle",)` and `_snapshot_shared_exceptions`
  (`:391-401`) snapshots only `BaseException` subclasses. Extend it to snapshot module-level
  registry objects (dicts, classes, enums) for `agent.index_drift` and `monitoring.bridge_watchdog`.
- Bring `tests/unit/test_worker_watchdog.py:47,52,58,1050-1058` under the same restoration (it
  leaves `HEARTBEAT_THRESHOLD=90` set across a span of tests).
- `monitoring/bridge_watchdog.py:87-93`: add the `handlers.clear()` that
  `monitoring/worker_watchdog.py:150-151` already has, so a reload does not stack a second
  `RotatingFileHandler`. One line of production code, idempotence only.
- **Red-state proof**: a test that reloads `agent.index_drift`, then asserts
  `covered_model_names()` is unchanged and no `FakeCoveredModel` survives — red on `main`, green on
  the branch.

### 7. Close the third rotation writer: popoto's bundled pytest plugin flushes db 15
- **Task ID**: build-popoto-plugin-db
- **Depends On**: build-claim-api
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Informed By**: spike-4 (the plugin is live and flushes db 15 before every test), spike-5 (the
  `redis_test_db` fast path is dead code *only because* this plugin is loaded)
- **Assigned To**: db-ownership-builder
- **Agent Type**: builder
- **Parallel**: true (touches `pyproject.toml` and `tests/conftest.py`'s `pytest_configure`; no file
  Tasks 3-6 own)
- **Preferred implementation — repoint, do not silence.** In `tests/conftest.py`'s `pytest_configure`,
  set `os.environ["POPOTO_TEST_DB"] = str(claim_test_db())`. `pytest_configure` runs before the
  plugin's session fixture resolves its target, and that fixture reads `POPOTO_TEST_DB` first (ahead
  of the `popoto_test_db` ini option and the built-in default of 15), so the plugin's `_swap_db` and
  its per-test `flushdb()` both land on this process's own claimed db. This keeps the plugin's
  `_popoto_reset_async` per-test event-loop reset, which this repo's `redis_test_db` does not
  replicate and which async tests currently depend on.
- **Alternative, only if the above proves unworkable:** `-p no:popoto` in `pyproject.toml:195`
  addopts. Simpler, but it removes `_popoto_reset_async` and the db-0 tripwire; validate the full
  async suite before choosing it. Record the choice and the reason in the PR body either way.
- **Never leave the plugin on its db-15 default.** Db 15 is `_TEST_DB_POOL_MAX` — a claimable slot —
  so every flush of it is a cross-process wipe of whichever sibling run holds slot 15.
- **Delete the `redis_test_db` fast path (`tests/conftest.py:592-597`) in this same change.** Per
  spike-5 it is unreachable today only because the plugin's module-level `from popoto import
  redis_db` guarantees the import happens at pytest startup. Choosing `-p no:popoto` would revive it
  as a live hazard in the same commit; removing it makes the disposition true under both options.
- **Red-state proof**: a test that writes a sentinel key to db 15 (or, better, to a slot claimed by a
  spawned `_spawn_flock_holder` child) and asserts it survives across a test boundary. Red on `main`
  — the current `PROBE sentinel_survived=False` result is exactly this test failing — and green on
  the branch. Paste both outputs into the PR body.
- Add a test asserting `POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"] == claim_test_db()`
  at the start of a test, so a future popoto upgrade that changes the plugin's resolution order fails
  here rather than by silently rotating the suite again.

### 8. Run provenance in the pytest header
- **Task ID**: build-provenance
- **Depends On**: build-claim-api
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Assigned To**: db-ownership-builder
- **Agent Type**: builder
- **Parallel**: true
- Emit `worker=<gwN|master> db=<claimed>` per worker, so a future rotation starts from a log line
  instead of a fresh recon (Risk 3). One line per worker; no per-test logging.
- **Never call `claim_test_db()` from the header hook.** `pytest_report_header` also runs in the
  xdist *controller*, which owns no test db; claiming there would burn a 16th slot from a pool of
  15 that spike-3 already found at 10/15 utilisation. Read the already-claimed value
  (`_db_claim._CLAIMED_TEST_DB`, `None` if unclaimed) instead.
- **Route worker output through `workeroutput`.** xdist does not surface a worker's
  `pytest_report_header` to the terminal, so a naive implementation prints nothing where it
  matters. Use the repo's existing controller/worker idiom at `tests/conftest.py:1082`
  (`if getattr(config, "workerinput", None):` is the worker branch): the worker writes
  `config.workeroutput["test_db"]`, and the controller surfaces the collected values from
  `pytest_report_header`/`pytest_terminal_summary`.
- Add a test asserting the controller path emits a line per worker under `-n 2`.

### 9. Validate: red-state proofs
- **Task ID**: validate-rotation-fixed
- **Depends On**: build-callsites, build-exhaustion-policy, build-recurrence-guard, build-reload-restore, build-popoto-plugin-db, build-provenance
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- **Pass condition:** every new regression test fails on `main` and passes on the branch, with both
  outputs pasted into the PR body. The three that carry the plan are (a) Task 2's adversarial
  flock-holder flush test, (b) Task 6's reload-restoration test, and (c) Task 7's db-15 sentinel
  survival test — one per rotation writer. Each runs in ~1 s and reproduces a real competing writer
  deterministically.
- **Record the accepted baseline failure set before asserting anything about the full suite.** Run
  `tests/unit/` once on `main` at the branch point and once on the branch, and write **the failing
  node ids** (names, never a count) into the PR body. The gate is "no failure on the branch outside
  the recorded `main` baseline, **and zero setup-phase errors**" — not exit 0. Exit 0 is unachievable
  by construction: issue #2628's premise is that `main`'s suite has a non-empty failure set, and
  nothing in this plan claims to fix those underlying failures, only to stop the set rotating. A gate
  that cannot pass is a gate that gets waived, and a waived gate certifies nothing.
- The absence of setup-phase errors is the specific signal that matters for Risk 1: a legitimate flush
  denied by the new guard surfaces as an *error in setup*, not as an assertion failure, so it is
  cleanly separable from the pre-existing baseline. Verify
  `tests/integration/test_session_archive_cold_boot.py:100,194` explicitly.
- **Optional, post-merge, non-quiescent soak:** two back-to-back `tests/unit/` runs *with* sibling
  runs present, diffing the failure sets. This is diagnostic, not a gate — see Risk 4 for why a
  quiesced double-run would have been vacuous. If a residual rotation shows up, report it with the
  Task 8 provenance lines and file a new issue rather than expanding this plan (No-Gos).

**Measurement context from a peer lane (record, do not cite as evidence of a fix).** Four full
`tests/unit/` runs were taken during round-2 investigation. Run 2, on `e6d0e2bc7` with Redis healthy,
gave 1 failed / 12079 passed / 0 errors, the sole failure being a stale `#730` intake guard test that
a peer has since deleted on `main` (`358da4fb5`, removing `TestIntakePathTerminalGuard` from
`tests/unit/test_recovery_respawn_safety.py` — a dead guard retired by M3). Run 3, with that test
gone, gave 12075 passed / 0 failed. **These runs were mostly quiesced and are therefore fully
consistent with the bug still being present**: every mechanism in this plan requires a *second live
pytest process* holding the victim slot, and under quiescence no such process exists. Clean quiesced
runs are exactly what Risk 4 predicts and are **not** evidence that the rotation is fixed. They are
recorded here only as a useful `main` baseline candidate for the comparison above. This plan does not
touch `tests/unit/test_recovery_respawn_safety.py` and neither duplicates nor contradicts
`358da4fb5`.

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-rotation-fixed
- **Assigned To**: test-db-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- `docs/features/test-db-ownership.md` + `docs/features/README.md` index entry.
- `tests/README.md` isolation section; `tests/db_claim.py` and `tests/conftest.py` docstrings.

### 11. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row in the Verification table.
- Confirm each Success Criterion, including the pasted red-state/green-state outputs for both
  writers.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No regression against the recorded baseline | `scripts/pytest-clean.sh tests/unit/ -q` on `main` at the branch point, then on the branch; diff the failing node ids | Every branch failure appears in the recorded `main` baseline, **and zero setup-phase errors**. NOT exit 0 — #2628's premise is that `main`'s suite already fails, so an exit-0 gate is unachievable and would be waived (see Task 9) |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Flush guard denies unclaimed db | `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard.py -q` | exit code 0 |
| Claim API + exhaustion policy | `scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -q` | exit code 0 |
| Reload restoration (writer 2) | `scripts/pytest-clean.sh tests/unit/test_index_drift.py tests/unit/test_index_drift_coverage.py -q` | exit code 0 |
| No self-derived `db=` anywhere in tests | `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard.py -q -k recurrence` | exit code 0 (the AST guard is the check; no grep substitute) |
| popoto plugin no longer on db 15 (writer 3) | `scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -q -k popoto_plugin` | exit code 0 |
| Legacy fallback no longer reachable on exhaustion | `grep -c 'falling back to legacy' tests/db_claim.py \|\| true` | match count == 1 (registry-unreachable path only) |
| Live-network test out of the unit suite | `grep -c 'class TestSearchIntegration' tests/unit/test_youtube_search.py \|\| true` | prints `0` (`grep -c` exits 1 on zero matches, hence the `\|\| true`; judge the printed count, not the exit code) |
| Ownership doc exists | `test -f docs/features/test-db-ownership.md` | exit code 0 |
| No stale xfails added | `grep -rn 'xfail' tests/unit/test_redis_flush_guard.py tests/unit/test_conftest_isolation_guards.py \|\| true` | prints nothing (same `grep` exit-code caveat) |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The retained registry-unreachable fallback (`tests/db_claim.py:130-139`) returns a db WITHOUT claiming a slot, so no fd lands in `_CLAIM_LOCK_FDS` (`:116`) and `claimed_test_dbs()` is empty. Combined with Task 2's fail-closed-on-empty rule, the autouse fixture's own `test_client.flushdb()` at `tests/conftest.py:617` is denied and EVERY test in that process errors in setup. A documented graceful degradation becomes a total suite outage. | ✅ RESOLVED | In the `except OSError` branch at `tests/db_claim.py:132-139`, add `_CLAIMED_DB_NUMS.add(_CLAIMED_TEST_DB)` immediately before `return _CLAIMED_TEST_DB` — the number set must be populated on BOTH the flock path and the fallback path; only the fd list is flock-only. Add a Task 1 test: monkeypatch `_test_db_claim_dir` to raise `OSError`, assert `claim_test_db() in claimed_test_dbs()` and that a flush on it is permitted. |
| BLOCKER | Risk & Robustness | Task 4 says only "update the `:576-577` assertion", but that line is inside `test_dead_holder_slot_is_reclaimed` (`tests/unit/test_conftest_isolation_guards.py:570-591`), whose structure requires `claim_test_db()` to RETURN PROMPTLY while the pool is fully held (claim, kill holder, re-claim). Under wait-then-fail it blocks 300 s then raises, so the test needs restructuring, not an assertion edit. Same 300 s block hits the replacement exhaustion test, against a `--timeout=420` ceiling. | ✅ RESOLVED | `TEST_DB_CLAIM_WAIT_S` must be read as a MODULE ATTRIBUTE at call time inside the retry loop (`_db_claim._TEST_DB_CLAIM_WAIT_S`), never a default arg or import-time local, or `monkeypatch.setattr(_db_claim, "_TEST_DB_CLAIM_WAIT_S", 0)` silently no-ops — the same shape that makes `_TEST_DB_POOL_MAX` patchable via `_reset_claim_state`. Rewrite `test_dead_holder_slot_is_reclaimed` as spawn holder → terminate/wait → reset `_CLAIMED_TEST_DB` → claim, dropping the pre-kill claim entirely. |
| CONCERN | History & Consistency | Task 5's recurrence guard catches only ONE of the two documented offenders. It greps for `PYTEST_XDIST_WORKER`/`workerinput` derivation, which catches `_own_test_db` but NOT `tests/unit/test_conftest_isolation_guards.py:362` (`divergent_db = 15 if base_test_db != 15 else 14`) — a hardcoded literal with no worker id. That site *invented* ownership rather than re-deriving it. The one-off `grep -c 'divergent_db = 15'` Verification row will not survive the next author writing `13`, so the "ends the series" claim is unearned. | ✅ RESOLVED | AST-walk `tests/**/*.py` for `ast.Call` resolving to `Redis`/`Redis.from_url` and inspect the `db=` keyword: flag unless the value is a `Call` to `claim_test_db`/`claim_scratch_test_db` or a `Name` bound from the `scratch_test_db`/`redis_test_db` fixture, with an explicit `{path: reason}` allowlist. Keep the `PYTEST_XDIST_WORKER` grep as a cheaper second leg. Scoping to the `db=` argument keeps this inside the Rabbit Hole boundary (it is not the rejected blanket no-raw-client lint). |
| CONCERN | Scope & Value | The 300 s default wait is refuted by the plan's own spike-3: with `hw.ncpu = 10`, two concurrent `-n auto` runs need 20 of 15 slots and a full run takes ~20 minutes, so in the exact contention state the wait targets, no slot frees inside the window. The wait never succeeds where it matters; it only turns an instant actionable error into a five-minute stall before the identical error — which Open Question 1 already names as the worse outcome. | ✅ RESOLVED | Set `TEST_DB_CLAIM_WAIT_S` default to 30, not 300, keeping it env-overridable with the provisional/tunable comment style of `_TEST_DB_POOL_MAX` (`tests/db_claim.py:44-48`), and poll the pool on a ~1 s interval rather than one long sleep so a freed slot is picked up promptly. A 30 s ceiling also bounds the exhaustion tests even if a future author forgets to patch the constant. |
| CONCERN | Risk & Robustness | Task 6's `pytest_report_header` provenance has two defects. (a) The hook also runs in the CONTROLLER; calling `claim_test_db()` there claims a 16th slot the controller never uses, against a pool of 15 that spike-3 already found at 10/15. (b) xdist does not surface worker `pytest_report_header` output to the terminal, so the per-worker lines the plan relies on for the next investigation may never appear in the log. | ✅ RESOLVED | Use the repo's existing controller/worker idiom at `tests/conftest.py:1082` (`if getattr(config, "workerinput", None):` is the worker branch); emit from the worker into `config.workeroutput["test_db"]` and surface it from the controller in `pytest_report_header`/`pytest_terminal_summary`. Read the ALREADY-claimed value (`_db_claim._CLAIMED_TEST_DB`, `None` if unclaimed) — never call `claim_test_db()` from the header hook. |
| NIT | Scope & Value | Verification rows 4 and 5 pass `-p no:randomly`, but `pytest-randomly` is not installed (`import pytest_randomly` → ModuleNotFoundError) and is absent from `pyproject.toml`. The flag is a no-op implying an ordering control the suite does not have. | ✅ RESOLVED | n/a (NIT) |
| BLOCKER | Risk & Robustness | **The flush-ownership mechanism explains only PART of the rotation; a second independent writer is unaddressed.** `tests/unit/test_index_drift.py:208` calls `importlib.reload(agent.index_drift)` with no try/finally, no re-reload, and no fixture — the reloaded module stays in `sys.modules` for the rest of that worker's run. The reload rebinds `DRIFT_COVERED_MODELS` (`agent/index_drift.py:144`) to a NEW empty dict and re-runs the three `register_drift_model` calls (`:384,394,410`) into it. `tests/unit/test_index_drift_coverage.py:26-32` binds `DRIFT_COVERED_MODELS`, `ModelDriftSpec`, `covered_model_names`, `register_drift_model` at COLLECTION time, so after the reload it holds the orphaned old dict. Its `restore_registry` fixture (`:37-42`) snapshots/restores the stale dict while `register_drift_model` at `:108` writes `FakeCoveredModel` into the live one — cleanup silently no-ops and a fake in-memory model stays registered for the rest of the worker. Damage lands in a file that never reloaded. `pyproject.toml` addopts uses `-n auto --dist=loadfile`, which assigns whole files to workers dynamically, so reloader and victim co-land on the same worker only sometimes — exactly the rotate-run-to-run signature, and independent of `flushdb`. The existing guard cannot see it: `tests/conftest.py:384` scopes `_SHARED_EXCEPTION_MODULES` to `("models.session_lifecycle",)` and `_snapshot_shared_exceptions` (`:391-401`) only snapshots `BaseException` subclasses — registry dicts, dataclasses and enums are outside it. | ✅ RESOLVED | Add a task: replace `tests/unit/test_index_drift.py:208`'s reload with `monkeypatch.setattr(index_drift, "AGENTSESSION_INDEX_DRIFT_TOLERANCE", ...)`, and widen the conftest reload guard beyond `BaseException`-in-one-module to snapshot module-level registries/classes for `agent.index_drift` and `monitoring.bridge_watchdog`. Also unrestored: `tests/unit/test_worker_watchdog.py:47,52,58,1050` (leaves `HEARTBEAT_THRESHOLD=90` until `:1058`) and `monitoring/bridge_watchdog.py:87-93` adds a `RotatingFileHandler` per reload with no `handlers.clear()` (`monitoring/worker_watchdog.py:150-151` does clear — bridge_watchdog does not). If this is scoped out rather than fixed, the plan's "ends the #2117 → #2606 → #2624 series" claim must be withdrawn and the reload writer filed as its own issue BEFORE the double-run measurement, or that measurement will rotate for reasons this plan does not fix. **Checked and cleared: the M2/M3 Room/Job guarded-repair suites are NOT a new writer** — `models/job.py:314+` and `models/room.py:208-266` delete index keys db-wide but no Room/Job test calls `flushdb`, and the autouse `redis_test_db` fixture (`tests/conftest.py:570`) already flushes at both setup and teardown of every test, so their blast radius never exceeds the existing per-process db. |
| CONCERN | Scope & Value | **The quiesced double-run pass condition is the one configuration in which the bug provably cannot fire, and a cheaper deterministic proof already has all its helpers in-repo.** The Problem statement (`:14-17`) requires "a **different live pytest process**" to own the flushed db. Under quiescence there is exactly one process, so no victim exists — the 40-minute measurement certifies "the failure set is stable when nothing contends," which is vacuous for this regression. It is also unfalsifiable in the useful direction: any rotation it does catch is by construction a different cause (Risk 3, `:432-441`) routed to a new issue. The quiescence precondition also contradicts the repo's operating model: `CLAUDE.md:14` states several agents test on this machine at once, `scripts/reap-xdist.sh:14-22` and `scripts/pytest-clean.sh:44-49` are both built to SPARE sibling runs, and the plan's own spike-3 (`:200-209`) found slots 1-10 already held. A `pgrep` check before/after cannot detect a sibling that starts and finishes inside the window. | ✅ RESOLVED | Demote the double-run to an optional post-merge soak; promote a holder-process guard test to the binding criterion. The helpers exist: `_spawn_flock_holder(claim_dir, slots)` (`tests/unit/test_conftest_isolation_guards.py:445-470`) spawns a real child holding `flock(LOCK_EX\|LOCK_NB)` on given slots; `_reset_claim_state` (`:472-489`) redirects `_db_claim._test_db_claim_dir` at `tmp_path` and can shrink `_TEST_DB_POOL_MAX`; `_close_fds` (`:492-503`) is the teardown; `test_claim_skips_slot_held_by_live_process` (`:556-568`) is the precedent. New case in `TestPerProcessDbClaim`: hold slot 1, `claim_test_db()` (returns != 1), assert `redis.Redis(db=1).flushdb()` raises and `redis.Redis(db=mine).flushdb()` is permitted. ~1 s, red on `main` (the guard at `tests/conftest.py:117-129` only rejects `db == 0`), green on the branch. Two riders: (a) the guard must read the claimed set as a MODULE ATTRIBUTE at call time — `_install_redis_db0_flush_guard()` runs at conftest import (`tests/conftest.py:150`), long before any claim exists; (b) `scratch_test_db`/`claim_scratch_test_db` do not exist yet (grep over `tests/` returns nothing) but Task 2's error message and Task 5's allowlist both name them, so Task 1 must land them first. If the double-run is kept at all, run it WITHOUT quiescence — a concurrent sibling is the only configuration in which a stable failure set means anything. Also fix `tests/README.md:9` (`~40s parallel`), which is ~30x stale against `CLAUDE.md:14` and `pyproject.toml`'s ~21 min. |
| NIT | History & Consistency | The Failure Path Test Strategy citation `tests/db_claim.py:95-99,103-107,111-115` is drifted: `:95-99` is docstring prose and the three `except OSError` handlers sit at `:102-103`, `:106-109`, `:114-115`. The identification of three handlers is correct; only the anchors are wrong. | ✅ RESOLVED | n/a (NIT) |

### Round 2 (re-critique of the revised plan)

All nine round-1 findings above were verified against real source and genuinely hold: the fallback
number-set registration, the full replacement of `test_dead_holder_slot_is_reclaimed` with a
call-time-module-attribute `_TEST_DB_CLAIM_WAIT_S`, and the new Task 6 second-writer closure are
all present, actionable, and internally consistent (Tasks 7-10 renumbered correctly; Task 8's
`Depends On` picks up both `build-reload-restore` and `build-provenance`). The changed decisions —
two ~1 s adversarial red-on-main tests as the pass condition, 30 s wait with ~1 s polling, the AST
walk of the `db=` kwarg with grep as a second leg, and Task 7 reading `_db_claim._CLAIMED_TEST_DB`
via `config.workeroutput` — are sound. Round 2 found one new blocker.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | **Task 1 extends `release_test_db_claim()` to clear `_CLAIMED_DB_NUMS` but does not rebind that set in `_reset_claim_state`, so the claim tests corrupt the real process-wide set.** `_reset_claim_state` (`tests/unit/test_conftest_isolation_guards.py:472-489`) deliberately swaps in a *fresh list* for `_CLAIM_LOCK_FDS` (`:486`) precisely so tests cannot mutate the live one; Task 1 adds only a `wait_s` parameter alongside `pool_max` and leaves the new number set bound to the live module object. `monkeypatch` cannot undo an in-place `.clear()`. Certain failure: `test_claim_is_in_pool_idempotent_and_releasable` (`:503-517`) calls `release_test_db_claim()` at `:513`, permanently emptying the real `_CLAIMED_DB_NUMS`; every subsequent test in that worker then has its autouse `redis_test_db` flush (`tests/conftest.py:617`) denied by Task 2's fail-closed-on-empty rule and errors in setup. Conversely the other claim tests leak `tmp_path` slot numbers into the real set, making the guard *permit* flushes on unowned dbs. Under `--dist=loadfile` which worker is hit varies per run — the fix would ship with a rotating failure set of its own, reintroducing the exact bug this plan exists to kill. | ✅ RESOLVED | Task 1 now carries a dedicated bullet: `_reset_claim_state` must `monkeypatch.setattr(_db_claim, "_CLAIMED_DB_NUMS", set(), raising=False)` (and the same for the new `_CLAIMED_SCRATCH_DB`), mirroring the `fresh_fds` treatment of `_CLAIM_LOCK_FDS` at `:486`, and return the fresh set beside `fresh_fds` so assertions can read it. A Task 1 test asserts that a claim/release cycle performed inside `_reset_claim_state` leaves the real process-wide `_CLAIMED_DB_NUMS` untouched. |
| CONCERN | Scope & Value | Task 2's `scratch_test_db` fixture has no declared scope or memoization, while `claim_scratch_test_db()` claims an *additional* pool slot per call and Task 1 gives it no release path. A function-scoped, non-memoized fixture therefore consumes a fresh slot for every test that requests it and never returns any, walking a 15-slot pool monotonically toward Task 4's new fail-hard `RuntimeError`. | ✅ RESOLVED | `scratch_test_db` is now declared `scope="session"` in Task 2, and Task 1 memoizes `claim_scratch_test_db()` behind a `_CLAIMED_SCRATCH_DB` module global mirroring `_CLAIMED_TEST_DB`. One scratch slot per process, held for the session, released by `release_test_db_claim()` at session end. A Task 1 test asserts N sequential `claim_scratch_test_db()` calls consume one slot, not N. The rationale is also recorded in Technical Approach. |
| CONCERN | Scope & Value | Verification row 1 asserts `scripts/pytest-clean.sh tests/unit/ -q` exits 0, but the premise of issue #2628 is that `tests/unit/` on `main` has a non-empty (and rotating) failure set. Nothing in this plan claims to fix the underlying test failures — only to stop them rotating — so an exit-0 gate is unachievable and will be waived under pressure at validation time, which is how a gate stops meaning anything. | ✅ RESOLVED | Verification row 1 is restated as a baseline diff: run `tests/unit/` on `main` at the branch point and on the branch, record the failing **node ids** (never a count) in the PR body, and gate on "every branch failure appears in the recorded `main` baseline, and zero setup-phase errors." The row explicitly says NOT exit 0, with the reason, so it cannot be quietly reinterpreted. Task 9 carries the matching instruction and notes that a denied legitimate flush surfaces as a *setup error*, which is what makes it separable from the pre-existing baseline. |
| NIT | History & Consistency | Two Verification rows expect "match count == 0" from `grep -c`, which exits 1 on zero matches. As written a mechanical runner reads the row as a failure. | ✅ RESOLVED | Both rows (and the `xfail` row, which had the same defect) now append `\|\| true` and state that the printed count, not the exit code, is what is judged. |
| NIT | Scope & Value | The Test Impact sweep does not name two sites the Task 5 AST guard will flag: `tests/integration/test_agent_catchup_recovery.py:61` and `tests/unit/test_email_bridge.py:1399-1406`. | ✅ RESOLVED | Both added to Test Impact with UPDATE dispositions and no allowlist entry. `tests/integration/test_agent_catchup_recovery.py:61` is a hardcoded `db=1` used only for a `ping` — converted to `claim_test_db()` because an allowlist entry here would teach the next author that hardcoding is negotiable. `tests/unit/test_email_bridge.py:1399-1406` derives from `POPOTO_REDIS_DB.connection_pool.connection_kwargs`, a *third* derivation route that reads a value the popoto plugin mutates (Task 7) — converted to `claim_test_db()`, and Task 5's AST guard is extended to reject `connection_kwargs`-sourced `db=` values as well as literals and worker-id arithmetic. |

**Process note.** The round-1 revision (`c6e48a514`) was committed to branch
`session/retire-dead-intake-guard-tests` instead of `main`, so `main` still carried the unrevised
plan and the first re-critique dispatch graded the stale document. This file is now the revised plan
restored onto `main`; per `CLAUDE.md`, plan docs commit directly to `main`. The round-2 revision was
verified on `main` with `git merge-base --is-ancestor` before being reported.

### Round-2 revision: scope added beyond the critique findings

Two items came in from a peer lane investigating #2628 in parallel. Investigating them turned up a
**third rotation writer** that neither critique round had seen, because both had scoped their search
to this repo's `tests/` directory.

- **Peer item A — "four tests assert `db == 15`."** Partly mistaken as filed, and much more serious
  than filed. There is no `tests/test_pytest_plugin.py` in this repo; the file is
  `/Users/valorengels/src/popoto/tests/test_pytest_plugin.py`, in the **popoto** repo, and its four
  `db == 15` assertions are correct there. But chasing it revealed that popoto ships that plugin as a
  `pytest11` entry point which **this repo loads on every run**, on its db-15 default, flushing a
  claim-pool slot before every test. That is now spike-4 and Task 7, and it is the highest-frequency
  of the three writers. The peer's instinct was right even though the file pointer was not.
- **Peer item B — the `redis_test_db` fast path at `tests/conftest.py:595`.** Investigated and
  explicitly dispositioned as **ruled out** (spike-5): the popoto plugin's module-level
  `from popoto import redis_db` runs at pytest startup, so `popoto.redis_db` is always in
  `sys.modules` and the branch is unreachable. It is *not* left hanging — and because it is dead only
  by virtue of that plugin being loaded, Task 7 requires deleting the branch in the same change so
  the disposition survives whichever Task 7 option is chosen.

**Peer commit `358da4fb5`** (deletes `TestIntakePathTerminalGuard` from
`tests/unit/test_recovery_respawn_safety.py`, a dead #730 guard retired by M3) landed on `main`
during this revision. This plan does not touch that file and neither duplicates nor contradicts it.
The peer's four-run measurement is recorded under Task 9 **with the explicit caveat that the runs were
mostly quiesced**, which makes clean results fully consistent with the bug still being present. It is
baseline data, not evidence of a fix, and must not be cited as such.

---

## Resolved Questions

All three open questions are decided; none blocks the build.

1. **Exhaustion policy — DECIDED: wait 30 s, poll at ~1 s, then raise.** The original 300 s was
   refuted by the plan's own spike-3: two concurrent `-n auto` runs on a 10-core box demand 20 of
   15 slots and each takes ~20 minutes, so in the exact contention state the wait targets, no slot
   frees inside the window. A long wait therefore never succeeds where it matters and only converts
   an instant, actionable error into a five-minute stall before the identical error. 30 s still
   absorbs a sibling already tearing down, stays well inside `--timeout=420`, and is env-overridable
   (`TEST_DB_CLAIM_WAIT_S`) if the trade proves wrong.
2. **Guard scope — DECIDED: `flushdb` only.** `flushdb` is the only operation whose blast radius is
   another process's entire dataset, and it is the operation the reproduced bug uses. A general
   "no write on an unclaimed db" guard would wrap every mutating command on both the sync and async
   clients — a per-command hot-path cost paid by every test in the suite, plus a far larger surface
   of legitimate call sites to audit, for a class of bug never observed here. The AST recurrence
   guard (Task 5) closes the same hole one layer earlier, at *construction* time, for zero runtime
   cost. Revisit only if a cross-db non-flush write is actually observed.
3. **Verification quiescence — DECIDED: no quiescence, and no double-run gate.** A quiesced
   double-run is the one configuration in which this bug provably cannot fire (the root cause needs
   a second live pytest process; under quiescence there is no victim), so it would have been an
   unfalsifiable pass condition. It also contradicts the repo's tooling, which is deliberately built
   to spare sibling runs. Replaced by two ~1 s adversarial tests, one per rotation writer, each red
   on `main`. The double-run survives only as an optional post-merge diagnostic soak, run *with*
   siblings present. Full reasoning in Risk 4.
