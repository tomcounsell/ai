---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-02
tracking: https://github.com/tomcounsell/ai/issues/2771
last_comment_id:
---

# Consolidate the two owners of "which db popoto is on" in tests/conftest.py

## Problem

Two independently-maintained mechanisms each believe they own the fact "which Redis db is popoto pointed at right now", and they disagree about *how* to own it.

1. **popoto's bundled `pytest11` plugin** (`.venv/lib/python3.14/site-packages/popoto/pytest_plugin.py`) swaps the connection pool **in place** on the existing `POPOTO_REDIS_DB` object (`_swap_db`, lines 100-116). Since #2683 it is driven by `POPOTO_TEST_DB`, which `tests/conftest.py::pytest_configure` exports before collection.
2. **`tests/conftest.py`'s `redis_test_db` autouse fixture** (line 914) throws a **new** `redis.Redis` object at `rdb.POPOTO_REDIS_DB` (line 964), then spends 20 lines cleaning up after that choice: a memoized `sys.modules` walk (`_popoto_modules_with_redis_db`, lines 659-710) that re-points every popoto submodule's stale `POPOTO_REDIS_DB` binding (lines 973-976), plus an eager `aioredis.Redis(...)` assignment to `rdb._POPOTO_ASYNC_REDIS_DB` (line 980), plus a three-way restore at teardown (lines 987-990).

Every line in that second list exists *only* because conftest replaces the object instead of mutating it. Object replacement is what strands the submodule bindings; the repatch loop is the cleanup; the module cache is the performance patch on the cleanup; and the cache's two-signal invalidation (with its own dedicated test class) is the correctness patch on the performance patch. Four layers of machinery deriving from one avoidable decision.

**Current behavior:**

- The two owners can drift. This is the exact failure shape #2628 exists to remove: a call site re-derives an answer instead of asking the single source of truth.
- The eager async bind at line 980 is not merely redundant, it is **actively wrong**. `get_async_redis_db()` (`popoto/redis_db.py:216-270`) deliberately mirrors the *current sync client's* `connection_pool.connection_kwargs` rather than re-reading `REDIS_URL`, precisely so the async path follows any runtime swap. The plugin's `_popoto_reset_async` nulls the global at setup *and* teardown of every test specifically so the client is built lazily **inside the test's own event loop** — its docstring says "a client built now would be bound to the wrong loop." conftest builds one in a synchronous setup context, which is the one thing that fixture exists to prevent. The recon's live ordering probe confirmed conftest sets up *after* the plugin, so the wrong-loop client is the one tests actually get.
- Object replacement silently drops popoto's connection cap. popoto builds `POPOTO_REDIS_DB` on a `redis.BlockingConnectionPool` with `max_connections=128` (`redis_db.py:127-160`) to stop `MaxConnectionsError` under `asyncio.gather` bursts. conftest's bare `redis.Redis(host=..., port=..., db=...)` gets an unbounded plain `ConnectionPool`, so the entire suite runs without that protection.
- The mechanism is expensive to reason about: `docs/features/test-isolation-hardening.md` spends its opening three paragraphs, and `tests/unit/test_conftest_isolation_guards.py` spends a whole test class, explaining a cache whose only consumer is a loop that should not exist.

**Desired outcome:**

One object, one authority per fact. `tests/conftest.py` remains the sole owner of **server resolution** (host/port, from `tests/db_claim.py` — this is load-bearing and cannot move, see the Technical Approach). popoto's plugin remains the sole owner of the **db number** (via the `POPOTO_TEST_DB` export it already honours). conftest mutates the canonical client's pool in place instead of replacing the object, and consequently:

- the submodule repatch loop, the module cache, and the cache's invalidation tests are deleted as dead by construction;
- the eager async bind is deleted, handing `_POPOTO_ASYNC_REDIS_DB` back to the plugin's lazy in-loop path (a behavioural improvement, not just a deletion);
- popoto's `BlockingConnectionPool` and its 128-connection cap survive into the test session.

## Freshness Check

**Baseline commit:** `ebe0105b4` (plan time, 2026-09-02)
**Issue filed at:** 2026-08-13T07:35:32Z
**Recon audited at:** `3b6eb651b` (2026-09-02, same day as this plan)
**Disposition:** Unchanged

The issue's `## Recon Summary` was itself written today at `3b6eb651b`, and it already
absorbed the drift that had accumulated between filing and recon. This check therefore
verifies the recon's own pointers, not the stale ones in the issue's Background section.

**File:line references re-verified (against `ebe0105b4`):**

| Recon claim | Status |
|---|---|
| `tests/conftest.py:964` — `rdb.POPOTO_REDIS_DB = test_client` (object replacement) | Holds, exact line |
| `tests/conftest.py:973-976` — submodule repatch loop | Holds, exact lines |
| `tests/conftest.py:980` — eager `rdb._POPOTO_ASYNC_REDIS_DB = aioredis.Redis(...)` | Holds, exact line |
| `tests/conftest.py:659-710` — `_popoto_modules_with_redis_db` + two-signal cache | Holds (comment block starts 659, function ends 710) |
| `tests/conftest.py:987-990` — three-way teardown restore | Holds, exact lines |
| `tests/conftest.py:887-911, 960-963` — `_assert_client_matches_claim_registry` and its call | Holds, exact lines |
| `tests/conftest.py:277-305` — `_export_claimed_redis_url` (#2805) | Holds, exact lines |
| `tests/conftest.py:323` — `os.environ["POPOTO_TEST_DB"] = str(db)` | Holds, exact line |
| `tests/conftest.py:738-771` — `_popoto_client_ownership_check` | Holds, exact lines |
| `popoto/pytest_plugin.py:100-116` — `_swap_db` in-place pool swap | Holds |
| `popoto/redis_db.py:216-270` — `get_async_redis_db()` mirrors the sync kwargs | Holds |
| `tests/unit/test_test_redis_server_resolution.py:44-54` — async test skips on `None` | Holds, exact lines |
| `tests/unit/test_conftest_isolation_guards.py:264,339,375` (cache tests) | Minor drift: the class now spans ~240-360; the cache-invalidation tests are at 264 and 339, and line 375 is inside `TestPopotoSplitBrainRoundTrip`'s docstring rather than a cache test. Claim unaffected. |

**Cited sibling issues/PRs re-checked:**

- **#2628** — CLOSED. The ownership-enforcement work whose No-Gos deferred this item.
- **PR #2683** (merged 2026-08-13, "Enforce test-DB ownership so the unit suite stops rotating") — the deferral's origin. Its No-Gos entry lives at `docs/archive/plans-completed/suite-failure-rotation-db-ownership.md:909-913` (the plan has been archived out of `docs/plans/` since the issue cited it) and names this work `[TRACKED → #2771]`.
- **#2799** — CLOSED 2026-08-31, merged as `ff20e0311`. **This is the one that changed the answer**, and the recon caught it: `redis_test_db` now resolves host/port through `db_claim` and asserts the client against the claim registry. It creates a new, load-bearing reason conftest builds its own connection kwargs, which is why full delegation to the plugin is *not* the right consolidation direction.
- **#2805** — CLOSED, merged as `d59f6509c` (PR #2958, 2026-08-25). Adds the process-wide `REDIS_URL` export. Its own docstring concedes it does not reach this process's popoto client, so it neither helps nor blocks this work.
- **#2763 / PR #2786** — CLOSED/merged. Subprocess db inheritance via `db_claim.subprocess_env`. Untouched by this plan.
- **#2655 / PR #2700** — merged. The AST recurrence guard against tests deriving their own `db=`. Relevant as a standing check that must stay green.
- **#2680** — merged. Four-layer production-flush hardening, including the flush ownership guard installed at `tests/conftest.py:253`. This plan must not weaken it.
- **#2037** — CLOSED. The original create-then-filter split-brain whose regression test (`TestPopotoSplitBrainRoundTrip`) this plan rewrites rather than deletes.
- **#2770** — the upstream-popoto sibling of this issue (plugin-side hardening); it lives in `~/src/popoto`, not here.

**Commits on main since the recon baseline touching referenced files:** none.
`git log 3b6eb651b..HEAD -- tests/conftest.py tests/db_claim.py tests/unit/test_conftest_isolation_guards.py tests/unit/test_test_redis_server_resolution.py` returns empty.

**Active plans in `docs/plans/` overlapping this area:** none. The nearest neighbours
(`overclaim-guard-greps-whole-worktree`, `fix-red-main-unit-tests`,
`module-scope-env-reads-migration`) touch the test suite but not popoto client
construction or `redis_test_db`.

**Notes:** This is not a bug fix in the "reproduce the symptom" sense — nothing is
currently failing. It is a correctness-and-consolidation chore whose *observable*
defects (wrong-loop async client, dropped connection cap) were established by the
recon's live probe rather than by a red test. That probe's finding is restated as
spike-1 below and must be re-confirmed by the builder before the deletions land.

## Prior Art

Six merged PRs have shaped `redis_test_db` and its neighbours. None of them attempted this
consolidation — every one of them *added* a layer, and #2683 explicitly deferred the removal.

- **PR #2061** (2026-07-13) *Fix xdist test-isolation flakes: popoto db-cache split-brain + agent-hooks corruption* — introduced the two-signal invalidation on `_popoto_modules_with_redis_db`'s cache. Succeeded at what it set out to do. This plan deletes the cache outright, which subsumes rather than contradicts it.
- **PR #2117** (2026-07-16) *Fix cross-process Redis test-db collision (#2060)* — replaced the `gw{N}->db{N+1}` derivation with the flock-backed per-process claim in `tests/db_claim.py`. Established `claim_test_db()` as the single authority for the db *number*.
- **PR #2680** (2026-08-13) *Harden production Redis against accidental flush (four layers)* — installed the flush ownership guard at `tests/conftest.py:253` and `tools/redis_flush_guard.py`. Constrains this work: whatever client conftest ends up with must still be on a claimed db before the first `flushdb()`.
- **PR #2683** (2026-08-13) *Enforce test-DB ownership so the unit suite stops rotating (#2628)* — moved the claim into `pytest_configure` and added the `POPOTO_TEST_DB` export that made the plugin honour this process's db. **This is the PR that created the two-owner condition and consciously deferred resolving it** (round-4 review concern 2: removing the repatch loop is a different blast radius from the flush guard).
- **PR #2700** (2026-08-13) *Recurrence guard: detect tests that derive their own Redis db= (#2655)* — AST guard in `tests/db_derivation_guard.py`. A standing constraint: the new conftest body must not trip it.
- **PR #2786** (2026-08-13) *Make test subprocesses inherit the parent's claimed test DB (#2763)* — `db_claim.subprocess_env`. Orthogonal; this plan does not touch the subprocess path.
- **PR #2958** (2026-08-25) *Pytest exports its own REDIS_URL; the line-keyed ALLOWLIST guard is deleted (#2805)* — process-wide `REDIS_URL` export. Explicitly documents that it cannot reach this process's own popoto client.
- **`ff20e0311`** (#2799, closed 2026-08-31) *Connect to the Redis server the db-claim registry names* — routed conftest's client through `db_claim.redis_test_host()/redis_test_port()` and added `_assert_client_matches_claim_registry`. **The most important precedent here**: it is the reason conftest cannot simply hand db-and-server resolution to the plugin.

**Upstream sibling:** #2770 tracks the mirror-image hardening inside `~/src/popoto` itself.
This plan deliberately requires no upstream change (see No-Gos).

## Research

**Queries used:**
- `redis-py swap connection_pool on existing Redis client in place safe disconnect`

**Key findings:**

1. **There is no public API for hot-swapping `connection_pool`; the attribute assignment is the sanctioned-by-practice route, and its one hazard is the disconnect timing.** Assigning `client.connection_pool = new_pool` affects only *subsequent* `get_connection()` calls — commands already holding a checked-out connection keep using the old pool, so calling `old_pool.disconnect()` immediately "will break commands still using them." Source: [redis-py connections docs](https://redis.readthedocs.io/en/stable/connections.html), [redis-py #932](https://github.com/andymccurdy/redis-py/issues/932).
   **How it informs the plan:** popoto's `_swap_db` disconnects the old pool immediately, and conftest's new in-place swap will do the same. That is safe *here specifically* because both run in pytest's synchronous fixture-setup phase, where no test code holds a connection — but the plan makes that precondition explicit in the docstring and in Race 1 rather than leaving it as an accident. It also means the swap must never be moved into a test body or an async context.

2. **`redis.Redis(connection_pool=pool)` does not take ownership of the pool; `Redis.from_pool()` does.** A client built with the plain constructor "will not close it," so the pool can be safely shared across clients. Verified locally: `redis.Redis(connection_pool=p).auto_close_connection_pool` is `False`. popoto builds `POPOTO_REDIS_DB = redis.Redis(connection_pool=pool)` (`redis_db.py:148,158`) — the plain form.
   **How it informs the plan:** the current teardown calls `test_client.close()` (conftest:986). Under in-place mutation that call would target the *canonical shared* client. Because the client does not own its pool, `close()` is not catastrophic — but it is meaningless, and the plan drops it in favour of explicitly disconnecting the pool object conftest itself created. Source: [redis-py connections docs](https://redis.readthedocs.io/en/stable/connections.html), [redis-py #2901](https://github.com/redis/redis-py/issues/2901).

3. **`max_connections` is not carried in `connection_kwargs`** — verified directly in this repo's venv: a `BlockingConnectionPool(max_connections=128)` reports `connection_kwargs == {db, host, port, socket_timeout}`. It is a pool-constructor argument, not a connection argument.
   **How it informs the plan:** this is why popoto's own `_swap_db` silently downgrades the pool. It reads `current_kwargs = dict(db_obj.connection_pool.connection_kwargs)` and rebuilds with `redis.ConnectionPool(connection_class=..., **current_kwargs)` — preserving the *connection* class but neither the `BlockingConnectionPool` *pool* class nor the 128-connection cap. The plugin's session-scoped `_popoto_test_db` therefore already strips the cap before conftest runs, so conftest cannot simply "preserve what's there": it must reconstruct a `BlockingConnectionPool` and read the cap from `popoto.redis_db._SYNC_MAX_CONNECTIONS` (which is itself `POPOTO_SYNC_MAX_CONNECTIONS`-overridable). Restoring the cap is a genuine side-benefit of this work, not a no-op.

4. **`redis.asyncio` has no GC fallback for pool cleanup** — an async pool must be explicitly `await`-disconnected. Source: [redis-py asyncio examples](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html).
   **How it informs the plan:** the current conftest creates an `aioredis.Redis` per test at line 980 and never awaits a disconnect on it — it merely rebinds the global at teardown. Deleting that line removes a per-test async pool leak in addition to the wrong-loop bug.

Sources:
- [Connecting to Redis — redis-py docs](https://redis.readthedocs.io/en/stable/connections.html)
- [Asyncio Examples — redis-py docs](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)
- [redis-py #932 — Graceful reconnection with connection pooling](https://github.com/andymccurdy/redis-py/issues/932)
- [redis-py #2901 — `auto_close_connection_pool` ignored](https://github.com/redis/redis-py/issues/2901)

## Spike Results

TBD

## Data Flow

TBD

## Architectural Impact

TBD

## Appetite

TBD

## Prerequisites

TBD

## Solution

TBD

## Failure Path Test Strategy

TBD

## Test Impact

- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPopotoModuleCacheInvalidation` (whole class, ~lines 240-360: `_snapshot_and_restore_cache_globals`, `test_identity_divergence_forces_rebuild_with_len_unchanged`, `test_len_branch_catches_a_brand_new_never_cached_holder`) — DELETE: these test `_popoto_modules_with_redis_db()`'s two-signal cache, which this plan deletes. A cache that no longer exists cannot have an invalidation bug; keeping the tests would require keeping the helper alive solely for them.
- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPopotoSplitBrainRoundTrip::test_create_then_filter_split_brain_and_fix` (~lines 386-448) — UPDATE: keep Step 1 (divert `popoto.models.query`'s local binding, prove the create-then-filter round trip misses) verbatim; replace Step 2's `for mod in _conftest._popoto_modules_with_redis_db(): mod.POPOTO_REDIS_DB = correct_test_client` with a single restore to the canonical object (`query_module.POPOTO_REDIS_DB = rdb.POPOTO_REDIS_DB`). The #2037 mechanism is still reproduced; the fix path becomes "there is one canonical client object and every binding is it", which is exactly the invariant this plan establishes. Also drop the bare `redis.Redis(db=...)` clients in favour of `db_claim.redis_test_host()/redis_test_port()` so the file stops carrying a latent #2799 shape.
- [ ] `tests/unit/test_test_redis_server_resolution.py::TestServerResolutionIsSingleSourced::test_async_client_matches_the_sync_client` (lines 44-54) — REPLACE: it reads `rdb._POPOTO_ASYNC_REDIS_DB` directly and `pytest.skip`s when it is `None`. Once the eager async bind is deleted, `None` is the steady state and this test becomes a permanent silent skip. Rewrite it as an async test that `await`s `rdb.get_async_redis_db()` inside the test's own loop and asserts host/port/db match the sync client's `connection_pool.connection_kwargs` — which tests the real contract (the async client mirrors the sync one) instead of an artefact of the fixture.
- [ ] `tests/unit/test_conftest_isolation_guards.py:1271-1299` (`_POPOTO_ASYNC_REDIS_DB` is `None` at session scope / ownership-check loop) — UPDATE (assertion strengthening, not repair): these already encode the post-change truth. Add a case asserting that after `redis_test_db` setup the async global is *still* `None`, which is the regression fence against a future re-introduction of an eager bind.
- [ ] New test file `tests/unit/test_popoto_client_identity.py` — ADD: three assertions that only hold under in-place mutation — (a) `rdb.POPOTO_REDIS_DB` is the *same object* across two tests in the same process, (b) every module in `sys.modules` whose name starts with `popoto` and which has a `POPOTO_REDIS_DB` symbol holds that same object, (c) the live pool is a `redis.BlockingConnectionPool` with `max_connections` matching popoto's `_SYNC_MAX_CONNECTIONS`.
- [ ] ~197 call sites across `tests/unit/` and `tests/integration/` request `redis_test_db` by name — NO CHANGE: the fixture keeps its name, its `autouse=True`, and its function scope. Only its body changes. Any diff that renames or rescopes it is out of contract.

## Rabbit Holes

TBD

## Risks

TBD

## Race Conditions

TBD

## No-Gos (Out of Scope)

TBD

## Update System

TBD

## Agent Integration

TBD

## Documentation

### Feature Documentation
- [ ] Update `docs/features/test-isolation-hardening.md` — its opening section (lines 9-11) documents `_popoto_modules_with_redis_db()`'s two-signal cache as load-bearing ("Neither branch may be dropped"). That statement becomes false when the helper is deleted. Replace it with the new invariant: there is exactly one `POPOTO_REDIS_DB` object per process, conftest mutates its pool in place, and no submodule binding ever needs re-pointing. Line 60's "authoritative mechanism explanations" list and line 64's "Test B" description must lose their references to the deleted helper and its tests.
- [ ] Update `docs/features/test-db-ownership.md` — describe the split ownership explicitly: `tests/conftest.py` owns *server resolution* (host/port, via `tests/db_claim.py`), popoto's bundled plugin owns the *db number* (via the `POPOTO_TEST_DB` export). Lines 113 and 119 already state the `_POPOTO_ASYNC_REDIS_DB is None` truth; extend them to say the async client is now built lazily in-loop by `get_async_redis_db()` and that conftest never binds it.
- [ ] No new `docs/features/` file and no `docs/features/README.md` index row — this is a consolidation inside two existing documented features, not a new capability.

### Inline Documentation
- [ ] Rewrite the `redis_test_db` docstring. Its current "CRITICAL: We replace the POPOTO_REDIS_DB object with a new Redis client" paragraph (conftest:928-934) becomes the exact opposite of the code. The replacement must state why the swap is in-place (submodule bindings follow for free), why SELECT is still not used (pool recycling), and why conftest still builds the connection kwargs itself rather than delegating to the plugin's `_swap_db` (host/port, #2799).
- [ ] Update `tests/db_claim.py`'s module docstring (line 4: "``tests/conftest.py``'s autouse ``redis_test_db`` fixture points Popoto at that db") and `redis_test_host`'s docstring (line 111: "the raw ``redis.Redis`` in ``conftest``'s ``redis_test_db`` fixture") — both describe the object-replacement shape that this plan removes.
- [ ] Delete the `_POPOTO_MODULE_CACHE` comment block (conftest:659-666) along with the helper.

## Success Criteria

TBD

## Team Orchestration

TBD

## Step by Step Tasks

TBD

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `./scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py tests/unit/test_test_redis_server_resolution.py tests/unit/test_popoto_client_identity.py tests/unit/test_redis_flush_guard.py -q` | exit code 0 |
| Full unit suite passes under xdist | `./scripts/pytest-clean.sh tests/unit/ -q` | exit code 0 |
| Integration suite passes | `./scripts/pytest-clean.sh tests/integration/ -q` | exit code 0 |
| Serial run also passes (no xdist masking) | `./scripts/pytest-clean.sh tests/unit/test_conftest_isolation_guards.py -p no:randomly -n0 -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| ANTI: conftest never reassigns the popoto sync global | `grep -cE '^\s*rdb\.POPOTO_REDIS_DB\s*=' tests/conftest.py` | match count == 0 |
| ANTI: conftest never writes the async global | `grep -cE 'rdb\._POPOTO_ASYNC_REDIS_DB\s*=' tests/conftest.py` | match count == 0 |
| ANTI: the submodule repatch helper is gone repo-wide | `grep -rc '_popoto_modules_with_redis_db' tests/` | match count == 0 |
| ANTI: no test skips on a `None` async client | `grep -rn 'popoto exposes no async client' tests/` | exit code 1 |
| Live client identity holds (one object, all bindings) | `./scripts/pytest-clean.sh tests/unit/test_popoto_client_identity.py -q -p no:randomly -n0` | exit code 0 |
| Live pool keeps popoto's connection cap | `./scripts/pytest-clean.sh tests/unit/test_popoto_client_identity.py::test_pool_is_blocking_with_popoto_cap -q -n0` | exit code 0 |
| db-derivation recurrence guard (#2655) still clean | `./scripts/pytest-clean.sh tests/unit/test_db_derivation_guard.py -q -n0` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

TBD
