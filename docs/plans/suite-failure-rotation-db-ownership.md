---
status: Ready
type: bug
revision_applied: true
revision_applied_at: 2026-08-07T09:33:36Z
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
guard cannot see it — `tests/conftest.py:385` scopes `_SHARED_EXCEPTION_MODULES` to
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

This plan fixes all three writers: Tasks 1-5 (unowned `flushdb`, including the popoto plugin's db-15
flush, which Task 2 repoints at the claimed db) and Task 7 (unrestored module reload). Anything that
still rotates afterward is a fourth cause and gets its own issue.

**Task 6 (the AST recurrence guard) is DESCOPED to #2656.** It prevents *future* regressions; it does
not stop the rotation. It produced the sole remaining blocker in critique rounds 4, 5, and 6 while
the three writer fixes were verified sound, so it ships on its own cadence rather than holding a
suite-correctness fix hostage. Task numbering is deliberately **not** renumbered — Tasks 1-5 and 7-11
keep their identifiers, and slot 6 is a descope marker.

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
  that fires from installed library code, where neither the ownership guard nor a `tests/`-scoped AST
  walk can see it. Without this task the plan would have shipped, the suite would have kept
  rotating, and the new guard would have reported clean. Task 2's plugin-agnostic session-scoped
  client-ownership check is the runtime answer to this class, and it is **in scope here** — it does
  not depend on the descoped AST guard (#2656).

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
  `_TEST_DB_CLAIM_WAIT_S`, and a sticky `_CLAIM_FAILURE`; `tests/conftest.py` gains a **new
  `pytest_configure` hook that establishes the claim at session start** (worker-side only), a
  `scratch_test_db` fixture, and a widened module-reload restoration guard, and loses its dead
  fast-path branch.
- **Zero production changes.** Every file this plan touches is under `tests/`, plus `pyproject.toml`
  hygiene. The `monitoring/bridge_watchdog.py` `handlers.clear()` line that an earlier draft carried
  was moved out (round-3 concern 7): it cannot change any test's pass/fail outcome, so it does
  nothing for the rotating failure set, while dragging a launchd-service restart into an otherwise
  pure-`tests/` PR. It is filed under No-Gos as its own issue. Task 7's widened reload guard still
  covers `monitoring.bridge_watchdog`'s module-level registry objects, which is the part that
  actually closes a rotation path, and touches no production file.
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

- **The claim is established at session start, not at first fixture.** A new worker-side
  `pytest_configure` in `tests/conftest.py` claims this process's db before a single fixture runs and
  exports it as `POPOTO_TEST_DB`. This is the substrate change round 3 forced (see *The claim
  lifecycle* below): it makes "claimed" the only state a test can ever observe, which is what lets
  the flush guard be fail-closed without fighting pytest's fixture ordering, and it repoints the
  popoto plugin in the same hook.
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
- **A structural recurrence guard — DESCOPED to #2656.** An AST walk over `tests/**/*.py` asserting
  that no `redis.Redis(db=...)` construction takes its `db=` value from anywhere but the claim API.
  It prevents the next recurrence but stops none of the three live writers, so it is not what makes
  this PR correct. The #2117 → #2606 → #2624 series is ended here by *enforcement* (the ownership
  flush guard, Task 3, plus the claim established before any fixture runs, Task 2); #2656 adds the
  construction-time backstop afterward.
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

### The claim lifecycle (the decision that ends the round-1→3 patch series)

Rounds 1-3 produced eight blockers that are all the **same shape**: *the fail-closed ownership guard
meets something that runs before, outside, or independently of the claim.* Round 3's three blockers
are three instances of it — popoto's autouse fixture setting up before `redis_test_db`; the
exhaustion raise re-entered per test because failure was never recorded; `pytest_configure` running
in a controller that owns no db. Patching them one at a time is why the finding count stopped
dropping.

So the claim lifecycle is named explicitly, and the guard's behavior is defined for **every** state:

| State | When | Guard behavior | How this plan realises it |
|---|---|---|---|
| `unclaimed-not-yet` | a process that will own a db, before its claim is established | **Fail-closed is wrong here** — this needs an *ordering guarantee*, not a denial | **Designed out.** Task 2 claims in `pytest_configure`, which pytest runs before collection and therefore before every fixture, autouse or not, in-repo or from an installed plugin. No test, and no plugin fixture, can observe this state. |
| `claimed` | normal operation on a worker (or on the master under `-n0`) | fail-closed permits exactly the claimed set | Tasks 1 + 3 |
| `unclaimable-permanently` | pool exhausted; the claim raised | fail-closed is correct, **and the failure is sticky** — the wait is paid once per process, never per test | Task 2 aborts the session with `pytest.exit` at the moment of failure; Task 5's `_CLAIM_FAILURE` memo makes any later direct caller re-raise instantly instead of re-entering the poll |
| `not-applicable-controller` | the xdist controller, which runs no tests and must never burn a pool slot | **fail-closed is wrong to *escalate* here, and claiming is wronger** — the controller simply never claims, and never flushes | Task 2's explicit controller branch: `workerinput` absent **and** `numprocesses` truthy → return without claiming |

**Why session-start rather than another guard.** The alternative considered — and rejected — was to
keep claiming lazily in `redis_test_db` and add an exemption so the first `_popoto_flush_db` of each
worker is permitted while the claimed set is still empty. That exemption is a hole exactly the width
of the bug: "permit a flush when we do not know who owns the target" is the pre-#2606 status quo
restated, and it would be load-bearing on every worker's first test forever. Claiming at
`pytest_configure` removes the state instead of excusing it, and it costs one hook.

**What it collapses.** The popoto repoint is no longer a separate task racing the guard (round-3
blocker 1): the same hook that claims the db exports `POPOTO_TEST_DB`, so the plugin is correct by
construction from the first test, and there is no commit window in which the guard is live and the
plugin is not repointed. Round-3 blocker 3 dissolves for the same reason — there is exactly one
`pytest_configure`, written once, with the controller branch in it, instead of two tasks disagreeing
about whether the controller may claim.

**Alignment with #2645 (today's production DB-0 flush incident).** #2645's Layer 1 is a guarded
connection helper that refuses dangerous operations on db 0. This plan's guard **is the same
mechanism, generalised**: db 0 is simply the db no test process can ever claim, so
"deny `flushdb` on any db not in `claimed_test_dbs()`" subsumes "deny `flushdb` on db 0" rather than
running beside it. Build it as one wrapper with the db-0 branch retained for its message, not as a
parallel idiom — two independently-maintained flush guards is how one of them drifts. The plan also
answers #2645's open question directly: its "rename-command vs ACLs for legitimate DB 1-15 flushes"
choice is looking for a **by-db discriminator**, and this plan's per-process flock-claimed set is
exactly that discriminator — ownership is decided by a kernel primitive, is queryable at call time,
and is already the thing that distinguishes a legitimate test flush from a cross-process wipe. If
#2645 lands a server-side rule, it should be keyed off the same claim registry rather than a static
db allowlist.

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
- **Exhaustion failure must be sticky, or the wait is paid per test.** `claim_test_db()` early-returns
  only when `_CLAIMED_TEST_DB is not None` (`tests/db_claim.py:127-129`), and that global is assigned
  **only on success paths** (`:133`, `:142`, `:146`). Replacing the `:146-153` fallback with a raise
  leaves it `None`, so every subsequent call re-enters the poll. With the claim living in the
  function-scoped autouse `redis_test_db`, that is 30 s × ~1200 tests per worker — roughly ten hours,
  and `--timeout=420` structurally cannot catch it because no single test exceeds 420 s. Two
  independent mechanisms fix it, and both are wanted: (a) the session-start claim means the poll is
  entered **once per process** by construction, and on failure Task 2 calls
  `pytest.exit(msg, returncode=3)` so the run aborts with one line instead of N-thousand setup
  errors; (b) a module-level `_CLAIM_FAILURE: str | None`, set to the formatted message immediately
  before the raise and re-raised as the **first statement** of `claim_test_db()` (ahead of the
  `_CLAIMED_TEST_DB` early return), so any direct caller outside the pytest hook — helper code,
  `subprocess_env`, `redis_test_url` — fails instantly instead of waiting again. Cleared by
  `release_test_db_claim()`, rebound by `_reset_claim_state`.
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
- **Recurrence guard by structure, not by discipline — DESCOPED to #2656.** The AST walk over
  `tests/**/*.py` (flag any `Redis`/`Redis.from_url` construction whose `db=` value does not come
  from the claim API) is a *recurrence* control: it stops the next author from re-introducing a
  self-derived db number. It stops none of the three writers this plan exists to fix, and its
  satisfiability inventory produced the sole blocker in three consecutive critique rounds. It is
  filed as #2656 with the full finding set (terminal-name `_callee_name` matching, the
  `[1..TEST_DB_POOL_MAX]` allowlist invariant, the two documented blind spots) so nothing is lost.
  What remains in scope here is the *enforcement* layer that actually stops damage: Task 3's
  ownership flush guard denies a `flushdb` on an unclaimed db at runtime, on every branch, in every
  repo state, and Task 2's plugin-agnostic client-ownership check catches a client pointed at an
  unowned db from installed library code — which the `tests/`-scoped AST walk could never see.
- **The guard must read the claimed set at call time.** `_install_redis_db0_flush_guard()` runs at
  conftest import (`tests/conftest.py:150`), long before any claim exists. The wrapper closure must
  call `db_claim.claimed_test_dbs()` on each invocation, never capture a snapshot.
- **Scope of the ownership guard: `flushdb` only** (Open Question 2, decided). Rationale: `flushdb`
  is the only operation whose blast radius is another process's *entire* dataset, and it is the
  operation the reproduced bug actually uses. A general "no write on an unclaimed db" guard would
  have to wrap every mutating command on both sync and async clients — a per-command hot-path cost
  on every test in the suite, and a much larger surface of legitimate call sites to audit, for a
  quieter class of bug that has never been observed here. The descoped AST recurrence guard (#2656)
  will close the same hole one layer earlier, at *construction* time and at zero runtime cost, once
  it lands. Revisit only if a cross-db non-flush write is ever actually observed.
- **Second writer: remove the reload, then widen the guard.** `tests/unit/test_index_drift.py:207-208`
  reloads `agent.index_drift` purely to observe a module constant's default. Replace it with a
  direct assertion plus `monkeypatch` where a non-default value is needed — no reload, no restore
  problem. Then widen `tests/conftest.py`'s reload guard (`:384`, `:391-401`) from
  "`BaseException` subclasses in `models.session_lifecycle`" to also snapshot module-level
  *registry* objects (dicts/classes/enums) for `agent.index_drift` and `monitoring.bridge_watchdog`,
  so the next unrestored reload is caught rather than silently leaked.
- **Third writer: repoint the popoto plugin at the claimed db; do not simply silence it.** Set
  `os.environ["POPOTO_TEST_DB"] = str(db)` in the **same session-start hook that establishes the
  claim** (Task 2) — not in a separate task, because a commit window in which the guard is live and
  the plugin is not repointed is a total suite outage (round-3 blocker 1). Verified against the
  installed source: `popoto/pytest_plugin.py:138-150`'s session fixture resolves
  `POPOTO_TEST_DB` env > `popoto_test_db` ini > default 15 **at fixture setup time**, i.e. during the
  first test, long after `pytest_configure`. The
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
  monotonically into Task 5's new fail-hard `RuntimeError` — the fixture would create the exhaustion
  the same PR makes fatal. Session scope matches `claim_test_db()`'s own per-process memoization: one
  scratch slot per process, held for the session, released by `release_test_db_claim()` at session
  end.
- **A `tests/`-scoped AST walk is blind to installed plugins, so the runtime client check carries this
  class on its own.** Any AST walk over `tests/**/*.py` — including the descoped #2656 guard — cannot
  see spike-4's writer, the highest-frequency of the three, because it lives in installed library
  code. Answering that with a popoto-specific assertion plus a prose rule in the
  docs would be the same "every call site is trusted to honour a convention" shape this plan
  diagnoses as the root-cause pattern, and a future popoto (or any other `pytest11` plugin) that adds
  a second client re-opens the hole. So add one **plugin-agnostic** session-scoped autouse check
  after the claim: walk the live clients the process holds via `popoto.redis_db`
  (`POPOTO_REDIS_DB` and `_POPOTO_ASYNC_REDIS_DB`) and assert each one's
  `connection_pool.connection_kwargs["db"]` is in `claimed_test_dbs()`, failing with "a client is
  pointed at db N which this process has not claimed". Costs one assertion per session and catches
  any future plugin that swaps the popoto globals, not just this popoto version.
  **Skip clients that do not exist — `_POPOTO_ASYNC_REDIS_DB` is `None` at every moment a
  session-scoped fixture can observe it, and `None` is its correct state** (round-4 blocker 1).
  `popoto/redis_db.py:97` initialises it to `None`; the plugin's `_popoto_reset_async` nulls it at
  both setup and teardown of every test (`popoto/pytest_plugin.py:214,217`) precisely so a client is
  never built in a sync setup context bound to the wrong event loop; `tests/conftest.py:612` captures
  that `None` and `:639` restores it. Dereferencing it raises `AttributeError` inside a session-scoped
  autouse fixture, which errors every test in the process in setup — the identical whole-suite-outage
  shape as round-3 blocker 1. Write it as
  `for client in (rdb.POPOTO_REDIS_DB, getattr(rdb, "_POPOTO_ASYNC_REDIS_DB", None)):` with an
  `if client is None: continue` guard before touching `connection_pool`. Never assert the async client
  is non-`None`. Async coverage, if ever wanted, must be a *function*-scoped check running after the
  test body where a lazily-built client may exist; the session-scoped fixture can only meaningfully
  assert on `POPOTO_REDIS_DB`.
- **The popoto fix must survive a popoto upgrade, and it does — by construction.** The `pytest11`
  plugin discovery is this lane's highest-value finding, so the fix is deliberately sited at the
  **connection/claim layer**, never at the installed plugin: (a) Task 2 exports `POPOTO_TEST_DB` from
  this repo's own `pytest_configure`, which is a *public, documented* resolution input the plugin
  reads (`popoto/pytest_plugin.py:138-150`, env > ini > default), not a monkeypatch of plugin
  internals; (b) the plugin-agnostic session check above asserts on whatever clients
  `popoto.redis_db` holds, whatever version put them there; (c) the Success Criterion
  `POPOTO_REDIS_DB...["db"] == claim_test_db()` is a **drift detector**: if a future popoto changes
  its resolution order or adds a client, that assertion fails loudly instead of the suite silently
  resuming its rotation. Nothing in this plan patches, vendors, or edits installed popoto code, and
  no task pins a popoto version. The upstream fix (making the plugin's flush honour an env-pinned db
  by default) is recorded as an out-of-scope follow-up under No-Gos.

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
- [ ] `tests/conftest.py:113-115` `_db_of` — the `except Exception: return 0` that assumes the
  dangerous db when it cannot determine one. Under the new guard this must still deny, not allow:
  add a test with a client whose `connection_pool` raises, asserting `RuntimeError`.
- [ ] No exception handlers are added by this change beyond those listed.

### Empty/Invalid Input Handling
- [ ] `claimed_test_dbs()` before any claim: must return an empty frozenset, and the guard must then
  deny every `flushdb` rather than allowing all of them. Fail-closed on empty is the whole point —
  test it explicitly. Two corollaries make this safe rather than catastrophic, and both are what
  round 3's blocker 1 was really about: (a) *every* return path of `claim_test_db()` populates the
  set (flock path and registry-unreachable fallback alike), so "empty" never means "the claim
  degraded"; (b) after Task 2, the only states in which the set is legitimately empty are the
  **xdist controller** (which runs no tests and must never flush) and the window before
  `pytest_configure` (in which no test or fixture has run). A test can therefore never observe an
  empty set, so fail-closed cannot deny a correct flush.
- [ ] Controller state: with a stub config carrying no `workerinput` and `numprocesses=2`,
  `pytest_configure` must leave `_db_claim._CLAIMED_TEST_DB` at `None` and `claimed_test_dbs()`
  empty — the controller owns no db and must not burn a 16th slot from a pool of 15.
- [ ] `-n0` master: with a stub config carrying no `workerinput` and `numprocesses` of `None`/`0`,
  `pytest_configure` MUST claim — the master runs the tests in that configuration.
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
  (`:473-489`) — UPDATE: add a `wait_s` parameter mirroring the existing `pool_max`, so every
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
- [ ] `monitoring/bridge_watchdog.py:87-93` — **NOT TOUCHED (moved out of scope, round-3 concern 7).**
  It stacks a `RotatingFileHandler` per reload with no `handlers.clear()`, but a duplicated log line
  cannot change any test's pass/fail outcome, so it contributes nothing to the rotating failure set —
  while being the plan's only production file and dragging a `./scripts/valor-service.sh restart`
  into an otherwise pure-`tests/` PR. Filed under No-Gos as its own issue. Task 7 still snapshots
  `monitoring.bridge_watchdog`'s module-level registry objects under the widened reload guard, which
  is the part that actually closes a rotation path.
- [ ] `tests/unit/test_youtube_search.py::TestSearchIntegration` (`:231-245`) — REPLACE: relocate to
  `tests/integration/test_youtube_search_live.py`. Leaves the unit-suite file with only offline
  tests. Carried by Task 4 (call-site hygiene): it is slow and network-dependent, not a
  cross-process db writer (round-3 nit 2).
- [ ] `tests/integration/test_agent_catchup_recovery.py:61` — UPDATE: `redis.Redis(db=1).ping()` is a
  hardcoded pool slot. Harmless in effect (a `ping`, never a flush) but it is exactly the shape
  #2656's guard will flag, and the surrounding docstring already claims the test writes only to the
  per-process db. Convert to `claim_test_db()`; it must never be allowlisted — an allowlist entry
  here would teach the next author that hardcoding is negotiable.
- [ ] `tests/unit/test_email_bridge.py:1399-1406` — UPDATE: derives the db by reading
  `POPOTO_REDIS_DB.connection_pool.connection_kwargs.get("db", 1)`. This is a *third* derivation
  route — not a worker-id derivation and not a literal. `claim_test_db()` must be the single source
  of the number, so convert it. (#2656 picks up the AST leg that would reject `connection_kwargs`-
  sourced `db=` values statically.)
- [ ] `tests/integration/test_notify_isolation.py:61-65` — UPDATE: `_raw_client` builds
  `redis.Redis(..., db=int(kw.get("db", 0) or 0))` from
  `kw = popoto_client.connection_pool.connection_kwargs` — the same third derivation route, and a
  genuine unowned-db construction. Convert to `db=claim_test_db()` (import from `tests.db_claim`),
  keeping the host/port/auth reads from `kw`. The docstring at `:53-57` already states the db number
  is irrelevant for pub/sub delivery, so the change is behavior-preserving. Carried by Task 4.
  **This conversion is in scope regardless of the Task 6 descope** — the guard that previously
  covered it is gone, so the fix must be the call-site change itself. An allowlist entry is forbidden
  in any case: post-Task-2 the value read from `connection_kwargs` is the claimed pool slot, not db 0.
- [ ] **Not a test in this repo:** `/Users/valorengels/src/popoto/tests/test_pytest_plugin.py` asserts
  `db == 15` in four places (`:66`, `:171`, `:293`, `:357`). Those belong to the **popoto** repo and
  are correct there — db 15 is that project's own test db. This plan does not edit them and must not.
  They are recorded here as corroboration for spike-4: popoto genuinely targets db 15, which is why
  its bundled plugin collides with this repo's claim pool. If the fix is ever taken upstream instead
  of downstream, those four tests become that PR's problem, not this one's.
- [ ] Sweep for other tests that build a raw `redis.Redis(db=...)` from anything but
  `claim_test_db()`. With the recurrence guard descoped to #2656 this sweep is manual for this PR:
  the four sites above (`test_agent_catchup_recovery.py:61`, `test_email_bridge.py:1399-1406`,
  `test_notify_isolation.py:61-65`, plus Task 4's two stale call sites) are the ones found; anything
  else surfaced during build gets converted the same way, not allowlisted.

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
  this bug and would churn dozens of files. The recurrence guard (#2656) targets the *derivation*,
  which is the actual defect.
- **Building the recurrence guard here.** It is now #2656. Three critique rounds spent their only
  blocker on its satisfiability inventory while the rotation fix sat finished — the clearest possible
  signal that it is a separate piece of work with a separate cadence.

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
not, and the abort message names the remedy. Critically, the abort is **once per process**: the claim
happens in Task 2's session-start hook and fails via `pytest.exit`, and Task 5's `_CLAIM_FAILURE`
memo makes any later direct call re-raise instantly. Without both, the raise would be re-entered by
the function-scoped autouse fixture and cost 30 s per test — about ten hours per worker, invisible to
`--timeout=420` because no single test exceeds it (round-3 blocker 2). The wait is deliberately short
(30 s, env-overridable `TEST_DB_CLAIM_WAIT_S`, polled at ~1 s): spike-3 shows that in genuine two-run contention no slot
frees inside any tolerable window, so a long wait buys nothing but delay before the same error. If
aborts prove common, the escalation is all-or-nothing controller allocation (Race 1), not a return
to colliding fallbacks.

### Risk 3: The fix is correct but the rotation persists from a third, independent cause
**Impact:** The plan ships and the suite still rotates; confidence in the instrument stays broken.
**Mitigation:** Three writers are now in scope and each gets its own deterministic red-state proof
(Task 3's flock-holder test, Task 7's registry-restoration test, Task 2's popoto-client ownership
assertion), so
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
**Location:** `tests/db_claim.py:120-153` (`claim_test_db`), as modified by Task 5.
**Trigger:** workers claim at session start (Task 2), which narrows but does not close the window —
xdist forks workers over a short interval, and two runs can still interleave. Run A acquires 8 of
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
`workerinput`. Note that this would **deliberately reverse** Task 2's `not-applicable-controller`
rule — the controller would then claim on behalf of the whole run rather than for itself, and would
have to release on `pytest_unconfigure`. That is a different lifecycle, not a tweak to this one, and
is exactly why it is deferred until a deadlock is actually observed. Claims pass down via
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

- [DESCOPED → #2656] **The AST recurrence guard, formerly Task 6.** An AST walk over `tests/**/*.py`
  rejecting any `Redis`/`Redis.from_url` construction whose `db=` value does not come from the claim
  API. **Rationale for the descope:** it prevents future regressions but stops none of the three
  rotation writers, and it produced the sole remaining blocker in critique rounds 4, 5, and 6 while
  the substrate around it was verified sound. Tracking issue **#2656** carries all five findings
  verbatim in substance: (a) every Redis construction site in `tests/` is attribute-qualified, so the
  original `node.func.id` matcher would have matched **zero** sites — a vacuously-green guard that
  could never detect a recurrence; (b) the `_callee_name` terminal-name fix (match `func.id` for
  `ast.Name` **and** `func.attr` for `ast.Attribute`) is correct and worth keeping; (c) the site
  `tests/integration/test_notify_isolation.py:61-65`, `redis.Redis(db=int(kw.get("db", 0) or 0))` —
  **this one is fixed here anyway, in Task 4**, because it is a genuine unowned-db construction and
  the guard that covered it is gone; (d) the six non-literal `from_url(redis_test_url)` sites that are
  out of the AST walk's reach; (e) the `[1..TEST_DB_POOL_MAX]` allowlist invariant, which is the
  durable part and must survive intact. Task numbering here is **not** renumbered: slot 6 is a
  descope marker and Tasks 7-11 keep their identifiers.
- [SEPARATE-SLUG #2628] A **fourth** rotation writer, if the optional post-merge soak surfaces one,
  gets its own issue rather than expanding this plan's scope. Three writers are in scope (unowned
  `flushdb`, unrestored module reload, popoto plugin's db-15 flush) and each has a deterministic
  proof; the soak is diagnostic, not a merge gate.
- [SEPARATE-SLUG #2628] **Upstream popoto PR: make the bundled `pytest11` plugin's flush honour an
  env-pinned db by default.** This is the more durable form of spike-4's fix — no downstream consumer
  could then collide with a claim pool — and there is precedent for landing changes upstream (popoto
  PR #518 is already in flight). It is deliberately **not built here**: it needs a popoto release plus
  a floor bump in this repo, which is a different blast radius and a different merge cadence than a
  pure-`tests/` PR. This plan takes the downstream repoint (Task 2), which lands in one commit, is
  fully under this repo's control, and — critically — is **already popoto-upgrade-durable** on its
  own: it drives the plugin through its documented `POPOTO_TEST_DB` resolution input and carries a
  drift-detecting assertion, so a future popoto cannot silently re-open the hole. File the upstream
  change as its own issue. Note that `/Users/valorengels/src/popoto/tests/test_pytest_plugin.py`'s
  four `db == 15` assertions belong to that upstream change, not to this one.
- [SEPARATE-SLUG #2628] Consolidating the two owners of "which db popoto is on" — dropping
  `redis_test_db`'s replace-and-repatch loop (`tests/conftest.py:616,626-628,640-641`) now that the
  plugin's in-place `_swap_db` already covers submodule bindings. Deferred: `tests/conftest.py:631`
  is what points `_POPOTO_ASYNC_REDIS_DB` at the test db in the sync setup phase, so removing the
  fixture is a separate blast radius from this PR (round-4 concern 2).
- [ORDERED] A general "no write on an unclaimed db" guard covering every mutating command rather
  than just `flushdb` (Resolved Question 2). Deferred: it costs a per-command wrapper on both sync
  and async clients paid by every test, and the AST recurrence guard (#2656) will block *construction*
  of a client on an unowned db at zero runtime cost. Build only if a cross-db non-flush write is
  actually observed.
- [ORDERED] The all-or-nothing controller slot allocation (Race 1) is built only if a deadlock is
  observed after the bounded-wait policy ships. Building it pre-emptively would add a
  `pytest_configure`/`workerinput` handshake to solve a condition that the timeout already converts
  into a clear failure.
- [SEPARATE-SLUG #2628] The `logger.handlers.clear()` fix in `monitoring/bridge_watchdog.py:87-93`
  (mirroring `monitoring/worker_watchdog.py:150-151`). A leaked `RotatingFileHandler` duplicates log
  lines but cannot change a test outcome, so it does nothing for the rotating failure set — while
  being the only production file in an otherwise pure-`tests/` PR and dragging a launchd restart with
  it. File it as its own issue. Task 7's widened reload guard still snapshots that module's registry
  objects, which is the rotation-relevant half and touches no production code.
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

**No service restart is required.** An earlier draft carried a one-line `monitoring/bridge_watchdog.py`
edit that would have shipped to a running launchd service and pulled `./scripts/valor-service.sh
restart` into this PR. That edit is out of scope (round-3 concern 7) and filed separately, so this
PR touches no running service and has no deploy step.

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
  **Lead the doc with the claim lifecycle table** (`unclaimed-not-yet` / `claimed` /
  `unclaimable-permanently` / `not-applicable-controller`) and the rule that the claim is established
  at session start so no test can observe an unclaimed process. That table is the design, and it is
  what stops the next agent from re-deriving a fourth ownership rule.
  Record the relationship to **#2645**: this guard is the general form of that incident's Layer-1
  db-0 rule, and the per-process flock-claimed set is the by-db discriminator #2645's rename-command
  vs ACL question is looking for.
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
- [ ] Every `redis.Redis(db=...)` site this plan converts takes its `db=` value from the claim API:
  the `PYTEST_XDIST_WORKER` derivation (`_own_test_db`), the hardcoded `15`
  (`divergent_db`), `test_agent_catchup_recovery.py:61`'s `db=1`, and the two
  `connection_kwargs`-derived sites (`test_email_bridge.py:1399-1406`,
  `test_notify_isolation.py:61-65`). Verified per-site in review; the *structural* assertion that no
  such site can reappear is descoped to #2656.
- [ ] `claim_test_db()` never returns a db number absent from `claimed_test_dbs()` — on **every**
  return path, flock and registry-unreachable fallback alike. On exhaustion it polls briefly, then
  raises.
- [ ] `TestSearchIntegration` no longer runs as part of `tests/unit/`.
- [ ] **The binding measurement (writer 1):** with a real child process holding `flock` on slot 1,
  the guard **intercepts** a `flushdb` aimed at db 1 — proven against a `SimpleNamespace` client stub
  carrying no connection, so no flush can reach an unowned db on any branch — and `flushdb()` on the
  claimed db is permitted with a real client. ~1 s, deterministic. **Red on `main` means no
  `RuntimeError` is raised** (the db-0-only guard does not intercept), never that a flush succeeded.
  Both outputs pasted into the PR body.
- [ ] **The binding measurement (writer 2):** reloading `agent.index_drift` leaves
  `covered_model_names()` unchanged and strands no `FakeCoveredModel`. Red on `main`. Output pasted
  into the PR body.
- [ ] **The binding measurement (writer 3):** inside a test,
  `POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"]` is in `claimed_test_dbs()` — i.e. the
  popoto plugin no longer sits on db 15 while this process owns another slot. Red on `main` (the
  same fact the `PROBE sentinel_survived=False` observation reports), green on the branch. Stated in
  ownership terms deliberately: a literal `redis.Redis(db=15)` sentinel would itself be an unowned-db
  construction, contradicting the invariant this plan exists to establish.
- [ ] The claim exists before any fixture runs: on a worker, `claimed_test_dbs()` is non-empty at the
  first `_popoto_flush_db`. On the xdist controller, `_db_claim._CLAIMED_TEST_DB` stays `None` and no
  pool slot is consumed. Under `-n0` the master claims.
- [ ] Pool exhaustion is paid **once per process**: the second `claim_test_db()` against a held pool
  returns in well under the wait window and raises the same message.
- [ ] `POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"] == claim_test_db()` inside any test —
  the popoto plugin and this repo's fixture agree on one db, so a future popoto upgrade that changes
  the plugin's resolution order fails a test instead of silently rotating the suite.
- [ ] The `redis_test_db` fast path at `tests/conftest.py:592-597` is deleted, so spike-5's
  disposition holds regardless of which Task 2 option (env export vs `-p no:popoto`) was chosen.
- [ ] A live-client ownership check runs once per session: every popoto client
  (`POPOTO_REDIS_DB`, `_POPOTO_ASYNC_REDIS_DB`) points at a db in `claimed_test_dbs()`. This is the
  plugin-agnostic runtime check; no static walk over `tests/` can see installed library code, so this
  criterion stands entirely on its own and is unaffected by the #2656 descope.
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
  - Role: `tests/db_claim.py` + `tests/conftest.py` — claimed-set accessor, scratch claim, the
    session-start `pytest_configure` claim (including the popoto repoint and the controller branch),
    ownership guard, exhaustion policy, provenance
  - Agent Type: builder
  - Domain: Redis/Popoto data, async/concurrency
  - Resume: true

- **Builder (call-sites)**
  - Name: `callsite-builder`
  - Role: the two stale call sites, the other unowned-db conversions, the replaced exhaustion tests,
    the youtube relocation, and the second rotation writer (module-reload registry restoration)
  - Agent Type: test-engineer
  - Resume: true

- **Validator (isolation)**
  - Name: `isolation-validator`
  - Role: the three red-state proofs and the baseline failure-set diff (never quiesced — Risk 4)
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
  is legitimately flock-only; the number set is not. Skipping this composes with Task 3's
  fail-closed-on-empty rule into a whole-process setup outage (every test errors at
  `tests/conftest.py:617`).
- Add `claimed_test_dbs() -> frozenset[int]`, empty before any claim.
- **Add the sticky failure memo `_CLAIM_FAILURE: str | None = None`.** Task 5 sets it to the
  formatted exhaustion message immediately before its `raise`; `claim_test_db()`'s **first
  statement** — ahead of the `_CLAIMED_TEST_DB` early return — becomes
  `if _CLAIM_FAILURE is not None: raise RuntimeError(_CLAIM_FAILURE)`. Without it, exhaustion is not
  memoized (`_CLAIMED_TEST_DB` is assigned only on success paths, `tests/db_claim.py:133,142,146`)
  and every later call re-enters the 30 s poll. Clear it in `release_test_db_claim()`; rebind it in
  `_reset_claim_state` alongside `_CLAIMED_DB_NUMS`. Defining it here keeps the claim-state globals
  together even though Task 5 is what writes it.
- Add `claim_scratch_test_db() -> int | None`: claims an additional pool slot, returns `None` when
  exhausted. Never falls back to a derived number. **Memoize it per process** exactly as
  `claim_test_db()` memoizes `_CLAIMED_TEST_DB` (add a `_CLAIMED_SCRATCH_DB` module global), so
  repeated calls return the same slot instead of consuming a new one each time.
- Extend `release_test_db_claim()` to clear the number set with the fds.
- Add `_TEST_DB_CLAIM_WAIT_S = int(os.environ.get("TEST_DB_CLAIM_WAIT_S", "30"))` next to
  `_TEST_DB_POOL_MAX` with the same provisional/tunable comment style
  (`tests/db_claim.py:43-48`). Task 5 consumes it; defining it here keeps the constants together.
- Add `wait_s` to `_reset_claim_state` (`tests/unit/test_conftest_isolation_guards.py:473-489`),
  mirroring `pool_max`.
- **`_reset_claim_state` must also rebind the number set, not just the fd list.** Add
  `monkeypatch.setattr(_db_claim, "_CLAIMED_DB_NUMS", set(), raising=False)` — and the same for the
  new `_CLAIMED_SCRATCH_DB` and `_CLAIM_FAILURE` — alongside the existing
  `fresh_fds` swap (`:486`), and return it beside `fresh_fds` so assertions can read it. Without
  this the claim tests mutate the live process-wide set: `test_claim_is_in_pool_idempotent_and_releasable`
  (`:504-518`) calls `release_test_db_claim()` at `:514`, and `monkeypatch` cannot undo an in-place
  `.clear()` — so the real set is permanently emptied and every later test in that worker has its
  autouse `redis_test_db` flush (`tests/conftest.py:617`) denied by Task 3's fail-closed-on-empty
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
- **Land this task first.** Task 3's denial message and Task 4's `scratch_test_db` fixture use both
  name `scratch_test_db`/`claim_scratch_test_db`, and neither symbol exists in `tests/` today.

### 2. Establish the claim at session start, and repoint the popoto plugin in the same hook
- **Task ID**: build-session-claim
- **Depends On**: build-claim-api
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Informed By**: spike-4 (the popoto plugin is live and flushes db 15 before every test), spike-5
  (the `redis_test_db` fast path is dead code *only because* this plugin is loaded), round-3
  blockers 1 and 3
- **Assigned To**: db-ownership-builder
- **Agent Type**: builder
- **Parallel**: false
- **This is the substrate task.** It removes the `unclaimed-not-yet` state rather than excusing it,
  which is what lets Task 3's guard be fail-closed without special cases. See *The claim lifecycle*
  in Solution. It must land **with or before** Task 3 — never after — and the two are best landed in
  one commit.
- **Add a `pytest_configure(config)` to `tests/conftest.py`.** There is **no `pytest_configure` in
  this file today** — the existing hooks are `pytest_runtest_teardown` (`:38`),
  `pytest_collection_modifyitems` (`:851`), and `pytest_unconfigure` (`:1071`); the
  `if getattr(config, "workerinput", None):` idiom the plan cites lives inside `pytest_unconfigure`
  at `:1082`. Write the hook fresh, with the controller branch **in it from the first line** — do not
  assume an existing hook to extend.
- **Shape of the hook, exactly:**
  ```
  is_worker = getattr(config, "workerinput", None) is not None
  if not is_worker and getattr(config.option, "numprocesses", None):
      return  # xdist controller: runs no tests, must never burn a pool slot
  db = claim_test_db()            # may raise on exhaustion -> pytest.exit below
  os.environ["POPOTO_TEST_DB"] = str(db)
  ```
  The `numprocesses` check is **load-bearing**: under `-n0` the master *does* run tests and must
  claim. Without it, `-n0` runs claim nothing and every flush is denied.
- **Why the controller must not claim (round-3 blocker 3).** `pytest_configure` runs in the xdist
  controller exactly as `pytest_report_header` does, and runs there *first*, before any worker forks.
  A controller claim is held for the whole session and never released, raising per-run demand from 10
  to 11 in the same PR that Task 5 makes exhaustion fatal — and spike-3 already found the pool at
  10/15. The branch also removes the path where execnet workers transiently inherit the controller's
  slot number through the exported env var.
- **On exhaustion, abort the session, do not raise per test.** Wrap the claim: on `RuntimeError`,
  call `pytest.exit(msg, returncode=3)`. That is one line of output instead of N-thousand setup
  errors, and combined with Task 1's `_CLAIM_FAILURE` memo it means the 30 s wait is paid **once per
  process** (round-3 blocker 2).
- **Repoint popoto here, in the same hook, not in a separate task (round-3 blocker 1).** Verified
  against the installed source: `popoto/pytest_plugin.py:138-150`'s session-scoped autouse
  `_popoto_test_db` resolves `POPOTO_TEST_DB` env > `popoto_test_db` ini > default **15** at *fixture
  setup time*, i.e. during the first test — long after `pytest_configure`. Exporting the env var here
  therefore makes the plugin's `_swap_db` and its per-test `_popoto_flush_db` (`:179-194`) both land
  on this process's own claimed db, correct by construction and with **no commit window** in which
  Task 3's guard is live while the plugin still targets db 15. It also keeps the plugin's
  `_popoto_reset_async` per-test event-loop reset (`:198`), which this repo's `redis_test_db` does not
  replicate.
- **Never leave the plugin on its db-15 default.** Db 15 is `_TEST_DB_POOL_MAX` — a claimable slot —
  so every flush of it is a cross-process wipe of whichever sibling run holds slot 15.
- **Alternative, only if the export proves unworkable:** `-p no:popoto` in `pyproject.toml:195`
  addopts. Simpler, but it removes `_popoto_reset_async` and the plugin's db-0 tripwire; validate the
  full async suite before choosing it. Record the choice and the reason in the PR body either way.
- **Delete the `redis_test_db` fast path (`tests/conftest.py:592-597`) in this same change.** Per
  spike-5 it is unreachable today only because the plugin's module-level `from popoto import
  redis_db` guarantees the import at pytest startup. Choosing `-p no:popoto` would revive it as a
  live hazard in the same commit; removing it makes the disposition true under both options.
- **Add the plugin-agnostic client-ownership check** (round-3 concern 6): a session-scoped autouse
  fixture, ordered after the claim, that walks `popoto.redis_db.POPOTO_REDIS_DB` and
  `_POPOTO_ASYNC_REDIS_DB` and asserts each client's
  `connection_pool.connection_kwargs["db"] in claimed_test_dbs()`, failing with "a client is pointed
  at db N which this process has not claimed". This catches *any* future `pytest11` plugin that swaps
  the popoto globals, not just this popoto version. No static walk over `tests/**/*.py` can cover
  this class — including the descoped #2656 guard — so this check is the sole control for it and is
  non-optional.
- **Guard the `None` client — this is round-4 blocker 1 and it is a whole-suite outage if missed.**
  `_POPOTO_ASYNC_REDIS_DB` is `None` at every moment a session-scoped fixture can observe it, and
  `None` is its *correct* state: `popoto/redis_db.py:97` initialises it to `None`, the plugin's
  `_popoto_reset_async` nulls it at both setup and teardown of every test
  (`popoto/pytest_plugin.py:214,217`) so no client binds to the wrong event loop, and
  `tests/conftest.py:612/639` captures and restores that `None`. Write the loop exactly as:
  ```
  import popoto.redis_db as rdb
  for client in (rdb.POPOTO_REDIS_DB, getattr(rdb, "_POPOTO_ASYNC_REDIS_DB", None)):
      if client is None:
          continue
      db = client.connection_pool.connection_kwargs.get("db", 0)
      assert db in claimed_test_dbs(), f"a client is pointed at db {db} which this process has not claimed"
  ```
  **Do NOT assert the async client is non-`None`.** An `AttributeError` here fires inside a
  session-scoped autouse fixture and errors *every* test in the process during setup — the identical
  shape as round-3 blocker 1. If async coverage is ever wanted it must be a **function**-scoped check
  running after the test body, where a lazily-built client may exist; the session-scoped fixture can
  only meaningfully assert on `POPOTO_REDIS_DB`.
- **Add a regression test for the `None` case**: with `rdb._POPOTO_ASYNC_REDIS_DB` monkeypatched to
  `None` (its normal state), the check passes rather than raising `AttributeError`.
- **Durability across a popoto upgrade is a hard requirement, and this shape meets it.** The fix
  lives at the connection/claim layer: `POPOTO_TEST_DB` is a public documented resolution input the
  plugin already reads, the walk above asserts on whatever clients `popoto.redis_db` holds regardless
  of version, and the `== claim_test_db()` criterion is the drift detector that fails loudly if a
  future popoto changes its resolution order. **Never patch, vendor, or edit the installed plugin**,
  and do not pin a popoto version to hold this behavior in place.
- **Red-state proof, stated in ownership terms so it needs no unowned client** (round-3 concern 4):
  a test asserting
  `popoto.redis_db.POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"] in claimed_test_dbs()`,
  and the stronger `== claim_test_db()`. **Both are assertion statements in that shipped test, not
  plan prose** — the `== claim_test_db()` line is the entire mechanical basis for the claim that this
  fix survives a popoto upgrade (bullet above), so if a future popoto changes its resolution order
  that assertion is the only thing that fails loudly. A build that lands the `in claimed_test_dbs()`
  half and leaves the equality as a comment has not landed the drift detector. **Red on `main`** — the plugin sits on 15 while the process
  claims elsewhere, which is the same fact the current `PROBE sentinel_survived=False` observation
  reports — and green on the branch. Paste both outputs into the PR body. **Do not** write the
  literal-`redis.Redis(db=15)` sentinel an earlier draft proposed: it is exactly the second
  documented offender shape and it writes to a database this process does not own — contradicting the
  invariant the plan exists to establish.
- Tests, all with stub configs (no nested pytest run): controller (`numprocesses=2`, no
  `workerinput`) leaves `_db_claim._CLAIMED_TEST_DB` at `None`; worker (`workerinput` present) claims
  and sets `POPOTO_TEST_DB`; `-n0` master (`numprocesses` falsy, no `workerinput`) claims.

### 3. Ownership-enforcing flush guard
- **Task ID**: build-flush-guard
- **Depends On**: build-claim-api, build-session-claim
- **Validates**: tests/unit/test_redis_flush_guard.py
- **Assigned To**: db-ownership-builder
- **Agent Type**: builder
- **Parallel**: false
- **The `build-session-claim` dependency is a correctness edge, not sequencing taste.** The guard
  patches the redis **class** method (`tests/conftest.py:142-147`), so popoto's `_popoto_flush_db`
  autouse fixture is subject to it, and `--setup-plan` shows that fixture setting up *before*
  `redis_test_db`. Landing this guard while the claim still happens lazily in `redis_test_db` denies
  the first flush of every worker and errors every test in setup in ~14 of 15 processes.
- Rewrite `_install_redis_db0_flush_guard` (`tests/conftest.py:103-150`) as an ownership guard:
  permit `flushdb` only when `_db_of(client)` is in `claimed_test_dbs()`.
- Keep `flushall` unconditionally blocked and `db == 0` blocked (an unclaimable db is denied by the
  same rule, but keep the explicit db0 branch and its 2026-06-03 rationale message).
- Keep `_db_of`'s fail-closed `except Exception: return 0`; add a test that a client whose
  `connection_pool` raises is denied.
- Deny when the claimed set is empty — fail closed. After Task 2 the only processes that can observe
  an empty set are the xdist controller (which runs no tests) and the pre-`pytest_configure` window,
  so this denies nothing legitimate.
- Message must name: attempted db, claimed set, and `scratch_test_db`.
- **Read the claimed set as a module attribute inside the wrapper, on every call.**
  `_install_redis_db0_flush_guard()` runs at conftest import time (`tests/conftest.py:150`), long
  before any claim exists; a captured snapshot would be permanently empty.
- **Compose with #2645, do not parallel it.** #2645's Layer-1 guarded connection helper and this
  guard are the same mechanism at different generality: db 0 is the db no test process can claim, so
  ownership subsumes the db-0 rule. Ship one wrapper with the db-0 branch retained for its message
  and its 2026-06-03 rationale. Two independently-maintained flush guards is how one of them drifts.
- Add a Task 3 assertion at the top of a test:
  `assert redis_db.POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"] in claimed_test_dbs()` —
  the cheapest possible proof that Task 2's ordering guarantee actually holds at test time.
- Add the `scratch_test_db` fixture: yields `claim_scratch_test_db()`, or `pytest.skip`s with an
  explicit reason when the pool is exhausted. **Declare it `scope="session"`** and rely on Task 1's
  memoization of `claim_scratch_test_db()`. A function-scoped, non-memoized fixture would claim a
  fresh slot for every requesting test with no release path, walking the 15-slot pool monotonically
  into Task 5's new fail-hard `RuntimeError` — the fixture would manufacture the exact exhaustion the
  same PR makes fatal. One scratch slot per process, held for the session, released by
  `release_test_db_claim()` at session end.
- **The binding red-state proof — the adversarial holder test.** Add to `TestPerProcessDbClaim`,
  using helpers that already exist in that file: `_spawn_flock_holder(claim_dir, [1])`
  (`:445-470`) spawns a real child holding `flock(LOCK_EX|LOCK_NB)` on slot 1; `_reset_claim_state`
  (`:473-489`) redirects the registry at `tmp_path`; `_close_fds` (`:492-503`) is teardown;
  `test_claim_skips_slot_held_by_live_process` (`:556-568`) is the structural precedent. The case:
  hold slot 1 → `claim_test_db()` (returns something != 1) → **deny half** (see below) → **permit
  half**: `redis.Redis(db=<mine>).flushdb()` succeeds, a real flush against a db this process
  demonstrably owns. Runs in ~1 s. This is the plan's pass condition — capture both outputs into the
  PR body.
- **The deny half MUST NOT construct a client on db 1, and MUST NOT reach Redis — on any branch**
  (round-4 blocker 3, the loudest finding of the round). An earlier draft asserted that
  `redis.Redis(db=1).flushdb()` raises. On `main` the guard rejects only `db == 0`
  (`tests/conftest.py:117-129`), so that call **does not raise — it executes**, and
  `_reset_claim_state` redirects only the *lock registry* to `tmp_path` (`:483`); the Redis server
  and its db numbering are untouched. Spike-3 records slots 1-10 routinely held by live siblings, so
  capturing the red state would have performed the exact cross-process wipe this plan exists to
  eliminate. Issue #2645 is this precise failure class. It is also the literal-unowned-db
  construction that round-3 concern 4 deleted from the plan for three stated reasons — offender
  shape, AST-guard rejection, writes to a db this process does not own — none of which had been
  applied to the plan's own most load-bearing test.
- **Adopted shape: assert the guard INTERCEPTS, driven through a client-layer stub.** The guard reads
  only `_db_of(client)`, which touches `client.connection_pool.connection_kwargs`
  (`tests/conftest.py:110-115`), so no real client is needed to exercise the deny path:
  ```
  victim = SimpleNamespace(
      connection_pool=SimpleNamespace(connection_kwargs={"db": 1}),
      execute_command=lambda *a, **k: pytest.fail(
          "guard did not intercept: a flush reached the client layer"
      ),
  )
  with pytest.raises(RuntimeError, match="has not claimed"):
      redis.Redis.flushdb(victim)
  ```
  The stub carries **no socket and no connection**, so there is no code path — on `main`, on the
  branch, or in any intermediate repo state — by which a flush can reach db 1 or any other database
  this process does not own. The `execute_command` spy is a second, independent backstop: if a future
  refactor made the guard fall through, the test fails loudly instead of flushing.
- **Red-on-`main` is inverted accordingly: the failure is that NO `RuntimeError` is raised.** On
  `main` the guard sees `db == 1`, waves it through, and the call falls into the real `flushdb`
  implementation, which cannot operate on the stub — so `pytest.raises(RuntimeError)` fails (it sees
  the spy's `pytest.fail`, or an `AttributeError`, not the guard's `RuntimeError`). That failure *is*
  the red state, and it is the honest one: on `main` the guard does not intercept. State it that way
  in the test's docstring and in the PR body, so nobody re-reads the red output as "a flush
  succeeded" and re-introduces a real one.
- **No throwaway-server or "safe db number" variant is needed or wanted.** The pool is `[1..15]` and
  db 0 is production, so there is no safe number; the stub removes the question entirely rather than
  answering it. (A `REDIS_PORT=6399` throwaway server keyed through the claim registry at
  `tests/db_claim.py:70-71` was the alternative on record; it is rejected here as a second server to
  operate for no additional coverage.)

### 4. Correct the two stale call sites, and move the live-network test out of the unit suite
- **Task ID**: build-callsites
- **Depends On**: build-flush-guard
- **Validates**: tests/unit/test_redis_flush_guard.py, tests/unit/test_conftest_isolation_guards.py
- **Informed By**: spike-1 (gw3 claims 11, derives 4 — the mismatch is the bug)
- **Assigned To**: callsite-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete `_own_test_db` from `tests/unit/test_redis_flush_guard.py:21-24`; both call sites
  (`:45-50`, `:53-57`) use `claim_test_db()`.
- `tests/unit/test_conftest_isolation_guards.py:361` (`divergent_db = 15 if base_test_db != 15 else
  14`) and its client at `:365`, plus `:420`: take the divergent db from the `scratch_test_db`
  fixture; delete the derivation and its now-wrong comment.
- Also convert `tests/integration/test_agent_catchup_recovery.py:61` (hardcoded `db=1`) and
  `tests/unit/test_email_bridge.py:1399-1406` (`connection_kwargs`-derived) to `claim_test_db()`.
- **Convert `tests/integration/test_notify_isolation.py:61-65`** — in `_raw_client` (`:52-69`),
  `db=int(kw.get("db", 0) or 0)` becomes `db=claim_test_db()` (import from `tests.db_claim`). Host,
  port, and auth still read from `kw = popoto_client.connection_pool.connection_kwargs`; only the db
  number changes source. The docstring at `:53-57` already states the db number is irrelevant for
  pub/sub delivery, so this is behavior-preserving. **This is required regardless of the Task 6
  descope:** it is a genuine unowned-db construction, and the AST guard that previously covered it is
  no longer in this PR, so the call-site conversion is the entire fix.
- None of these four sites may be allowlisted — post-Task-2 the value they resolve to is a claimed
  pool slot, not db 0.
- Move `TestSearchIntegration` (`tests/unit/test_youtube_search.py:231-245`) to
  `tests/integration/test_youtube_search_live.py`. It is call-site hygiene, not a rotation writer
  (round-3 nit 2).
- Verify `tests/integration/test_session_archive_cold_boot.py:100,194` still passes unchanged (it
  flushes its own claimed client) — Risk 1.

### 5. Wait-then-fail on pool exhaustion, with a sticky failure
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
- **Make the failure sticky (round-3 blocker 2).** Set Task 1's `_CLAIM_FAILURE` to the formatted
  message immediately *before* the `raise`, and make
  `if _CLAIM_FAILURE is not None: raise RuntimeError(_CLAIM_FAILURE)` the **first statement** of
  `claim_test_db()` — ahead of the `_CLAIMED_TEST_DB` early return at `:127-129`, which never fires
  on the failure path because that global is assigned only on success (`:133`, `:142`, `:146`).
  Without this the wait is re-entered on every call; with the pre-Task-2 lazy claim that was 30 s ×
  ~1200 tests per worker (~10 hours), which `--timeout=420` cannot catch because no single test
  exceeds 420 s. Task 2's session-start claim already reduces the poll to once per process; the memo
  is what protects the remaining direct callers (`redis_test_url`, `subprocess_env`, helper code).
- Add a test: with the pool held, the **second** `claim_test_db()` returns in well under the window
  (`_reset_claim_state(..., wait_s=2)`, assert elapsed < 0.5 s) and raises the same message.
- Clear `_CLAIM_FAILURE` in `release_test_db_claim()`; rebind it in `_reset_claim_state`.
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

### 6. DESCOPED → #2656 (recurrence guard, AST walk over `db=`)
- **Status**: **NOT BUILT IN THIS PR.** Descoped to issue #2656 after critique round 6.
- **Task ID**: *(retired — no build agent is assigned, and nothing depends on it)*
- Slot 6 is deliberately left in place rather than renumbered: Tasks 1-5 and 7-11 keep their
  identifiers, their `Task ID`s, and their `Depends On` edges, so no cross-reference in this plan or
  in the critique history has to be re-read.
- **Why it left this plan.** The guard prevents a *future* regression. It stops none of the three
  rotation writers, so removing it changes nothing about whether this PR fixes the rotation. It
  produced the sole remaining blocker in critique rounds 4, 5, and 6 — always its satisfiability
  inventory, never the substrate around it — while Tasks 1-5 and 7-11 were verified sound across all
  six rounds. Holding a suite-correctness fix behind a regression-prevention backstop was the wrong
  trade.
- **What moved to #2656** (all five findings carried over in substance):
  - (a) Every Redis construction site in `tests/` is attribute-qualified (`redis.Redis`,
    `aioredis.Redis`, `_redis.Redis`, `redis_mod.Redis.from_url`), so the original `node.func.id`
    matcher would have matched **zero** sites — a vacuously-green guard that could never detect a
    recurrence.
  - (b) The `_callee_name` terminal-name fix — match `func.id` for `ast.Name` **and** `func.attr` for
    `ast.Attribute`, routed through one helper used by both the callee match and the permit match —
    is correct and worth keeping.
  - (c) `tests/integration/test_notify_isolation.py:61-65`,
    `redis.Redis(db=int(kw.get("db", 0) or 0))`. **This site is still fixed here, by Task 4** — it is
    a genuine unowned-db construction and the guard that covered it is gone, so the call-site
    conversion carries it alone.
  - (d) The six non-literal `from_url(redis_test_url)` sites that are out of the AST walk's reach
    (`tests/unit/test_valor_email.py:47`, `tests/unit/test_email_history.py:29`,
    `tests/unit/test_email_bridge.py:1154`, `tests/unit/test_email_relay.py:34`,
    `tests/unit/test_send_message.py:29`, `tests/integration/test_valor_email.py:30`).
  - (e) The `[1..TEST_DB_POOL_MAX]` allowlist invariant — no allowlist entry may name a db inside the
    claim pool; every entry must resolve to db 0. This is the durable part and must survive intact.
- **What stays in scope here, and why the descope is safe.** Runtime enforcement is untouched:
  Task 3's ownership flush guard denies a `flushdb` on an unclaimed db on every branch and in every
  repo state, and Task 2's plugin-agnostic session-scoped client-ownership check covers the installed
  library code that no `tests/`-scoped AST walk could ever see. Both are stronger controls than a
  static walk; the walk was the cheap backstop, not the enforcement.
### 7. Close the second rotation writer: module-reload registry leak
- **Task ID**: build-reload-restore
- **Depends On**: none
- **Validates**: tests/unit/test_index_drift.py, tests/unit/test_index_drift_coverage.py, tests/unit/test_conftest_isolation_guards.py
- **Informed By**: critique BLOCKER 3 (independent of `flushdb`; same rotate-run-to-run signature)
- **Assigned To**: callsite-builder
- **Agent Type**: test-engineer
- **Parallel**: true (touches no file Tasks 1-6 touch)
- Replace `importlib.reload(agent.index_drift)` at `tests/unit/test_index_drift.py:207-208` with a
  direct assertion on `AGENTSESSION_INDEX_DRIFT_TOLERANCE`, plus `monkeypatch.setattr` wherever a
  non-default value is wanted. The reload existed only to observe a module constant; it leaves a
  freshly-rebound `DRIFT_COVERED_MODELS` in `sys.modules` for the rest of the worker.
- Rebind `tests/unit/test_index_drift_coverage.py`'s `restore_registry` fixture (`:37-42`) and its
  `register_drift_model` call sites to go through the *module* object, not the collection-time
  `from agent.index_drift import ...` names, so a fixture can never restore an orphaned dict while
  the test writes to the live one.
- Widen the conftest reload guard: `tests/conftest.py:385` currently scopes
  `_SHARED_EXCEPTION_MODULES` to `("models.session_lifecycle",)` and `_snapshot_shared_exceptions`
  (`:391-401`) snapshots only `BaseException` subclasses. Extend it to snapshot module-level
  registry objects (dicts, classes, enums) for `agent.index_drift` and `monitoring.bridge_watchdog`.
- Bring `tests/unit/test_worker_watchdog.py:47,52,58,1050-1058` under the same restoration (it
  leaves `HEARTBEAT_THRESHOLD=90` set across a span of tests).
- **Do NOT edit `monitoring/bridge_watchdog.py`.** An earlier draft added a `handlers.clear()` there.
  It is out of scope (round-3 concern 7): a duplicated log line cannot change any test outcome, and
  it would make this the only production file in the PR and pull a launchd restart into it. The
  widened snapshot above already covers that module's registry objects, which is the rotation-
  relevant part. The `handlers.clear()` is filed separately (No-Gos).
- **Red-state proof**: a test that reloads `agent.index_drift`, then asserts
  `covered_model_names()` is unchanged and no `FakeCoveredModel` survives — red on `main`, green on
  the branch.

### 8. Run provenance in the pytest header
- **Task ID**: build-provenance
- **Depends On**: build-session-claim
- **Validates**: tests/unit/test_conftest_isolation_guards.py
- **Assigned To**: db-ownership-builder
- **Agent Type**: builder
- **Parallel**: true
- Emit `worker=<gwN|master> db=<claimed>` per worker, so a future rotation starts from a log line
  instead of a fresh recon (Risk 3). One line per worker; no per-test logging.
- **Never call `claim_test_db()` from the header hook.** `pytest_report_header` also runs in the
  xdist *controller*, which owns no test db; claiming there would burn a 16th slot from a pool of
  15 that spike-3 already found at 10/15 utilisation. Read the already-claimed value
  (`_db_claim._CLAIMED_TEST_DB`, `None` if unclaimed) instead. This is the **same rule as Task 2's
  controller branch**, applied to a second hook — the `not-applicable-controller` state is a property
  of the process, not of any one hook, and both hooks must honour it. There is no longer any task in
  this plan that claims from the controller.
- **Route worker output through `workeroutput`.** xdist does not surface a worker's
  `pytest_report_header` to the terminal, so a naive implementation prints nothing where it
  matters. Use the repo's existing controller/worker idiom at `tests/conftest.py:1082`
  (`if getattr(config, "workerinput", None):` is the worker branch): the worker writes
  `config.workeroutput["test_db"]`, and the controller surfaces the collected values from
  `pytest_report_header`/`pytest_terminal_summary`.
- **Test both halves with stub configs, not a nested pytest run** (round-3 concern 5). A real `-n 2`
  child would claim two more flock slots while the outer suite holds up to 10 (spike-3 found 1-10
  held), and under Task 5's fail-hard exhaustion it raises instead of degrading — the PR whose
  purpose is to remove run-to-run variation would introduce a new source of it. No nested-pytest
  subprocess test exists in the suite today (the `pytest.main([__file__])` occurrences under
  `tests/unit/` are all `__main__` guards). Instead: (a) call the worker branch with a stub config
  carrying `workerinput` and a `workeroutput` dict, assert
  `config.workeroutput["test_db"] == _db_claim._CLAIMED_TEST_DB`; (b) call the controller's
  `pytest_report_header`/`pytest_terminal_summary` with stub collected outputs
  `[{"test_db": 3}, {"test_db": 7}]` and assert both appear in the emitted text. If a real `-n 2` run
  is ever added, it must pass `TEST_DB_POOL_MAX`/`TEST_DB_CLAIM_WAIT_S` and a `tmp_path` claim dir to
  the child via `subprocess_env` (`tests/db_claim.py:187`) so it never competes for the real pool.

### 9. Validate: red-state proofs
- **Task ID**: validate-rotation-fixed
- **Depends On**: build-session-claim, build-callsites, build-exhaustion-policy, build-reload-restore, build-provenance *(`build-recurrence-guard` removed — Task 6 is descoped to #2656)*
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- **Pass condition:** every new regression test fails on `main` and passes on the branch, with both
  outputs pasted into the PR body. The three that carry the plan are (a) Task 3's adversarial
  flock-holder flush test, (b) Task 7's reload-restoration test, and (c) Task 2's popoto-client
  ownership assertion
  (`POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"] in claimed_test_dbs()`) — one per
  rotation writer. Each runs in ~1 s and reproduces a real competing writer deterministically.
  Also confirm Task 2's controller/`-n0` stub-config tests and Task 5's second-call-returns-fast
  test, which are the round-3 blocker regressions.
- **Capturing the red state must not flush a db this process does not own — verify this before
  running anything on `main`** (round-4 blocker 3). Task 3's deny half is asserted against a
  `SimpleNamespace` stub with no connection, so the `main`-side capture makes **zero** Redis contact
  on the deny path; its red signal is the *absence* of a `RuntimeError`, not a successful flush.
  Before capturing, grep the new tests for any `redis.Redis(db=` whose argument is not
  `claim_test_db()`/`claim_scratch_test_db()` — there must be none. No throwaway server, and no
  "safe db number", is required or permitted: the pool is `[1..15]` and db 0 is production.
- **The `main` baseline is RECORDED — do not re-derive it.** Full `tests/unit/` on `main` at commit
  `d5539cb7f`, via `scripts/pytest-clean.sh`, run **without** quiescence:

  > **12075 passed, 1 skipped, 0 failed, 0 errors, 699.19s** — 15:15–15:27 local, 2026-08-07.

  **Caveat, non-optional, and it travels with the number wherever the number goes:** a green run does
  **not** show the rotation is fixed. It shows the failure set was *empty on that run*. The popoto
  writer needs a second live pytest process holding the victim slot **at the moment of the flush** to
  do damage, and the degree of genuine concurrent load inside that 12-minute window is
  **UNVERIFIED**. Do not cite this run as evidence of a fix, before or after the branch lands. The
  binding evidence is and remains the ~1 s adversarial flock-holder tests, red on `main` and green on
  the branch.
- **Because the recorded baseline failure set is EMPTY, the gate simplifies to "zero failures and
  zero setup-phase errors"** on the branch run. This is strictly stronger than the round-2
  "every branch failure appears in the recorded `main` baseline" formulation *and* it is now
  achievable, so it removes the waiver risk that motivated the weaker wording. Still record the
  branch run's failing node ids (names, never counts) in the PR body — if the count is non-zero the
  names are what triage starts from.
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
superseded as the baseline of record by the `d5539cb7f` run above and are kept only as corroboration
that a clean `main` run is unremarkable rather than surprising. This plan does not
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
| No regression against the recorded baseline | `scripts/pytest-clean.sh tests/unit/ -q` on the branch (the `main` baseline is already recorded — do NOT re-run it) | **Zero failures and zero setup-phase errors.** The recorded `main` baseline at `d5539cb7f` is 12075 passed / 1 skipped / 0 failed / 0 errors / 699.19s, so the failure set to match is empty and the gate is achievable as stated. Record the branch run's failing node ids (names, never counts) in the PR body. A green run does **not** prove the rotation is fixed — see the caveat in Task 9 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Flush guard denies unclaimed db | `scripts/pytest-clean.sh tests/unit/test_redis_flush_guard.py -q` | exit code 0 |
| Claim API + exhaustion policy | `scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -q` | exit code 0 |
| Reload restoration (writer 2) | `scripts/pytest-clean.sh tests/unit/test_index_drift.py tests/unit/test_index_drift_coverage.py -q` | exit code 0 |
| Converted call sites take `db=` from the claim API | `git diff origin/main... -- tests/unit/test_redis_flush_guard.py tests/unit/test_conftest_isolation_guards.py tests/integration/test_agent_catchup_recovery.py tests/unit/test_email_bridge.py tests/integration/test_notify_isolation.py` | every `db=` in the diff resolves to `claim_test_db()` / `claim_scratch_test_db()` / the `scratch_test_db` fixture. Reviewed per-site; the structural AST check is descoped to #2656 |
| popoto client sits on a claimed db (writer 3) | `scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -q -k popoto_plugin` | exit code 0 |
| Session-start claim: controller claims nothing, `-n0` master does | `scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -q -k session_claim` | exit code 0 (stub configs; no nested pytest run) |
| Exhaustion is paid once per process, not per test | `scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -q -k exhaustion` | exit code 0, whole selection completes in seconds |
| No production file touched | `git diff --name-only origin/main... \| grep -v '^tests/\\\|^docs/\\\|^pyproject.toml$' \|\| true` | prints nothing (judge the printed lines, not the exit code) |
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
| CONCERN | Risk & Robustness | Task 8's `pytest_report_header` provenance (Task 6 at the time of this critique) has two defects. (a) The hook also runs in the CONTROLLER; calling `claim_test_db()` there claims a 16th slot the controller never uses, against a pool of 15 that spike-3 already found at 10/15. (b) xdist does not surface worker `pytest_report_header` output to the terminal, so the per-worker lines the plan relies on for the next investigation may never appear in the log. | ✅ RESOLVED | Use the repo's existing controller/worker idiom at `tests/conftest.py:1082` (`if getattr(config, "workerinput", None):` is the worker branch); emit from the worker into `config.workeroutput["test_db"]` and surface it from the controller in `pytest_report_header`/`pytest_terminal_summary`. Read the ALREADY-claimed value (`_db_claim._CLAIMED_TEST_DB`, `None` if unclaimed) — never call `claim_test_db()` from the header hook. |
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
walk of the `db=` kwarg with grep as a second leg, and Task 8 (Task 7 at the time) reading `_db_claim._CLAIMED_TEST_DB`
via `config.workeroutput` — are sound. Round 2 found one new blocker.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | **Task 1 extends `release_test_db_claim()` to clear `_CLAIMED_DB_NUMS` but does not rebind that set in `_reset_claim_state`, so the claim tests corrupt the real process-wide set.** `_reset_claim_state` (`tests/unit/test_conftest_isolation_guards.py:472-489`) deliberately swaps in a *fresh list* for `_CLAIM_LOCK_FDS` (`:486`) precisely so tests cannot mutate the live one; Task 1 adds only a `wait_s` parameter alongside `pool_max` and leaves the new number set bound to the live module object. `monkeypatch` cannot undo an in-place `.clear()`. Certain failure: `test_claim_is_in_pool_idempotent_and_releasable` (`:504-518`) calls `release_test_db_claim()` at `:514`, permanently emptying the real `_CLAIMED_DB_NUMS`; every subsequent test in that worker then has its autouse `redis_test_db` flush (`tests/conftest.py:617`) denied by Task 2's fail-closed-on-empty rule and errors in setup. Conversely the other claim tests leak `tmp_path` slot numbers into the real set, making the guard *permit* flushes on unowned dbs. Under `--dist=loadfile` which worker is hit varies per run — the fix would ship with a rotating failure set of its own, reintroducing the exact bug this plan exists to kill. | ✅ RESOLVED | Task 1 now carries a dedicated bullet: `_reset_claim_state` must `monkeypatch.setattr(_db_claim, "_CLAIMED_DB_NUMS", set(), raising=False)` (and the same for the new `_CLAIMED_SCRATCH_DB`), mirroring the `fresh_fds` treatment of `_CLAIM_LOCK_FDS` at `:486`, and return the fresh set beside `fresh_fds` so assertions can read it. A Task 1 test asserts that a claim/release cycle performed inside `_reset_claim_state` leaves the real process-wide `_CLAIMED_DB_NUMS` untouched. |
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

### Round 3 (re-critique of the three-writer plan)

Round 3 verified all round-1 and round-2 resolutions against real source and they hold. It found three
new blockers, all of them in the *composition* of the now-three-writer task graph rather than in any
single task: Task 2's fail-closed guard is a total suite outage until Task 7 lands, Task 4's exhaustion
raise is re-paid per test rather than per process, and Task 7 mandates the exact controller-side
`claim_test_db()` call that Task 8 explicitly forbids.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | **Task 2 without Task 7 is a total suite outage, and no dependency edge orders them.** Both declare `Depends On: build-claim-api` and Task 7 is `Parallel: true`, so Task 2 (#2) naturally lands five commits before Task 7 (#7). The existing guard patches the CLASS method (`tests/conftest.py:142-147`), so popoto's `_popoto_flush_db` — a function-scoped autouse fixture calling `redis_db.POPOTO_REDIS_DB.flushdb()` before every test (`popoto/pytest_plugin.py:179-194`) — is subject to it. Until Task 7 repoints the plugin that flush targets db 15 while the process claimed another slot, so once Task 2 lands every test errors in setup in ~14 of 15 processes. The plan's own `--setup-plan` trace also shows `_popoto_flush_db` sets up BEFORE `redis_test_db`, so on the first test of a worker `claimed_test_dbs()` is still empty and fail-closed denies even a correctly-targeted flush. | ✅ RESOLVED | **Substrate change, not a patch.** Task 2 is new and is the load-bearing task: `tests/conftest.py` gains a `pytest_configure` that claims the db at session start, before collection and therefore before EVERY fixture — popoto's autouse `_popoto_flush_db` included. The `unclaimed-not-yet` state is designed out rather than excused, so fail-closed never denies a correct flush (see *The claim lifecycle* table in Solution). The popoto repoint moved INTO that same hook, so there is no commit window in which the guard is live and the plugin still targets db 15; the old standalone popoto task is deleted. Task 3 (the guard, formerly Task 2) now declares `Depends On: build-claim-api, build-session-claim` with an explicit note that this is a correctness edge, and carries the assertion `redis_db.POPOTO_REDIS_DB.connection_pool.connection_kwargs['db'] in claimed_test_dbs()`. |
| BLOCKER | Risk & Robustness | **Task 4's exhaustion failure is not memoized, so the 30 s wait is paid per test, not per process.** `claim_test_db()` returns early only when `_CLAIMED_TEST_DB is not None` (`tests/db_claim.py:127-129`), and that global is assigned only on success paths (`:133`, `:142`, `:146`). Replacing `:146-153` with a raise leaves it at `None`, so the next call re-enters the poll loop. The only caller is the function-scoped autouse `redis_test_db` (`tests/conftest.py:570-571`, claiming at `:608`), so a run Risk 2 describes as "a run abort after the wait window" instead burns 30 s x tests-per-worker (~1200 tests => ~10 hours) before finishing. `--timeout=420` cannot catch it: no single test exceeds 420 s. This makes the exhaustion path — the machine's normal state under two concurrent runs per spike-3 — strictly worse to operate than the collision it replaces. | ✅ RESOLVED | **Sticky by two independent mechanisms, both specified in task bodies.** Task 1 adds `_CLAIM_FAILURE: str | None = None` to the claim-state globals; Task 5 sets it immediately before the exhaustion `raise` and makes `if _CLAIM_FAILURE is not None: raise RuntimeError(_CLAIM_FAILURE)` the FIRST statement of `claim_test_db()`, ahead of the `_CLAIMED_TEST_DB` early return, cleared by `release_test_db_claim()` and rebound in `_reset_claim_state`. Independently, Task 2's session-start claim means the poll is entered once per PROCESS by construction, and on failure calls `pytest.exit(msg, returncode=3)` — one line instead of N-thousand setup errors. Task 5 adds the regression test: with the pool held, the SECOND `claim_test_db()` returns in < 0.5 s under `_reset_claim_state(..., wait_s=2)`. |
| BLOCKER | History & Consistency | **Task 7 mandates the controller-side `claim_test_db()` that Task 8 explicitly forbids.** Task 8 states "Never call `claim_test_db()` from the header hook ... `pytest_report_header` also runs in the xdist *controller*, which owns no test db; claiming there would burn a 16th slot from a pool of 15 that spike-3 already found at 10/15 utilisation." Task 7 then instructs setting `os.environ["POPOTO_TEST_DB"] = str(claim_test_db())` from `pytest_configure` — which runs in the controller exactly as `pytest_report_header` does, and runs there FIRST, before any worker forks. The slot is held for the whole session and never released, raising per-run demand from 10 to 11 in the same PR that Task 4 makes exhaustion fatal. Compounding trap: `tests/conftest.py` has NO `pytest_configure` today (only `pytest_runtest_teardown:38`, `pytest_collection_modifyitems:851`, `pytest_unconfigure:1071`), so a builder reading "the repo's existing ... `pytest_configure`" writes a fresh hook with no controller branch. The cited idiom at `:1082` is real but sits inside `pytest_unconfigure`. | ✅ RESOLVED | **One hook, written once, with the controller branch in it.** The standalone popoto task is gone; Task 2 is the only task that calls `claim_test_db()` from a hook, and its body spells out the code: `is_worker = getattr(config, 'workerinput', None) is not None`, then `if not is_worker and getattr(config.option, 'numprocesses', None): return` before the claim. Task 2 explicitly states that `tests/conftest.py` has NO `pytest_configure` today (hooks at `:38`, `:851`, `:1071`; the `workerinput` idiom at `:1082` sits inside `pytest_unconfigure`) so the builder writes it fresh rather than hunting for one to extend. The `numprocesses` check is called out as load-bearing for `-n0`. Task 8 now cross-references the same rule as a property of the process, not of a hook. Tests use stub configs: controller (`numprocesses=2`) leaves `_CLAIMED_TEST_DB` at `None`; worker claims; `-n0` master claims. |
| CONCERN | Scope & Value | **Task 7's red-state proof is code that Task 5's AST guard is authored to reject.** Task 7 proves the fix with "a test that writes a sentinel key to db 15", but Task 5 permits a `db=` value only when it is a `Call` to `claim_test_db`/`claim_scratch_test_db` or a `Name` bound from the `scratch_test_db`/`redis_test_db` fixture — a literal `redis.Redis(db=15)` is exactly the second documented offender shape. No allowlist entry is planned. Task 7's hedge ("or, better, to a slot claimed by a spawned `_spawn_flock_holder` child") does not help: a slot held by a child is by construction not in this process's `claimed_test_dbs()`, so the client is still an unowned-db construction. The sentinel also writes to a database this process does not own, contradicting the plan's central invariant. | ✅ RESOLVED | **Proof restated in ownership terms; the literal-15 sentinel is deleted from the plan.** Task 2's red-state proof is now `POPOTO_REDIS_DB.connection_pool.connection_kwargs['db'] in claimed_test_dbs()` (and the stronger `== claim_test_db()`), red on `main` because the plugin sits on 15 while the process claims elsewhere. The task body explicitly forbids writing `redis.Redis(db=15)` — it is the second documented offender shape, it writes to a db this process does not own, and it would contradict the plan's central invariant. Task 6 states that the AST allowlist starts empty and stays empty in this PR; no entry is granted. |
| CONCERN | Risk & Robustness | **Task 8's `-n 2` provenance test is a new contention-dependent flake.** "Add a test asserting the controller path emits a line per worker under `-n 2`" requires a nested pytest run that claims 2 more flock slots while the outer suite holds up to 10 (spike-3 found 1-10 held). Under Task 4's fail-hard exhaustion the nested run raises instead of degrading, so the PR whose purpose is to remove run-to-run variation introduces a new source of it. No nested-pytest subprocess test exists in the suite today — the `pytest.main([__file__])` occurrences under `tests/unit/` are all `__main__` guards. | ✅ RESOLVED | **Nested pytest replaced with stub configs.** Task 8 now specifies: (a) call the worker branch with a stub config carrying `workerinput` and a `workeroutput` dict, assert `config.workeroutput['test_db'] == _db_claim._CLAIMED_TEST_DB`; (b) call the controller's `pytest_report_header`/`pytest_terminal_summary` with stub collected outputs `[{'test_db': 3}, {'test_db': 7}]` and assert both appear. The body records that no nested-pytest subprocess test exists in the suite today, and that if one is ever added it must be handed `TEST_DB_POOL_MAX`/`TEST_DB_CLAIM_WAIT_S` and a `tmp_path` claim dir via `subprocess_env` (`tests/db_claim.py:187`) so it never competes for the real pool. |
| CONCERN | History & Consistency | **The recurrence guard is structurally blind to the writer class the plan says is most frequent.** Task 5's AST walk parses `tests/**/*.py` only, but spike-4's writer lives in installed library code (`popoto.pytest_plugin`, a `pytest11` entry point — confirmed present at popoto 1.8.0). The plan's answer is one popoto-specific assertion plus a prose rule in the docs ("any installed pytest plugin touching Redis must be pointed at the claimed db") — which is the same "convention each call site is trusted to honour" shape the plan diagnoses as the root-cause pattern. A future popoto that adds a second client, or any other pytest11 plugin, re-opens the hole, so the "ends the series" claim is unearned for the plugin class. | ✅ RESOLVED | **Plugin-agnostic runtime check added to Task 2, and the blind spot is named in Task 6's code comment.** Task 2 adds a session-scoped autouse fixture, ordered after the claim, that walks `popoto.redis_db.POPOTO_REDIS_DB` and `_POPOTO_ASYNC_REDIS_DB` and asserts each client's `connection_pool.connection_kwargs['db'] in claimed_test_dbs()`, failing with "a client is pointed at db N which this process has not claimed". Costs one assertion per session and catches any future `pytest11` plugin that swaps the popoto globals. Task 6 carries the reciprocal instruction: state in the AST walk's comment that it is blind to installed library code and that Task 2's runtime check is its counterpart, so neither is mistaken for complete. Also recorded as a Technical Approach bullet. |
| CONCERN | Scope & Value | **The `monitoring/bridge_watchdog.py` production edit does not serve the stated problem.** A leaked `RotatingFileHandler` duplicates log lines; it cannot change any test's pass/fail outcome, so it contributes nothing to the rotating failure set. It is nevertheless the plan's only production change, it ships to a running launchd service, and it drags a `./scripts/valor-service.sh restart` requirement (recorded in the plan's own Update System section) into an otherwise pure-`tests/` PR. There is also no Verification row for it. | ✅ RESOLVED | **Moved out of scope entirely.** The `monitoring/bridge_watchdog.py` `handlers.clear()` is removed from Task 7, its Test Impact row now reads NOT TOUCHED with the reason, Architectural Impact states **zero production changes**, Update System states no service restart is required, and a `[SEPARATE-SLUG]` No-Go files it as its own issue. Task 7 still snapshots that module's registry objects under the widened reload guard — the rotation-relevant half, which touches no production file. A new Verification row (`git diff --name-only` filtered to non-`tests/`) fails the PR if any production file reappears. |
| NIT | History & Consistency | Internal cross-references drifted when Task 6 was inserted and later tasks renumbered: the round-1 table says "Task 6's `pytest_report_header` provenance" and the Round 2 prose says "Task 7 reading `_db_claim._CLAIMED_TEST_DB` via `config.workeroutput`" — both are now Task 8. Two anchors are off by one: `_reset_claim_state` is at `tests/unit/test_conftest_isolation_guards.py:473` (plan says `:472`) and `divergent_db = 15 if base_test_db != 15 else 14` is at `:361` (plan says `:362`). Identifications are correct; only the pointers are stale. | ✅ RESOLVED | Fixed: the round-1 table's "Task 6's `pytest_report_header`" and the Round-2 prose's "Task 7 reading `_db_claim._CLAIMED_TEST_DB`" are corrected to Task 8 in a Round-3 renumbering note, and both anchors are corrected in the task bodies — `_reset_claim_state` at `tests/unit/test_conftest_isolation_guards.py:473` and `divergent_db = 15 if base_test_db != 15 else 14` at `:361` (verified against source). |
| NIT | Scope & Value | Task 5 bundles the `TestSearchIntegration` relocation, which is legitimate hygiene but is not a rotation writer (it is slow and network-dependent, not a cross-process db writer), into the plan's most load-bearing structural task. | ✅ RESOLVED | Moved: the `TestSearchIntegration` relocation now lives in Task 4 (call-site hygiene), whose title is updated to name it, and Task 6 is retitled "Recurrence guard (AST walk over `db=`)" with the relocation removed from its `Validates` list. The Test Impact row records why. |

### Round-3 revision: the substrate changed, so the tasks were restructured

The three round-3 blockers were not three bugs. They were three instances of one: **the fail-closed
ownership guard meeting something that runs before, outside, or independently of the claim.** Rounds
1-3 had been patching instances one at a time, which is why the finding count stopped falling. This
revision names the claim lifecycle explicitly — `unclaimed-not-yet`, `claimed`,
`unclaimable-permanently`, `not-applicable-controller` — defines the guard's behavior for each, and
picks the substrate that makes one decision cover all four.

**Design decision: the claim moves to session start.** A new Task 2 adds a worker-side
`pytest_configure` to `tests/conftest.py` that claims the db before collection and therefore before
every fixture, autouse or installed-plugin. Fail-closed-at-flush-time was retained as the *guard*,
but it is no longer fighting pytest's fixture ordering, because the state it could get wrong no
longer exists at test time. Concretely:

- `unclaimed-not-yet` is **designed out**, not excused. The rejected alternative was an exemption
  permitting each worker's first `_popoto_flush_db` while the claimed set was still empty — which is
  "permit a flush when we do not know who owns the target", the pre-#2606 status quo restated, and it
  would have been load-bearing on every worker's first test forever. (Blocker 1.)
- `unclaimable-permanently` is paid **once per process**: the hook claims once and `pytest.exit`s on
  failure, and Task 1's `_CLAIM_FAILURE` memo covers the remaining direct callers. (Blocker 2.)
- `not-applicable-controller` has exactly one implementation, in one hook, with the `numprocesses`
  branch that keeps `-n0` correct. The task that used to mandate a controller-side claim is gone —
  absorbed into the hook that already knows it must not. (Blocker 3.)

**Task graph changes.** The standalone popoto task (old Task 7) is **deleted**; its content lives in
Task 2, which is why the ordering hazard between the guard and the repoint cannot recur — they are
one commit by construction. Old Task 2 (the guard) is now Task 3 and depends on `build-session-claim`.
Tasks 3-6 shifted to 4-7. `TestSearchIntegration`'s relocation moved from the recurrence-guard task to
the call-site task. The task count is unchanged at 11.

**Scope removed.** The `monitoring/bridge_watchdog.py` edit is out; this PR now touches zero
production files and needs no service restart.

**Alignment recorded.** Technical Approach now states how this guard composes with #2645's Layer-1
db-0 helper (one wrapper, not two idioms) and offers the per-process flock-claimed set as the by-db
discriminator #2645's rename-command-vs-ACL question is looking for.

**Round-3 structural check:** all four repo-mandated sections present and substantive; 11 tasks with no
numbering gaps; every `Depends On` id resolves and no cycles; both prerequisites PASS (redis `PONG`,
`scripts/check-interpreter-pin.sh` exit 0); every cited file exists except
`docs/features/test-db-ownership.md` and `tests/integration/test_youtube_search_live.py`, which this plan
creates. Spike-4 independently re-verified: popoto 1.8.0 registers `pytest11 popoto = popoto.pytest_plugin`
and `pyproject.toml:195` addopts carry `-p no:postgresql` only.

### Round 4 (re-critique of the restructured three-writer plan)

Round 4 re-verified every round-1/2/3 resolution against real source and they hold; the claim-lifecycle
substrate is sound and the `pytest_configure` ordering guarantee is correct (popoto's plugin resolves
`POPOTO_TEST_DB` at *fixture* setup, `popoto/pytest_plugin.py:138-150`, long after any conftest
`pytest_configure`). The three new blockers are all in the plan's **proof and enforcement machinery**
rather than in the substrate: the runtime client check dereferences a value that is always `None`, the
AST recurrence guard is red on ten call sites the plan never enumerates, and the binding red-state proof
performs the very cross-process wipe the plan exists to prevent when it is captured on `main`.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | **Task 2's plugin-agnostic client-ownership check dereferences `_POPOTO_ASYNC_REDIS_DB`, which is `None` at every moment a session-scoped fixture can observe it.** `popoto/redis_db.py:97` initialises it to `None`; the plugin's `_popoto_reset_async` sets it to `None` at every test's setup AND teardown (`popoto/pytest_plugin.py:214,217`) and its docstring (`:208-212`) states the client is deliberately NOT pre-created because a client built in the sync setup context binds to the wrong event loop; `tests/conftest.py:612` captures that `None` as `original_async` and restores it at `:639`. Asserting `client.connection_pool.connection_kwargs["db"]` on `None` raises `AttributeError` inside a session-scoped autouse fixture, erroring EVERY test in the process in setup — the identical whole-suite-outage shape as round-3 blocker 1. | ✅ RESOLVED | **Fixed in Task 2's body and in Technical Approach.** The walk is now written as `for client in (rdb.POPOTO_REDIS_DB, getattr(rdb, "_POPOTO_ASYNC_REDIS_DB", None)):` with an explicit `if client is None: continue` before `connection_pool` is touched, and the task states that `None` is the async client's *correct* state (`popoto/redis_db.py:97`; `_popoto_reset_async` nulls it at `popoto/pytest_plugin.py:214,217`; `tests/conftest.py:612/639` captures and restores it). The task forbids asserting the async client is non-`None` and records that async coverage, if ever wanted, must be a **function**-scoped check after the test body. A regression test is added: with `_POPOTO_ASYNC_REDIS_DB` at `None`, the check passes rather than raising `AttributeError`. |
| BLOCKER | History & Consistency | **Task 6's AST guard, with the mandated-empty allowlist, is red on ten unenumerated call sites — several of which cannot be converted — and its `db=`-keyword scoping is blind to the other db-0 footgun.** Unconvertible: `tests/unit/test_redis_flush_guard.py:28,40,62,69` (`redis.Redis(db=0)` / `aioredis.Redis(db=0)` — the db-0 guard's OWN tests, which must use a literal `0`) and `tests/integration/test_redis_models.py:696`. Not covered by the permit list as written: `tests/conftest.py:615,631` (`db=test_db`, a `Name` bound from a *Call* to `claim_test_db()` at `:608`, not "from a fixture"), `tests/unit/test_conftest_isolation_guards.py:298,398`, `tests/_worker_guard.py:91`. Blind spot: scoping to the `db=` keyword misses `Redis.from_url(".../0")` — live at `tests/unit/test_redis_flush_guard.py:34` and `tests/unit/test_agent_session.py:690,782` — which `tests/conftest.py:97` names in the same breath as the `db=0` footgun. Task 9's "every new test passes on the branch" is unreachable as specified. | ✅ RESOLVED | **The allowlist ships POPULATED and the permit list resolves one assignment hop.** Task 6 now permits a `db=` value that is (a) a `Call` to `claim_test_db`/`claim_scratch_test_db`, (b) a `Name` whose nearest binding in the same function is such a `Call` — which is what covers `tests/conftest.py:608→615,631` — or (c) a fixture *parameter* named `scratch_test_db`/`redis_test_db`. A seeded `{path: reason}` table ships with exactly three entries, all db 0 and all verified against source: `tests/unit/test_redis_flush_guard.py` (`:28,40,62,69` — the db-0 guard's own tests), `tests/integration/test_redis_models.py` (`:695-697` — asserts nothing leaked into production), `tests/_worker_guard.py` (`:91` — registrations intentionally live on db 0). "The allowlist stays empty" is replaced by the enforceable property **"no allowlist entry may name a db in `[1..TEST_DB_POOL_MAX]`"**. The walk is extended to `Redis.from_url`/`ConnectionPool.from_url` string literals (parsing the trailing `/N`), covering `test_redis_flush_guard.py:34` and `test_agent_session.py:690,782`; non-literal `from_url` arguments are named as an acknowledged blind spot in the code comment rather than claimed. Task 6 gains an explicit green-state gate. |
| BLOCKER | Risk & Robustness + History & Consistency | **Task 3's binding red-state proof issues a REAL `flushdb()` on db 1 when Task 9 captures it on `main`, wiping whichever sibling pytest process holds slot 1.** On `main` the guard rejects only `db == 0` (`tests/conftest.py:117-129`), so `redis.Redis(db=1).flushdb()` does not raise — it executes. `_reset_claim_state` redirects only the *lock registry* to `tmp_path` (`tests/unit/test_conftest_isolation_guards.py:483`); the Redis server and its db numbering are untouched, and spike-3 records slots 1-10 routinely held by live siblings. Capturing the red state therefore performs the exact cross-process wipe this plan exists to eliminate. It is also the literal-unowned-db construction round-3 concern 4 deleted from the plan (the db-15 sentinel) for three stated reasons — offender shape, AST-guard rejection, writes to a db this process does not own — none of which were applied to the plan's own most load-bearing test. | ✅ RESOLVED | **Deny half redesigned to the guard-intercepts shape; no code path in this plan flushes an unowned db on any branch.** The `redis.Redis(db=1)` construction is deleted. The deny path is asserted against a `SimpleNamespace` whose `connection_pool.connection_kwargs == {"db": 1}` — the guard reads only `_db_of(client)` (`tests/conftest.py:110-115`), so the stub carries no socket and no connection and cannot reach Redis on `main`, on the branch, or in between. An `execute_command` spy that calls `pytest.fail("guard did not intercept")` is a second independent backstop. The red-on-`main` leg is **inverted**: the failure is that NO `RuntimeError` is raised (the db-0-only guard does not intercept), never that a flush succeeded — stated in the test docstring and in the PR body so the output cannot be misread. The permitted half keeps a real client, on this process's own claimed db. Task 9 gains a pre-capture check for stray `redis.Redis(db=` constructions. The `REDIS_PORT=6399` throwaway-server variant is recorded and rejected: a second server to operate for no additional coverage. No "safe db number" is sought — the pool is `[1..15]` and db 0 is production (#2645). |
| CONCERN | Risk & Robustness | **Worker-side `pytest.exit` on exhaustion is defeated by xdist worker replacement, so the 30 s wait is re-paid ~41 times, not once.** xdist treats a worker dying during startup as a crashed node and **clones a replacement** (`xdist/dsession.py:263-266`) up to `--max-worker-restart`, whose default this repo inherits as `numprocesses * 4` (`xdist/dsession.py:555-568`) — 40 at `-n auto` on a 10-core box. Each replacement is a FRESH process, so Task 1's `_CLAIM_FAILURE` module global is `None` in it. The run costs ~41 × 30 s plus 41 conftest imports, emits 41 "replacing crashed worker" lines, and stops only on `maximum crashed workers reached: 40`. Risk 2's "the abort is once per process" and the matching Success Criterion are unachievable while `pyproject.toml:190-194` declines `--max-worker-restart=0`. | ⏸ DEFERRED | Stays deferred: it degrades the *exhaustion* path's ergonomics (a slow, noisy abort instead of a fast one), not the correctness of the ownership invariant this plan ships, and the controller-side free-slot probe is a new hook behavior that would need its own proof. Revisit if an exhaustion abort is actually observed in the wild. |
| CONCERN | Scope & Value | **After Task 2 there are two independently-maintained owners of "which db popoto is on", and the plan never picks one.** popoto's `_popoto_test_db` swaps the pool **in place** on the single `POPOTO_REDIS_DB` object (`popoto/pytest_plugin.py:108-126`), so every `from ..redis_db import POPOTO_REDIS_DB` binding follows automatically; `redis_test_db` instead **replaces** the object (`tests/conftest.py:616`), which is the only reason it needs the submodule re-patch loop at `:626-628` and the restore loop at `:640-641`. Per-test cost is three `flushdb()` round-trips (`popoto/pytest_plugin.py:194`, `tests/conftest.py:617`, `:636`) plus two client constructions, across ~12,000 tests. The plan applies exactly this anti-drift argument to #2645 ("Two independently-maintained flush guards is how one of them drifts") but not to the duplication it is creating. | ⏸ DEFERRED | Stays deferred: `tests/conftest.py:631` is what points `_POPOTO_ASYNC_REDIS_DB` at the test db in the sync setup phase, so removing `redis_test_db`'s replace-and-repatch loop is a separate blast radius; the existing Success Criterion `POPOTO_REDIS_DB...["db"] == claim_test_db()` is already the drift detector between the two owners. Filed as a `[SEPARATE-SLUG]` No-Go. |
| CONCERN | Scope & Value | **The new `connection_kwargs` AST leg is justified by a hazard the same PR removes, and it bans a pattern this repo's own conftest recommends.** Task 6's third leg and Task 4's `test_email_bridge.py:1399-1406` conversion are justified as "it reads a value the popoto plugin can and does mutate (Task 2), so it is unsound" — but after Task 2 the value the plugin mutates it to IS this process's claimed db, so the derivation becomes correct by construction. Meanwhile `tests/conftest.py:511-513` still documents that exact derivation as the sanctioned way a subprocess inherits the claimed db, and that comment is doubly stale: the site it names (`_run_cli_hook` in `tests/unit/test_tool_budget_enforcement.py`) no longer contains the pattern at all. The PR therefore bans in `tests/` a pattern its own conftest recommends, without touching the recommendation — the "convention each call site re-derives" failure the plan diagnoses as its root-cause pattern. | ⏸ DEFERRED | Stays deferred: the leg's *effect* (one authority for "which db do I own") is right even though its stated soundness rationale is weakened by Task 2, and re-justifying it plus fixing the stale `tests/conftest.py:511-513` comment is editorial work that does not change what gets built. Revisit at docs time. |
| NIT | History & Consistency | Anchor and cross-reference drift survived round 3's renumbering sweep. Resolved Question 2 still calls the AST recurrence guard "(Task 5)" — it is Task 6 after the round-3 restructure. Three line anchors are off by one to three: `_SHARED_EXCEPTION_MODULES` is at `tests/conftest.py:385` (plan says `:384`); `_db_of`'s `except Exception: return 0` is at `tests/conftest.py:113-115` (plan says `:110-113`); `test_claim_is_in_pool_idempotent_and_releasable` spans `tests/unit/test_conftest_isolation_guards.py:504-518` (plan says `:503-517`). Identifications are correct; only the pointers are stale. | ✅ RESOLVED | Fixed in the task bodies (free, so taken): Resolved Question 2 now says Task 6; `_SHARED_EXCEPTION_MODULES` corrected to `tests/conftest.py:385`; `_db_of`'s `except Exception: return 0` to `tests/conftest.py:113-115`; `test_claim_is_in_pool_idempotent_and_releasable` to `tests/unit/test_conftest_isolation_guards.py:504-518` with its `release_test_db_claim()` call at `:514`. All four re-verified against source this round. Historical finding text in earlier critique tables is left verbatim as the record of what was found. |

**Round-4 structural check:** all four repo-mandated sections present and substantive; 11 tasks, no
numbering gaps; every `Depends On` id resolves (`build-claim-api`, `build-session-claim`,
`build-flush-guard`, `build-callsites`, `build-exhaustion-policy`, `build-recurrence-guard`,
`build-reload-restore`, `build-provenance`, `validate-rotation-fixed`, `document-feature`) with no
cycles; both prerequisites PASS (redis `PONG`, `scripts/check-interpreter-pin.sh` exit 0); every cited
file exists except `docs/features/test-db-ownership.md` and
`tests/integration/test_youtube_search_live.py`, which this plan creates. Independently re-verified this
round: `tests/conftest.py` still has no `pytest_configure` (hooks at `:38`, `:851`, `:1071`; the
`workerinput` idiom at `:1082` sits inside `pytest_unconfigure`), popoto 1.8.0 ships `pytest11` and
resolves `POPOTO_TEST_DB` at fixture setup (`popoto/pytest_plugin.py:138-150`), and `pyproject.toml:195`
addopts carry `-p no:postgresql` only.

### Round-4 revision: three blockers fixed, three concerns held deferred, plan settled

All three round-4 blockers sat in the plan's **proof and enforcement machinery**, not in the
claim-lifecycle substrate — which round 4 re-verified as sound. The substrate is unchanged by this
revision; the task graph is unchanged at 11 tasks; no scope was added.

- **Blocker 1 (`None` async client)** — one `if client is None: continue` guard, plus the rule that
  `None` is that client's *correct* state and must never be asserted against. Task 2.
- **Blocker 2 (unbuildable empty allowlist)** — the allowlist ships **populated** with three
  reasoned db-0 entries, the permit list resolves one assignment hop, the walk covers `from_url`
  string literals, and "stays empty" is replaced by the enforceable bound **"no entry may name a db
  in `[1..TEST_DB_POOL_MAX]`"**. Task 6 can now go green, which it structurally could not before.
- **Blocker 3 (the proof performed the wipe)** — the deny half is asserted against a
  `SimpleNamespace` with no connection and an `execute_command` spy, and the red-on-`main` leg is
  inverted to "no `RuntimeError` is raised". **No code path in this plan flushes a db the test
  process does not own, on any branch, in any repo state.** Task 3, with a pre-capture check in
  Task 9.

**Held deferred, with justification, not re-opened:** xdist worker-replacement defeating the
`_CLAIM_FAILURE` memo (exhaustion-path ergonomics, not invariant correctness); the two owners of
"which db popoto is on" (removing `redis_test_db` is a separate blast radius — filed as a No-Go); the
`connection_kwargs` AST leg's weakened soundness rationale (the leg's effect is still right; revisit
at docs time). The anchor/naming NIT was fixed because it was free.

**Folded in beyond the findings.** (a) Popoto-upgrade durability is now a stated hard requirement and
the design is shown to meet it: the fix sits at the connection/claim layer, drives the plugin through
its public `POPOTO_TEST_DB` input, never patches installed code, pins no version, and carries a drift
detector. The upstream popoto PR is filed as a No-Go follow-up. (b) The `main` baseline is **recorded**
(`d5539cb7f`: 12075 passed / 1 skipped / 0 failed / 0 errors / 699.19s, non-quiesced), which lets
Verification row 1 tighten from "every branch failure appears in the baseline" to **"zero failures and
zero setup-phase errors"** — and the run's caveat travels with the number: a green run shows the
failure set was empty *on that run*, the concurrent load in that window is UNVERIFIED, and the binding
evidence remains the ~1 s adversarial tests.

**Final sweep performed.** Every RESOLVED row in every round's table was re-checked against the task
body it points at, because BUILD reads task bodies and not this table. Confirmed present as concrete
instructions: `_CLAIMED_DB_NUMS.add` on the fallback path (Task 1), the `raising=False` rebinds in
`_reset_claim_state` (Task 1), `_CLAIM_FAILURE` as `claim_test_db()`'s first statement (Tasks 1+5),
`scope="session"` on `scratch_test_db` (Tasks 1+3), the `numprocesses` controller branch (Tasks 2+8),
`config.workeroutput` provenance (Task 8), the `SimpleNamespace` deny proof (Task 3), the seeded
allowlist and `from_url` leg (Task 6), the widened reload guard (Task 7), `tests/README.md:9`
(Documentation), and the `\|\| true` exit-code caveat on every `grep -c` Verification row. The
`-p no:randomly` flag survives only inside the historical round-1 finding text and appears in no
Verification row.

### Round 5 (re-critique of the round-4 revision)

Round 5 re-verified the claim-lifecycle substrate and the round-4 blocker fixes against real source
and against live probes on `main`, and they hold: Task 3's `SimpleNamespace` deny half was executed
on `main` and produced `Failed: guard did not intercept: flush reached the client layer` — an honest
red state that makes zero Redis contact, exactly as specified. The two new blockers are again in the
**proof and enforcement machinery**, not the substrate: the writer-3 binding measurement is already
green on `main` at the observation point the plan names, and the AST guard's permit list is still
unbuildable for the third round running.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | **The writer-3 binding proof is already GREEN on `main` at the observation point the plan names, so it cannot go red and certifies nothing.** Inside a test body, `tests/conftest.py:615-616` has replaced `rdb.POPOTO_REDIS_DB` with `redis.Redis(db=claim_test_db())`, so `POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"] == claim_test_db()` holds on `main` today. Verified by probe on current `main`: an in-test read printed `db=2, claim_test_db()=2` (PASS), while the same read taken from a session-scoped autouse fixture printed `db=15, claim=1` (the real red state) with `POPOTO_TEST_DB=None`. The plugin's db-15 client is observable only BEFORE `redis_test_db` setup and AFTER its teardown — which the plan's own `--setup-plan` trace documents and then contradicts by siting the proof "inside a test". Affects the Success Criterion, Task 2's red-state proof, and Task 9's pass condition. | ⏸ SUPERSEDED by round-6 BLOCKER 1 (⏸ DEFERRED — team-lead scope ruling; does not gate BUILD) | Delete the "inside any test" wording from the Success Criterion and from Tasks 2 and 9. Site the writer-3 proof at (a) the session-scoped client-ownership check Task 2 already adds — verified red on `main` (`db=15` vs `claim=1`), green after the repoint — and (b) `assert os.environ["POPOTO_TEST_DB"] == str(claim_test_db())`, red on `main` because the var is unset. Never read `rdb.POPOTO_REDIS_DB` from a function-scoped test body for this proof: `tests/conftest.py:615-616` (restored at `:639-641`) makes that read a measurement of this repo's own fixture, not of the plugin. |
| BLOCKER | History & Consistency | **Task 6's permit list still cannot go green, and the plan states the opposite as settled fact.** Task 6 asserts `tests/unit/test_conftest_isolation_guards.py:298,398` "get **no** entry — permit rule (b) covers them". Verified against source: the bindings are `test_db_num = _conftest._redis_test_db_num()` (`:296`) and `base_test_db = _conftest._redis_test_db_num()` (`:352`) — a Call to `_redis_test_db_num`, which is NOT in the permitted callee set, so rule (b) resolves the hop and then rejects the callee. Second leg: the permitted calls in that same file are attribute-qualified (`_db_claim.claim_test_db()` at `:509,511,532,533,562,577,583,603`), so a matcher inspecting `node.func.id` misses all eight. Task 6's green-state gate and Task 9's pass condition are unreachable as specified. This is the third recurrence of one defect (round-3 concern, round-4 blocker 2, both marked RESOLVED) — the plan's own root-cause pattern applied to its own enforcement mechanism. | ✅ RESOLVED by the round-5 revision below — and now MOOT: the task is DESCOPED to #2656 | State the permitted callee set as `{"claim_test_db", "claim_scratch_test_db", "_redis_test_db_num"}` matched on the *terminal name*: `func.id` for `ast.Name` AND `func.attr` for `ast.Attribute`. `tests/conftest.py:656-658` is `def _redis_test_db_num(request=None): return claim_test_db()`, a pure alias, so permitting it is sound. Preferred alternative (removes the alias rather than blessing it): Task 4 converts `tests/unit/test_conftest_isolation_guards.py:296,352` to `claim_test_db()` and deletes `_redis_test_db_num`, repointing its only other caller `tests/conftest.py:669`. Either way the matcher MUST resolve `ast.Attribute` funcs, or the guard is red on eight sites in the file it must go green on. |
| | | *(round-5 revision)* | ✅ RESOLVED | **Terminal-name matching, applied to the callee match AND the permit match.** Task 6 now mandates one helper, `_callee_name(node)`, returning `func.id` for `ast.Name` and `func.attr` for `ast.Attribute`, through which every name comparison in the walk is routed. Re-verified against source this round that the callee side was the larger hole: *every* construction site in `tests/` is attribute-qualified (`redis.Redis`, `aioredis.Redis`, `_redis.Redis`, `redis_mod.Redis.from_url`), so a `node.func.id` matcher would have found zero and been vacuously green — never red. `_redis_test_db_num` is added to the permitted callee set as an alias rather than converting `:296`/`:352`: `tests/conftest.py:658` is `return claim_test_db()`, a pure delegation, and both tests exist to prove conftest's own resolution path lands on a real test client, so substituting the delegate would delete the assertion's subject. `tests/unit/test_agent_session.py` is promoted to an unconditional fourth allowlist row (db 0), removing the round-4 "joins the allowlist if not otherwise converted" hedge that contradicted the table's "and no others". Task 6's green-state gate now carries a full satisfiability inventory of every `db=`/`from_url` site in `tests/`, each marked permit-rule / allowlist / Task-4-conversion / out-of-reach. The `[1..TEST_DB_POOL_MAX]` allowlist invariant and the green-state gate are unchanged. |
| CONCERN | Risk & Robustness | **Task 2's session-scoped client-ownership check declares no dependency on the fixture that makes it true.** It asserts `POPOTO_REDIS_DB`'s db is in `claimed_test_dbs()`, but popoto's session-scoped `_popoto_test_db` is what swaps the client onto that db, and the ordering between two same-scope autouse fixtures is decided by fixture-collection nodeid order (plugin `""` before conftest `"tests"`), not by anything the plan states. If that order inverts, the check runs while `POPOTO_REDIS_DB` is still on its pre-swap db, the assertion fires inside a session-scoped autouse fixture, and every test in the process errors in setup — the identical shape as round-3 blocker 1 and round-4 blocker 1. | ⏸ SUPERSEDED by round-6 CONCERN 1 (⏸ DEFERRED — team-lead scope ruling; does not gate BUILD) | Write the fixture as `def _client_ownership_check(_popoto_test_db):` so pytest orders it after the swap by dependency rather than by nodeid accident; `_popoto_test_db` is a session-scoped autouse fixture at `popoto/pytest_plugin.py:138` and is requestable by name. Keep the `if client is None: continue` guard — re-verified this round that `_POPOTO_ASYNC_REDIS_DB` is `None` at session-fixture setup, though it is a live `db=N` client inside a test body (`tests/conftest.py:631`). |
| CONCERN | Risk & Robustness | **Exhaustion becomes fatal in what spike-3 calls this machine's normal state, while the one-line mitigation is deferred out of a file the PR already edits.** Two concurrent `-n auto` runs demand 20 of 15 slots and the probe already found slots 1-10 held. Round 4 established that xdist clones a replacement for a worker dying during startup up to `numprocesses * 4` (40 at `-n auto`), each a fresh process where `_CLAIM_FAILURE` is `None` — so the "once per process" abort is ~41 x 30 s of retries before the run dies. The mitigation is a single flag in `pyproject.toml:195`, which this PR already touches for addopts hygiene, so "separate blast radius" does not apply. | ⏸ SUPERSEDED by round-6 CONCERN 2 (⏸ DEFERRED — `--max-worker-restart=0` is NOT shipped; `pyproject.toml:190-194` records the prior negative control) | Append `--max-worker-restart=0` to `pyproject.toml:195` addopts (currently `--tb=short -p no:postgresql -n auto --dist=loadfile --timeout=420 --timeout-method=thread`; the flag is absent). Record the trade in the PR body: it also converts a worker that dies mid-run for unrelated reasons into a run-level abort, which is the correct posture for a PR whose thesis is that a partially-completed suite is a broken instrument. If it is NOT shipped, reword Risk 2's "the abort is once per process" and the matching Success Criterion to "once per worker process, up to `--max-worker-restart` replacements". |
| CONCERN | Scope & Value | **The plan ships two pieces of build work whose stated justification it has already withdrawn.** Round 4 established that after Task 2 the `connection_kwargs` value the popoto plugin mutates IS this process's claimed db, so `test_email_bridge.py:1399-1406`'s derivation becomes correct by construction — yet Task 6 still adds a third AST leg to ban it and Task 4 still converts the site, with the withdrawal parked at "revisit at docs time". Meanwhile `tests/conftest.py:511-513` still documents that exact derivation as the sanctioned subprocess pattern (naming a call site that no longer contains it), so the PR bans in `tests/` a pattern its own conftest recommends and leaves the recommendation standing. | ✅ RESOLVED by the round-7 descope: Task 6 is gone, so the `connection_kwargs` AST leg is gone with it. Task 4 still converts `test_email_bridge.py:1399-1406` and `test_notify_isolation.py:61-65`, now on the stated basis "`claim_test_db()` must be the single source of the number". The stale `tests/conftest.py:511-513` comment stays deferred to docs time | Keep the leg but restate its rationale as "one authority, not soundness" (the value is that `claim_test_db()` is the single source, not that `connection_kwargs` is wrong post-Task-2), and delete the now-false `tests/conftest.py:511-513` comment in Task 4 — both files are already in the PR. Or drop the leg: remove it from Task 6's bullet list and remove `tests/unit/test_email_bridge.py:1399-1406` from Test Impact, so the green-state gate is not carrying a rule with no stated basis. |
| NIT | History & Consistency | Anchor drift survived the round-4 sweep again: the plan cites `tests/unit/test_conftest_isolation_guards.py:298,398` as the sites rule (b) covers, but those are the `redis.Redis(db=...)` *construction* lines; the assignments rule (b) must resolve are at `:296` and `:352`. | ✅ RESOLVED | Task 6's rule (b) now cites both pairs explicitly: the constructions at `:298`/`:398` and the `_conftest._redis_test_db_num()` bindings they resolve to at `:296`/`:352`. Verified against source. |
| NIT | Scope & Value | Verification row 1 gates on a full `tests/unit/` run (~12 minutes by the plan's own recorded baseline) while the plan states twice that a green run does not prove the rotation is fixed. Its actual value is narrower: a Risk-1 regression check for legitimate flushes newly denied by the fail-closed guard, which surface as setup-phase errors. | ⏸ DEFERRED (NIT; round-6 scope ruling) | n/a (NIT) |

**Round-5 structural check:** all four repo-mandated sections present and substantive (Documentation,
Update System, Agent Integration, Test Impact); 11 tasks with no numbering gaps; every `Depends On` id
resolves with no cycles (Task 9 reaches `build-flush-guard` transitively via `build-callsites`); both
prerequisites PASS (redis `PONG`, `scripts/check-interpreter-pin.sh` exit 0); every cited file exists
except `docs/features/test-db-ownership.md` and `tests/integration/test_youtube_search_live.py`, which
this plan creates. Independently re-verified this round: popoto 1.8.0 registers
`pytest11 popoto = popoto.pytest_plugin`, `pyproject.toml:195` addopts carry `-p no:postgresql` only
and no `--max-worker-restart`, `testpaths = ["tests"]` makes `tests/conftest.py` an initial conftest so
Task 2's new `pytest_configure` will in fact be called, and popoto's session teardown flush
(`popoto/pytest_plugin.py`) is wrapped in `except Exception: pass` so a post-release denial cannot
error the session.

### Round 6 (re-critique of the round-5 revision)

Round 6 re-verified the claim-lifecycle substrate and the round-5 terminal-name AST fix against real
source and they hold. Both new blockers are again in the **proof and enforcement machinery**: the
writer-3 binding measurement was executed on current `main` this round and PASSED (so it cannot go
red), and an independent AST sweep implementing the plan's own permit rules found one construction
site absent from the round-5 satisfiability inventory.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | **The writer-3 binding measurement is already GREEN on `main` at the observation point the plan still names.** Success Criteria `:997-1002` and `:1008` (and Tasks 2/9) site the proof "inside a test", but `tests/conftest.py:615-616` has by then replaced `rdb.POPOTO_REDIS_DB` with `redis.Redis(db=claim_test_db())`, so the assertion holds on `main` today and can never go red. Re-verified this round by direct probe on current `main`: `PROBE in-test: POPOTO_REDIS_DB db=1 claim_test_db()=1 POPOTO_TEST_DB=None` — 1 passed. Round 5 raised this as BLOCKER 1; revision commit `301240dd2` touched Task 6 only and the wording at `:997`/`:1008` is unchanged. | ⏸ DEFERRED (round-6 scope ruling: settled or deferred by team-lead; does not gate BUILD) | Delete the "inside a test" / "inside any test" siting from the Success Criterion and from Tasks 2 and 9. Site the writer-3 proof where the plugin's client is actually observable: (a) inside Task 2's session-scoped client-ownership fixture, `rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs["db"] in claimed_test_dbs()` (round 5 measured `db=15` vs `claim=1` there), and (b) `assert os.environ["POPOTO_TEST_DB"] == str(claim_test_db())`, red on `main` because the var is unset (probe confirms `POPOTO_TEST_DB=None`). Never read `rdb.POPOTO_REDIS_DB` from a function-scoped test body for this proof — `tests/conftest.py:615-616`, restored at `:639-641`, makes that a measurement of this repo's own fixture, not of the plugin. |
| BLOCKER | History & Consistency | **Task 6's satisfiability inventory is incomplete for the fourth round running, so the green-state gate is again unreachable.** `tests/integration/test_notify_isolation.py:62-69` builds `redis.Redis(..., db=int(kw.get("db", 0) or 0))` from `kw = popoto_client.connection_pool.connection_kwargs` (`:61`) — exactly the `connection_kwargs` third derivation route Task 6 rejects — and the file appears nowhere in the plan (Test Impact, allowlist table, or the `:1491-1513` inventory). Verified by an independent AST sweep over `tests/**/*.py` implementing the plan's own permit rules (terminal-name matching, one assignment hop, rules a/b/c, four seeded allowlist rows): every other site resolves as the inventory claims; this is the sole unaccounted-for one. | ✅ RESOLVED by the round-7 revision. Task 4 converts `tests/integration/test_notify_isolation.py:61-65` to `db=claim_test_db()` (host/port/auth still from `kw`; behavior-preserving per the `:53-57` docstring), and the conversion is explicitly marked as surviving the Task 6 descope. Task 6's inventory no longer exists to be incomplete: the AST guard is DESCOPED to #2656, which carries this site as finding (c) so the follow-up starts from a complete picture | Convert it in Task 4 and add it to Task 6's inventory. In `_raw_client` (`tests/integration/test_notify_isolation.py:52-69`) replace `db=int(kw.get("db", 0) or 0)` with `db=claim_test_db()` (import from `tests.db_claim`), keeping the host/port/auth reads from `kw` — the docstring at `:53-57` already states the db number is irrelevant for pub/sub delivery, so the change is behavior-preserving. An allowlist entry is FORBIDDEN by the plan's own "no allowlist entry may name a db in `[1..TEST_DB_POOL_MAX]`" rule: post-Task-2 the value read from `connection_kwargs` is the claimed pool slot, not db 0. Add the inventory row `tests/integration/test_notify_isolation.py:62 \| Task 4 converts`. |
| CONCERN | Risk & Robustness | **Task 2's session-scoped client-ownership check still declares no dependency on the fixture that makes it true** (`:1174-1180` says only "ordered after the claim"). popoto's session-scoped autouse `_popoto_test_db` (`popoto/pytest_plugin.py:138-164`, `_swap_db(test_db)` at `:164`) is what puts `POPOTO_REDIS_DB` on the claimed db, and the two are ordered only by fixture-collection nodeid (plugin `""` before conftest `"tests"`). If that inverts, the assertion fires inside a session-scoped autouse fixture and errors every test in the process in setup — the same shape as round-3 blocker 1 and round-4 blocker 1. Round-5 CONCERN, still `pending`. | ⏸ DEFERRED (round-6 scope ruling: settled or deferred by team-lead; does not gate BUILD) | Write the signature as `def _client_ownership_check(_popoto_test_db):` — `_popoto_test_db` is session-scoped autouse at `popoto/pytest_plugin.py:138` and is requestable by name, which forces pytest to order the check after `_swap_db` by dependency rather than by nodeid accident. Keep the `if client is None: continue` guard: `_POPOTO_ASYNC_REDIS_DB` is `None` at session-fixture setup (`popoto/pytest_plugin.py:214,217`) though it is a live `db=N` client inside a test body (`tests/conftest.py:631`). |
| CONCERN | History & Consistency | **Round 5's still-`pending` `--max-worker-restart=0` remedy contradicts a documented repo decision the plan never cites.** `pyproject.toml:190-194` records "NOT used: --max-worker-restart=0" with a negative control (`os._exit(1)` mid-run showed xdist restarts the worker and the run finishes in ~3 s, so worker death was never the #2535 wedge). A revision pass reading the round-5 row at face value would silently overwrite that experimental result. | ⏸ DEFERRED (round-6 scope ruling: settled or deferred by team-lead; does not gate BUILD) | Mark the round-5 row `⏸ DEFERRED` citing `pyproject.toml:190-194` as the prior decision, and fix the claim the finding actually invalidates: reword Risk 2's "the abort is **once per process**" (`:786`) and the Success Criterion "Pool exhaustion is paid **once per process**" (`:1006`) to "once per worker process, up to `--max-worker-restart` replacements (default `numprocesses * 4`)". Do not add the flag in this PR. |
| CONCERN | History & Consistency | **`revision_applied: true` certifies a revision pass that did not happen.** Frontmatter `:4-5` declares the revision applied (and the substrate carries `plan_revising: false`), but four of round 5's six findings — including BLOCKER 1 — are still `pending` in the Critique Results table, and the only revision commit (`301240dd2`) addressed Task 6 alone. That flag is what the router's build gate reads. | ⏸ DEFERRED (round-6 scope ruling: settled or deferred by team-lead; does not gate BUILD) | A row's `Addressed By` cell must never remain `pending` once `revision_applied: true` is written; the only legal terminal values are `✅ RESOLVED` (with the task body actually carrying the instruction) or `⏸ DEFERRED` (with a stated reason). Make it mechanical in the revision pass: grep the `## Critique Results` section for `\| pending \|` and refuse to set `revision_applied: true` while any match remains. |
| CONCERN | Scope & Value | **The `connection_kwargs` AST leg still ships on a rationale the plan retracted two rounds ago, and the conftest recommendation it contradicts is still standing.** Task 6 `:1468-1470` justifies the leg as "it reads a value the popoto plugin can and does mutate ... so it is unsound", but post-Task-2 that value IS the claimed db; meanwhile `tests/conftest.py:511-513` still recommends the same derivation as the sanctioned subprocess pattern and names a call site (`_run_cli_hook` in `test_tool_budget_enforcement.py`) that no longer contains it. | ⏸ DEFERRED (round-6 scope ruling: settled or deferred by team-lead; does not gate BUILD) | Keep the leg — this round found a second live instance of the pattern (`tests/integration/test_notify_isolation.py:61-65`), which strengthens it — but restate the basis as "`claim_test_db()` must be the single source of the number", not "the value is wrong". Delete the stale `tests/conftest.py:511-513` comment in Task 4; both files are already in the PR diff, so this adds no blast radius. |
| NIT | Risk & Robustness | `_test_db_claim_release` is a session-scoped autouse fixture in `tests/conftest.py:516-520` (nodeid `tests`), so it tears down BEFORE popoto's `_popoto_test_db` (nodeid `""`). Under the fail-closed guard the claimed set is empty by then, so popoto's session teardown `flushdb()` (`popoto/pytest_plugin.py:171`) is denied — swallowed at `:172-173`, as the round-5 structural check noted, but the consequence is the claimed db is left unflushed at session end. | ⏸ DEFERRED (round-6 scope ruling: settled or deferred by team-lead; does not gate BUILD) | n/a (NIT) |
| NIT | Scope & Value | Appetite (`:358-366`) still declares `Size: Medium` / "Review rounds: 1" for a 1950-line plan carrying 11 tasks and six critique rounds, so it cannot be used to budget the build. | ⏸ DEFERRED (round-6 scope ruling: settled or deferred by team-lead; does not gate BUILD) | n/a (NIT) |

**Round-6 structural check:** all four repo-mandated sections present and substantive (Documentation,
Update System, Agent Integration, Test Impact); 11 tasks with no numbering gaps; every `Depends On` id
resolves with no cycles; both prerequisites PASS (redis `PONG`, `scripts/check-interpreter-pin.sh`
exit 0); every cited file exists except `docs/features/test-db-ownership.md` and
`tests/integration/test_youtube_search_live.py`, which this plan creates. Independently verified this
round: `config.workerinput` is set at `xdist/remote.py:424` **before** `pytest_cmdline_main` at `:427`,
so Task 2's worker branch does see it at `pytest_configure` time; popoto 1.8.0 resolves
`POPOTO_TEST_DB` at fixture setup (`popoto/pytest_plugin.py:147-152`); the six `from_url(redis_test_url)`
sites the inventory calls out-of-reach are exactly six.

### Round-7 revision: Task 6 descoped to #2656; the rotation fix ships

Six critique rounds verified the substrate — Tasks 1-5 and 7-11 — and it holds. Every round from 4
onward spent its sole remaining blocker on the same task: the AST recurrence guard's satisfiability
inventory. That guard prevents a *future* regression; it stops none of the three rotation writers.
Holding a measuring-instrument fix behind a regression-prevention backstop was the wrong trade, so
this revision does exactly two things and nothing else.

**1. Task 6 is descoped to #2656.** The task body is replaced with a descope marker; task numbering
is deliberately **not** renumbered (Tasks 1-5 and 7-11 keep their identifiers, `Task ID`s, and
`Depends On` edges, so no cross-reference in this plan or its critique history has to be re-read).
`build-recurrence-guard` is removed from Task 9's `Depends On`. The Verification row that gated on
the guard is replaced with a per-site diff review of the converted call sites. Issue **#2656** carries
all five findings: the vacuously-green `node.func.id` matcher (zero sites would have matched, because
every construction site in `tests/` is attribute-qualified), the correct `_callee_name` terminal-name
fix, the `test_notify_isolation.py:61-65` site, the six out-of-reach non-literal
`from_url(redis_test_url)` sites, and the `[1..TEST_DB_POOL_MAX]` allowlist invariant.

**2. `tests/integration/test_notify_isolation.py:61-65` is folded into Task 4** —
`db=int(kw.get("db", 0) or 0)` becomes `db=claim_test_db()`, host/port/auth still read from `kw`,
behavior-preserving per the `:53-57` docstring. This survives the descope on purpose: it is a genuine
unowned-db construction, and the guard that used to cover it is gone, so the call-site conversion is
now the entire fix.

**What was deliberately NOT reopened.** Blocker 3's deny half (a `SimpleNamespace` stub with zero
Redis contact and an inverted red-on-`main` leg — failure is the *absence* of a `RuntimeError`,
empirically validated on `main`) holds. Blocker 1's `None`-skip guard for `_POPOTO_ASYNC_REDIS_DB`
holds. Neither was restructured, re-argued, or "improved".

**Hard requirements re-confirmed intact after the edits.** (a) The binding pass condition never
executes a flush against a db this process does not own, on any branch or in any repo state — Task 3's
deny proof is stub-only and Task 9's capture instruction is unchanged. (b) The popoto fix is sited at
the connection/claim layer (`POPOTO_TEST_DB` export from this repo's own `pytest_configure`, plus the
plugin-agnostic runtime client check), never by patching the installed plugin, so it survives a popoto
upgrade. (c) The `== claim_test_db()` drift detector remains a real test, not prose — Task 2 states
explicitly that a build landing the `in claimed_test_dbs()` half and leaving the equality as a comment
has not landed the drift detector. It is the entire basis for the upgrade-durability claim, and the
descope does not touch it.

No critique row remains `pending`; every row now terminates in ✅ RESOLVED, ⏸ DEFERRED, or ⏸
SUPERSEDED with a stated reason.

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
   guard, now descoped to **#2656**, closes the same hole one layer earlier, at *construction* time,
   for zero runtime cost. Revisit only if a cross-db non-flush write is actually observed.
3. **Verification quiescence — DECIDED: no quiescence, and no double-run gate.** A quiesced
   double-run is the one configuration in which this bug provably cannot fire (the root cause needs
   a second live pytest process; under quiescence there is no victim), so it would have been an
   unfalsifiable pass condition. It also contradicts the repo's tooling, which is deliberately built
   to spare sibling runs. Replaced by two ~1 s adversarial tests, one per rotation writer, each red
   on `main`. The double-run survives only as an optional post-merge diagnostic soak, run *with*
   siblings present. Full reasoning in Risk 4.
