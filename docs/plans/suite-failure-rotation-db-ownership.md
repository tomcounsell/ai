---
status: Planning
type: bug
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
process has not claimed fails loudly at the offending line, in that test, every time. Two
consecutive quiesced `tests/unit/` runs then produce byte-identical failure sets.

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
- **Interface changes**: `tests/db_claim.py` gains `claimed_test_dbs()` and
  `claim_scratch_test_db()`; `tests/conftest.py` gains a `scratch_test_db` fixture. All
  test-infrastructure surface — no production code changes.
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

**Not a build prerequisite, but a hard precondition of Task 7's measurement:** the machine must be
free of other pytest processes (`pgrep -f bin/pytest` returns nothing) before and after each
verification run. A re-baseline taken while sibling agents test measures noise, not the fix
(spike-3). It is deliberately kept out of the table above so it cannot block the build itself.

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
- **Loud exhaustion**: `claim_test_db()` waits for a slot, then fails with an actionable message,
  instead of silently returning a colliding database.
- **A structural recurrence guard**: a test asserting that no module under `tests/` derives a db
  number from `PYTEST_XDIST_WORKER`/`workerid` outside `tests/db_claim.py`. This is what ends the
  #2117 → #2606 → #2624 series.
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
- **Ownership is the flock set, not a number.** `tests/db_claim.py` currently memoizes a single
  `_CLAIMED_TEST_DB` and keeps fds in `_CLAIM_LOCK_FDS`. Track claimed slot *numbers* alongside the
  fds and expose `claimed_test_dbs() -> frozenset[int]`. `claim_test_db()` keeps returning the
  primary slot; `claim_scratch_test_db()` adds another. `release_test_db_claim()`
  (`tests/db_claim.py:156-175`) clears both.
- **Do not fight xdist.** Worker assignment stays nondeterministic (Research). The plan makes the
  suite's *result* deterministic, not its scheduling.
- **Exhaustion: wait, then fail.** Replace the `_legacy_test_db_num()` fallback at
  `tests/db_claim.py:144-153` with a bounded retry (`TEST_DB_CLAIM_WAIT_S`, default 300, an
  env-overridable provisional constant) that rescans the pool, then a `RuntimeError` naming the
  contention and pointing at `scripts/reap-xdist.sh --apply`. A colliding database is strictly worse
  than a clear failure: it produces exactly the corruption this plan exists to remove. Keep the
  *registry-unreachable* fallback at `:132-139` unchanged — that is a genuinely different, single-
  process condition (no writable `/tmp`).
- **Hold-and-wait is a real deadlock shape** — see Race 1. The bounded timeout is what converts a
  deadlock into a loud, diagnosable failure; the all-or-nothing controller allocation is the
  escalation if one is ever observed, and is deliberately not built now.
- **Recurrence guard by structure, not by discipline.** A test walks `tests/**/*.py`, greps for
  `PYTEST_XDIST_WORKER` and `workerinput`-derived db arithmetic, and asserts the only hit is
  `tests/db_claim.py` itself plus the tests that deliberately exercise it. Each future straggler
  then fails at authorship instead of after a merge.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tests/db_claim.py:95-99,103-107,111-115` — three `except OSError` blocks in
  `_try_claim_db_slot`. Two are load-bearing (`os.open` failure → not ours; `flock` failure → held
  by another live process) and gain assertions that the claim is *not* granted. The third (the
  `os.ftruncate`/`os.write` of debug metadata) is correctly best-effort; document why with an
  inline comment rather than testing it.
- [ ] `tests/db_claim.py:130-139` — the registry-unreachable `except OSError`: assert it still logs
  a WARNING and still returns the legacy number (this path is retained deliberately).
- [ ] `tests/conftest.py:110-113` `_db_of` — the `except Exception: return 0` that assumes the
  dangerous db when it cannot determine one. Under the new guard this must still deny, not allow:
  add a test with a client whose `connection_pool` raises, asserting `RuntimeError`.
- [ ] No exception handlers are added by this change beyond those listed.

### Empty/Invalid Input Handling
- [ ] `claimed_test_dbs()` before any claim: must return an empty frozenset, and the guard must then
  deny every `flushdb` rather than allowing all of them. Fail-closed on empty is the whole point —
  test it explicitly.
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
- [ ] `tests/unit/test_conftest_isolation_guards.py:576-577` — UPDATE: `assert
  _db_claim.claim_test_db() == 1  # legacy master fallback` asserts the same removed behavior on the
  exhaustion path.
- [ ] `tests/unit/test_youtube_search.py::TestSearchIntegration` (`:231-245`) — REPLACE: relocate to
  `tests/integration/test_youtube_search_live.py`. Leaves the unit-suite file with only offline
  tests.
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
not, and the abort message names the remedy. The 300 s default is a provisional, env-overridable
constant (`TEST_DB_CLAIM_WAIT_S`); slots free continuously as processes exit. If aborts prove
common, the escalation is all-or-nothing controller allocation (Race 1), not a return to colliding
fallbacks.

### Risk 3: The fix is correct but the rotation persists from a second, independent cause
**Impact:** The plan ships and the suite still rotates; confidence in the instrument stays broken.
**Mitigation:** The verification protocol is explicitly a *measurement*, not a formality: two
quiesced back-to-back runs with a diffed failure set. If a residual rotation survives, the
provenance header added in Task 6 (worker id + claimed db) makes the next investigation start with
evidence instead of a fresh recon. File any residue as a new issue rather than widening this one.

### Risk 4: The verification runs are themselves invalidated by sibling agents
**Impact:** A green double-run that proves nothing, or a red one that indicts the fix falsely.
**Mitigation:** `pgrep -f 'bin/pytest'` must return zero before *and* after each verification run;
record both checks in the PR body alongside the failure-set diff. spike-3 showed slots 1-10 already
held during planning, so this is the expected state, not a hypothetical.

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
**Mitigation:** The wait is bounded (`TEST_DB_CLAIM_WAIT_S`, default 300 s) and expires into a
`RuntimeError` naming the contention — a deadlock becomes a loud, diagnosable failure rather than a
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

- [SEPARATE-SLUG #2628] The residual rotation, if any survives verification, is re-measured under
  this same issue before the PR merges; anything still unexplained gets its own issue rather than
  expanding this plan's scope.
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

No update-system changes required. Every file touched is test infrastructure (`tests/`) plus
`pyproject.toml` marker/addopts hygiene; nothing ships to a running service, no new dependency or
config file needs propagating, and no migration is needed for existing checkouts. `/update` picks
the changes up as ordinary source on the next sync.

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
- [ ] Add the entry to the `docs/features/README.md` index table.

### Inline Documentation
- [ ] `tests/README.md` — update the isolation section: tests must never construct a
  `redis.Redis(db=N)` from a self-derived number; `claim_test_db()` and the `scratch_test_db`
  fixture are the only sources. Note that `--dist=loadfile` gives co-location but not assignment
  determinism, so cross-file leaks surface differently every run.
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
- [ ] No module under `tests/` derives a Redis db number from `PYTEST_XDIST_WORKER` or
  `workerinput` except `tests/db_claim.py` and the tests that deliberately exercise it — asserted by
  a test, not by review.
- [ ] `claim_test_db()` never returns an unclaimed db number: on exhaustion it waits, then raises.
- [ ] `TestSearchIntegration` no longer runs as part of `tests/unit/`.
- [ ] **The measurement:** two consecutive `tests/unit/` runs on the fix branch, on a quiesced
  machine (`pgrep -f 'bin/pytest'` = 0 before and after each), produce **identical failure sets**.
  The diff and both quiescence checks are pasted into the PR body.
- [ ] Every regression test added fails on `main` and passes on the branch — red-state output pasted
  into the PR body.
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
  - Role: the two stale call sites, the replaced exhaustion tests, the youtube relocation, the
    recurrence guard
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
- Track claimed slot *numbers* alongside `_CLAIM_LOCK_FDS` in `tests/db_claim.py`; register the
  number in the same step that appends the fd (Race 3).
- Add `claimed_test_dbs() -> frozenset[int]`, empty before any claim.
- Add `claim_scratch_test_db() -> int | None`: claims an additional pool slot, returns `None` when
  exhausted. Never falls back to a derived number.
- Extend `release_test_db_claim()` to clear the number set with the fds.
- Add tests: empty-before-claim; scratch is a different number and appears in the set; release
  clears both; scratch returns `None` under a monkeypatched tiny pool.

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
- Add the `scratch_test_db` fixture: yields `claim_scratch_test_db()`, or `pytest.skip`s with an
  explicit reason when the pool is exhausted.
- **Red-state proof**: on `main`, a test flushing an unclaimed pool db passes; on the branch it
  raises. Capture both.

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
- Replace the exhaustion fallback at `tests/db_claim.py:144-153` with a bounded retry over the pool,
  then `RuntimeError` naming the pool size, the contention, and `scripts/reap-xdist.sh --apply`.
- `TEST_DB_CLAIM_WAIT_S` (default 300) as a named env-overridable constant with a
  provisional/tunable comment, matching `_TEST_DB_POOL_MAX`'s existing style at
  `tests/db_claim.py:44-48`.
- Leave the registry-unreachable fallback at `:132-139` unchanged; add a test pinning that it still
  warns and still returns the legacy number, so the two paths cannot be conflated later.
- REPLACE `test_pool_exhaustion_falls_back_with_warning` (`:592-605`) with
  `test_pool_exhaustion_raises_rather_than_colliding`; update the `:576-577` assertion.
- Record Race 1 (hold-and-wait) as a comment at the retry loop, naming the deferred all-or-nothing
  escalation.

### 5. Recurrence guard and live-test relocation
- **Task ID**: build-recurrence-guard
- **Depends On**: build-callsites
- **Validates**: tests/unit/test_redis_flush_guard.py, tests/integration/test_youtube_search_live.py (create)
- **Assigned To**: callsite-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Add a structural test: walk `tests/**/*.py`, assert no module derives a db number from
  `PYTEST_XDIST_WORKER`/`workerinput` except `tests/db_claim.py` and the tests that deliberately
  exercise it (explicit allowlist, so an addition is a conscious act).
- **Red-state proof**: it must fail on `main` (catching `_own_test_db`). Capture that output — a
  recurrence guard that has never been seen red is not known to work.
- Move `TestSearchIntegration` (`tests/unit/test_youtube_search.py:231-245`) to
  `tests/integration/test_youtube_search_live.py`.

### 6. Run provenance in the pytest header
- **Task ID**: build-provenance
- **Depends On**: build-claim-api
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Assigned To**: db-ownership-builder
- **Agent Type**: builder
- **Parallel**: true
- Emit `worker=<gwN|master> db=<claimed>` per worker via `pytest_report_header`, so a future
  rotation starts from a log line instead of a fresh recon (Risk 3).
- Keep it to one line per worker; no per-test logging.

### 7. Validate: red-state proofs and the quiesced measurement
- **Task ID**: validate-rotation-fixed
- **Depends On**: build-callsites, build-exhaustion-policy, build-recurrence-guard, build-provenance
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm every new regression test fails on `main` and passes on the branch; collect the outputs.
- Confirm `pgrep -f 'bin/pytest' | wc -l` is `0`, then run `scripts/pytest-clean.sh tests/unit/`
  twice back to back, re-checking quiescence between runs.
- Diff the two failure sets. **Identical** is the pass condition; empty is the goal.
- If a residual rotation survives, report it with the provenance header lines rather than expanding
  this plan (No-Gos).

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-rotation-fixed
- **Assigned To**: test-db-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- `docs/features/test-db-ownership.md` + `docs/features/README.md` index entry.
- `tests/README.md` isolation section; `tests/db_claim.py` and `tests/conftest.py` docstrings.

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row in the Verification table.
- Confirm each Success Criterion, including the pasted failure-set diff and quiescence checks.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Flush guard denies unclaimed db | `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard.py -q -p no:randomly` | exit code 0 |
| Claim API + exhaustion policy | `scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -q -p no:randomly` | exit code 0 |
| Stale worker-id db derivation is gone | `grep -rn 'PYTEST_XDIST_WORKER\|workerinput' tests/ --include='*.py' \| grep -v 'tests/db_claim.py' \| grep -c 'int(worker'` | match count == 0 |
| No hardcoded scratch db 15 | `grep -c 'divergent_db = 15' tests/unit/test_conftest_isolation_guards.py` | match count == 0 |
| Legacy fallback no longer reachable on exhaustion | `grep -n 'falling back to legacy' tests/db_claim.py \| wc -l` | output contains 1 |
| Live-network test out of the unit suite | `grep -c 'class TestSearchIntegration' tests/unit/test_youtube_search.py` | match count == 0 |
| Ownership doc exists | `test -f docs/features/test-db-ownership.md` | exit code 0 |
| No stale xfails added | `grep -rn 'xfail' tests/unit/test_redis_flush_guard.py tests/unit/test_conftest_isolation_guards.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Exhaustion policy — wait-then-fail, or fail immediately?** The plan waits up to 300 s for a
   slot, then raises. A run that aborts after five minutes is worse for an agent than one that
   aborts instantly with the same message. Preference?
2. **Does the ownership guard belong on `flushdb` only, or on writes generally?** Flushing is the
   destructive case and the one causing this bug. A broader "no raw client on an unclaimed db"
   guard would catch quieter cross-db writes too, at the cost of touching many more call sites.
   This plan scopes to `flushdb`; say the word if the wider net is wanted now.
3. **Verification quiescence.** The double-run measurement needs the machine free of other pytest
   processes for roughly 40 minutes. Is that acceptable to schedule, and should the other lanes be
   paused for it?
