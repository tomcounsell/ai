---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2207
last_comment_id: 5053674965
---

# Harden AgentSession index rebuild against phantom re-inflation

## Problem

On 2026-07-22, Redis db0 held ~6.27M keys (grew to 7.39M) — almost all
`AgentSession:None:{uuid}:None:None:None:None` phantom hashes containing only
Popoto `$IndexF` index bookkeeping, no real session data. Normal keyspace is
thousands. The acute flood was purged (db0 → 18,986 keys, confirmed clean and
stable 2026-07-23 02:32Z) and the one-off remediation shipped. This plan closes
the two **durable** engineering gaps that let the flood happen and, worse,
amplified it.

**Current behavior:**
- `AgentSession.rebuild_indexes()` runs `field.on_save` for every field over
  every `AgentSession:*` hash (popoto `base.py:2849-2856`). Decoding an
  identity-less phantom hash regenerates its `AutoKeyField` uuid, so
  `INDEX_SWAP_LUA` HSETs onto a brand-new key — creating a fresh phantom. Every
  rebuild pass over a polluted keyspace **duplicates** the phantom population.
- The #2101/PR #2102 A1 guard (`AgentSession.repair_indexes`,
  `models/agent_session.py:2168-2203`) shims **only** the `status` field's
  `on_save` to skip identity-less hashes. The three other `IndexedField`s —
  `task_type`, `claude_session_uuid`, `claude_pid` — are un-shimmed. `MONITOR`
  caught `INDEX_SWAP_LUA` HSETting exactly those three fields + their
  `\x00idxset` pointers, matching the un-shimmed set exactly.
- Worker startup Step 1 (`worker/__main__.py:736-753`) calls
  `scripts.popoto_index_cleanup.run_cleanup`, which invokes **raw
  `model_class.rebuild_indexes()`** (`scripts/popoto_index_cleanup.py:216-220`),
  NOT the A1-guarded `repair_indexes()`. So Step 1 re-inflates **all four**
  fields, including `status`, with no guard at all.
- The 30s per-model timeout in `run_cleanup` is illusory:
  `with concurrent.futures.ThreadPoolExecutor(...) as executor:` —
  `future.result(timeout=30)` raises `TimeoutError`, but exiting the `with`
  block calls `executor.shutdown(wait=True)`, which blocks forever on the
  un-killable rebuild thread. This is the 8h zero-heartbeat wedge (worker PID
  36598); the wedged rebuild also re-inflated phantoms the entire time.

**Desired outcome:**
- `rebuild_indexes` over an identity-less-polluted keyspace re-inflates **zero**
  phantoms — for all four IndexedFields, on both the `repair_indexes` and the
  worker Step 1 code paths.
- Worker startup Step 1 can never block the serve loop: its per-model budget is
  actually enforced (a slow/poisoned model is abandoned, not waited on), so the
  worker heartbeats within threshold even against a degenerate keyspace.

## Freshness Check

**Baseline commit:** `9441f597b`
**Issue filed at:** 2026-07-22T07:45:20Z
**Disposition:** Unchanged

**File:line references re-verified against current main:**
- `models/agent_session.py:145,184,230,262` — the four `IndexedField`s
  (`status`, `task_type`, `claude_session_uuid`, `claude_pid`) — still hold.
- `models/agent_session.py:2168-2203` — A1 shim guards only `status.on_save` —
  still holds.
- `worker/__main__.py:736-753` — Step 1 calls `run_cleanup` — still holds.
- `scripts/popoto_index_cleanup.py:213-227` — `with ThreadPoolExecutor` +
  `future.result(timeout=30)` + implicit `shutdown(wait=True)` — still holds.
- popoto `base.py:2849-2856` — per-field `on_save` loop in `rebuild_indexes` —
  still holds (popoto 1.8.0, pip-installed, not vendored).

**Cited sibling issues/PRs re-checked:**
- #2101 / PR #2102 — CLOSED/MERGED; shipped the `status`-only A1 guard this plan
  generalizes.
- #2204 — catchup re-handling; a plausible historical *producer* of the churn,
  but the re-inflation *mechanism* is settled and independent. Out of scope.
- #1459, #2086 — prior class-set-orphan work; unchanged, complementary.

**Commits on main since issue was filed (touching referenced files):** none
(`git log --since=2026-07-22T07:45:20Z -- worker/__main__.py
scripts/popoto_index_cleanup.py models/agent_session.py` → empty).

**Active plans in `docs/plans/` overlapping this area:**
`session-recovery-observation-audit.md` (detect-only index drift, #2086) —
complementary, no overlap in the code this plan edits.

**Notes:** Keyspace already clean; this is pure code hardening, no data work.

## Prior Art

- **PR #2102 (#2101)**: "Fix AgentSession pending-index phantom leak — A1 rebuild
  guard." Installed the transient `status.on_save` shim inside `repair_indexes()`
  that skips the SADD for identity-less hashes during rebuild. **Succeeded for
  `status`, but scoped to one field** — it left the other three IndexedFields
  un-guarded and never touched the raw-`rebuild_indexes()` worker Step 1 path.
  This plan is the direct generalization.
- **PR #1078 (#1069)**: "Fix agent-session-cleanup phantom-record destruction."
  Made the cleanup pass phantom-aware. Complementary; not the rebuild path.
- **Issues #1459, #2086**: class-set orphan cleanup (`clean_indexes`, SSCAN) and
  detect-only drift reconciliation. Orthogonal — those handle gone-hash orphans,
  not identity-less-hash re-inflation.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #2102 (A1 guard) | Shimmed `status.on_save` to skip identity-less hashes during `repair_indexes()` rebuild | Scoped to a single field. `task_type`, `claude_session_uuid`, `claude_pid` remained un-guarded and kept re-inflating. Also never covered the worker Step 1 `run_cleanup` path, which calls raw `rebuild_indexes()` (no guard at all). |

**Root cause pattern:** the fix was field-specific (`status`) when the defect is
field-agnostic — *any* IndexedField's `on_save` re-inflates when run against an
identity-less hash whose decode regenerates the AutoKey. The correct altitude is
"skip identity-less instances for **all** IndexedFields," ideally at the
per-instance level, on **every** rebuild entry point.

## Data Flow

1. **Entry point A — worker startup:** `worker/__main__.py` Step 1 →
   `run_cleanup()` → per-model `ThreadPoolExecutor.submit(rebuild_indexes)` →
   popoto `rebuild_indexes` per-field `on_save` loop → `INDEX_SWAP_LUA` HSET.
2. **Entry point B — hourly reflection / recovery:** `agent-session-cleanup`
   reflection (`session_health.py:4954`), `session_pickup.py:452,612`,
   `agent_session_queue.py` reaper, and `scripts/update/run.py:1818` all call
   `AgentSession.repair_indexes()` → (A1-guarded) `rebuild_indexes`.
3. **Re-inflation:** for an identity-less hash, `decode_popoto_model_hashmap`
   regenerates the AutoKey uuid; `on_save` for each IndexedField issues
   `INDEX_SWAP_LUA` with the new key as `KEYS[1]`, HSETting the field + its
   `\x00idxset` pointer → a fresh phantom hash.
4. **Output (fixed):** identity-less instances are skipped before any IndexedField
   `on_save` runs, on both entry points → no new phantom hashes.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none public. The generalized A1 guard stays internal to
  `AgentSession.repair_indexes`; `run_cleanup`'s timeout mechanism changes
  internally but keeps its return-dict contract.
- **Coupling:** slightly *reduces* it — the guard becomes field-list-driven
  (derived from `cls._meta` IndexedFields) rather than hardcoding `status`.
- **Data ownership:** unchanged. Still ORM-only, no raw-Redis writes on Popoto
  keys.
- **Reversibility:** high — both changes are localized and revert cleanly.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the "skip identity-less for all IndexedFields"
  altitude and the Step-1 bounding approach)
- Review rounds: 1

## Prerequisites

No prerequisites — the keyspace is already clean and this work has no external
dependencies. (Redis running locally is required for the integration test, which
is standard for this repo's test suite.)

## Solution

### Key Elements

- **Generalized A1 guard**: extend the identity-less skip in
  `AgentSession.repair_indexes` from `status` alone to **every** IndexedField on
  the model (`status`, `task_type`, `claude_session_uuid`, `claude_pid`), derived
  from `cls._meta` so a future IndexedField is covered automatically.
- **Guard the worker Step 1 path**: stop worker Step 1 from re-inflating
  AgentSession. Route AgentSession through the guarded `repair_indexes()` instead
  of raw `rebuild_indexes()` — cleanest by excluding `AgentSession` from
  `run_cleanup`'s generic sweep (like `Reflection` already is), because worker
  Step 2 (`cleanup_corrupted_agent_sessions`, `session_health.py:4954`) already
  calls the guarded `repair_indexes()` unconditionally. Step 1's raw AgentSession
  rebuild is therefore both redundant and dangerous.
- **Actually-enforced Step 1 budget**: fix `run_cleanup` so a per-model timeout
  truly abandons the still-running rebuild thread instead of blocking on
  `shutdown(wait=True)`. A poisoned or slow model is skipped; the worker proceeds
  to serve and heartbeats within threshold.

### Flow

Worker boots → Step 1 `run_cleanup` sweeps non-AgentSession models under an
enforced per-model budget (abandons any that overrun) → AgentSession is handled
by the A1-guarded `repair_indexes()` in Step 2 → identity-less hashes skipped for
all IndexedFields → zero phantom re-inflation → worker heartbeats within
threshold and begins serving.

### Technical Approach

- **Generalize the guard (`models/agent_session.py:2168-2203`).** Replace the
  single-field `status_field` shim with a loop that installs the same
  identity-less-skip wrapper on the `on_save` of every IndexedField in
  `cls._meta` (enumerate via the meta field map; keep the existing
  `_filter_hydrated_sessions([instance])` identity check — it is the canonical
  test). Restore every wrapped field's original `on_save` in the `finally` block
  (mirror the existing single-`del` pattern for each field). Preserve the
  transient-shim design (guard only for the duration of the `rebuild_indexes()`
  call; live `save()` stays unguarded so a genuine brand-new session still indexes
  to `:pending`). Keep the 2-tuple return arity and the
  `_last_quarantined_identityless` counter (now summed across fields).
- **Exclude AgentSession from `run_cleanup`'s generic sweep**
  (`scripts/popoto_index_cleanup.py`). Add `"AgentSession"` to the existing
  `_SCHEDULER_STATE_MODELS`-style exclusion (or a new, clearly-named
  `_GUARDED_ELSEWHERE` frozenset) with a comment: AgentSession is rebuilt via the
  A1-guarded `AgentSession.repair_indexes()` in worker Step 2 and the hourly
  reflection; a raw `rebuild_indexes()` here re-inflates identity-less phantoms.
- **Make the Step 1 budget real** (`scripts/popoto_index_cleanup.py:213-227`).
  Do not use `with ThreadPoolExecutor(...) as executor:` (its `__exit__` blocks on
  `shutdown(wait=True)`). Instead submit to an executor that is **not** joined on
  timeout — on `TimeoutError`, log and move on, calling `executor.shutdown(wait=False)`
  (or use a daemon thread) so the overrunning rebuild is abandoned and the worker
  proceeds. Keep the per-model try/except and the summary dict.
- **Popoto is pip-installed (1.8.0), not vendored** — the fix lives entirely in
  repo code as a transient shim, consistent with the existing A1 pattern. No
  upstream popoto change.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `run_cleanup`'s per-model `try/except` swallows and logs at WARNING — assert
  a poisoned/slow model produces the WARNING and the sweep continues to the next
  model (observable behavior, not silent pass).
- [ ] The generalized guard's `finally` restore must run even if
  `rebuild_indexes()` raises — assert original `on_save` is restored on every
  wrapped field after an exception.

### Empty/Invalid Input Handling
- [ ] Identity-less hash (all key fields None) — assert it is skipped for every
  IndexedField (the core fix). Covered by the extended
  `test_repair_does_not_reinflate_from_identityless_hashes`.
- [ ] Empty keyspace — `repair_indexes()` and `run_cleanup()` return cleanly
  (no-op), no exception.

### Error State Rendering
- [ ] Worker Step 1 timeout path logs a visible WARNING naming the model; assert
  it appears and that startup proceeds (heartbeat continues). No user-facing
  surface — this is a bridge/worker-internal change.

## Test Impact

- [ ] `tests/unit/test_agentsession_pending_index_leak.py::test_repair_does_not_reinflate_from_identityless_hashes`
  — UPDATE: extend from `status`-only to assert **no re-inflation across all four
  IndexedFields** (`status`, `task_type`, `claude_session_uuid`, `claude_pid`);
  seed identity-less hashes carrying each field's `\x00idxset` pointer and assert
  `dbsize` does not grow across a `repair_indexes()` pass.
- [ ] `tests/unit/test_worker_entry.py` — UPDATE/ADD: assert AgentSession is
  excluded from `run_cleanup`'s model set, and that a model whose
  `rebuild_indexes` overruns the budget is abandoned (the call returns without
  blocking on the still-running thread) so worker startup proceeds.
- [ ] `tests/unit/test_session_health_phantom_guard.py` — REVIEW (likely no
  change): it asserts `repair_indexes` runs unconditionally; confirm the
  multi-field guard does not alter its expectations.

## Rabbit Holes

- **Re-litigating the raw-Redis purge exception.** Already resolved by the shipped
  ORM-compliant `scripts/purge_phantom_agent_sessions.py`. Do not touch it.
- **Chasing the historical producer (#2204 catchup / MISCONF retries).** The
  producer is understood and the acute flood is purged; this plan fixes the
  re-inflation + wedge mechanism only. Producer hardening is separately tracked.
- **Reimplementing popoto's `rebuild_indexes` loop in-repo.** Tempting for a
  per-instance skip, but fragile against popoto upgrades. Stay with the transient
  per-field `on_save` shim — same proven pattern as the existing A1 guard.
- **Rewriting `run_cleanup` into an async/background subsystem.** Out of appetite.
  The minimal fix is: don't join the overrunning thread, and don't rebuild
  AgentSession here.

## Risks

### Risk 1: A wrapped field's `on_save` is not restored after an exception
**Impact:** a leaked shim would suppress legitimate indexing for that field on
live saves (inverse bug — a real new session fails to index).
**Mitigation:** restore every wrapped field in a `finally` with a per-field
guarded `del` (mirror the existing single-field pattern); add a test asserting
all originals are restored after `rebuild_indexes()` raises.

### Risk 2: Abandoned rebuild thread keeps consuming CPU/Redis after timeout
**Impact:** a truly wedged rebuild thread lingers until process exit.
**Mitigation:** with the guard fix, AgentSession (the only known degenerate case)
no longer rebuilds in Step 1 at all, so the abandoned-thread path becomes a rare
safety net rather than the norm. Bound is defense-in-depth; the thread is a
daemon so it never blocks interpreter shutdown.

### Risk 3: Excluding AgentSession from Step 1 leaves a real orphan window
**Impact:** class-set/KeyField orphans could go un-rebuilt at Step 1.
**Mitigation:** Step 2 (`cleanup_corrupted_agent_sessions`) already calls the
guarded `repair_indexes()` unconditionally, and Step 2b `clean_indexes()` sweeps
class-set orphans — AgentSession index hygiene is fully covered downstream.

## Race Conditions

### Race 1: transient `on_save` shim assumes single-threaded rebuild
**Location:** `models/agent_session.py:2168-2212`
**Trigger:** a concurrent `AgentSession(...).save()` on another thread while the
shim is installed would hit the guarded `on_save`.
**Data prerequisite:** none.
**State prerequisite:** rebuild runs single-threaded in its actual call contexts
(worker startup Step 2, hourly reflection tick, update script) — the same
assumption the existing A1 guard already relies on and documents.
**Mitigation:** preserve the existing single-threaded-rebuild assumption and its
docstring note; do not broaden the shim's temporal scope. The guard only *skips*
work for identity-less hashes, so even a concurrent healthy save delegates to the
original `on_save` unchanged.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2204] Fixing the historical *producer* of the churn (catchup
  re-handling / re-enqueue loop) — tracked on its own issue; this plan fixes the
  re-inflation + startup-wedge mechanism only.
- Nothing else deferred — the generalized guard, the worker Step 1 exclusion, and
  the enforced budget are all in scope for this plan.

## Update System

No update system changes required — this is a pure code change to
`models/agent_session.py`, `scripts/popoto_index_cleanup.py`, and
`worker/__main__.py` behavior. The one-off `/update` migration
`purge_phantom_agent_sessions` already ships (self-cleans other machines on next
update) and is unchanged by this plan. No new deps, config, or migration.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change to index
rebuild and worker startup. No new CLI entry point, MCP surface, or bridge import.
The worker already runs `run_cleanup` and `repair_indexes` at startup; behavior is
corrected in place.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/popoto-index-hygiene.md` — document the generalized
  (all-IndexedField) A1 guard, the AgentSession exclusion from `run_cleanup`, and
  the enforced-budget/abandon-on-timeout semantics of Step 1.
- [ ] Update the A1-guard docstring in `models/agent_session.py:2112-2143` to
  describe the multi-field guard (currently says "only status is an IndexedField
  here").

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site for this area.

### Inline Documentation
- [ ] Comment the `run_cleanup` AgentSession exclusion explaining the
  re-inflation hazard.
- [ ] Comment the non-joining timeout path explaining why the old
  `with`-context-manager join was the wedge.

## Success Criteria

- [ ] `AgentSession.repair_indexes()` re-inflates zero phantoms across all four
  IndexedFields (extended `test_repair_does_not_reinflate_from_identityless_hashes`
  passes; `dbsize` stable across a rebuild pass over seeded identity-less hashes).
- [ ] AgentSession is excluded from `run_cleanup`'s generic model sweep; grep
  confirms the exclusion and a comment explains it.
- [ ] Worker Step 1 no longer blocks on an overrunning per-model rebuild (no
  `shutdown(wait=True)` on the critical path); a slow model is abandoned and
  startup proceeds.
- [ ] Worker startup completes and heartbeats within its normal threshold (360s)
  on a degenerate keyspace (verified via the worker-entry test's timeout path).
- [ ] `python -m tools.doctor` `agentsession-index-drift` check passes.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it never builds directly.

### Team Members

- **Builder (index-guard)**
  - Name: `index-guard-builder`
  - Role: Generalize the A1 guard to all IndexedFields in
    `AgentSession.repair_indexes`; update docstring.
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

- **Builder (worker-step1)**
  - Name: `worker-step1-builder`
  - Role: Exclude AgentSession from `run_cleanup`; make the per-model timeout
    actually abandon the overrunning thread.
  - Agent Type: builder
  - Domain: async-concurrency
  - Resume: true

- **Test Engineer (rebuild-tests)**
  - Name: `rebuild-test-engineer`
  - Role: Extend the identity-less re-inflation test to all four fields; add the
    worker Step 1 exclusion + budget-abandon tests.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (final)**
  - Name: `final-validator`
  - Role: Verify all success criteria and Verification rows.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Generalize the A1 guard to all IndexedFields
- **Task ID**: build-index-guard
- **Depends On**: none
- **Validates**: tests/unit/test_agentsession_pending_index_leak.py, tests/unit/test_session_health_phantom_guard.py
- **Assigned To**: index-guard-builder
- **Agent Type**: builder
- **Domain**: redis-popoto
- **Parallel**: true
- In `models/agent_session.py:2168-2203`, replace the single `status_field` shim
  with a loop that wraps `on_save` for every IndexedField in `cls._meta`
  (`status`, `task_type`, `claude_session_uuid`, `claude_pid`), reusing
  `_filter_hydrated_sessions([instance])` as the identity check.
- Restore each wrapped field's original `on_save` in the `finally` (per-field
  guarded `del`). Sum `_last_quarantined_identityless` across fields. Preserve the
  2-tuple return.
- Update the docstring (`:2112-2143`) to describe the multi-field guard.

### 2. Exclude AgentSession from run_cleanup + enforce Step 1 budget
- **Task ID**: build-worker-step1
- **Depends On**: none
- **Validates**: tests/unit/test_worker_entry.py
- **Assigned To**: worker-step1-builder
- **Agent Type**: builder
- **Domain**: async-concurrency
- **Parallel**: true
- In `scripts/popoto_index_cleanup.py`, exclude `AgentSession` from the generic
  sweep (add to the exclusion frozenset with an explanatory comment: rebuilt via
  the guarded `repair_indexes()` in worker Step 2 / hourly reflection).
- Replace the `with ThreadPoolExecutor(...) as executor:` block so a per-model
  `TimeoutError` abandons the still-running rebuild thread instead of blocking on
  `shutdown(wait=True)` — use `shutdown(wait=False)` / a daemon thread; log a
  WARNING naming the model; continue the sweep.

### 3. Extend + add tests
- **Task ID**: build-tests
- **Depends On**: build-index-guard, build-worker-step1
- **Assigned To**: rebuild-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Extend `test_repair_does_not_reinflate_from_identityless_hashes` to seed
  identity-less hashes with each of the four IndexedFields' `\x00idxset` pointers
  and assert zero re-inflation (`dbsize` stable) across a `repair_indexes()` pass.
- Add a test asserting AgentSession is absent from `run_cleanup`'s model set.
- Add a test asserting a model whose `rebuild_indexes` overruns the budget is
  abandoned (call returns without blocking) and the sweep continues.
- Add a test asserting all wrapped `on_save` originals are restored after
  `rebuild_indexes()` raises.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-index-guard, build-worker-step1, build-tests
- **Assigned To**: rebuild-test-engineer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/popoto-index-hygiene.md` per the Documentation section.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-index-guard, build-worker-step1, build-tests, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows and confirm every success criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Re-inflation test passes | `pytest tests/unit/test_agentsession_pending_index_leak.py -q` | exit code 0 |
| Worker entry tests pass | `pytest tests/unit/test_worker_entry.py -q` | exit code 0 |
| Phantom guard tests pass | `pytest tests/unit/test_session_health_phantom_guard.py -q` | exit code 0 |
| AgentSession excluded from run_cleanup sweep | `grep -n "AgentSession" scripts/popoto_index_cleanup.py` | output contains AgentSession |
| No blocking context-manager join on rebuild | `grep -n "with concurrent.futures.ThreadPoolExecutor" scripts/popoto_index_cleanup.py` | match count == 0 |
| Doctor index-drift passes | `python -m tools.doctor --json` | output contains agentsession-index-drift |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. For bounding Step 1, prefer (a) excluding AgentSession from `run_cleanup` and
   relying on Step 2's guarded `repair_indexes()` (the plan's recommendation), or
   (b) also keeping a generic non-blocking budget for all models? The plan does
   both (exclusion + real budget) — confirm that is the intended belt-and-suspenders.
2. Any appetite to additionally cap `rebuild_indexes` iteration (e.g., abort if it
   scans more than N× the class-set cardinality) as a deeper guardrail, or is the
   identity-less skip + exclusion sufficient for this slug?
