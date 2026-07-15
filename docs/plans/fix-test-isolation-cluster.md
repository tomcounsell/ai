---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-15
tracking: https://github.com/tomcounsell/ai/issues/2093
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-07-15T04:58:30Z
---

# Fix Test-Isolation Cluster (5 unit tests failing under `-n auto`)

## Problem

Five unit tests pass in isolation (`pytest ... -n0`) but fail intermittently under
`pytest tests/unit/ -n auto` (pytest-xdist). They are order-dependent flakes driven by
shared, cross-worker state — not by the code they nominally test. The nightly reported 11
failures on 2026-07-14; these account for most of the gap versus the deterministic in-isolation
failures.

**Current behavior:** Under `-n auto`, sibling tests on the same xdist worker mutate global
process state (module-level singletons, the worktree filesystem) that the affected tests read,
flipping their assertions.

**Desired outcome:** All five tests pass reliably under `-n auto` across repeated runs, made
independent of ambient state at the test source (not by disabling parallelism).

Affected tests:
- `tests/unit/test_session_lifecycle.py::TestFinalizeSessionRejectFromTerminal::test_finalize_session_reject_from_terminal_blocks_by_default`
- `tests/unit/test_session_lifecycle.py::TestFinalizeSessionRejectFromTerminal::test_finalize_session_reject_from_terminal_completed_to_killed`
- `tests/unit/test_session_lifecycle.py::TestConcurrentPendingRunClaim::test_claim_bypass_still_blocked_by_generic_cas`
- `tests/unit/test_bot_registry_routing.py::test_should_respond_sync_non_bot_dm_still_responds`
- `tests/unit/test_plan_migration_invariant.py::TestNoPhantomFunctionReference::test_handle_merge_completion_has_zero_python_definitions`

## Freshness Check

**Baseline commit:** 20d524a3a1383b9c2f46b798e4d5db1f198b2de6
**Issue filed at:** 2026-07-15T03:52:46Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/routing.py:50-51` — module globals `RESPOND_TO_DMS = True`, `DM_WHITELIST = set()` — still holds.
- `bridge/routing.py:1214-1218` — `should_respond_sync` reads those globals in the DM path — still holds.
- `bridge/telegram_bridge.py:674-675` — `_routing_module.DM_WHITELIST = DM_WHITELIST` overwrites the routing global at import — still holds.
- `tests/unit/test_bot_registry_routing.py:15-23` — `registered_bot` fixture saves/restores `BOT_ID_TO_PROJECT` only (NOT the DM whitelist) — still holds.
- `tests/unit/test_plan_migration_invariant.py` `TestNoPhantomFunctionReference` — greps `str(REPO_ROOT)` recursively — still holds.
- `models/session_lifecycle.py:359` (terminal guard), `:379-388` and `:665` (CAS re-read) — still holds.

**Cited sibling issues/PRs re-checked:**
- #2060 — OPEN. Integration-timing instance of the same class; addressed in No-Gos.
- #1897 — CLOSED (umbrella). Fixed by PR #2061 (popoto db-cache split-brain + agent-hooks corruption). Different mechanism from this cluster.

**Commits on main since issue was filed (touching referenced files):** none relevant.

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **PR #2061**: "Fix xdist test-isolation flakes: popoto db-cache split-brain + agent-hooks corruption" (merged, closed #1897). Fixed two mechanisms: popoto db-cache split-brain and hooks-less-parent corruption. Our cluster is a *different* mechanism — module-global (non-Redis) leakage and a filesystem-scan race — so it was not covered by #2061.
- **#1897 umbrella** (closed): established the pattern of filing individual xdist-flake instances rather than expanding one umbrella. #2093 is one such instance batch; #2060 is another.
- **Confirmed by reproduction:** a full `pytest tests/unit/ -n auto` run produced 8 order-dependent failures (including the in-scope `test_should_respond_sync_non_bot_dm_still_responds`), confirming the class is live and non-deterministic across worker/file assignment.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2061 (#1897) | Fixed popoto db-cache split-brain + hooks-less-parent corruption | Targeted the Redis/hooks mechanisms; did not touch module-global routing state (`DM_WHITELIST`) or the invariant test's full-tree grep, which are the actual drivers here. |

**Root cause pattern:** tests that read process-global mutable state (a module-level singleton, or the entire worktree filesystem) without pinning that state at the test boundary are order-dependent. The fix belongs at the reader's boundary (reset the global in the fixture; scope the scan to tracked source), not at the parallel runner.

## Data Flow

1. **Entry point:** `pytest tests/unit/ -n auto` distributes test *files* to worker processes via `--dist=loadfile`.
2. **Polluter (bot_registry):** any sibling test on the same worker that imports `bridge.telegram_bridge` runs its module body, which executes `routing.DM_WHITELIST = <real whitelist>` (`bridge/telegram_bridge.py:674-675`).
3. **Reader (bot_registry):** `test_should_respond_sync_non_bot_dm_still_responds` calls `should_respond_sync(sender_id=111111, is_dm=True)`; `bridge/routing.py:1217-1218` sees a non-empty `DM_WHITELIST` not containing `111111` → returns `False` → assertion `is True` fails.
4. **Polluter (plan_migration):** any concurrent worker creating/deleting files under `data/`, `logs/`, `__pycache__/`, `.venv/` inside `REPO_ROOT`.
5. **Reader (plan_migration):** `subprocess.run(["grep", "-rn", ..., str(REPO_ROOT)])` traverses those volatile trees; a directory vanishing mid-walk makes grep exit `2` → `assert result.returncode in (0, 1)` fails ("grep failed").

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies (test-only + one test-helper change). Redis running locally is already required for the suite.

## Solution

### Key Elements

- **bot_registry fixture hardening**: the `registered_bot` fixture additionally saves, resets, and restores `routing.RESPOND_TO_DMS` (→ `True`) and `routing.DM_WHITELIST` (→ empty `set()`) so the DM path is deterministic regardless of whether a sibling imported the bridge.
- **plan_migration source-scoped scan**: replace the full-tree `grep -rn ... REPO_ROOT` with `git grep` over tracked `*.py` (atomic index snapshot, excludes `.venv/.git/data/logs/__pycache__`), excluding the test file via pathspec. This removes both the filesystem race and any accidental scan of runtime/untracked artifacts.
- **session_lifecycle ambient-independence (hygiene, not a confirmed-root-cause fix)**: code-read proves all three tests are already logically ambient-independent — the two `reject_from_terminal` tests raise at the terminal guard (`models/session_lifecycle.py:359`) *before* the CAS block, and `test_claim_bypass_still_blocked_by_generic_cas` fully mocks `get_authoritative_session`, so no test has a Redis-dependent assertion path. They did not reproduce. The only shared touchpoint keyed on the default id `"test-session-lc"` that runs before the guard is `reset_self_draft_attempts(session_id)` — an **idempotent Redis DELETE** (`agent/steering.py:246-264`) with no read/assert, so it cannot flip an assertion either. F3 therefore gives each test a unique `session_id` purely as **defense-in-depth** (eliminates any cross-test coupling on the shared default id) and is explicitly NOT credited with fixing a confirmed bug. No production-code change.

### Flow

`pytest -n auto` → worker imports a sibling that pollutes a global → affected test resets/pins the global (fixture) or scans only tracked source (git grep) → assertion evaluates against controlled state → pass, order-independent.

### Technical Approach

- **F1 (bot_registry):** extend the existing `registered_bot` fixture in `tests/unit/test_bot_registry_routing.py`. Save `routing.RESPOND_TO_DMS` and a copy of `routing.DM_WHITELIST`; set `RESPOND_TO_DMS = True` and `DM_WHITELIST = set()` before `yield`; restore both after. This mirrors the save/restore pattern already used in `tests/e2e/test_message_pipeline.py:100-108`.
- **F2 (plan_migration):** in `TestNoPhantomFunctionReference::test_handle_merge_completion_has_zero_python_definitions`, run `git grep -n _handle_merge_completion -- '*.py' ':!tests/unit/test_plan_migration_invariant.py'` with `cwd=REPO_ROOT`. `git grep` exit codes match plain grep (0 = matches, 1 = none), so keep `assert returncode in (0, 1)` and `assert stdout.strip() == ""`. This is race-free (reads the tracked index, not a live directory walk).
- **F3 (session_lifecycle):** parametrize `_make_session` calls in the three tests with unique ids (e.g. `f"test-...-{uuid4().hex[:8]}"`), so the best-effort Redis touch (`reset_self_draft_attempts`'s idempotent DELETE) operates on a namespace no sibling can collide with. Keep behavior otherwise identical. This is hardening only — the WHY comment must cite the shared-default-id decoupling of `reset_self_draft_attempts`, NOT a CAS mechanism (the CAS path is provably off the executed path for all three tests).

## Failure Path Test Strategy

### Exception Handling Coverage
- No `except Exception: pass` blocks are added. `models/session_lifecycle.py` already wraps its best-effort Redis calls in try/except; F3 does not change that behavior, only the session_id inputs. No new handlers in scope.

### Empty/Invalid Input Handling
- F1 explicitly sets `DM_WHITELIST` to the empty set — the exact ambient value the passing case assumes — making the "empty whitelist" path deterministic. F2's `git grep` on zero matches returns exit 1 with empty stdout, which the assertions already handle.

### Error State Rendering
- No user-visible output. The affected assertions ARE the failure-path checks (StatusConflictError raised, guard fires, grep finds nothing). Each is preserved.

## Test Impact

- [ ] `tests/unit/test_bot_registry_routing.py::test_should_respond_sync_non_bot_dm_still_responds` — UPDATE: harden the shared `registered_bot` fixture to reset `RESPOND_TO_DMS`/`DM_WHITELIST`; the other tests in this file using the fixture stay green (they don't exercise the DM whitelist path).
- [ ] `tests/unit/test_plan_migration_invariant.py::TestNoPhantomFunctionReference::test_handle_merge_completion_has_zero_python_definitions` — UPDATE: swap `grep -rn REPO_ROOT` for `git grep` scoped to tracked `*.py`; assertions unchanged.
- [ ] `tests/unit/test_session_lifecycle.py::TestFinalizeSessionRejectFromTerminal::test_finalize_session_reject_from_terminal_blocks_by_default` — UPDATE: unique session_id.
- [ ] `tests/unit/test_session_lifecycle.py::TestFinalizeSessionRejectFromTerminal::test_finalize_session_reject_from_terminal_completed_to_killed` — UPDATE: unique session_id.
- [ ] `tests/unit/test_session_lifecycle.py::TestConcurrentPendingRunClaim::test_claim_bypass_still_blocked_by_generic_cas` — UPDATE: unique session_id.

## Rabbit Holes

- **Re-enabling the DB-0 tripwire.** Tempting, but DB0 is production on dev/bridge machines; the tripwire is correctly skipped when DB0 is non-idle. Do not try to force a clean DB0 — per-test isolation already exists via the `redis_test_db` autouse fixture. Out of scope.
- **A global "reset all bridge module globals" autouse fixture.** Over-broad; would mask real coupling and risk masking legitimate bugs. Fix only the reader that needs it.
- **Chasing a Redis root cause for the 3 session_lifecycle tests.** They are logically deterministic and did not reproduce; do not invent a Redis fix. Hardening to unique ids is the proportionate change.
- **Fixing the other flakes surfaced by the repro** (e.g. `test_sustainability`, `test_stall_advisory_reflection`, `test_sdlc_router_oscillation`). Same class, different singletons — out of scope for #2093's five tests.

## Risks

### Risk 1: `git grep` unavailable or behaves differently in CI
**Impact:** F2 test errors instead of asserting.
**Mitigation:** `git` is a hard dependency of the repo and the test already shells out to git elsewhere (`TestMigrationInvariantBehavior` runs `git init/add/commit`). `git grep` exit-code semantics (0/1) match plain grep; assertions are unchanged.

### Risk 2: Resetting `DM_WHITELIST` masks a real regression in DM routing
**Impact:** A genuine whitelist bug could pass unnoticed in this test.
**Mitigation:** This test's purpose is the *bot loop-guard* (non-bot sender still responds), not whitelist enforcement; whitelist behavior is covered elsewhere. Resetting to the documented default (`True`/empty) restores the test's intended precondition, it doesn't weaken a whitelist assertion.

## Race Conditions

### Race 1: Recursive grep vs. concurrent filesystem churn
**Location:** `tests/unit/test_plan_migration_invariant.py` `TestNoPhantomFunctionReference`
**Trigger:** a sibling xdist worker creates/deletes files/dirs under `REPO_ROOT` (`data/`, `logs/`, `__pycache__/`) while the test's `grep -r` walks the tree.
**Data prerequisite:** none — the target string lives only in the test file.
**State prerequisite:** the scan must observe a consistent view of source.
**Mitigation:** F2 uses `git grep` against the tracked index (atomic snapshot); no live directory walk, so no vanishing-directory error.

### Race 2: `DM_WHITELIST` populated by sibling import
**Location:** `bridge/routing.py:1217`
**Trigger:** sibling imports `bridge.telegram_bridge`, mutating the module global before the test reads it.
**Data prerequisite:** the DM path must see the intended `RESPOND_TO_DMS`/`DM_WHITELIST`.
**State prerequisite:** globals pinned for the test's duration.
**Mitigation:** F1 save/reset/restore in the fixture.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2060] `test_cli_hook_denies_over_budget_exit_2` — a Redis write-visibility timing flake in an *integration* test, a distinct instance of the same class. It stays tracked under #2060 (its DB-0 tripwire cannot be re-enabled on production-DB0 machines). This plan's targeted module-global + scan-scope fixes do not touch it and do not conflict with it. Not folded, to keep the unit-test cluster fix small and reviewable.
- Fixing the additional order-dependent flakes surfaced by the repro run (`test_sustainability`, `test_stall_advisory_reflection`, `test_sdlc_router_oscillation`, `test_agent_session_queue`, `test_hook_user_prompt_submit`) — same disease, different singletons. [SEPARATE-SLUG #2093] scopes exactly the five listed tests; the rest belong in a follow-up instance issue.

## Update System

No update system changes required — this is a test-only + test-helper change with no deployment, dependency, or config impact.

## Agent Integration

No agent integration required — this is a test-suite hygiene change. No new tool, MCP surface, or bridge wiring.

## Documentation

The change is test-only. No feature docs are created.

### Feature Documentation
- [ ] No `docs/features/` change — no user-facing or system behavior changes; the fix hardens test determinism only. Justification: the affected files are `tests/unit/*` plus a shared test fixture; there is no feature surface to document.

### Inline Documentation
- [ ] Add a one-line comment at each fixed site explaining WHY:
  - bot_registry: "reset DM_WHITELIST/RESPOND_TO_DMS — a sibling importing telegram_bridge pollutes them (#2093)".
  - plan_migration: "git grep scans tracked source only — grep -r races on concurrent runtime-dir churn (#2093)".
  - session_lifecycle: "unique session_id — defense-in-depth to decouple the shared-default-id `reset_self_draft_attempts` DELETE (#2093); NOT a CAS fix, that path is unreached here".

## Success Criteria

**F1/F2 — root-caused, confirmed by reproduction:**
- [ ] `test_should_respond_sync_non_bot_dm_still_responds` is independent of ambient `DM_WHITELIST`/`RESPOND_TO_DMS` — confirmed by co-scheduling a bridge-importing sibling under `-n auto` (red before F1, green after).
- [ ] `test_handle_merge_completion_has_zero_python_definitions` uses a source-scoped, race-free scan (`git grep`), root-causing its order-dependence specifically (grep-r racing on runtime-dir churn).

**F3 — hardening applied (no confirmed bug):**
- [ ] The three `test_session_lifecycle` tests use unique session_ids and remain green in isolation and under `-n auto`. This validates the diff landed and did not regress; it is NOT evidence of a fixed root cause (none was found — the tests are ambient-independent by construction).
- [ ] **Re-flake routing rule:** if any of the trio re-flakes post-merge, it routes to a NEW follow-up investigation issue (same class as the other No-Go flakes), NOT treated as a regression of this fix.

**Cross-cutting:**
- [ ] All five listed tests pass under `pytest tests/unit/ -n auto` across repeated runs.
- [ ] #2060 relationship explicitly resolved (kept separate; rationale documented above).
- [ ] Tests pass (`/do-test` on the touched files).
- [ ] Format clean (`python -m ruff format`).

## Team Orchestration

### Team Members

- **Builder (test-isolation)**
  - Name: iso-builder
  - Role: Apply F1/F2/F3 to the three test files
  - Agent Type: builder
  - Resume: true

- **Validator (test-isolation)**
  - Name: iso-validator
  - Role: Verify all five tests pass in isolation and under a repeated `-n auto` subset
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Harden bot_registry + plan_migration + session_lifecycle tests
- **Task ID**: build-test-isolation
- **Depends On**: none
- **Validates**: tests/unit/test_bot_registry_routing.py, tests/unit/test_plan_migration_invariant.py, tests/unit/test_session_lifecycle.py
- **Assigned To**: iso-builder
- **Agent Type**: builder
- **Parallel**: false
- F1: extend `registered_bot` fixture to save/reset(`RESPOND_TO_DMS=True`, `DM_WHITELIST=set()`)/restore.
- F2: swap the phantom-reference grep to `git grep -n _handle_merge_completion -- '*.py' ':!tests/unit/test_plan_migration_invariant.py'` (cwd=REPO_ROOT); keep exit-code (0,1) + empty-stdout assertions.
- F3: give the three session_lifecycle tests unique session_ids.
- Add the one-line WHY comments (see Documentation).
- `python -m ruff format` the three files.

### 2. Validate isolation
- **Task ID**: validate-test-isolation
- **Depends On**: build-test-isolation
- **Assigned To**: iso-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the five tests with `-n0` (must pass).
- Run a repeated `-n auto` subset that co-schedules a bridge-importing sibling and a filesystem-churning sibling with the affected files (e.g. include `tests/unit/test_bot_registry_routing.py tests/unit/test_plan_migration_invariant.py tests/unit/test_session_lifecycle.py` plus a bridge-importing file) across a few iterations.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| bot_registry test passes | `python -m pytest tests/unit/test_bot_registry_routing.py::test_should_respond_sync_non_bot_dm_still_responds -q -p no:cacheprovider` | exit code 0 |
| plan_migration test passes | `python -m pytest "tests/unit/test_plan_migration_invariant.py::TestNoPhantomFunctionReference::test_handle_merge_completion_has_zero_python_definitions" -q -p no:cacheprovider` | exit code 0 |
| session_lifecycle trio passes | `python -m pytest tests/unit/test_session_lifecycle.py -q -p no:cacheprovider -k "reject_from_terminal or claim_bypass_still_blocked_by_generic_cas"` | exit code 0 |
| full-tree grep removed | `grep -n 'str(REPO_ROOT)' tests/unit/test_plan_migration_invariant.py` | exit code 1 |
| git grep adopted | `grep -c 'git' tests/unit/test_plan_migration_invariant.py` | output > 0 |
| whitelist reset present | `grep -c 'DM_WHITELIST' tests/unit/test_bot_registry_routing.py` | output > 0 |
| Format clean | `python -m ruff format --check tests/unit/test_bot_registry_routing.py tests/unit/test_plan_migration_invariant.py tests/unit/test_session_lifecycle.py` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency (+Risk, +Scope) | F3 rationale cited a CAS re-read path that is never reached (terminal guard fires first; `test_claim_bypass` mocks `get_authoritative_session`), contradicting the plan's own "never depend on a Redis read" claim | F3 re-diagnosed around the real pre-guard touchpoint `reset_self_draft_attempts` (idempotent DELETE, `agent/steering.py:246-264`); reclassified as defense-in-depth hygiene, WHY comments corrected to not cite CAS | The DELETE has no read/assert, so even it cannot flip an assertion — F3 is hygiene, not a confirmed fix |
| CONCERN | Scope & Value / Risk / History | Trio success criterion is unfalsifiable (F3 is inert; tests pass with or without the diff) | Success Criteria split: F1/F2 root-caused+repro-confirmed, F3 as hardening; added explicit post-merge re-flake routing rule (→ follow-up investigation, not regression) | Guards against the exact prose-only-speculative-fix anti-pattern `test_plan_migration_invariant` exists to prevent |
