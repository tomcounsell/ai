---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-02
tracking: https://github.com/tomcounsell/ai/issues/2771
last_comment_id: 5505101875
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

**Issue comments incorporated (through `last_comment_id: 5505101875`):**

- **Comment 5281092164** (tomcounsell, 2026-08-13) — a blocking audit note: *"this issue's stated precondition is not yet true on `main`. Do not start it before PR #2683 merges, or it will consolidate onto an authority that does not exist."* At the time, `tests/conftest.py` had no `pytest_configure` and therefore never exported `POPOTO_TEST_DB`. **This precondition is now satisfied**: PR #2683 merged 2026-08-13 and `pytest_configure` is at `tests/conftest.py:308` with the export at `:323`, verified at the plan baseline. The comment also correctly separates #2770 (upstream popoto, the hardcoded db-15 default) from #2771 (this downstream cleanup); the No-Gos honour that split. The block is cleared and the work is startable.
- **Comment 5505101875** (2026-09-02) — the `## Recon Summary`, mirrored into the issue body and absorbed throughout this plan.

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

### spike-1: Which mechanism wins the per-test race — the plugin's async reset, or conftest's eager bind?
- **Assumption**: "The plugin's `_popoto_reset_async` (which nulls `_POPOTO_ASYNC_REDIS_DB` so the client is rebuilt lazily in the test's own loop) runs after `redis_test_db`, making conftest's eager bind harmless dead weight."
- **Method**: prototype (live probe test run with `-p no:randomly -n0`), performed during recon at `3b6eb651b`
- **Finding**: **Assumption is FALSE.** The probe printed `ASYNC_IS_NONE: False` inside the test body, proving `redis_test_db` sets up *after* `_popoto_reset_async`. The eager, synchronously-constructed, wrong-event-loop client is the one tests actually observe today.
- **Confidence**: high (direct observation, not inference)
- **Impact on plan**: converts the async-bind deletion from "remove redundant code" to "remove a live defect". It also fixes the direction of the change: deleting line 980 hands the binding back to the plugin's lazy in-loop path. The builder must re-run this probe as the RED state before deleting, and re-run it after to see `ASYNC_IS_NONE: True`.

### spike-2: Does the plugin's `_swap_db` preserve popoto's `BlockingConnectionPool` and its 128-connection cap?
- **Assumption**: "`_swap_db` mutates in place and therefore preserves everything about the pool except the db number, so conftest could delegate to it wholesale."
- **Method**: code-read of `popoto/pytest_plugin.py:100-116` plus a direct in-venv check of redis-py's pool attributes
- **Finding**: **No.** `_swap_db` copies `connection_pool.connection_kwargs` and rebuilds with `redis.ConnectionPool(connection_class=..., **kwargs)`. Verified in this repo's venv that a `BlockingConnectionPool(max_connections=128)` reports `connection_kwargs == {db, host, port, socket_timeout}` — `max_connections` is a *pool* argument, not a connection argument, and the pool class itself is hardcoded to plain `ConnectionPool` in `_swap_db`. So the plugin's own session-scoped swap already strips popoto's cap before conftest ever runs.
- **Confidence**: high
- **Impact on plan**: conftest cannot "preserve what's already there" — by the time it runs, the cap is already gone. Its in-place swap must *reconstruct* a `redis.BlockingConnectionPool` and read the cap from `popoto.redis_db._SYNC_MAX_CONNECTIONS`. This turns the recon's "incidental finding 5" into an explicit, tested deliverable, and it means the new `test_pool_is_blocking_with_popoto_cap` assertion is red on main today.

### spike-3: Can the plugin be made the owner of host/port too (full delegation)?
- **Assumption**: "The consolidation direction in the issue body — delete conftest's client construction entirely and let the plugin own everything — is achievable."
- **Method**: code-read of `_swap_db`'s signature, `popoto/redis_db.py:127-160`, `tests/db_claim.py:106-125`
- **Finding**: **No, and attempting it would regress #2799.** `_swap_db` preserves whatever host/port the pool already carries, and that pool was built at popoto *import* time from the ambient `REDIS_URL`. `db_claim` resolves host/port independently from `REDIS_HOST`/`REDIS_PORT`. Under `REDIS_PORT=641x` the two disagree — the claim registry keyed to 641x while the client sits on 6379, flushing production db N. `_popoto_test_db` calls `_swap_db(test_db)` with no host/port extras, so the plugin has no channel through which to learn the registry's server. `_swap_db` *does* accept `**extra_kwargs`, but supplying them requires an upstream popoto change (out of this repo — see #2770).
- **Confidence**: high
- **Impact on plan**: **inverts the issue's stated direction.** The consolidation is not "plugin owns everything"; it is a clean *split by fact*: conftest owns server resolution, the plugin owns the db number. This is the "if not, document why the split is required" branch of the issue's own scope statement, and it is what the Technical Approach implements.

### spike-4: Does `_popoto_flush_db`'s drift re-swap threaten conftest's reconstructed pool?
- **Assumption**: "The plugin's per-test `_popoto_flush_db` will re-swap the pool and undo conftest's work every test."
- **Method**: code-read of `pytest_plugin.py`'s `_popoto_flush_db`
- **Finding**: **No, by construction.** It re-swaps *only* when `connection_kwargs['db'] != _popoto_test_db`. Since `pytest_configure` exports `POPOTO_TEST_DB = claim_test_db()` and conftest swaps to that same `claim_test_db()`, the db never drifts and the re-swap is a permanent no-op. The two mechanisms compose instead of competing.
- **Confidence**: high
- **Impact on plan**: no defensive coordination code is needed. But it *is* a silent coupling — if a future test rebinds the global to a db-0 client, the plugin's recovery re-swap would restore the db while flattening the pool class. The new `test_pool_is_blocking_with_popoto_cap` assertion is the tripwire that would catch it, which is why it is in Success Criteria rather than being a nice-to-have.

## Data Flow

The fact travelling through this system is **"which Redis server and db does popoto write to right now"**. Today it has two independent carriers; after this change it has one object.

**Today (two owners):**

1. **Import time** — `popoto.redis_db` reads the ambient `REDIS_URL` and builds `pool = redis.BlockingConnectionPool(max_connections=128)`, then `POPOTO_REDIS_DB = redis.Redis(connection_pool=pool)`. Every popoto submodule that does `from ..redis_db import POPOTO_REDIS_DB` captures *this object* as a local binding. This happens **before `tests/conftest.py` is imported** (popoto ships a `pytest11` entry point).
2. **`pytest_configure` (conftest:308)** — `claim_test_db()` takes a flock-backed unique db from the machine-global registry, then `os.environ["POPOTO_TEST_DB"] = str(db)` and `_export_claimed_redis_url()`.
3. **Session fixture `_popoto_test_db` (plugin)** — reads `POPOTO_TEST_DB`, calls `_swap_db(db)`: in-place pool rebuild on the canonical object. All submodule bindings follow automatically. **Cap and pool class are lost here** (spike-2).
4. **Session fixture `_popoto_client_ownership_check` (conftest:737)** — depends on `_popoto_test_db`, asserts every live popoto client is on a claimed db.
5. **Per test, plugin first** — `_popoto_reset_async` nulls `_POPOTO_ASYNC_REDIS_DB`; `_popoto_flush_db` re-swaps on drift (no-op) and `flushdb()`s.
6. **Per test, conftest second** — `redis_test_db` resolves host/port from `db_claim`, builds a **brand-new** `redis.Redis`, asserts it against the claim registry, **assigns it over the canonical global** (conftest:964) — at which point every submodule binding is stranded on the old object — then walks `sys.modules` to repair them (973-976), then eagerly builds an `aioredis.Redis` (980), clobbering the plugin's deliberate `None`.
7. **Test body** — sync writes go through whichever binding the calling module captured; async reads go through the wrong-loop client from step 6.
8. **Teardown** — conftest restores three things (987-990); the plugin then nulls the async global again.

**After (one owner per fact):**

1. **Import time** — unchanged.
2. **`pytest_configure`** — unchanged. This remains the single authority for the **db number**, published to the plugin through `POPOTO_TEST_DB`.
3. **Session fixture `_popoto_test_db` (plugin)** — unchanged. Owns applying the db number.
4. **Session fixture `_popoto_pool_install` (conftest, new)** — runs after the plugin's session fixture. Resolves host/port from `db_claim` (the single authority for the **server**) and, **once for the whole session**, builds a `BlockingConnectionPool` with popoto's own cap and assigns it to `rdb.POPOTO_REDIS_DB.connection_pool` **in place**, keeping the object identity. If the pool is already a `BlockingConnectionPool` on the target `(host, port, db)` it does nothing.
5. **Session fixture `_popoto_client_ownership_check`** — unchanged.
6. **Per test, plugin first** — unchanged. It now always sees the installed `BlockingConnectionPool`, because nothing puts the client back on the import-time pool between tests.
7. **Per test, conftest second** — `redis_test_db` runs `_assert_client_matches_claim_registry` against the canonical client and `flushdb()`s it. **No pool work. No submodule walk. No async assignment.**
8. **Test body** — sync writes go through the one canonical object, whatever binding captured it. The first `await get_async_redis_db()` builds the async client lazily *inside the test's own loop*, mirroring the canonical sync client's kwargs.
9. **Per-test teardown** — `flushdb()`. The plugin nulls the async global. Nothing to restore, because per-test setup changed nothing but the data.
10. **Session teardown** — `_popoto_pool_install`'s finalizer restores the pool it displaced and disconnects the one it built. This is the *only* place the pool is ever unwound.

**The invariant this establishes:** `rdb.POPOTO_REDIS_DB` is the same Python object for the entire process lifetime, its `connection_pool` is the same object for the entire session, and `id()` of every `popoto.*` submodule's `POPOTO_REDIS_DB` equals the client. That single sentence replaces the old steps 6-8's four layers of machinery, and it is directly assertable (see Test Impact, new test file).

## Architectural Impact

- **New dependencies:** none. No new imports beyond what `tests/conftest.py` already pulls (`redis`, `popoto.redis_db`, `tests.db_claim`). The `redis.asyncio` import in `redis_test_db` is *removed*.
- **Interface changes:** `_popoto_modules_with_redis_db()` and the `_POPOTO_MODULE_CACHE` / `_POPOTO_MODULE_CACHE_LEN` globals are deleted from `tests/conftest.py`. They are test-suite-internal; the only importers are `tests/unit/test_conftest_isolation_guards.py` (whose consuming tests are deleted or rewritten in the same change). `redis_test_db` keeps its exact name, `autouse=True`, and function scope — the contract ~197 call sites depend on is unchanged.
- **Coupling:** **decreases.** Today conftest is coupled to popoto's *module layout* (it must know which submodules hold a `POPOTO_REDIS_DB` symbol, and re-derive that set whenever `sys.modules` shifts) and to popoto's *async global name*. After the change it is coupled only to `popoto.redis_db.POPOTO_REDIS_DB` and `popoto.redis_db._SYNC_MAX_CONNECTIONS` — two named attributes on one module. A popoto refactor that adds, removes, or renames submodules stops being a test-suite concern.
- **Data ownership:** this is the whole point. Ownership moves from "two mechanisms, overlapping, undocumented" to a declared split: `tests/db_claim.py` owns the db *number* and the *server*; `tests/conftest.py` applies the server; popoto's plugin applies the number; `popoto.redis_db.get_async_redis_db()` owns the async client and derives it from the sync one. Each fact has exactly one writer.
- **Reversibility:** high. The change is confined to one fixture body, one deleted helper, and three test files. `git revert` restores the prior behaviour with no data migration, no config change, and no deploy step. There is no production code in the blast radius at all.
- **Risk concentration:** `tests/conftest.py`'s `redis_test_db` is autouse across the entire suite, so a defect here fails *everything* rather than failing narrowly. That is the argument for the staged task ordering in Step by Step Tasks (prove the invariant with a new test file first, delete second) and for the "serial run also passes" verification row.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully pinned by the issue's recon and the four spikes above)
- Review rounds: 1

The coding is roughly a 30-line net deletion in one fixture plus three test-file edits. The cost is not typing — it is that `redis_test_db` is autouse over the whole suite, so the verification loop is a full `tests/unit/` run (~20 minutes) plus `tests/integration/`, and a mistake surfaces as thousands of errors rather than one. Budget the time for the runs, not the edit.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| A reachable Redis server on the db-claim registry's host/port | `.venv/bin/python -c "import redis, sys; sys.path.insert(0,'.'); from tests import db_claim; redis.Redis(host=db_claim.redis_test_host(), port=int(db_claim.redis_test_port())).ping()"` | The whole change is about which server/db the suite talks to; nothing is verifiable without one |
| popoto installed with its bundled pytest plugin | `.venv/bin/python -c "import popoto.pytest_plugin as p; assert hasattr(p, '_swap_db')"` | The plan makes the plugin the db-number authority; a popoto without `_swap_db` invalidates spikes 2-4 |
| popoto exposes the sync connection cap constant | `.venv/bin/python -c "import popoto.redis_db as r; assert isinstance(r._SYNC_MAX_CONNECTIONS, int)"` | conftest reads the cap from here rather than hardcoding 128 |
| Free slot in the machine-global db-claim pool | `.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from tests.db_claim import claim_test_db; print(claim_test_db())"` | A pool-exhausted machine cannot run the suite at all |
| Venv on the committed interpreter pin | `.venv/bin/python -c "import pathlib,sys; pin=pathlib.Path('.python-version').read_text().strip(); assert '.'.join(map(str,sys.version_info[:2])).startswith(pin.rsplit('.',1)[0]), (pin, sys.version)"` | `scripts/pytest-clean.sh` aborts on an off-pin venv. Scoped deliberately: `python -m tools.doctor` also reports unrelated machine state (e.g. Redis index drift) and would fail this gate for reasons that have nothing to do with this plan |

## Solution

### Key Elements

- **A declared ownership split, written down where the code lives.** `tests/db_claim.py` is the authority for *which db number* and *which server*. popoto's plugin is the authority for *applying the db number* to the client. `tests/conftest.py` is the authority for *applying the server*. `popoto.redis_db.get_async_redis_db()` is the authority for the async client, derived from the sync one. Four facts, four writers, no overlaps. The `redis_test_db` docstring states this split explicitly so the next person does not re-derive it.
- **In-place pool mutation, installed once per session.** conftest stops assigning a new object to `rdb.POPOTO_REDIS_DB` and instead swaps the `connection_pool` attribute on the existing object, from a new session-scoped `_popoto_pool_install` fixture. Object identity is preserved, so every `from ..redis_db import POPOTO_REDIS_DB` binding in every popoto submodule follows for free. `redis_test_db` keeps only per-test hygiene (registry assertion plus the two flushes), so no test ever puts the client back on the plugin's import-time pool.
- **Three deletions that become dead by construction.** The submodule repatch loop (conftest:967-976), the `_popoto_modules_with_redis_db` helper and its two-signal cache (conftest:659-710), and the eager `_POPOTO_ASYNC_REDIS_DB` assignment (conftest:980, with its save at 949 and restore at 988).
- **Pool-class and cap restoration.** The reconstructed pool is a `redis.BlockingConnectionPool` with `max_connections=rdb._SYNC_MAX_CONNECTIONS`, restoring the protection popoto intended and that both current owners were stripping.

  **Resolved (critique round 1): the cap restoration is IN SCOPE, not split out.** Under in-place mutation the pool has to be constructed anyway, so constructing popoto's own pool type is free and constructing a lesser one would be a deliberate regression. It is also the plan's only assertion that is demonstrably RED on `main` today, which is the change's strongest falsifiability evidence. Success Criteria assertion (c), the two cap-specific Verification rows, and the cap half of task 2 are therefore all binding.
- **An identity invariant with a test that can fail.** A new `tests/unit/test_popoto_client_identity.py` asserts the one-object property, the all-bindings-agree property, and the pool-class/cap property. Two of its three assertions are red on main today.

### Flow

Not a user-facing flow. The mechanism flow, as a sequence within one pytest process:

**popoto import (pool built from ambient `REDIS_URL`)** → **`pytest_configure` claims db N, exports `POPOTO_TEST_DB=N`** → **plugin session fixture applies db N in place** → **conftest session fixture `_popoto_pool_install` applies claim-registry host/port in place, once** → **conftest session ownership check asserts db N is claimed** → **per test: plugin nulls async global, flushes** → **per test: conftest asserts registry match, flushes** → **test body: one canonical sync client; async client built lazily in-loop on first `await`** → **per-test teardown: flush; plugin nulls async global** → **session teardown: restore the displaced pool, disconnect the installed one**

### Technical Approach

**1. Split pool ownership from per-test hygiene: install the pool once per session, flush per test.**

The pool swap is a *session*-lifetime concern and must not live in a function-scoped
setup/teardown pair. Critique round 1 established why (BLOCKER, Critique Results row 1): if
the function teardown restores `client.connection_pool = old_pool`, then `old_pool` at the
first swap is the plugin's plain `ConnectionPool` (spike-2 — `_swap_db` strips the
`BlockingConnectionPool` class), so the idempotence guard can never observe "already a
`BlockingConnectionPool` on the target `(host, port, db)`". Every test would rebuild, which
contradicts the steady-state no-op this design depends on. Worse, restoring between tests
parks the canonical client on the plugin's import-time-`REDIS_URL` pool, and the plugin's
`_popoto_flush_db` runs *first* next test and `flushdb()`s whatever host that pool carries.
Spike-4 cleared db-number drift only, never host drift — today's full-object replacement has
no restore-then-reapply cycle, so this hazard would be introduced by the change rather than
inherited.

**1a. New session-scoped fixture `_popoto_pool_install` (`tests/conftest.py`).** Autouse at
session scope, ordered after the plugin's `_popoto_test_db` (depend on it explicitly so the
db number is already applied). It performs the swap exactly once and unwinds exactly once:

- Resolve `test_db = claim_test_db()`, `test_host = db_claim.redis_test_host()`, `test_port = int(db_claim.redis_test_port())`.
- Take `client = rdb.POPOTO_REDIS_DB` (the canonical object; never rebind this name onto the module).
- **Idempotence guard:** if `client.connection_pool` is already a `BlockingConnectionPool`
  whose `connection_kwargs` report the target `(host, port, db)`, do nothing. Normalize types
  on both sides before comparing — a pool built from a URL may carry `port` as `int` while
  `db_claim.redis_test_port()` returns `str`. Under session scoping this guard is genuinely
  reachable: it is what makes a second install attempt (from any future caller) a no-op
  rather than a rebuild.
- Otherwise build `new_pool = redis.BlockingConnectionPool(host=test_host, port=test_port, db=test_db, socket_timeout=5, socket_connect_timeout=5, max_connections=rdb._SYNC_MAX_CONNECTIONS)`, save `old_pool = client.connection_pool`, assign `client.connection_pool = new_pool`, then `old_pool.disconnect()`, and record that an install happened on a session-level installed-flag.
  - Carry forward `password` / `username` from the old `connection_kwargs` if present, mirroring what `_swap_db` preserves — an authenticated `REDIS_URL` must keep working.
  - The immediate `disconnect()` is safe *only* because this runs in pytest's synchronous
    setup phase with no in-flight commands (see Research finding 1 and Race 1). Say so in a
    comment.
- `yield`.
- Session finalizer, and **only** here: if the installed-flag is set, restore
  `client.connection_pool = old_pool` and `new_pool.disconnect()`. Do **not** call
  `client.close()` — the client does not own its pool (Research finding 2) and the object is
  shared.

**1b. Rewrite `redis_test_db`'s body (`tests/conftest.py:914-990`) as per-test hygiene only.**
It keeps its exact name, `autouse=True`, and function scope — the contract ~197 call sites
depend on. It now depends on `_popoto_pool_install` and does no pool work at all:

- Take `client = rdb.POPOTO_REDIS_DB`.
- Call `_assert_client_matches_claim_registry(client, test_host, test_port)` against the
  canonical client — the #2799 guard is preserved verbatim, just pointed at the object
  instead of a replacement. It stays per-test: it is the check that catches drift introduced
  *during* a test.
- `client.flushdb()`.
- `yield`.
- Teardown: `client.flushdb()`. **No pool restore, no `disconnect()`, no object rebinding.**
- Delete the `import redis.asyncio as aioredis` line; nothing in the fixture needs it.

The steady state is therefore exactly what this plan claims: one pool install for the whole
session, and per test nothing but two flushes and one registry assertion.

**2. Delete `_popoto_modules_with_redis_db` and its cache (`tests/conftest.py:659-710`), including the comment block at 659-666.** Its only consumer is the loop deleted in step 1. Leave no compatibility shim.

**3. Rewrite the `redis_test_db` docstring.** The current "CRITICAL: We replace the POPOTO_REDIS_DB object" paragraph becomes false. The replacement must state: (a) the db number comes from the plugin via `POPOTO_TEST_DB`, the server from `db_claim`, and why that split exists (#2799 — `_swap_db` cannot learn the registry's host/port); (b) why the swap is in-place (submodule bindings follow; no repatch needed); (c) why SELECT is still not used (pool recycling drops back to db 0); (d) why the async global is deliberately untouched (the plugin nulls it so `get_async_redis_db()` builds it inside the test's own loop).

**4. Add `tests/unit/test_popoto_client_identity.py`.** Three assertions, per Test Impact. Written first so the pool-class assertion is demonstrably RED on main before step 1 lands.

**5. Update the three affected test files** per the Test Impact dispositions — delete the cache-invalidation class, rewrite the split-brain fix step against the canonical object, and rewrite the async-mirror test to `await get_async_redis_db()` instead of reading the global and skipping.

**6. Update the two feature docs and the two `db_claim.py` docstrings** per the Documentation section.

**Corrected file:line references** (the issue body cites 616/626-628/631/640-641, which are stale by two commits — see Freshness Check for the full table): object replacement is now **964**, the repatch loop **967-976**, the eager async bind **980**, the async save **949**, the teardown restore **987-990**, the helper and cache **659-710**.

**Explicitly NOT doing:** delegating host/port to `_swap_db` via its `**extra_kwargs`. That would require an upstream popoto change (spike-3, and #2770 tracks the upstream side).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tests/conftest.py`'s `redis_test_db` has **no** `except` blocks today and must gain none. The `_assert_client_matches_claim_registry` call raises `RuntimeError` deliberately and that must stay unguarded — a swallowed registry mismatch is #2799 returning silently. Verify with `grep -n 'except' tests/conftest.py` scoped to the fixture's line range: expected zero.
- [ ] The plugin's `_popoto_test_db` teardown wraps `flushdb()` in `except Exception: pass` (`pytest_plugin.py:169-172`). That is upstream code and out of scope to change, but the plan must not *rely* on that flush: conftest's own teardown flush is the one that matters and is unguarded.
- [ ] `old_pool.disconnect()` is called without a guard. If the server is already gone at teardown, redis-py raises. Add a test asserting the fixture does not mask a genuine connection failure — an unreachable server must fail the test loudly rather than silently leaving the suite on the wrong pool.

### Empty/Invalid Input Handling
- [ ] `db_claim.redis_test_host()` / `redis_test_port()` already handle the set-but-empty env-var case via `or` rather than `.get(k, default)` (#2957), and `tests/unit/test_test_redis_server_resolution.py::TestEmptyEnvFallsThroughToDefault` covers it. The new fixture body must keep resolving through those functions and must not reintroduce a `.get(..., default)`; the existing tests then cover the empty-input path for free.
- [ ] `rdb._SYNC_MAX_CONNECTIONS` is derived from `POPOTO_SYNC_MAX_CONNECTIONS` with popoto's own `ValueError` fallback to 128. Add a case asserting the fixture reads the attribute rather than hardcoding 128, so a machine that overrides the env var gets the override.
- [ ] The idempotence guard compares `connection_kwargs.get("host")` and `.get("port")` — a pool built from a URL may carry `port` as `int` while `db_claim.redis_test_port()` returns `str`. Normalize both sides and add a case that a str/int mismatch does not cause an infinite re-swap every test.

### Error State Rendering
- [ ] The user-visible failure surface here is a pytest error message, and it must stay diagnostic. Assert that a deliberately mismatched client still produces `_assert_client_matches_claim_registry`'s full message (client host:port vs registry host:port, plus the #2799 reference) rather than a bare `AssertionError`.
- [ ] Assert the session-scoped `_popoto_client_ownership_check` still fires its "a popoto client is pointed at db N which this process has not claimed" message when the sync client is on an unclaimed db. Deleting the async bind must not weaken this check — it already skips `None` async clients by design.
- [ ] Failure must not be silent-by-skip. The rewritten `test_async_client_matches_the_sync_client` must **fail**, not `pytest.skip`, when the async client diverges from the sync one — the current skip-on-`None` shape is exactly the silent-pass this plan removes.

## Test Impact

- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPopotoModuleCacheInvalidation` (whole class, ~lines 240-360: `_snapshot_and_restore_cache_globals`, `test_identity_divergence_forces_rebuild_with_len_unchanged`, `test_len_branch_catches_a_brand_new_never_cached_holder`) — DELETE: these test `_popoto_modules_with_redis_db()`'s two-signal cache, which this plan deletes. A cache that no longer exists cannot have an invalidation bug; keeping the tests would require keeping the helper alive solely for them.
- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPopotoSplitBrainRoundTrip::test_create_then_filter_split_brain_and_fix` (~lines 386-448) — UPDATE: keep Step 1 (divert `popoto.models.query`'s local binding, prove the create-then-filter round trip misses) verbatim; replace Step 2's `for mod in _conftest._popoto_modules_with_redis_db(): mod.POPOTO_REDIS_DB = correct_test_client` with a single restore to the canonical object (`query_module.POPOTO_REDIS_DB = rdb.POPOTO_REDIS_DB`). The #2037 mechanism is still reproduced; the fix path becomes "there is one canonical client object and every binding is it", which is exactly the invariant this plan establishes. Also drop the bare `redis.Redis(db=...)` clients in favour of `db_claim.redis_test_host()/redis_test_port()` so the file stops carrying a latent #2799 shape.
- [ ] `tests/unit/test_test_redis_server_resolution.py::TestServerResolutionIsSingleSourced::test_async_client_matches_the_sync_client` (lines 44-54) — REPLACE: it reads `rdb._POPOTO_ASYNC_REDIS_DB` directly and `pytest.skip`s when it is `None`. Once the eager async bind is deleted, `None` is the steady state and this test becomes a permanent silent skip. Rewrite it as an async test that `await`s `rdb.get_async_redis_db()` inside the test's own loop and asserts host/port/db match the sync client's `connection_pool.connection_kwargs` — which tests the real contract (the async client mirrors the sync one) instead of an artefact of the fixture.
- [ ] `tests/unit/test_conftest_isolation_guards.py:1271-1299` (`_POPOTO_ASYNC_REDIS_DB` is `None` at session scope / ownership-check loop) — UPDATE (assertion strengthening, not repair): these already encode the post-change truth. Add a case asserting that after `redis_test_db` setup the async global is *still* `None`, which is the regression fence against a future re-introduction of an eager bind.
- [ ] New test file `tests/unit/test_popoto_client_identity.py` — ADD: four assertions that only hold under session-scoped in-place mutation — (a) `rdb.POPOTO_REDIS_DB` is the *same object* across two tests in the same process, (b) every module in `sys.modules` whose name starts with `popoto` and which has a `POPOTO_REDIS_DB` symbol holds that same object, (c) the live pool is a `redis.BlockingConnectionPool` with `max_connections` matching popoto's `_SYNC_MAX_CONNECTIONS`, (d) `id(rdb.POPOTO_REDIS_DB.connection_pool)` is the *same* across two tests in the same session. Assertion (d) is the direct fence against the round-1 BLOCKER: a function-scoped restore/rebuild cycle breaks it while leaving (a)-(c) green.
- [ ] ~197 call sites across `tests/unit/` and `tests/integration/` request `redis_test_db` by name — NO CHANGE: the fixture keeps its name, its `autouse=True`, and its function scope. Only its body changes. Any diff that renames or rescopes it is out of contract.

## Rabbit Holes

- **Patching `~/src/popoto` to accept host/port in `_swap_db`.** Tempting because it looks like the "real" fix, and `_swap_db` already takes `**extra_kwargs`. It is a different repo, a different release cycle, and #2770 already owns that surface. Spike-3 established that conftest owning server resolution is sufficient here.
- **Trying to make the plugin's and conftest's fixtures run in a chosen order.** The current ordering (plugin first, conftest second) is what the design relies on, and it falls out of pytest's plugin-before-conftest fixture resolution plus the explicit `_popoto_test_db` dependency on the session check. Do not add `pytest_collection_modifyitems` reordering, fixture-order shims, or `@pytest.mark.order`. If ordering ever needs asserting, assert it in a test; do not engineer it.
- **Auditing the ~197 `redis_test_db` call sites.** They request a fixture by name and never touch its internals. Reading them is days of work that cannot change the answer. The contract is "same name, same autouse, same scope" — hold that and the call sites are irrelevant.
- **Generalizing the connection-cap fix into a "pool policy" abstraction.** The cap is one integer read from one popoto attribute. A `PoolFactory` / policy object would be more code than the thing it configures.
- **Chasing every bare `redis.Redis(db=...)` in the test tree.** `TestPopotoSplitBrainRoundTrip` has two, and fixing those two is in scope because the file is already being edited. A repo-wide sweep for the same shape is #2655's guard territory and, where it hits production code, #3003's.
- **Rewriting `_popoto_client_ownership_check` to also assert the async client is non-`None`.** Its docstring explains at length why it must not: an `AttributeError` raised in a session fixture errors *every test in the process during setup*. Deleting the eager bind makes `None` even more firmly the correct state, not less.
- **Fixing the flaky-looking failures the first full-suite run surfaces.** A 20-minute autouse-fixture change will surface unrelated pre-existing flakes. Classify against main (`baseline-verifier`) before touching anything; do not fold unrelated repairs into this diff.

## Risks

### Risk 1: A stranded submodule binding survives somewhere the identity test does not look
**Impact:** A popoto submodule holds a `POPOTO_REDIS_DB` that is not the canonical object — the #2037 split-brain, which manifests as intermittent create-then-query misses under xdist rather than a clean failure. This is the failure mode the deleted repatch loop was built to prevent, so removing it without proof is the single biggest hazard in this plan.
**Mitigation:** The new identity test enumerates `sys.modules` for *every* name starting with `popoto` that has a `POPOTO_REDIS_DB` symbol and asserts each `is rdb.POPOTO_REDIS_DB` — the same discovery query the deleted helper used, repurposed from "repair" to "assert". Because it is an assertion rather than a repair, a stranded binding fails loudly instead of being silently patched. Run it late in a full suite (not just standalone) so the maximum number of popoto submodules are imported. Additionally, `TestPopotoSplitBrainRoundTrip` is kept (not deleted) precisely to keep a live reproduction of the mechanism in the suite.

### Risk 2: Dropping the eager async bind changes behaviour for tests that were silently relying on it
**Impact:** Spike-1 proved the eager client is the one tests currently observe. Any async test that touches popoto and passes today might pass *because* of the wrong-loop client (e.g. it never actually awaits, or it reads `connection_kwargs` without connecting). Removing it could surface real failures.
**Mitigation:** Treat any new async failure as a genuine find, not as collateral — the plugin's lazy in-loop path is the correct behaviour and popoto's own docstrings say so. Run `tests/integration/` as well as `tests/unit/`, since async popoto usage concentrates there. If a test fails, fix the test, do not restore the bind; restoring it is explicitly out of contract (there is an anti-criterion for it in Verification).

### Risk 3: The idempotence guard misjudges "already correct" and the swap never happens
**Impact:** Silent worst case — the fixture becomes a no-op, the suite runs on whatever server popoto imported from the ambient `REDIS_URL`, and if that is production db 0 the flush guard is the only thing standing between the suite and real data.
**Mitigation:** `_assert_client_matches_claim_registry` stays in the function-scoped `redis_test_db` and runs on **every** test, unconditionally and independently of the session-scoped guard, so a wrong "already correct" verdict at install fails on the very first test rather than being silently tolerated. Type-normalize host/port before comparing (str vs int `port` is the realistic way this misjudges). Under session scoping the guard is consulted once per session rather than once per test, which shrinks this risk surface to a single evaluation. The four-layer flush hardening from #2680 remains a backstop, not the primary defence.

### Risk 4: A pre-existing red suite masks a regression introduced here
**Impact:** `main`'s unit suite has a history of a rotating failure set (#2628) and there is an open `fix-red-main-unit-tests` plan. Attributing a real regression to "pre-existing" is the exact mistake this repo has been burned by.
**Mitigation:** Capture a full `tests/unit/` and `tests/integration/` baseline on `main` *before* the first edit, and classify every post-change failure against it with the `baseline-verifier` agent. Never dismiss a failure as pre-existing without that comparison.

### Risk 5: A future popoto release changes `_swap_db` or `_SYNC_MAX_CONNECTIONS`
**Impact:** The plan reads two private-ish popoto names (`_SYNC_MAX_CONNECTIONS`, and it depends on `_swap_db`'s in-place semantics). An upstream rename breaks the suite at import or leaves the cap unset.
**Mitigation:** `_popoto_client_ownership_check` already exists as the "future popoto stopped honouring us" tripwire, and the new pool-class assertion extends it to the cap. Read the cap defensively (`getattr(rdb, "_SYNC_MAX_CONNECTIONS", 128)`) with a comment naming #2770 as the upstream coordination point. The dependency is already there today via the plugin; this plan makes it *visible* rather than adding it.

## Race Conditions

### Race 1: Pool swap versus in-flight commands on the old pool
**Location:** `tests/conftest.py`, the new session-scoped `_popoto_pool_install` fixture — the `client.connection_pool = new_pool; old_pool.disconnect()` pair at install, and the mirrored restore/`disconnect()` in its session finalizer.
**Trigger:** `disconnect()` tears down every connection hosted by the pool being dropped. Any caller holding a checked-out connection from it gets a broken socket mid-command (Research finding 1, redis-py #932).
**Data prerequisite:** none.
**State prerequisite:** no thread or coroutine may hold a checked-out connection from the pool being disconnected at the moment of disconnect.
**Mitigation:** Both ends run in pytest's synchronous fixture phases with no test body executing. The install runs during session-scoped setup, before the first test body starts; the restore runs in the session finalizer, after the last test's teardown has completed. popoto's `async_save`/`async_delete` funnel into the sync pool via a thread pool, but only from inside a running test. The plan states this precondition in a code comment and forbids moving either operation into a test body or an async context. The same reasoning already licenses popoto's `_swap_db`, which does the identical thing.

Session scoping is what makes this argument hold for the whole run rather than only the first test. A function-scoped restore would put the client back on the plugin's import-time pool between every pair of tests, and the plugin's `_popoto_flush_db` — which runs first in the next test's setup — would then `flushdb()` whatever host that pool carries. Spike-4 cleared db-number drift, not host drift, so that would be a new hazard rather than an inherited one. It is the reason pool ownership is session-scoped (Critique Results row 1).

### Race 2: Concurrent pytest processes and the db-claim pool
**Location:** `tests/db_claim.py` claim registry; `tests/conftest.py::pytest_configure`.
**Trigger:** Several pytest processes run on this machine at once (a full-suite run plus a targeted one is routine here). Two processes on the same db number would flush each other's data mid-test.
**Data prerequisite:** the claim registry directory keyed by the resolved port must exist and be writable.
**State prerequisite:** each process holds an exclusive `fcntl.flock` on its db slot for its whole lifetime.
**Mitigation:** Unchanged by this plan and deliberately so — `claim_test_db()` remains the sole authority for the number, and the OS releases flocks on process death. The plan must not introduce any second derivation of the db number; the anti-criteria in Verification enforce that conftest never writes a db anywhere except through `claim_test_db()`.

### Race 3: Async client construction across event loops
**Location:** `popoto/redis_db.py::get_async_redis_db` guarded by `_async_redis_lock`; `popoto/pytest_plugin.py::_popoto_reset_async`.
**Trigger:** pytest-asyncio creates a fresh event loop per test function. A client created in one test's loop is bound to a closed loop in the next ("Future attached to a different loop"). Concurrent coroutines could also race to build the singleton.
**Data prerequisite:** the sync client's `connection_kwargs` must already carry the correct host/port/db before the first `await`, since the async client mirrors them.
**State prerequisite:** `_POPOTO_ASYNC_REDIS_DB` must be `None` at the start of every test, and the first construction must happen inside the running loop.
**Mitigation:** This is precisely what deleting the eager bind *fixes*. The plugin nulls the global at setup and teardown of every test and rebuilds a fresh `asyncio.Lock()` with it; `get_async_redis_db()` double-checks under that lock. The data prerequisite is satisfied because conftest's in-place swap completes during synchronous setup, strictly before any test body can `await`. Today's code violates the state prerequisite by constructing the client synchronously at conftest:980.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG #2770]` Moving host/port resolution into popoto's own `_swap_db` (or otherwise changing `~/src/popoto`). Spike-3 established this is neither necessary nor sufficient here, and #2770 already owns the upstream plugin-hardening surface. **Anti-criterion:** the Verification table asserts conftest never delegates server resolution by grepping that `_swap_db` is not called from `tests/`.
- `[SEPARATE-SLUG #3003]` Sweeping the repo for hand-built raw Redis clients that bypass the ORM. The two bare `redis.Redis(db=...)` clients inside `TestPopotoSplitBrainRoundTrip` *are* fixed here because that test is being edited anyway; the other ~20 production call sites belong to #3003.
- `[SEPARATE-SLUG #2535]` The broader "concurrent test runs corrupt each other" cluster (suite lock skipping targeted runs, the 99% wedge, the shared editable install). Adjacent to Race 2 but a different mechanism and a different fix.
- `[ORDERED]` Deleting popoto's bundled plugin dependency entirely and having conftest own both facts. That would only be safe after #2770 lands upstream and the pinned popoto version is bumped here — a sequenced, human-gated release event in another repo. Until then the split is the correct design, not a compromise.
- `[SEPARATE-SLUG #2628]` Any repair of `main`'s pre-existing red unit tests surfaced by this plan's full-suite runs. Risk 4 requires classifying them against a baseline; classified-as-pre-existing failures get reported, not fixed in this diff.

Everything else the issue asks for is in scope: the audit is complete (spikes 1-4), the redundant repatch loop and eager async bind are deleted here, and the "document why the split is required" branch is answered in the Technical Approach and carried into `docs/features/test-db-ownership.md`.

## Update System

No update system changes required. This work is confined to `tests/`, two `docs/features/` pages, and no runtime code — `scripts/update/run.py` and the `/update` skill neither read nor propagate any of it. Specifically:

- No new dependency, so no `pyproject.toml` / `uv.lock` change and no `uv sync` implication.
- No new `.env` key, so no `.env.example` declaration and no `check_env_completeness` impact.
- No Popoto **model** change (this touches the Redis *client*, not any model schema), so `scripts/update/migrations.py` gets no new entry and `data/migrations_completed.json` is untouched.
- No bridge, worker, or agent code changes, so no `./scripts/valor-service.sh restart` is needed after merge. `/update` should still be run after the PR merges per repo convention, but purely to move the git ref.

## Agent Integration

No agent integration required. Nothing here is reachable by, or visible to, the agent at runtime:

- No new CLI entry point in `pyproject.toml [project.scripts]`.
- No new MCP tool in `mcp_servers/` or `.mcp.json`.
- `bridge/telegram_bridge.py` imports nothing from `tests/`, and `tests/conftest.py` is loaded only by pytest's own collection machinery.
- The one indirect agent-facing effect is positive and needs no wiring: restoring popoto's `BlockingConnectionPool` cap during tests means agent-code paths exercised under test (`Model.async_save` bursts via `asyncio.gather`) run against the same connection ceiling production uses, so a `MaxConnectionsError` that production would hit is now reproducible in the suite instead of being masked by an unbounded test pool.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/test-isolation-hardening.md` — its opening section (lines 9-11) documents `_popoto_modules_with_redis_db()`'s two-signal cache as load-bearing ("Neither branch may be dropped"). That statement becomes false when the helper is deleted. Replace it with the new invariant: there is exactly one `POPOTO_REDIS_DB` object per process, conftest mutates its pool in place once per session, and no submodule binding ever needs re-pointing. Line 60's "authoritative mechanism explanations" list and line 64's "Test B" description must lose their references to the deleted helper and its tests.
- [ ] **Rename the section heading at `docs/features/test-isolation-hardening.md:7`.** It currently reads `## Popoto module re-pointing cache invalidation` — a title naming the exact mechanism this plan deletes. Retitle it (e.g. `## Popoto client identity`) or fold its content into the in-place-mutation invariant section. A body rewrite that leaves the old heading standing is the historical-artifact-in-docs that CLAUDE.md forbids, so the deliverable is checked against the heading, not only against the enumerated line ranges above.
- [ ] Update `tests/README.md` — it references `_popoto_modules_with_redis_db` as a live mechanism. It is a live doc, not an archived record, so it must describe only the new status quo: one canonical client object, session-scoped in-place pool install, no submodule re-pointing.
- [ ] Update `docs/features/test-db-ownership.md` — describe the split ownership explicitly: `tests/conftest.py` owns *server resolution* (host/port, via `tests/db_claim.py`), popoto's bundled plugin owns the *db number* (via the `POPOTO_TEST_DB` export). Lines 113 and 119 already state the `_POPOTO_ASYNC_REDIS_DB is None` truth; extend them to say the async client is now built lazily in-loop by `get_async_redis_db()` and that conftest never binds it.
- [ ] No new `docs/features/` file and no `docs/features/README.md` index row — this is a consolidation inside two existing documented features, not a new capability.

### Inline Documentation
- [ ] Rewrite the `redis_test_db` docstring. Its current "CRITICAL: We replace the POPOTO_REDIS_DB object with a new Redis client" paragraph (conftest:928-934) becomes the exact opposite of the code. The replacement must state why the swap is in-place (submodule bindings follow for free), why SELECT is still not used (pool recycling), and why conftest still builds the connection kwargs itself rather than delegating to the plugin's `_swap_db` (host/port, #2799).
- [ ] Update `tests/db_claim.py`'s module docstring (line 4: "``tests/conftest.py``'s autouse ``redis_test_db`` fixture points Popoto at that db") and `redis_test_host`'s docstring (line 111: "the raw ``redis.Redis`` in ``conftest``'s ``redis_test_db`` fixture") — both describe the object-replacement shape that this plan removes.
- [ ] Delete the `_POPOTO_MODULE_CACHE` comment block (conftest:659-666) along with the helper.

## Success Criteria

- [ ] `tests/conftest.py` contains exactly zero assignments to `rdb.POPOTO_REDIS_DB` and zero assignments to `rdb._POPOTO_ASYNC_REDIS_DB`. The client object popoto built at import time is the one the whole session uses.
- [ ] `_popoto_modules_with_redis_db`, `_POPOTO_MODULE_CACHE`, and `_POPOTO_MODULE_CACHE_LEN` do not appear in **live code or live feature docs**. Two records legitimately keep the name and must not be rewritten: `docs/archive/plans-completed/xdist-test-isolation-flakes.md` (a historical record) and this plan document (which necessarily names what it deletes).
- [ ] `redis_test_db` keeps its name, `autouse=True`, and function scope; the ~197 requesting call sites are unmodified.
- [ ] `tests/unit/test_popoto_client_identity.py` exists and passes, asserting: (a) `rdb.POPOTO_REDIS_DB` is the same object across tests in a process, (b) every `popoto.*` module in `sys.modules` holding a `POPOTO_REDIS_DB` symbol holds *that* object, (c) the live pool is a `redis.BlockingConnectionPool` whose `max_connections` equals `rdb._SYNC_MAX_CONNECTIONS`.
- [ ] Assertion (c) is demonstrated RED on `main` before the change lands, and the RED output is pasted into the PR description as the paper trail.
- [ ] Spike-1's probe is re-run and now prints `ASYNC_IS_NONE: True` inside a test body (it prints `False` on main), proving the async global is back under the plugin's lazy in-loop control.
- [ ] `tests/unit/test_test_redis_server_resolution.py::test_async_client_matches_the_sync_client` awaits `get_async_redis_db()` and **fails** on divergence — it can no longer `pytest.skip` its way to green.
- [ ] `tests/unit/test_conftest_isolation_guards.py::TestPopotoSplitBrainRoundTrip` still reproduces the #2037 split-brain in Step 1 and still goes green in Step 3, with the fix step expressed as a restore to the canonical object.
- [ ] `docs/features/test-isolation-hardening.md` no longer describes the deleted cache as load-bearing **and no longer carries a section heading named after it**, `docs/features/test-db-ownership.md` states the conftest-owns-server / plugin-owns-db-number split with the #2799 reason, and `tests/README.md` describes only the new status quo.
- [ ] `tests/db_claim.py`'s module docstring (line 4) and `redis_test_host` docstring (line 111) no longer describe conftest as building a replacement client.
- [ ] Full `tests/unit/` and `tests/integration/` runs are green, or every failure is classified against a pre-change `main` baseline by `baseline-verifier` and shown to be pre-existing.
- [ ] The suite passes both under xdist and under `-n0 -p no:randomly` (ordering must not be load-bearing in a new way).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No xfail markers added or left behind — this change introduces none and converts none (no xfails exist for this defect; it was found by audit, not by a failing test).

## Team Orchestration

The lead agent orchestrates and never builds directly. This is a Small-appetite change with a
whole-suite blast radius, so the team is deliberately thin — one builder holding the whole
context beats parallel builders coordinating on one fixture — with an independent validator and
a baseline classifier.

### Team Members

- **Builder (conftest consolidation)**
  - Name: `conftest-builder`
  - Role: Owns every edit to `tests/conftest.py` and the three affected test files, in the task order below. Sole writer to `tests/conftest.py`.
  - Agent Type: `builder`
  - Domain: Redis/Popoto data + async/concurrency (paste the matching `DOMAIN_FRAMING.md` rules into the assignment)
  - Resume: true

- **Validator (invariant + anti-criteria)**
  - Name: `identity-validator`
  - Role: Read-only. Confirms the one-object invariant holds, runs every Verification row including the anti-criteria greps, and confirms the RED-state paper trail exists.
  - Agent Type: `validator`
  - Resume: true

- **Baseline classifier**
  - Name: `suite-baseline`
  - Role: Captures the pre-change `tests/unit/` + `tests/integration/` baseline on `main` and classifies every post-change failure as regression vs pre-existing. Never edits code.
  - Agent Type: `baseline-verifier`
  - Resume: true

- **Documentarian**
  - Name: `db-ownership-docs`
  - Role: The two `docs/features/` updates and the `tests/db_claim.py` docstring corrections.
  - Agent Type: `documentarian`
  - Resume: true

## Step by Step Tasks

### 1. Capture the pre-change baseline
- **Task ID**: baseline-main
- **Depends On**: none
- **Validates**: n/a (produces the artifact everything else is judged against)
- **Assigned To**: `suite-baseline`
- **Agent Type**: baseline-verifier
- **Parallel**: true
- Run `./scripts/pytest-clean.sh tests/unit/ -q` and `./scripts/pytest-clean.sh tests/integration/ -q` on unmodified `main` and record the full failure set (node ids, not counts).
- Persist it to the lane's scratchpad so later classification is a diff, not a memory.
- Do not attempt to fix anything. Red tests on main are #2628's territory (see No-Gos).

### 2. Prove the invariant is currently violated (RED state)
- **Task ID**: build-identity-test
- **Depends On**: none
- **Validates**: `tests/unit/test_popoto_client_identity.py` (create)
- **Informed By**: spike-2 (the plugin's `_swap_db` strips the `BlockingConnectionPool` and its cap; `max_connections` is not in `connection_kwargs`)
- **Assigned To**: `conftest-builder`
- **Agent Type**: builder
- **Parallel**: true
- Create `tests/unit/test_popoto_client_identity.py` with the four assertions from Test Impact: object stability across tests, all-`popoto.*`-bindings-agree, pool is `BlockingConnectionPool` with `max_connections == rdb._SYNC_MAX_CONNECTIONS`, and **pool object identity stable across two tests in the same session** (the assertion that fails if per-test restore is ever reintroduced).
- Read the cap as `getattr(rdb, "_SYNC_MAX_CONNECTIONS", 128)` with a comment naming #2770 as the upstream coordination point — never hardcode `128` in the fixture (there is an anti-criterion for that).
- Run it on unmodified `main`. The pool-class assertion must FAIL. Capture that output verbatim for the PR description.
- Also re-run spike-1's probe on `main` and capture `ASYNC_IS_NONE: False`.

### 3. Install the pool once per session and reduce `redis_test_db` to per-test hygiene
- **Task ID**: build-inplace-swap
- **Depends On**: build-identity-test
- **Validates**: `tests/unit/test_popoto_client_identity.py`, `tests/unit/test_redis_flush_guard.py`, `tests/unit/test_db_derivation_guard.py`
- **Informed By**: spike-3 (conftest must keep owning host/port or #2799 regresses), spike-4 (`_popoto_flush_db` only re-swaps on db drift, so the two compose), Research findings 1-3
- **Assigned To**: `conftest-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Add the session-scoped autouse fixture `_popoto_pool_install` per Technical Approach step 1a: depends on the plugin's `_popoto_test_db`; idempotence guard with type-normalized host/port comparison; `BlockingConnectionPool` reconstruction carrying `password`/`username` forward; in-place `client.connection_pool` assignment; `old_pool.disconnect()` with the "synchronous setup phase, no in-flight commands" comment; a session-level installed-flag; yield; session finalizer restores the displaced pool and disconnects the one we built.
- Rewrite `redis_test_db` per Technical Approach step 1b: depends on `_popoto_pool_install`; `_assert_client_matches_claim_registry` against the canonical client; flush; yield; flush. **Nothing else.** It must not touch `connection_pool`, must not call `disconnect()`, and must not rebind `rdb.POPOTO_REDIS_DB`.
- The function-scoped teardown restoring `client.connection_pool = old_pool` is the round-1 BLOCKER. Do not reintroduce it in any form — it makes the idempotence guard unreachable and exposes the client to `_popoto_flush_db` on the plugin's import-time host between tests.
- Do NOT call `client.close()` (the client does not own its pool, and the object is shared).
- Delete the `import redis.asyncio as aioredis` line.
- Rewrite the docstring per Technical Approach step 3 — the current "CRITICAL: We replace the POPOTO_REDIS_DB object" paragraph is now false and must not survive in any form.
- Confirm `tests/unit/test_popoto_client_identity.py` now passes, and spike-1's probe now prints `ASYNC_IS_NONE: True`.

### 4. Delete the now-dead repatch machinery
- **Task ID**: build-delete-cache
- **Depends On**: build-inplace-swap
- **Validates**: `tests/unit/test_conftest_isolation_guards.py`
- **Assigned To**: `conftest-builder`
- **Agent Type**: builder
- **Parallel**: false
- Delete `_popoto_modules_with_redis_db`, `_POPOTO_MODULE_CACHE`, `_POPOTO_MODULE_CACHE_LEN`, and the explanatory comment block above them (conftest:659-710).
- Delete `TestPopotoModuleCacheInvalidation` (the class and its `_snapshot_and_restore_cache_globals` fixture) from `tests/unit/test_conftest_isolation_guards.py`.
- Rewrite `TestPopotoSplitBrainRoundTrip::test_create_then_filter_split_brain_and_fix`: keep Step 1's reproduction verbatim; replace Step 2's helper-driven repair with `query_module.POPOTO_REDIS_DB = rdb.POPOTO_REDIS_DB`; replace both bare `redis.Redis(db=...)` clients with `db_claim`-resolved host/port.
- No compatibility shim, no deprecation alias, no commented-out original. Delete means delete.

### 5. Fix the async-mirror test
- **Task ID**: build-async-test
- **Depends On**: build-inplace-swap
- **Validates**: `tests/unit/test_test_redis_server_resolution.py`
- **Informed By**: spike-1 (the eager bind was what made this test observable at all)
- **Assigned To**: `conftest-builder`
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: false
- Rewrite `test_async_client_matches_the_sync_client` as an async test that `await`s `rdb.get_async_redis_db()` inside the test's own event loop and asserts host/port/db match the sync client's `connection_kwargs`.
- Remove the `pytest.skip("popoto exposes no async client in this version")` branch entirely — divergence must fail, not skip.
- Extend `tests/unit/test_conftest_isolation_guards.py:1271-1299` with a case asserting `_POPOTO_ASYNC_REDIS_DB` is still `None` immediately after `redis_test_db` setup (the fence against re-introducing an eager bind).

### 6. Validate the invariant and the anti-criteria
- **Task ID**: validate-invariant
- **Depends On**: build-delete-cache, build-async-test
- **Assigned To**: `identity-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every row in the Verification table, including all four anti-criteria greps.
- Run `tests/unit/test_popoto_client_identity.py` at the END of a full `tests/unit/` run (not standalone) so the maximum number of popoto submodules are imported when the all-bindings-agree assertion fires — this is the Risk 1 mitigation and a standalone run does not exercise it.
- Confirm the RED-state output from task 2 is present in the PR description.
- Report pass/fail per row; do not fix anything.

### 7. Classify the full-suite result
- **Task ID**: classify-failures
- **Depends On**: validate-invariant
- **Assigned To**: `suite-baseline`
- **Agent Type**: baseline-verifier
- **Parallel**: false
- Diff the post-change `tests/unit/` and `tests/integration/` failure sets against the task-1 baseline.
- Every new failure is a regression until proven otherwise by a run on `main`. Return the classification, not a verdict on whether to ship.
- Async failures specifically: per Risk 2, a genuine async failure is a find. Route it back to `conftest-builder` to fix the test — never to restore the eager bind.

### 8. Documentation
- **Task ID**: document-ownership-split
- **Depends On**: validate-invariant
- **Assigned To**: `db-ownership-docs`
- **Agent Type**: documentarian
- **Parallel**: false
- Apply every item in the Documentation section: `docs/features/test-isolation-hardening.md` (**the section heading at line 7**, plus lines 9-11, 60, 64), `docs/features/test-db-ownership.md` (lines 113, 119), `tests/README.md`, `tests/db_claim.py` (lines 4, 111).
- The line numbers are a starting point, not the contract. Judge the result by whether any live doc still describes the deleted helper, the deleted cache, or object replacement — headings included.
- No new `docs/features/` file and no README index row — this is a change to two documented features, not a new one.
- The docs must describe only the new status quo. No "previously we..." narration.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: classify-failures, document-ownership-split
- **Assigned To**: `identity-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run the full Verification table plus `python -m ruff check .` and `python -m ruff format --check .`.
- Walk every Success Criteria checkbox and mark it met or unmet with evidence.
- Confirm no live code or live doc still references the deleted helper. The check must exclude the two records that legitimately keep the name (`docs/archive/`, and this plan document itself):
  `grep -rn '_popoto_modules_with_redis_db' docs/features/ tests/ --include='*.py' --include='*.md' | grep -v 'docs/archive/'` — expected: no output.
- A bare `grep -rn ... docs/ tests/` can never return empty and must not be used as the gate.

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
| ANTI (No-Go #2770): tests never call popoto's `_swap_db` | `grep -rc '_swap_db' tests/` | match count == 0 |
| ANTI (No-Go #3003): no bare `redis.Redis(db=...)` in the edited guard file | `grep -cE 'redis\.Redis\(db=' tests/unit/test_conftest_isolation_guards.py` | match count == 0 |
| ANTI: the connection cap is read from popoto, not hardcoded | `grep -cE 'max_connections\s*=\s*[0-9]' tests/conftest.py` | match count == 0 |
| Live client identity holds (one object, all bindings) | `./scripts/pytest-clean.sh tests/unit/test_popoto_client_identity.py -q -p no:randomly -n0` | exit code 0 |
| Live pool keeps popoto's connection cap | `./scripts/pytest-clean.sh tests/unit/test_popoto_client_identity.py::test_pool_is_blocking_with_popoto_cap -q -n0` | exit code 0 |
| Pool is installed once per session, not per test | `./scripts/pytest-clean.sh tests/unit/test_popoto_client_identity.py -k pool_identity -q -n0 -p no:randomly` | exit code 0 |
| db-derivation recurrence guard (#2655) still clean | `./scripts/pytest-clean.sh tests/unit/test_db_derivation_guard.py -q -n0` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness (Adversary) + History & Consistency (Consistency Auditor) | **The per-test teardown restore makes the idempotence guard permanently unreachable, and the "steady-state no-op" claim is false as specified.** Technical Approach step 1 says teardown restores `client.connection_pool = old_pool` and disconnects `new_pool` (line 259), and Data Flow item 8 (line 193) confirms this is per-test by design. But `old_pool` at test 1's swap is the plugin's plain `ConnectionPool` (spike-2: `_swap_db` strips the `BlockingConnectionPool` class), so test 2's guard — "already the target `(host, port, db)` **and** already a `BlockingConnectionPool`" (line 252) — necessarily fails and swaps again. Every test pays the full teardown/rebuild the guard was introduced to eliminate, contradicting line 252 directly. Worse, the restore puts the canonical client back on the plugin's import-time-`REDIS_URL` pool between tests, and the plugin's `_popoto_flush_db` runs FIRST each test and `flushdb()`s whatever pool is attached. Spike-4 only cleared db-number drift, never the host the restored pool carries — today's full-object replacement has no restore-then-reapply cycle, so this hazard is introduced by this plan. | pending | Move pool ownership to session scope: swap once behind a session-level installed-flag and unwind (restore + `disconnect()`) only in a session-scoped finalizer, never at function-scoped teardown. Keep `flushdb()` per-test. Concretely, the function teardown must not run `client.connection_pool = old_pool; new_pool.disconnect()` when `old_pool` is not itself a `BlockingConnectionPool` on the claimed db — otherwise the next test's idempotence check is guaranteed to fail. This is also what actually delivers the plan's stated "no-op beyond the flush" steady state. |
| CONCERN | Scope & Value (User) | **Open Question 1 is presented as unresolved while the tasks proceed as if it were answered yes.** The plan concedes the `BlockingConnectionPool` cap restoration "is not what #2771 asked for" and offers "Split it into its own issue if you want a single-variable diff" (lines 587-588), yet Success Criteria makes assertion (c) plus its RED-state paper trail mandatory (line 403), Verification adds two cap-specific rows (lines 572, 574), and task 2 builds it unconditionally (lines 463-473). The open question gates nothing, so an owner reading it as live has no mechanism to act on it. | pending | Resolve it before build starts. If in scope, delete it from Open Questions and state the ruling. If deferred, cut the assertion-(c) Success Criterion, the two cap Verification rows, and the cap half of task 2, and add a `[SEPARATE-SLUG]` No-Go in the same convention as line 359 — note that deferring also removes the plan's only assertion that is demonstrably RED on main today. |
| CONCERN | History & Consistency (Archaeologist) | **The documentarian instruction is line-scoped and misses the section heading it invalidates.** Task 8 and the Documentation section enumerate `docs/features/test-isolation-hardening.md` lines 9-11, 60, 64, but the section's own heading at line 7 is `## Popoto module re-pointing cache invalidation` — a title naming a mechanism this plan deletes. A documentarian following the literal line list rewrites the body and leaves a section titled after a deleted cache, exactly the historical-artifact-in-docs that CLAUDE.md forbids. | pending | Add "and rename the section heading at line 7" to the Documentation bullet and to task 8, e.g. retitle to "Popoto client identity" or fold the content into the in-place-mutation invariant section, so the deliverable is checked against the heading and not only the enumerated line ranges. |
| CONCERN | Structural (cross-reference) | **Task 9's completion grep is unsatisfiable as written, and one doc holding the symbol is not in the Documentation section.** Success Criterion line 400 requires `_popoto_modules_with_redis_db` to appear nowhere in the repo, and task 9 (line 554) asserts `grep -rn '_popoto_modules_with_redis_db' docs/ tests/` is empty. Verified repo-wide, the symbol currently lives in six tracked files: `tests/conftest.py`, `tests/unit/test_conftest_isolation_guards.py`, `tests/README.md`, `docs/features/test-isolation-hardening.md`, `docs/archive/plans-completed/xdist-test-isolation-flakes.md`, and this plan document itself. The archived plan is a historical record that must not be rewritten and the plan doc necessarily names what it deletes, so the grep can never return empty. `tests/README.md` is a genuine live reference and appears in no Documentation bullet or task. | pending | Add `tests/README.md` to the Documentation section and to task 8's file list. Rewrite the task-9 check to exclude the records that legitimately keep the name: `grep -rn '_popoto_modules_with_redis_db' docs/features/ tests/ --include='*.py' --include='*.md' \| grep -v 'docs/archive/'` expected empty, and narrow Success Criterion line 400 from "anywhere in the repo" to "in live code and live feature docs". |
| NIT | Risk & Robustness (Operator) | **The `grep -rc` anti-criteria rows do not emit the single number their Expected column states.** Recursive `grep -c` prints one `path:count` line per scanned file including zero-count files — verified here, `grep -rc '_popoto_modules_with_redis_db' tests/` emits 862 lines, not `0`. A validator reading raw output cannot judge PASS/FAIL against "match count == 0". | pending | n/a (NIT) |
| NIT | Scope & Value (Simplifier) | **The declared team and the actual team contradict each other.** Appetite says "Team: Solo dev, code reviewer" (line 210) while Team Orchestration stands up four named agents plus a lead (lines 424-447) for a change the plan itself sizes at "roughly a 30-line net deletion in one fixture plus three test-file edits" (line 216). Downgraded from the critic's CONCERN: the roster is defensible given the whole-suite blast radius, so the defect is the inconsistent Appetite line, not the roster. | pending | n/a (NIT) |
| NIT | Scope & Value (Simplifier) | Technical Approach step 1 prescribes local variable names (`client`, `old_pool`, `new_pool`), exact kwargs, and call ordering (lines 246-260), plus a four-point docstring content list (line 264) — unusually prescriptive for a Small chore and leaving the builder no latitude. | pending | n/a (NIT) |

---

## Open Questions

The four spikes resolved every technically-verifiable assumption. What remains is judgement, not investigation.

1. **Should the async-mirror test become a fail, or is a skip acceptable?** The plan makes it fail on divergence. That is strictly better as a test, but it converts a test that has been silently skipping (or silently passing on a wrong-loop client) into one that can block a PR. If `tests/integration/`'s async popoto usage turns out to have latent divergence, this test becomes the thing that surfaces it — good, but it may cost a round of unrelated-looking fixes. Confirm the appetite covers that.

2. **How much of a full-suite run is the acceptance bar?** The plan requires green (or baseline-classified) `tests/unit/` **and** `tests/integration/`, which is roughly 20 minutes plus integration time, run at least twice (baseline and post-change) and again under `-n0`. On a machine running several agents' suites concurrently that is a real resource commitment. If the bar should be `tests/unit/` only, say so — but note that async popoto usage concentrates in `tests/integration/`, which is exactly where Risk 2 lives.

3. **Anything the recon and spikes missed?** The one thing this plan cannot prove from the outside is that no popoto submodule captures `POPOTO_REDIS_DB` in some way other than a module-level `from ..redis_db import` — a class attribute set at import time, a default argument, a closure. The identity test enumerates module-level symbols only, which is the same surface the deleted repatch loop covered, so the change is no worse than the status quo. But "no worse than the status quo" is not "proven", and the status quo has been the source of #2037 and #2061.
