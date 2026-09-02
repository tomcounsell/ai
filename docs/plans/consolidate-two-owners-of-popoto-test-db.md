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

TBD

## Freshness Check

TBD

## Prior Art

TBD

## Research

TBD

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
