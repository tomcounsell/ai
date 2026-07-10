---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/1897
last_comment_id: 4933699033
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
- **Impact on plan**: The fix must make the popoto db re-point ordering-independent — invalidate `_POPOTO_MODULE_CACHE` on a signal that tracks popoto-module imports directly (count/frozenset of `popoto`-prefixed module names), not on total `len(sys.modules)`.

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
- **Coupling**: reduces hidden coupling — the popoto db re-point stops depending on an unrelated global (`len(sys.modules)`); the `agent` import consistency stops depending on which test last swapped the SDK.
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

- **Robust popoto db-cache invalidation (fixes #1)**: `_POPOTO_MODULE_CACHE` invalidation keyed on the set of `popoto`-prefixed module names, not `len(sys.modules)`.
- **agent-hooks consistency guard (fixes #2)**: a small autouse repair that detects the hooks-less-`agent`-parent state and evicts all `agent.*` so the next import self-heals.
- **Deterministic regression tests**: one per instance, each reproducing the corrupt precondition directly (no reliance on a fragile multi-file ordering) and asserting hermeticity.

### Flow

Poisoning ordering → (Fix 1) every imported popoto db-holding submodule is re-pointed to the test db each test → in-process write and subprocess read agree on db → exit 2 as expected.

Poisoning ordering → (Fix 2) hooks-less `agent` parent is detected at setup and repaired by full `agent.*` eviction → next `agent.hooks.*` resolution self-heals → `monkeypatch.setattr` succeeds.

### Technical Approach

- **Fix 1 — `tests/conftest.py` `_popoto_modules_with_redis_db()` / cache key.** Replace the `len(sys.modules)`-based `_POPOTO_MODULE_CACHE_KEY` with a key that tracks popoto imports directly — e.g. the count (or a `frozenset`) of module names in `sys.modules` starting with `"popoto"`. This preserves the O(1)-amortized fast path (the expensive scan runs only when a new popoto submodule is imported) while making the db re-point ordering-independent. `redis_test_db`'s re-point loop (lines 244-246) then always covers every db-holding popoto submodule.
- **Fix 2 — `tests/conftest.py` agent-consistency guard.** Add a minimal autouse guard (or fold into `mock_claude_sdk_cleanup`) that, at setup, detects `"agent" in sys.modules and "agent.hooks" in sys.modules and not hasattr(sys.modules["agent"], "hooks")` and repairs it by deleting every `agent.*` key from `sys.modules` (proven to self-heal on next import). This is root-cause-agnostic: it neutralizes the corruption regardless of whether an SDK swap, `importlib.reload`, or `patch.dict` created it, so it also covers `test_ui_reflections_data.py` and any other victim. Keep the existing `mock_claude_sdk_cleanup` eviction (it fixes a separate contamination); the guard is belt-and-suspenders for the partial-mutation vectors that eviction's `sdk_after != sdk_before` condition misses.
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

- [ ] `tests/conftest.py::_popoto_modules_with_redis_db` / `redis_test_db` — UPDATE: change cache-invalidation key from `len(sys.modules)` to popoto-module-name signal. Existing behavior preserved for the common case; verified by existing suite staying green.
- [ ] `tests/conftest.py::mock_claude_sdk_cleanup` — UPDATE (or adjacent new autouse fixture): add the agent-hooks consistency guard. Existing eviction behavior retained.
- [ ] `tests/integration/test_tool_budget_enforcement.py::test_cli_hook_denies_over_budget_exit_2` — no code change; must pass under the poisoning ordering after Fix 1 (this is the instance-#1 acceptance).
- [ ] `tests/unit/test_teammate_write_restriction.py` (all classes via `fake_project`) — no code change; must pass under `--dist=loadfile` after Fix 2 (instance-#2 acceptance).
- [ ] `tests/unit/test_ui_reflections_data.py` — no code change; covered by Fix 2.
- [ ] NEW `tests/unit/test_conftest_isolation_guards.py` — REPLACE/create: deterministic regression tests for both fixes (reproduce the corrupt precondition directly; assert repair + hermeticity).

## Rabbit Holes

- **Chasing the exact multi-file ordering that triggers the len-collision.** The collision is machine/collection-order dependent and not worth reproducing exactly. Reproduce the corrupt PRECONDITION directly in a unit test instead.
- **"Fixing" popoto to reference `POPOTO_REDIS_DB` indirectly.** The 31 by-value captures are a popoto-internal design; re-pointing them in the fixture is the right seam. Do not fork/patch popoto.
- **Rewriting `mock_claude_sdk_cleanup` from scratch or removing the SDK mock.** It fixes a real, separate contamination (module-level SDK mocks bleeding across the session). Leave its core behavior; only add the guard.
- **Trying to make the whole suite ordering-independent in one pass.** Scope to the two proven root causes. New instances get logged under this umbrella as they are observed and root-caused.
- **Deleting the seeders' module-level `from agent.hooks` imports.** Cosmetic; does not remove the corruption vector.

## Risks

### Risk 1: Fix-1 cache key reintroduces the per-test O(n) scan
**Impact:** Unit suite slows if the cache rebuilds every test.
**Mitigation:** Key on the COUNT of `popoto`-prefixed module names (cheap: one pass building that count is unavoidable, but we can track it incrementally or accept a single filtered comprehension over `sys.modules` — still far cheaper than the current behavior's worst case, and popoto module count is stable after warmup). Benchmark unit-suite wall time before/after; require no material regression.

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
- [ ] Update `tests/README.md` — add a "Test isolation under xdist" blind-spot/gotcha entry documenting: the two proven root causes, the `_POPOTO_MODULE_CACHE` invalidation contract (key on popoto-module signal, never `len(sys.modules)`), and the agent-hooks consistency guard. Point future flake investigations here.
- [ ] Update `docs/features/full-suite-pytest-lock.md` OR add a short `docs/features/test-isolation-hardening.md` cross-referencing #1967/#1981 (concurrency) vs this umbrella (single-run isolation), so the two are not conflated.

### Inline Documentation
- [ ] Docstring on the corrected `_popoto_modules_with_redis_db` cache key explaining WHY `len(sys.modules)` is wrong (non-monotonic under `agent.*` eviction).
- [ ] Docstring on the agent-hooks guard explaining the CPython cache-hit / hooks-less-parent mechanism and why full `agent.*` eviction self-heals.

## Success Criteria

- [ ] `tests/integration/test_tool_budget_enforcement.py::test_cli_hook_denies_over_budget_exit_2` passes under the 2026-07-10 poisoning cross-file ordering (previously `1 failed, 21 passed`).
- [ ] `tests/unit/test_teammate_write_restriction.py` and `tests/unit/test_ui_reflections_data.py` pass under `-n auto --dist=loadfile` across repeated runs (no `agent.hooks` AttributeError at setup).
- [ ] New `tests/unit/test_conftest_isolation_guards.py` deterministically reproduces both corrupt preconditions and asserts the fixes repair them.
- [ ] `_POPOTO_MODULE_CACHE` invalidation no longer references `len(sys.modules)` (grep confirms).
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
- In `tests/conftest.py`, change `_POPOTO_MODULE_CACHE_KEY` to track the count/frozenset of `popoto`-prefixed module names instead of `len(sys.modules)`.
- Preserve the fast-path skip when no popoto modules are imported.
- Add docstring explaining why `len(sys.modules)` was wrong.

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
- Test B: import a fresh popoto db-holding submodule mid-test; assert `redis_test_db` re-points its `POPOTO_REDIS_DB` to the test client (db != 0).

### 4. Validate acceptance
- **Task ID**: validate-isolation
- **Depends On**: build-regression-tests
- **Assigned To**: isolation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the instance-#1 poisoning ordering and confirm exit 2.
- Run `test_teammate_write_restriction.py` + `test_ui_reflections_data.py` under `-n auto --dist=loadfile` repeatedly; confirm no AttributeError.
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
| Cache key no longer uses len(sys.modules) | `grep -n "len(sys.modules)\|len(_sys.modules)" tests/conftest.py` | exit code 1 |
| No product code changed | `git diff --name-only main -- agent/ models/ .claude/hooks/ \| wc -l` | output contains 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Guard placement**: fold the agent-hooks consistency guard into `mock_claude_sdk_cleanup`, or add a separate small autouse fixture? (Recommendation: separate fixture — keeps the two concerns independent and the guard root-cause-agnostic.)
2. **Fix-1 cache key shape**: count of popoto module names (cheapest) vs frozenset (detects renames/reimports at equal count)? A count collision is far less likely than a total-`len` collision, but a frozenset is strictly safer at marginal cost. Which do you prefer?
3. **Umbrella scope**: is fixing the two proven instances (plus the regression harness) the right cut for this PR, leaving the umbrella issue open for future instances — or do you want a broader "isolation flake detector" (e.g. a CI mode that re-runs suspected phantoms in isolation) folded in here?
