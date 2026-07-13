---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/1897
last_comment_id: 4933699033
revision_applied: true
revision_applied_at: 2026-07-13T06:59:48Z
---

# Test-isolation flakes under xdist: cross-file ordering and worker-setup failures (umbrella)

## Problem

Merge-gate full-suite runs periodically flag large batches of "failures" that are pure test-isolation artifacts: a test passes in isolation (and on the PR branch) but fails under a particular xdist parallel/loadfile ordering. On 2026-07-10 alone, PR #2005's gate flagged ~150 such phantom failures and PR #2006's gate flagged 73 — every one reproducible on clean `main` in a fresh worktree, none a real product regression. Each event burns supervisor time disproving phantoms, and it erodes trust in the gate.

Two instances are root-caused at plan time, and they share a single upstream mechanism: the autouse fixture `mock_claude_sdk_cleanup` in `tests/conftest.py` mutates `sys.modules` (evicting `agent.*`) in a way that is sensitive to import order and to `len(sys.modules)`.

- **Instance #1 — budget CLI-hook exit-code flip.** `tests/integration/test_tool_budget_enforcement.py::test_cli_hook_denies_over_budget_exit_2` writes an over-budget `AgentSession` in-process and reads it back from a real subprocess. Under a poisoning ordering, the in-process write lands on Redis db=0 while the subprocess reads db=1, so the session is "not found" → silent-allow → exit 0 instead of exit 2 → `1 failed, 21 passed`.
- **Instance #2 — `AttributeError` on `agent.hooks` at xdist worker setup.** `tests/unit/test_teammate_write_restriction.py` (and `test_ui_reflections_data.py`) fail during fixture setup, before any test body, when pytest's dotted-string `monkeypatch.setattr` / `patch` resolver walks `agent.hooks.*` while the `agent` package object is in a "hooks-less" stale state. The owner flagged this as the first target: "likely a single import-order/monkeypatch root cause behind most of the 73."

**Current behavior:**
- Certain unit/integration tests fail only under specific xdist worker compositions; they pass with `-n0`, in their natural full-file run, and often on re-run. The gate cannot distinguish these from real regressions without an expensive isolation control run.

**Desired outcome:**
- The two proven root causes are eliminated at the fixture layer so the affected tests are hermetic under any ordering. A deterministic regression test locks in each fix. The merge gate stops surfacing this class of phantom failure for the two instances, and the umbrella issue becomes the durable home for any future instance.

## Freshness Check

**Baseline commit:** `ac608db4`
**Issue filed at:** 2026-07-04T07:56:22Z (broadened to the umbrella class 2026-07-10T08:56:41Z)
**Disposition:** Minor drift

**File:line references re-verified:**
- `tests/unit/test_tool_budget_enforcement.py` (issue body) — **drifted**: the file no longer exists at that path. The test relocated to `tests/integration/test_tool_budget_enforcement.py:234` (real Redis + real subprocess). Last touched by the #1873/#1892 pipeline (`be18b5c2`) that surfaced the issue. Plan uses the integration path throughout.
- `tests/conftest.py:127-161` `mock_claude_sdk_cleanup` — confirmed present; evicts `agent.*` from `sys.modules` when the SDK entry was swapped during a test.
- `tests/conftest.py:167-185` `_POPOTO_MODULE_CACHE` / `_popoto_modules_with_redis_db()` — confirmed; cache invalidated on `len(sys.modules)` change.
- `tests/conftest.py:188-259` `redis_test_db` — confirmed; re-points canonical `rdb.POPOTO_REDIS_DB` unconditionally (line 234) but re-points the 31 by-value popoto captures only through the fragile cache.
- `agent/hooks/__init__.py` / `agent/hooks/pre_tool_use.py:45` — confirmed both import `claude_agent_sdk` at module load; `agent/__init__.py` binds `hooks` onto `agent` only transitively via `sdk_client.py:45`.
- `tests/unit/test_teammate_write_restriction.py:59-62` — confirmed dotted `monkeypatch.setattr("agent.hooks.pre_tool_use.TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES", ...)` in the `fake_project` fixture (runs at setup for every test in the file).

**Cited sibling issues/PRs re-checked:**
- #2004 — **open**, in build. Fixes merge-gate baseline decay (artifact freshness), NOT the flakes. Adjacent, must not duplicate.
- #1967 / PR #1981 — **merged**. Fixed concurrent full-suite core oversubscription + cross-run sentinel collisions. Different layer; does not touch either root cause here.
- #1873 / PR #1892 — **merged**. The pipeline during which instance #1 was surfaced; relocated the budget test to `tests/integration/`.

**Commits on main since issue was filed (touching referenced files):**
- `be18b5c2` Tech-debt: tool-budget + reclaim-bridge advisory cleanup (#1873/#1892) — relocated the budget test to `tests/integration/`; the failing test still exists there (line 234). Does not change the root cause.
- SDK version bumps (`0.2.111`→`0.2.115`) — irrelevant to the isolation mechanism.

**Active plans in `docs/plans/` overlapping this area:** `concurrent_pytest_coordination.md` (#1967, status Complete) is adjacent but non-overlapping — it addresses cross-run oversubscription and sentinel collisions, not the `_POPOTO_MODULE_CACHE` staleness or the `agent.hooks` eviction. No coordination needed.

**Re-validated at HEAD `d1c66fa4`** (plan-validation pass, same day): 7 commits landed on main since baseline `ac608db4`; none touched `tests/conftest.py`, the two failing tests, or `agent/hooks/`. Every cited reference re-confirmed exact — `_POPOTO_MODULE_CACHE_KEY: int` / `cur = len(_sys.modules)` at conftest.py:168/177, dotted `setattr` at `test_teammate_write_restriction.py:59-60`, budget test at `test_tool_budget_enforcement.py:234`. No new drift; disposition holds.

**Notes:** Instance #1's file path in the issue body is stale (unit→integration); all references corrected here. The bug is still live on `ac608db4` (owner reproduced the class on clean main today); a naive serial two-file repro passes, confirming the flake is xdist-worker-composition / `len(sys.modules)`-collision sensitive rather than a plain import-order bug.

## Prior Art

- **#1967 / PR #1981 (merged)** — Concurrent full-suite pytest coordination (advisory lock + per-run sentinel isolation). Fixed cross-run oversubscription and db=0 sentinel races. Adjacent; the flaky-filter/lock it adds can absorb residual contention but does not address single-run cross-file isolation.
- **PR #1584 (merged)** — "fix pre-existing test-suite failures — full suite green (test-only)." Prior test-suite hygiene sweep; shows the suite has a history of ordering-sensitive fragility.
- **PR #1290 (merged)** — "speed up unit suite via slow marks, fixture cache, terser output." The `_POPOTO_MODULE_CACHE` (`len(sys.modules)` memo) is the kind of speed optimization whose invalidation heuristic is the instance-#1 defect. No prior attempt targeted this specific cache.
- No prior issue/PR addressed the `agent.hooks`-stale-parent or the `_POPOTO_MODULE_CACHE`-staleness root causes. This is the first fix for both.

## Research

No relevant external findings needed — this is purely internal test-infrastructure work (pytest-xdist, CPython import machinery, popoto Redis binding). The mechanisms were proven directly against the codebase (see Spike Results). Two library facts relied upon are standard behavior: (1) pytest's `MonkeyPatch.setattr`/`patch` dotted-string resolver walks the path by attribute access with an `__import__` recovery that hits the `sys.modules` cache; (2) CPython `_find_and_load` short-circuits on a `sys.modules` cache hit and does not re-bind a submodule attribute onto a freshly-imported parent package.

## Spike Results

Two code-read/prototype spikes ran at plan time (parallel Explore agents) and PROVED both root causes empirically.

### spike-1: agent.hooks AttributeError mechanism
- **Assumption**: "The `agent.hooks` AttributeError is a lazy-submodule import-order bug seeded by `mock_claude_sdk_cleanup`'s eviction."
- **Method**: code-read + prototype (reproduced the corrupt state in a Python REPL)
- **Finding**: Confirmed and made precise. The crash is raised inside pytest's own `_pytest/monkeypatch.py` `resolve()` (attribute-walk at line 71/86), not project code. It fires deterministically iff `sys.modules["agent"]` is present but lacks the `hooks` attribute WHILE `sys.modules["agent.hooks"]` is still cached. `agent/hooks/__init__.py` imports the SDK; `agent`'s `hooks` attribute is bound only transitively via `sdk_client.py:45`. When the top-level `agent` object is replaced/re-imported while `agent.hooks` stays cached, CPython's cache-hit short-circuit skips re-binding `hooks` onto the new `agent` → hooks-less parent → `AttributeError`. FULL eviction of `agent.*` self-heals (next import rebuilds cleanly); a PARTIAL/stale mutation corrupts. Seeders: module-level `from agent.hooks... import` in `test_pre_tool_use_start_stage.py:14`, `test_post_tool_use_stage_completion.py:14`, `test_stop_hook_review.py:8`, `test_tool_call_delivery.py:25`, `hooks/test_pre_compact_hook.py:18`. Victim/trigger: `test_teammate_write_restriction.py:59-62`.
- **Confidence**: high
- **Impact on plan**: The fix must guarantee `agent`/`agent.hooks` consistency after ANY `sys.modules` mutation (not only SDK swaps). A detect-and-repair guard that evicts all `agent.*` when the hooks-less-parent state is observed neutralizes the corruption regardless of which test created it (SDK swap, `importlib.reload`, `patch.dict`).

### spike-2: budget CLI-hook Redis-db split-brain
- **Assumption**: "Instance #1 leaks Redis or module-level budget state into the CLI-hook exit path."
- **Method**: code-read
- **Finding**: No budget-state leak — the verdict is pure over `session.tool_call_count` (Redis), and every threshold override uses auto-reverted `monkeypatch.setattr`; `_run_cli_hook` sets `MAX_TOOL_CALLS_PER_SESSION` in the subprocess env explicitly. The real leak is the Redis-**db binding**: `redis_test_db` re-points the canonical `rdb.POPOTO_REDIS_DB` unconditionally but re-points popoto's 31 by-value `POPOTO_REDIS_DB` captures (write path: `models/base.py:59`, `models/query.py:62`, `models/db_key.py:34`, `models/encoding.py:366`, `fields/indexed_field_mixin.py:51`) only via `_POPOTO_MODULE_CACHE`, which is memoized on `len(sys.modules)` and rebuilt only when that length CHANGES. `mock_claude_sdk_cleanup`'s `agent.*` eviction perturbs `len(sys.modules)` non-monotonically; a colliding length leaves the cache stale, so a write-path submodule keeps its db=0 binding. In-process write → db=0; `_run_cli_hook` derives the subprocess db from the canonical binding → db=1; subprocess finds nothing → exit 0.
- **Confidence**: high
- **Impact on plan**: The fix must make the popoto db re-point ordering-independent — invalidate `_POPOTO_MODULE_CACHE` on a compound trigger where **object identity** is a mandatory branch and count/len is never the sole key. A count or `frozenset`-of-names key alone still false-greens the actual corruption: `mock_claude_sdk_cleanup` can evict and a later import can **re-create** a popoto db-holding module object under the *same name* at the *same count*, so a name/count signal sees no change while the cached object is now stale (pointing at the pre-swap `POPOTO_REDIS_DB` binding). The identity branch — rebuild when `any(sys.modules.get(name) is not mod for name, mod in cache.items())` — is the only signal that survives an equal-count, equal-name-set replacement; a `len(sys.modules)`-change branch complements it to catch brand-new never-cached db-holders (see Technical Approach Fix 1). (Resolves Open Question 2: compound len-OR-identity; neither count nor frozenset alone.)

## Data Flow

**Instance #2 (agent.hooks corruption):**
1. **Worker collection**: a loadfile worker loads a file with a module-level `from agent.hooks... import` → `sys.modules["agent.hooks"]` cached, `agent` carries `hooks`.
2. **Mutation**: a later test swaps `sys.modules["claude_agent_sdk"]` (or reloads/patches an `agent.*` entry) → `mock_claude_sdk_cleanup` teardown evicts `agent.*`, OR a partial mutation replaces `agent` while `agent.hooks` stays cached.
3. **Corruption**: the next `import agent` (via `sdk_client.py:45 from agent.hooks import ...`) hits the cached `agent.hooks` and does NOT re-bind `hooks` onto the fresh `agent` → hooks-less parent.
4. **Crash**: `test_teammate_write_restriction.py:59-62` (or a dotted `patch` in `test_ui_reflections_data.py`) forces pytest's `resolve()` attribute-walk → `AttributeError: 'module' object at agent.hooks has no attribute 'hooks'`, at fixture setup.

**Instance #1 (Redis-db split-brain):**
1. **Entry**: `make_session(calls=10)` → `AgentSession.create/save` writes through popoto submodules whose `POPOTO_REDIS_DB` capture points at whatever db `redis_test_db` last re-pointed them to.
2. **Stale re-point**: `_POPOTO_MODULE_CACHE` is stale (len collision) → a write-path submodule was NOT re-pointed → still db=0. Session written to db=0.
3. **Subprocess read**: `_run_cli_hook` reads the db from the canonical binding (`POPOTO_REDIS_DB.connection_pool...db` → db=1), passes `REDIS_URL=.../1` to the subprocess.
4. **Output**: subprocess `get_by_id` on db=1 finds nothing → "no-session silent allow" → exit 0; assertion expects exit 2 → fail.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none in product code. Changes are confined to `tests/conftest.py` fixtures plus new test files. No change to `agent/`, `models/`, or `.claude/hooks/` runtime code.
- **Coupling**: reduces hidden coupling — the popoto db re-point stops depending SOLELY on an unrelated global (`len(sys.modules)` remains only as a cheap growth detector, backstopped by the identity branch); the `agent` import consistency stops depending on which test last swapped the SDK.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — fixture changes are revertible in one commit; regression tests are additive.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm scope stays at the two proven root causes; don't chase every phantom)
- Review rounds: 1 (fixture changes are subtle; a reviewer should sanity-check the cache-invalidation key and the repair guard)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "import redis; redis.Redis().ping()"` | Instance-#1 regression test uses real Redis + subprocess |
| pytest-xdist installed | `python -c "import xdist"` | Reproduce ordering under `-n auto --dist=loadfile` |

Run via `python scripts/check_prerequisites.py docs/plans/xdist-test-isolation-flakes.md` if the checker parses this table; otherwise run the commands directly.

## Solution

### Key Elements

- **Robust popoto db-cache invalidation (fixes #1)**: `_POPOTO_MODULE_CACHE` invalidation on a **compound trigger** — `len(sys.modules)` change (catches brand-new lazily-imported db-holders) OR **object-identity divergence** of the cached popoto db-holding modules (catches equal-count replacement/eviction). Count/len may gate additions but must never be the SOLE invalidation key, and identity alone cannot see a never-cached new holder — both branches are required. **Fix 1 also subsumes issue #2037** (real-record create-then-`Model.query.filter(...)` visibility race under `--dist=loadfile` co-scheduling): the same stale cache leaves popoto's read-path binding (`popoto/models/query.py:62`) unpatched while the write path (`popoto/models/base.py:59`) is patched, so a just-created record lands on one db and `filter` reads another. Test C below locks this manifestation in.
- **agent-hooks consistency guard (fixes #2)**: a small autouse repair that detects the hooks-less-`agent`-parent state and evicts all `agent.*` so the next import self-heals.
- **Deterministic regression tests**: one per instance, each reproducing the corrupt precondition directly (no reliance on a fragile multi-file ordering) and asserting hermeticity.

### Flow

Poisoning ordering → (Fix 1) every imported popoto db-holding submodule is re-pointed to the test db each test → in-process write and subprocess read agree on db → exit 2 as expected.

Poisoning ordering → (Fix 2) hooks-less `agent` parent is detected at setup and repaired by full `agent.*` eviction → next `agent.hooks.*` resolution self-heals → `monkeypatch.setattr` succeeds.

### Technical Approach

- **Fix 1 — `tests/conftest.py` `_popoto_modules_with_redis_db()` / cache key. COMPOUND trigger: `len(sys.modules)` change OR object-identity divergence.** Neither signal alone is sufficient, and the two are complementary:
  - **`len(sys.modules)` alone is insufficient** (critique finding A): an eviction-then-reimport can yield an *equal* `len(sys.modules)` and an equal set of `popoto` names but a different, stale module object — the len key sees no change and the cache keeps pointing at the pre-swap `POPOTO_REDIS_DB` binding.
  - **Object-identity alone is insufficient** (this-revision blocker 1): an identity comprehension over *already-cached* names — `any(sys.modules.get(name) is not mod ...)` — structurally cannot see a **brand-new lazily-imported** `popoto` db-holder that was never in the cache. It would silently miss the new submodule, leaving it bound to db=0 → the same split-brain the fix is meant to close.
  - **Therefore rebuild when EITHER holds:** `len(sys.modules) != _POPOTO_MODULE_CACHE_LEN` (cheap detector of any net import/growth — catches the new lazy db-holder) **OR** `any(sys.modules.get(name) is not mod for name, mod in _POPOTO_MODULE_CACHE.items())` (catches equal-count in-place replacement and eviction, which `get()` returns `None` for). Together these are complete: a genuinely-new db-holder cannot be imported without either growing `len(sys.modules)` or evicting a cached member (which trips the identity branch).
  - **Cache shape + return contract (this-revision blocker 2).** Store the cache internally as a `{name: module}` mapping (names are required to run `sys.modules.get(name)` in the identity check). **`_popoto_modules_with_redis_db()` MUST return `list(_POPOTO_MODULE_CACHE.values())`** — a list of module objects — because its sole consumer, `redis_test_db` at `tests/conftest.py:244-246`, iterates the result and does `_mod.POPOTO_REDIS_DB = test_client`. Returning the dict itself would iterate str keys → `AttributeError` on `str.POPOTO_REDIS_DB` → every popoto re-point silently stops. Line 244 is an explicit change site (the loop variable stays a module; only the internal cache structure and the return expression change).
  - This preserves the fast-path amortization: the `len` comparison is O(1); the identity comprehension iterates only the small cached set (~31 entries), and the expensive full `sys.modules` scan runs only on a genuine `len` change or identity divergence. `redis_test_db`'s re-point loop then always covers every currently-live db-holding popoto submodule, ordering-independent.
  - **Do NOT use `len`/count/`frozenset`-of-names as the *sole* key** — each alone false-greens one of the two vectors above. The compound `len`-OR-identity trigger is required (see the RED/GREEN binding gate in Success Criteria).
- **Fix 2 — `tests/conftest.py` agent-consistency guard (separate autouse fixture — see Resolved Decisions #1).** Add a minimal autouse guard, kept independent of `mock_claude_sdk_cleanup`, that at setup detects `"agent" in sys.modules and "agent.hooks" in sys.modules and not hasattr(sys.modules["agent"], "hooks")` and repairs it by deleting every `agent.*` key from `sys.modules` (proven to self-heal on next import). This is root-cause-agnostic: it neutralizes the corruption regardless of whether an SDK swap, `importlib.reload`, or `patch.dict` created it, so it also covers `test_ui_reflections_data.py` and any other victim. Keep the existing `mock_claude_sdk_cleanup` eviction (it fixes a separate contamination); the guard is belt-and-suspenders for the partial-mutation vectors that eviction's `sdk_after != sdk_before` condition misses.
- **Prefer the guard over deleting the seeders' module-level imports** — removing the `from agent.hooks... import` lines would only move the seeding, not fix the corruption vector, and would churn six unrelated test files.
- **Do not touch product code.** `agent/__init__.py`'s eager import chain and `agent/hooks/__init__.py`'s SDK import are correct in production (a full fresh import is always consistent); the defect is test-only sys.modules churn.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The instance-#1 subprocess path already exercises the "no-session silent allow" fail-open branch (`.claude/hooks/pre_tool_use.py:254-255`). The regression test asserts the observable correct outcome (exit 2 when the session IS resolvable), so a silent db mismatch can no longer masquerade as fail-open. No `except Exception: pass` is added in scope.
- [ ] The Fix-2 guard must not raise if `agent` is not imported yet — guard body is a pure `sys.modules` membership/`hasattr` check; add a test asserting it is a no-op when `agent` is absent.

### Empty/Invalid Input Handling
- [ ] Fix-1 cache key: assert correct behavior when zero `popoto` modules are imported (pure-logic tests) — the fast path must still skip the scan and the fixture must yield without error.
- [ ] Fix-2 guard: assert no-op when `agent.hooks` is absent, and when `agent` carries `hooks` normally (healthy state must be left untouched).

### Error State Rendering
- [ ] No user-visible output — this is test infrastructure. The "error state" is a spurious test failure; the regression tests assert the previously-failing conditions now pass deterministically.

## Test Impact

- [ ] `tests/conftest.py::_popoto_modules_with_redis_db` / `redis_test_db` — UPDATE: change cache invalidation from the sole `len(sys.modules)` key to the compound trigger (`len` change OR object-identity divergence) over the cached `{name: module}` mapping; return contract stays a list of module objects (`list(cache.values())`) for the `redis_test_db` re-point loop at `tests/conftest.py:244-246`. Existing behavior preserved for the common case; verified by existing suite staying green.
- [ ] `tests/conftest.py` — ADD a separate new autouse fixture for the agent-hooks consistency guard (kept independent of `mock_claude_sdk_cleanup`, whose existing eviction behavior is retained unchanged). Per Resolved Decisions #1.
- [ ] `tests/integration/test_tool_budget_enforcement.py::test_cli_hook_denies_over_budget_exit_2` — no code change; must pass under the poisoning ordering after Fix 1 (this is the instance-#1 acceptance).
- [ ] `tests/unit/test_teammate_write_restriction.py` (all classes via `fake_project`) — no code change; must pass under `--dist=loadfile` after Fix 2 (instance-#2 acceptance).
- [ ] `tests/unit/test_ui_reflections_data.py` — no code change; covered by Fix 2.
- [ ] NEW `tests/unit/test_conftest_isolation_guards.py` — REPLACE/create: deterministic regression tests for both fixes (reproduce the corrupt precondition directly; assert repair + hermeticity), plus the #2037 real-record create-then-`query.filter` round-trip regression (Test C).

## Rabbit Holes

- **Chasing the exact multi-file ordering that triggers the len-collision.** The collision is machine/collection-order dependent and not worth reproducing exactly. Reproduce the corrupt PRECONDITION directly in a unit test instead.
- **"Fixing" popoto to reference `POPOTO_REDIS_DB` indirectly.** The 31 by-value captures are a popoto-internal design; re-pointing them in the fixture is the right seam. Do not fork/patch popoto.
- **Rewriting `mock_claude_sdk_cleanup` from scratch or removing the SDK mock.** It fixes a real, separate contamination (module-level SDK mocks bleeding across the session). Leave its core behavior; only add the guard.
- **Trying to make the whole suite ordering-independent in one pass.** Scope to the two proven root causes. New instances get logged under this umbrella as they are observed and root-caused.
- **Deleting the seeders' module-level `from agent.hooks` imports.** Cosmetic; does not remove the corruption vector.

## Risks

### Risk 1: Fix-1 identity check reintroduces a per-test O(n) scan
**Impact:** Unit suite slows if the cache rebuilds every test or the identity check walks all of `sys.modules` per test.
**Mitigation:** The per-test identity check iterates only the **cached set** (~31 popoto db-holders), not all ~1500 `sys.modules` entries — `any(sys.modules.get(name) is not mod for name, mod in cache.items())` is a dict-get per cached entry, cheap and constant after warmup. The `len` branch is an O(1) comparison per test (same cost as the pre-fix key). The expensive full `sys.modules` comprehension runs only when either branch fires (net import/eviction changes `len`, or identity divergence) — no more often than the pre-fix len-key rebuilt, plus the rare identity-divergence case the old key wrongly skipped. Benchmark unit-suite wall time before/after; require no material regression (captured as a Success Criterion).

### Risk 2: The agent-hooks guard masks a real product import bug
**Impact:** A genuine `agent.hooks` import failure could be silently repaired in tests.
**Mitigation:** The guard only acts on the specific corrupt state (parent present, `hooks` submodule cached, attribute missing) — a state that cannot occur from a clean import. A genuine import error raises before this state is reachable. Add an assertion in the regression test that a healthy `agent` is left untouched.

### Risk 3: Fix is incomplete — residual phantom failures persist
**Impact:** The gate still surfaces isolation phantoms from a third, un-root-caused mechanism.
**Mitigation:** Scope is explicitly the two proven instances. Success is measured against those two, not "zero phantoms forever." The umbrella issue stays open to collect and root-cause future instances. Re-run the exact 2026-07-10 poisoning orderings (budget test cross-file; teammate/reflections under loadfile) as acceptance.

## Race Conditions

No race conditions in the classic concurrency sense — xdist workers are separate processes and each worker is single-threaded. The "races" here are import-ordering and cache-staleness within a single worker's sequential test run, addressed by the deterministic fixture fixes above. State prerequisite (instance #1): every popoto db-holding submodule must be re-pointed to the test db BEFORE `make_session` writes — enforced by the corrected `redis_test_db` invalidation. State prerequisite (instance #2): `agent` must carry a valid `hooks` binding BEFORE any dotted `agent.hooks.*` resolution — enforced by the setup-phase repair guard.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2004] Merge-gate baseline decay / artifact-freshness enforcement — a distinct concern already in build; this plan does not touch baseline artifact handling.
- Nothing else deferred — the two root causes proven at plan time are both in scope. There is no third concrete, observed-and-root-caused instance to plan; future instances will be logged under this umbrella as they are observed (planning a fix for an un-observed flake is not possible).

## Update System

No update system changes required — this is purely test-infrastructure work in `tests/conftest.py` and new test files. No new dependencies, no `scripts/update/` changes, no `migrations.py` entry (no Popoto schema change).

## Agent Integration

No agent integration required — no CLI entry point, no MCP surface, no bridge import. The change is invisible to the running agent; it only affects the test suite's isolation behavior.

## Documentation

### Feature Documentation
- [ ] Update `tests/README.md` — add a "Test isolation under xdist" blind-spot/gotcha entry documenting: the two proven root causes, the `_POPOTO_MODULE_CACHE` invalidation contract (compound trigger: `len(sys.modules)` change OR object-identity divergence of the cached popoto modules; count/len may gate additions but must never be the SOLE invalidation key — a sole count/name-set signal false-greens an equal-count module replacement, and identity alone misses never-cached new holders), and the agent-hooks consistency guard. Point future flake investigations here.
- [ ] Update `docs/features/full-suite-pytest-lock.md` OR add a short `docs/features/test-isolation-hardening.md` cross-referencing #1967/#1981 (concurrency) vs this umbrella (single-run isolation), so the two are not conflated.

### Inline Documentation
- [ ] Docstring on the corrected `_popoto_modules_with_redis_db` cache key explaining WHY a sole `len(sys.modules)` key is wrong (non-monotonic under `agent.*` eviction; equal-count replacement invisible), why identity alone is also insufficient (never-cached new db-holders invisible), and hence the compound `len`-OR-identity trigger.
- [ ] Docstring on the agent-hooks guard explaining the CPython cache-hit / hooks-less-parent mechanism and why full `agent.*` eviction self-heals.

## Success Criteria

**Authoritative (falsifiable) gates:**
- [ ] **Fix-1 binding gate is RED before / GREEN after.** `tests/unit/test_conftest_isolation_guards.py::Test B` forces the equal-count popoto-module replacement directly (seed a stale cache, swap a cached db-holder's object under the same name), asserts `redis_test_db` re-points the fresh object's `POPOTO_REDIS_DB` to the test client, and is verified to FAIL on the pre-fix sole-`len` key and PASS on the compound trigger (via its identity branch). (Not the naive fresh-import check — that false-greens the len key.)
- [ ] **Fix-2 guard gate.** `tests/unit/test_conftest_isolation_guards.py::Test A` constructs the corrupt hooks-less-`agent` state directly, asserts the guard repairs it (dotted `monkeypatch.setattr("agent.hooks.*", ...)` resolves without AttributeError) and leaves a healthy `agent` untouched.
- [ ] **Instance-#2 batch acceptance (the ~73-file gate).** The ~73-file batch flagged by PR #2006's gate — or, if the exact list is unrecoverable, the full `tests/unit/` suite as its superset — runs under `--dist=loadfile` repeatedly across **varying worker counts** (`-n 2`, `-n 4`, `-n auto`; worker-count variation is the composition-perturbation lever since `pytest-randomly` is not installed) with **zero** `agent.hooks` AttributeError at fixture setup. The ~150-failure batch from PR #2005's gate shares the same two mechanisms per the recon; the `tests/unit/` superset run covers its unit-scope members, and any #2005 member NOT explained by these two fixes is filed as a follow-up instance under the umbrella rather than silently claimed. This is a hard check, not a framing claim.
- [ ] New `tests/unit/test_conftest_isolation_guards.py` deterministically reproduces both corrupt preconditions and asserts the fixes repair them.
- [ ] **#2037 round-trip gate (Test C).** `tests/unit/test_conftest_isolation_guards.py::Test C` reproduces the real-record create-then-`query.filter` split-brain directly (read-path binding pointed at a different test db → created record invisible to `filter`), then asserts the fixed re-point makes the identical round trip succeed. This is the falsifiable acceptance for #2037, which is closed as subsumed when this plan merges.
- [ ] **`len(sys.modules)` is never the SOLE invalidation key.** Count/len may gate additions (the compound trigger's growth branch may legitimately reference `len((sys|_sys).modules)`), but the invalidation predicate MUST also contain the per-entry identity check: `grep -n "is not" tests/conftest.py` matches the identity comprehension over the cached `{name: module}` entries, and code review confirms the rebuild fires on identity divergence even when `len` is unchanged (this is exactly what Test B proves at runtime).

**Corroborating (best-effort, may pass vacuously):**
- [ ] `tests/integration/test_tool_budget_enforcement.py::test_cli_hook_denies_over_budget_exit_2` passes under the 2026-07-10 poisoning cross-file ordering (previously `1 failed, 21 passed`). Best-effort only — the poisoning composition is collection-order/machine dependent and can pass vacuously; the authoritative signal is the Fix-1 binding gate above.
- [ ] `tests/unit/test_teammate_write_restriction.py` and `tests/unit/test_ui_reflections_data.py` pass under `-n auto --dist=loadfile` across repeated runs (no `agent.hooks` AttributeError at setup). Corroborates the batch acceptance and Fix-2 guard gates.

**General:**
- [ ] Full unit suite wall-time shows no material regression vs baseline.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (conftest-fixes)**
  - Name: `conftest-builder`
  - Role: Implement Fix 1 (popoto cache key) and Fix 2 (agent-hooks guard) in `tests/conftest.py`; write the deterministic regression tests.
  - Agent Type: builder
  - Domain: async/imports + Redis/Popoto (paste the sys.modules/import-machinery and Popoto-binding rules from `DOMAIN_FRAMING.md`)
  - Resume: true

- **Validator (isolation-acceptance)**
  - Name: `isolation-validator`
  - Role: Verify both instances pass under their poisoning orderings and under `--dist=loadfile`; confirm no unit-suite time regression; confirm healthy state untouched.
  - Agent Type: validator
  - Resume: true

- **Documentarian (test-isolation-docs)**
  - Name: `isolation-doc`
  - Role: `tests/README.md` blind-spot entry + the isolation-hardening doc/cross-reference.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 core agents (builder, validator, documentarian) suffice. For the import-machinery and Popoto specifics, add `Domain:` framing to the builder task rather than a specialist agent.

## Step by Step Tasks

### 1. Fix popoto db-cache invalidation (instance #1)
- **Task ID**: build-popoto-cache
- **Depends On**: none
- **Validates**: `tests/integration/test_tool_budget_enforcement.py::test_cli_hook_denies_over_budget_exit_2`, new `tests/unit/test_conftest_isolation_guards.py`
- **Informed By**: spike-2 (confirmed: len(sys.modules) memo stales the db re-point)
- **Assigned To**: conftest-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tests/conftest.py`, replace the sole-`len(sys.modules)` `_POPOTO_MODULE_CACHE_KEY` with the **compound trigger**: store the cache as `{name: module}` (plus a `_POPOTO_MODULE_CACHE_LEN` memo) and rebuild when `len(sys.modules) != _POPOTO_MODULE_CACHE_LEN` (catches brand-new lazily-imported db-holders) OR `any(sys.modules.get(name) is not mod for name, mod in cache.items())` (catches equal-count replacement/eviction). Neither branch alone suffices — see Technical Approach Fix 1.
- **Return contract:** `_popoto_modules_with_redis_db()` must return `list(cache.values())` — module objects, never the dict — because `redis_test_db` (`tests/conftest.py:244-246`) iterates the result doing `_mod.POPOTO_REDIS_DB = test_client`; iterating dict keys would silently break every re-point.
- Preserve the fast-path skip when no popoto modules are imported.
- Add docstring explaining why a sole `len(sys.modules)` key is wrong (non-monotonic under `agent.*` eviction; equal-count replacement invisible), why identity alone is also insufficient (never-cached new db-holders invisible) — hence the compound trigger.

### 2. Add agent-hooks consistency guard (instance #2)
- **Task ID**: build-agent-hooks-guard
- **Depends On**: none
- **Validates**: `tests/unit/test_teammate_write_restriction.py`, `tests/unit/test_ui_reflections_data.py`, new `tests/unit/test_conftest_isolation_guards.py`
- **Informed By**: spike-1 (confirmed: hooks-less parent + cached agent.hooks → pytest resolve() AttributeError; full eviction self-heals)
- **Assigned To**: conftest-builder
- **Agent Type**: builder
- **Parallel**: true
- Add an autouse setup-phase guard in `tests/conftest.py` that detects the hooks-less-`agent`-parent state and evicts all `agent.*` from `sys.modules`.
- Guard must be a no-op for healthy and absent `agent`.
- Add docstring explaining the CPython cache-hit mechanism.

### 3. Deterministic regression tests
- **Task ID**: build-regression-tests
- **Depends On**: build-popoto-cache, build-agent-hooks-guard
- **Assigned To**: conftest-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_conftest_isolation_guards.py`.
- Test A: construct the corrupt `agent`/`agent.hooks` state directly; assert the guard repairs it and a dotted `monkeypatch.setattr("agent.hooks.pre_tool_use.<attr>", ...)` resolves without AttributeError; assert healthy state untouched.
- **Test C (#2037 real-record round-trip regression):** deterministically reproduce the create-then-`query.filter` split-brain that #2037 observed under `--dist=loadfile` co-scheduling. Construct the corrupt precondition directly (no multi-file ordering): with `redis_test_db` active, re-point ONE popoto db-holding read-path module's local binding (`sys.modules["popoto.models.query"].POPOTO_REDIS_DB`) to a client on a **different test db** (never db 0 / production — derive both dbs from the per-worker test-db scheme), create a real record (e.g. `PipelineLedger`-shaped or a test-scoped Popoto model) via `save()`, and assert `Model.query.filter(...)` misses it (demonstrating the #2037 mechanism). Then run the fixed re-point path (`_popoto_modules_with_redis_db()` walk) and assert the identical create-then-`filter` round trip finds the record. Filter on a string field, not a bool (Popoto stores bools as strings "True"/"False" — a known filter footgun). Clean up created records via the ORM (`instance.delete()`), never raw Redis.
- **Test B (falsifiable binding gate — MUST force the collision directly, not rely on a fresh import):** After popoto is imported and the cache is warm, seed the *stale* precondition the len-key produced in production: swap a cached popoto db-holding module object in `sys.modules` for a **fresh module object of the same name** carrying its own `POPOTO_REDIS_DB` — an equal-count, equal-name-set replacement — and pre-seed the post-fix globals (`_POPOTO_MODULE_CACHE` mapping + `_POPOTO_MODULE_CACHE_LEN` memo, both of which the compound design KEEPS) to the current state so the `len` branch does NOT fire and only the identity branch can catch the swap. Then assert `_popoto_modules_with_redis_db()` returns the **new** object (identity rebuild) and that `redis_test_db` re-points that submodule's `POPOTO_REDIS_DB` to the test client (`db != 0`). This test is engineered to be **RED on the pre-fix sole-len key** (stale cache → returns the old object → db not re-pointed) and **GREEN on the compound trigger** (identity branch fires). Do NOT rely on the naive "import a fresh popoto submodule mid-test" check as the sole assertion: it changes `len(sys.modules)`, so even the old sole-len key rebuilds and the test false-greens without proving the fix. Optionally add a companion assertion for the len branch: import a genuinely-new popoto db-holder and assert it appears in the rebuilt cache.

### 4. Validate acceptance
- **Task ID**: validate-isolation
- **Depends On**: build-regression-tests
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- **Primary (authoritative) gate:** confirm the deterministic RED/GREEN regression tests in `tests/unit/test_conftest_isolation_guards.py` pass (Test A guard repair, Test B forced-collision binding). These are the falsifiable acceptance signals.
- **Instance-#2 batch acceptance (promoted from framing to a hard check):** run the ~73-file batch that PR #2006's gate flagged — the actual `agent.hooks` AttributeError repro. Reconstruct it as `tests/unit/` under `--dist=loadfile` (the batch that surfaced the 73), repeated across **varying worker counts** (`-n 2`, `-n 4`, `-n auto` — the composition-perturbation lever; `pytest-randomly` is not installed, so do not plan on seed variation), and confirm **zero** `agent.hooks` AttributeError at fixture setup. If the exact 73-file list is not recoverable, run the full `tests/unit/` suite under `--dist=loadfile` as the superset and assert the same. This proves the "most of the 73" claim rather than asserting it. For PR #2005's ~150 batch: the superset run covers its unit-scope members; any member not explained by the two fixes is filed as a follow-up umbrella instance, not claimed.
- **Best-effort (may pass vacuously):** re-run the exact 2026-07-10 poisoning orderings (budget test cross-file; teammate/reflections under loadfile) and confirm exit 2 / no AttributeError. Treat these as corroborating, NOT authoritative — the precise poisoning composition is collection-order/machine dependent and can pass vacuously on any given run, so they cannot be the sole gate.
- Confirm unit-suite wall time has no material regression.
- Report pass/fail.

### 5. Documentation
- **Task ID**: document-isolation
- **Depends On**: validate-isolation
- **Assigned To**: isolation-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Add the `tests/README.md` blind-spot entry and the isolation-hardening doc/cross-reference.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-isolation
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full unit + integration suites; verify all success criteria including docs.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Budget CLI-hook test passes | `pytest "tests/integration/test_tool_budget_enforcement.py::test_cli_hook_denies_over_budget_exit_2" -q` | exit code 0 |
| Teammate + reflections pass under loadfile | `pytest tests/unit/test_teammate_write_restriction.py tests/unit/test_ui_reflections_data.py -n auto --dist=loadfile -q` | exit code 0 |
| Regression tests exist and pass | `pytest tests/unit/test_conftest_isolation_guards.py -q` | exit code 0 |
| Identity branch present in cache invalidation (len never the sole key) | `grep -n "is not" tests/conftest.py` | exit code 0 (matches the identity comprehension; Test B proves it fires with `len` unchanged) |
| No product code changed | `git diff --name-only main -- agent/ models/ .claude/hooks/ \| wc -l` | output contains 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

**Verdict:** two critique passes; all findings from both passes are embedded below and resolved in this revision.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Major | correctness | Fix-1 cache invalidation keyed on popoto module names/count still false-greens an equal-count eviction-then-reimport (same name, new stale object). | Solution › Technical Approach (Fix 1); Task 1; spike-2 Impact | Identity check is mandatory: rebuild when `any(sys.modules.get(name) is not mod for name, mod in cache.items())`. Not count, not frozenset as sole key. Superseded in detail by pass-2 Blocker 1 (compound trigger). Resolves Open Question 2. |
| Blocker (pass 2) | correctness | Identity-ONLY predicate is internally contradictory: `any([])` is `False` on an empty/pre-warm cache (initial build never triggers) and an identity check over already-cached names structurally cannot detect a brand-new never-cached popoto db-holder — reopening the instance-#1 split-brain. | Technical Approach Fix 1; Task 1; Key Elements; Resolved Decisions #2 | **Compound trigger:** rebuild when `len(sys.modules) != _POPOTO_MODULE_CACHE_LEN` (growth/new-holder branch) OR per-entry identity divergence. Count/len may gate additions but must never be the SOLE invalidation key. |
| Blocker (pass 2) | correctness | Cache-shape change (`{name: module}`) silently breaks the consumer contract: `redis_test_db` iterates the return value doing `_mod.POPOTO_REDIS_DB = ...`; returning the dict iterates str keys → re-point silently stops. | Technical Approach Fix 1 (return contract); Task 1 | `_popoto_modules_with_redis_db()` MUST return `list(cache.values())`; `tests/conftest.py:244-246` named as an explicit change site. |
| Minor (pass 2) | acceptance | Batch gate said "repeated seeds" but `pytest-randomly` is not installed; seed variation is not available. | Success Criteria (batch acceptance); Task 4 | Reworded to varying worker counts (`-n 2/4/auto`) as the composition-perturbation lever. |
| Minor (pass 2) | test-design | Test B pre-seeded `_POPOTO_MODULE_CACHE_KEY`, a global the fix was deleting. | Task 3 Test B | Compound design KEEPS a len memo (`_POPOTO_MODULE_CACHE_LEN`); Test B pre-seeds the post-fix globals so only the identity branch can catch the swap. |
| Minor (pass 2) | scope-claims | PR #2005's ~150-failure batch cited in Problem but only #2006's 73 was covered by acceptance. | Success Criteria (batch acceptance); Task 4 | `tests/unit/` superset run covers #2005's unit-scope members; unexplained members are filed as follow-up umbrella instances, not claimed. |
| Major | acceptance-falsifiability | Acceptance leaned on the "2026-07-10 poisoning ordering" re-run, which can pass vacuously; a `len()`/count key false-greens the naive fresh-import test. | Success Criteria (authoritative gates); Task 3 Test B; Task 4 | Binding gate forces the collision directly (seed stale cache + swap a cached db-holder object under the same name) and must be RED pre-fix / GREEN post-fix. Poisoning re-run downgraded to best-effort/corroborating. |
| Medium | test-coverage | The ~73-file batch (Instance 2, `agent.hooks` AttributeError) was framing ("most of the 73"), not a proven check. | Success Criteria (batch acceptance); Task 4 validator | Promoted to a hard Success Criterion + validator step: run the ~73-file batch (or full `tests/unit/` superset) under `-n auto --dist=loadfile`, assert zero `agent.hooks` AttributeError. |
| Nit (D) | scope-clarity | Open Question 2 (count vs frozenset) left open. | Resolved Decisions; spike-2 Impact | Resolved in-plan: object identity, neither count nor frozenset. |
| Nit (E) | verification | Success-criterion grep for `len(sys.modules)` missed the aliased `import sys as _sys` (`_sys.modules`) form used in `tests/conftest.py`. | Success Criteria grep; Verification table | Original grep gate covered both forms; that gate is superseded by pass-2 Blocker 1 — the compound trigger legitimately references `len((sys\|_sys).modules)`, so the criterion is now "never the SOLE key" (identity branch must exist), not "no reference". |

---

## Resolved Decisions

All three open questions are resolved as of this critique-revision pass; none remain blocking for build.

1. **Guard placement — RESOLVED: separate autouse fixture.** The agent-hooks consistency guard is a distinct, root-cause-agnostic concern from `mock_claude_sdk_cleanup`'s SDK-swap eviction; keeping it in its own small autouse fixture keeps the two independent and the guard applicable to non-SDK mutation vectors (`importlib.reload`, `patch.dict`).
2. **Fix-1 cache key shape — RESOLVED: compound `len`-OR-identity trigger (pass-2 refinement of "object identity").** A count or `frozenset`-of-names key ALONE false-greens the corruption vector (equal-count eviction-then-reimport yields a same-name stale object); identity ALONE cannot see a brand-new never-cached popoto db-holder (and `any([])` no-ops pre-warm). Therefore rebuild when `len(sys.modules) != _POPOTO_MODULE_CACHE_LEN` OR `any(sys.modules.get(name) is not mod for name, mod in cache.items())`. Count/len may gate additions but must never be the SOLE invalidation key. This is the design used in Technical Approach Fix 1, Task 1, and asserted by the Test B binding gate.
3. **Umbrella scope — RESOLVED: fix the two proven instances + deterministic regression harness only.** No broader "isolation flake detector" / CI re-run mode is folded in here (that would be a separate slug). The umbrella issue #1897 stays open as the durable home for future instances as they are observed and root-caused (see No-Gos).
