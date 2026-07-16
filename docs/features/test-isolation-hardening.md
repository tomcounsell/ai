# Test isolation hardening (single-run, cross-file)

This doc covers **single-run, single-worker-sequence** test isolation hardening in `tests/conftest.py` (umbrella issue [#1897](https://github.com/tomcounsell/ai/issues/1897)). It is a distinct concern from **cross-run** concurrency coordination — two independent full-suite runs racing on the same host — which is documented in [`docs/features/full-suite-pytest-lock.md`](full-suite-pytest-lock.md) (issue #1967 / PR #1981). Do not conflate the two: this doc is about a test passing in isolation but failing under a particular xdist worker composition within one run; that doc is about two separate `pytest` invocations oversubscribing CPU cores or colliding on a hardcoded Redis sentinel.

## The problem

Merge-gate full-suite runs periodically flagged large batches of "failures" that were pure test-isolation artifacts: a test passed in isolation and on its PR branch, but failed under a specific `pytest-xdist` `--dist=loadfile` worker ordering, and usually passed again on re-run. On 2026-07-10, PR #2005's gate flagged ~150 such phantom failures and PR #2006's flagged 73 — every one reproducible on a clean `main` checkout, none a real product regression.

Two instances were root-caused. Both trace back to the same upstream mechanism: the autouse `mock_claude_sdk_cleanup` fixture in `tests/conftest.py` mutates `sys.modules` (evicting `agent.*` keys) in a way that is sensitive to import order and to `len(sys.modules)`.

## Root cause 1: popoto db-cache split-brain

`tests/conftest.py`'s `redis_test_db` autouse fixture re-points every popoto submodule's `POPOTO_REDIS_DB` symbol to a per-worker test Redis client. It discovers which submodules to re-point via `_popoto_modules_with_redis_db()`, a memoized helper — walking all ~1500 entries in `sys.modules` on every test would be too slow, so the result is cached and only rebuilt when the cache is judged stale.

The original cache judged staleness solely by `len(sys.modules)` changing. That signal is **non-monotonic** under `mock_claude_sdk_cleanup`'s `agent.*` eviction: an eviction followed by a reimport can land back at the exact same `len(sys.modules)`, with a popoto submodule replaced under the same dotted name but as a *different, stale* module object. The len-only cache saw no change and kept serving the stale object — one still pointing at the pre-swap (production, db=0) `POPOTO_REDIS_DB` binding.

The observable failure: an in-process write landed on db=0 (the stale binding) while a subprocess or a `Model.query.filter(...)` read derived its db from the canonical, correctly-repointed binding (db=1+). The write was invisible to the read — a "split-brain." This manifested as `tests/integration/test_tool_budget_enforcement.py::test_cli_hook_denies_over_budget_exit_2` silently flipping from exit 2 to exit 0, and independently as issue #2037's create-then-`filter` visibility race.

**Fix:** a compound invalidation trigger — rebuild the cache when `len(sys.modules)` changes (catches a brand-new, never-cached db-holder) **OR** when any already-cached module's identity has changed under its name (catches the equal-count eviction-then-reimport). Count/len alone false-greens the second case; identity alone is blind to the first (an `any()` over a cache that never held the new module is vacuously false). Neither branch may be dropped. See the docstring on `_popoto_modules_with_redis_db()` in `tests/conftest.py` for the full mechanism, including the `redis_test_db` consumer contract (`_popoto_modules_with_redis_db()` must return a `list` of module objects, not the internal `{name: module}` dict).

## Root cause 2: agent-hooks hooks-less-parent corruption

`agent/hooks/__init__.py` imports the Claude SDK at module load; `agent`'s `hooks` attribute is bound onto the `agent` package object only transitively, the moment `agent.hooks` is freshly imported. CPython's import machinery does **not** keep a package's attribute tree in sync with `sys.modules` independently — if `sys.modules["agent"]` gets replaced (or partially rebuilt) while `sys.modules["agent.hooks"]` survives from an earlier import, the fresh `agent` object never gets `hooks` re-bound onto it.

The result is a "hooks-less parent": both `agent` and `agent.hooks` report as cached in `sys.modules`, but `agent.hooks` is not reachable as an attribute of `agent`. Any dotted-string `monkeypatch.setattr("agent.hooks.pre_tool_use.SOME_ATTR", ...)` — which pytest resolves via attribute-walk — then raises `AttributeError` at test *setup*, before the test body runs. This hit `tests/unit/test_teammate_write_restriction.py` and `tests/unit/test_ui_reflections_data.py` under `--dist=loadfile` co-scheduling with files that seed the corruption via a module-level `from agent.hooks... import`.

**Fix:** a new autouse fixture, `agent_hooks_consistency_guard`, independent of `mock_claude_sdk_cleanup`, checks at setup for the exact corrupt precondition (`"agent" in sys.modules and "agent.hooks" in sys.modules and not hasattr(sys.modules["agent"], "hooks")`) and repairs it by evicting **every** `agent.*` key from `sys.modules`. Partial eviction would just reproduce the same partial-tree problem on the next import; full eviction guarantees the next `import agent.hooks...` rebuilds the whole parent→child chain consistently. The guard is root-cause-agnostic — it repairs the corruption regardless of which mutation (SDK swap, `importlib.reload`, `patch.dict`) created it. See the docstring on `agent_hooks_consistency_guard()` in `tests/conftest.py` for the full mechanism.

## Root cause 3: cross-process test-DB collision (issue #2060)

`test_cli_hook_denies_over_budget_exit_2` kept flaking intermittently **even in
single-test isolation** — a genuinely *separate* root cause from #1897's two
within-run mechanisms above (root cause 1 was the popoto db-cache split-brain;
#2060 is not that). The residual mechanism is **cross-process**, not
xdist-ordering, which is why it reproduces standalone.

`redis_test_db`/`_redis_test_db_num` used to partition the test DB **only by
xdist worker id within one run**: `gw{N} → db{N+1}`, and master/non-xdist →
db1. That is unique across workers *inside* a single `pytest` invocation, but
**not across concurrent pytest processes** — a background full-suite run's `gw0`
and a standalone `pytest ::test` (master) both derive **db1**. Because
`redis_test_db` calls `flushdb()` at every test's setup *and* teardown, two
processes that landed on the same db number **wipe each other's data mid-test**.

The target test is uniquely exposed: it writes an over-budget `AgentSession`,
then a beat later reads it back from a *freshly spawned* CLI-hook subprocess
(`.claude/hooks/pre_tool_use.py`). If a concurrent process flushed the shared db
in that window, the subprocess resolves no session (`AgentSession.get_by_id →
None`), the budget backstop takes its genuine-no-session path, silently fails
open, and the hook exits `0` instead of `2` → `assert 0 == 2`. Reproduced
against `main`: with a concurrent `flushdb` loop on the shared db the test fails
~5-10/10; with the db isolated, 0.

The `full-suite-pytest-lock.md` advisory lock (#2064) reduces this — it
serializes two *full-suite* runs — but does not cover full-suite-vs-single-test
or manual-script-vs-pytest, which still shared db1.

**Fix:** each pytest **process** atomically claims a **unique** db from the pool
`[1..TEST_DB_POOL_MAX]` (default 15; db0 is production) via a held
`fcntl.flock` on a per-db lock file in a machine-global registry
(a fixed `/tmp/valor-pytest-db-claims-<port>/{n}.lock` — deliberately NOT
`$TMPDIR`, so a launchd worker and an interactive shell share one registry).
The flock is single-winner
across processes and is **released automatically by the kernel when a process
dies**, so a crashed/`SIGKILL`ed run never strands a db — no PID-liveness
heuristic or reaper. The claim is memoized for the process lifetime (all its
tests share the one db) and both `redis_test_db` and `_redis_test_db_num` read
it, so `redis_test_url` and the fixture never diverge. The `_run_cli_hook`
subprocess already derives its db from the live
`POPOTO_REDIS_DB.connection_pool.connection_kwargs['db']`, so it inherits the
claim automatically. If the pool is exhausted (more concurrent pytest processes
than test DBs) or the registry is unreachable, the claim falls back to the
legacy `worker_id+1` derivation with a WARNING — never worse than before. See
the `_claim_test_db()` / `_try_claim_db_slot()` docstrings in `tests/conftest.py`
for the full mechanism.

## Source of truth

- `tests/conftest.py` — `mock_claude_sdk_cleanup`, `agent_hooks_consistency_guard`, `_popoto_modules_with_redis_db()`, and `_claim_test_db()`/`_try_claim_db_slot()` docstrings are the authoritative mechanism explanations; this doc intentionally summarizes rather than duplicates them.
- `tests/unit/test_conftest_isolation_guards.py` — deterministic regression suite:
  - **Test A** — constructs the corrupt hooks-less-`agent` state directly, asserts the guard repairs it, and asserts a healthy `agent` is left untouched.
  - **Test B** — a falsifiable RED/GREEN binding gate: forces an equal-count popoto-module replacement directly (not a naive fresh import, which would false-green even the old len-only key) and asserts the compound trigger's identity branch catches it.
  - **Test C** — reproduces the #2037 create-then-`Model.query.filter(...)` split-brain directly against a corrupted read-path binding, then asserts the fixed re-point makes the identical round trip succeed.
  - **`TestPerProcessDbClaim`** (#2060) — asserts the per-process db claim: a claim is in-pool, memoized, and releasable; a slot held by another **live** process is skipped (two processes never share a db); a **dead** holder's slot is reclaimed; and pool exhaustion falls back to the legacy derivation with a WARNING.
- Umbrella issue [#1897](https://github.com/tomcounsell/ai/issues/1897) is the durable home for logging any future instance of this phantom-failure class; #2060 is the cross-process instance.

## See also

- [`docs/features/full-suite-pytest-lock.md`](full-suite-pytest-lock.md) — the companion **cross-run** concurrency doc (advisory lock serializing overlapping full-suite invocations). Read that doc for CPU oversubscription and hardcoded-sentinel collisions between separate `pytest` processes; read this doc for phantom failures within a single run.
