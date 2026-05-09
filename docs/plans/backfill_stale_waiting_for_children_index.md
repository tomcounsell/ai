---
status: Planning
type: chore
appetite: Small
owner: Tom Counsell
created: 2026-05-09
tracking: https://github.com/tomcounsell/ai/issues/1361
last_comment_id:
---

# Backfill Stale `waiting_for_children` Index Members

## Problem

The `$IndexF:AgentSession:status:waiting_for_children` Popoto index can contain members whose underlying `AgentSession` hash exists but whose actual `status` field is something else (e.g., `killed`, `completed`). These are not phantoms — `repair_indexes()`'s phantom detector (`hgetall(member)` returns empty) does not see them.

The hourly `agent-session-cleanup` reflection (`agent/session_health.py:1924`) only invokes `repair_indexes()` when **either** corrupt records were deleted **or** phantoms were observed (`agent/session_health.py:2027`). Genuine pre-`615eab9c` stale members never trip that gate, so they persist indefinitely.

Fix B (`agent/session_health.py:1577-1591`) masks the symptom — when iterating `waiting_for_children`, the guard re-reads the authoritative hash and skips any parent already in a terminal status. But Fix B never srem's the stale member. It costs an `HGETALL` per stale entry per hour, forever.

**Current behavior:**
- `repair_indexes()` only runs when corruption or phantoms are observed — it never gets a chance to flush genuine stale members for which the underlying hash is fine.
- Fix B's guard fires once per stale member per hour for the lifetime of the deployment. Production logs show zero firings in the visible window, but the existence of the guard is the smoking gun.
- Operators have no observability into how many stale members exist or which statuses they sit in.
- The existing test suite (`tests/unit/test_agent_session_index_corruption.py`, 432 lines) covers `running`, `pending`, `dormant`, `completed`, `failed`, `killed`, `abandoned`, `cancelled`. Zero coverage for `waiting_for_children`.

**Desired outcome:**
- Hourly `agent-session-cleanup` always runs `repair_indexes()` (no gate). Per-status stale-member counts are emitted as analytics so future drift is observable.
- Real Popoto-backed unit test confirms `transition_status(s, "waiting_for_children")` followed by `finalize_session(s, "killed")` leaves the `waiting_for_children` index member empty.
- Fix B stays in place as belt-and-suspenders — but on a healthy deployment its guard message should never appear after this lands.

## Freshness Check

**Baseline commit:** `bf6a3f8c312afc95dc5ff4d344d295bb7e500149`
**Issue filed at:** 2026-05-09T13:16:20Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_health.py:1577-1591` — Fix B guard — still holds. `_TERMINAL_STATUSES` membership check still wraps a `get_authoritative_session()` call followed by `continue`.
- `agent/session_health.py:2027` — `if cleaned > 0 or phantoms_filtered > 0:` — still holds. Surrounds the `repair_indexes()` invocation as documented.
- `models/agent_session.py:1905` — `repair_indexes()` classmethod — still holds. Returns `(stale_count, rebuilt_count)`. Counts only phantom members.
- `tests/unit/test_agent_session_index_corruption.py` — still 432 lines, still no `waiting_for_children` reference.
- `config/reflections.yaml:31-37` — `agent-session-cleanup` reflection wired to `agent.agent_session_queue.cleanup_corrupted_agent_sessions` on a 3600s interval.
- `analytics/collector.py:115` — `record_metric(name, value, dimensions)` API confirmed.
- `commit 615eab9c` — confirmed at HEAD: `fix(lifecycle): backfill _saved_field_values[status] before save to fix lazy-load index leak`, dated Apr 7 2026.

**Cited sibling issues/PRs re-checked:**
- #1335 — closed 2026-05-09T13:17:18Z with verdict "Fix B sufficient" (precondition for this chore).
- #1208 (PR for kill-is-terminal plan) — closed; shipped Fix B and deferred Fix E. Fix E is what this plan addresses on a finite-horizon basis.
- #1078 (PR for #1069) — merged 2026-04-20. **Direct prior art**: introduced `repair_indexes()` and the `cleaned > 0 or phantoms_filtered > 0` gate this plan opens.

**Commits on main since issue was filed (touching referenced files):**
- None on `agent/session_health.py`, `models/agent_session.py`, `models/session_lifecycle.py`, or the existing test file in the < 1-hour window between filing and planning.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/sdlc-1271.md` (`status: docs_complete`) — extends `cleanup_corrupted_agent_sessions()` for OS-process orphan reaping. Coexists with this work in the same function with separate concerns. No code-level conflict.
- `docs/plans/stalled-session-user-visible-alert.md` (`status: Critique-Resolved`) — touches `agent/session_health.py` for a UX concern (stalled-session alerts), not the index-cleanup pass. Confirmed via `grep`: no shared function or symbol.

**Notes:** No drift. All file:line pointers used in the plan are verbatim verified.

## Prior Art

- **PR #1078 (Fix agent-session-cleanup phantom-record destruction, #1069)**: Merged 2026-04-20. Introduced `_filter_hydrated_sessions()` (drops Popoto descriptor phantoms via `isinstance(s.agent_session_id, str)`), introduced `AgentSession.repair_indexes()`, and added the `cleaned > 0 or phantoms_filtered > 0` gate on its invocation in `cleanup_corrupted_agent_sessions`. **Direct prior art** — this plan opens the gate that #1078 added.
- **PR #1208 (kill-is-terminal)**: Shipped Fix B at `agent/session_health.py:1577-1591` (the operational mask). Deferred Fix E (root-cause investigation) to investigation #1335.
- **Issue #1335 (closed 2026-05-09, "Fix B sufficient")**: Concluded that root-cause patching is unnecessary because `615eab9c` closed the production drift source. This plan implements the cleanup horizon that #1335's closure assumes.
- **Commit `615eab9c` (Apr 7 2026)**: `fix(lifecycle): backfill _saved_field_values[status] before save to fix lazy-load index leak`. The watershed: every `transition_status()` and `finalize_session()` call after this commit reliably srems the old index member.

## Research

No relevant external findings — this is purely internal Popoto/Redis index maintenance work. No external libraries or APIs are involved.

## Data Flow

End-to-end trace for the new behavior:

1. **Entry point:** Reflection scheduler tick fires `agent-session-cleanup` every 3600s (`config/reflections.yaml:31-37`).
2. **Dispatch:** Calls `agent.agent_session_queue.cleanup_corrupted_agent_sessions` (re-export of `agent.session_health.cleanup_corrupted_agent_sessions`).
3. **Existing pass (unchanged):** Filter Popoto descriptor phantoms → iterate AgentSession records → delete corrupted → set `cleaned`, `phantoms_filtered`.
4. **NEW: Per-status pre-scan.** Before invoking `repair_indexes()`, walk `$IndexF:AgentSession:status:*` keys and count members whose `HGETALL` returns a non-empty hash but whose `status` field doesn't match the index key's status segment. This produces a `dict[str, int]` of per-status stale-member counts. (Phantoms — empty `HGETALL` — are NOT counted here; they are still counted by `repair_indexes()` as `stale_count`.)
5. **NEW: Unconditional `repair_indexes()` invocation.** Remove the `if cleaned > 0 or phantoms_filtered > 0:` gate. Always invoke `repair_indexes()` so every tick re-establishes index correctness.
6. **NEW: Metric emission.** For every `(status, count)` pair from step 4 with `count > 0`, call `record_metric("agent_session.indexed_field.stale_members", count, {"status": status})`.
7. **NEW: Logging.** INFO log line names `(stale_count, rebuilt_count, per_status_drift)` so operators see the sweep ran and what it found.
8. **Existing pass (unchanged):** Cross-process orphan reap (#1271) runs after.

## Architectural Impact

- **New dependencies:** None. Uses `analytics.collector.record_metric` (already imported elsewhere in `models/session_lifecycle.py`) and `popoto.models.query.POPOTO_REDIS_DB` (already used by `repair_indexes()`).
- **Interface changes:** None public. `repair_indexes()` signature is preserved (`(stale_count, rebuilt_count)`). The new per-status pre-scan logic is a private helper in `agent/session_health.py`.
- **Coupling:** No change. The pre-scan is in the same function that already invokes `repair_indexes()`.
- **Data ownership:** No change. Redis index ownership remains with Popoto's `IndexedFieldMixin`.
- **Reversibility:** Trivial. Reverting the change restores the gate. Stale members would re-accumulate at the historical rate (≈ zero post-`615eab9c`).

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is self-contained; acceptance criteria are explicit in the issue)
- Review rounds: 1 (standard PR review; no design review needed — the design is mechanical)

This is a single-afternoon chore. Total LOC: roughly +60 in `session_health.py`, +50 in the test file. No new modules, no new config, no new dependencies.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Real-Redis test fixture | `pytest tests/unit/test_agent_session_index_corruption.py -q --collect-only` | Confirms the existing index-corruption test file collects (Popoto-backed real-Redis fixture is already present in the test suite). |
| Popoto installed | `python -c "from popoto.models.query import POPOTO_REDIS_DB; print(POPOTO_REDIS_DB.ping())"` | Confirms the Redis connection used by `repair_indexes()`. Should print `True`. |

Run all checks: `python scripts/check_prerequisites.py docs/plans/backfill_stale_waiting_for_children_index.md`

## Solution

### Key Elements

- **Per-status pre-scan helper** (`agent/session_health.py`): Iterates `$IndexF:AgentSession:status:*` keys, returns `dict[str, int]` of per-status stale counts (members whose hash's `status` field disagrees with the index key segment). Treats missing-hash members as "not counted here" (phantoms are `repair_indexes()`'s job).
- **Gate removal** (`agent/session_health.py:2027`): Replace `if cleaned > 0 or phantoms_filtered > 0:` with unconditional invocation. Logging stays inside `try/except` so a `repair_indexes()` failure cannot abort the cleanup function.
- **Metric emission** (`agent/session_health.py`): Wraps `record_metric()` in a fire-and-forget `try/except` consistent with existing analytics call sites.
- **Test gap closure** (`tests/unit/test_agent_session_index_corruption.py`): New test class `TestWaitingForChildrenExitTransition` using a real Popoto-backed `AgentSession` (the file already imports the real Redis fixture). Asserts that after `transition_status(s, "waiting_for_children")` then `finalize_session(s, "killed")`, the `$IndexF:AgentSession:status:waiting_for_children` set does NOT contain `s.db_key.redis_key`.

### Flow

Reflection scheduler tick → `cleanup_corrupted_agent_sessions()` → corrupt-record pass (unchanged) → **NEW** per-status pre-scan → **NEW** unconditional `repair_indexes()` → **NEW** per-status `record_metric()` emissions → orphan reap (unchanged) → return `{"corrupted": cleaned, "orphans": orphans_reaped}` (unchanged contract).

### Technical Approach

- **Decision: keep the existing dict return contract.** `cleanup_corrupted_agent_sessions()` continues to return `{"corrupted": int, "orphans": int}`. The per-status drift counts go to logs and metrics, not the return value — adding fields would force an update to `worker/__main__.py`, `scripts/update/run.py`, and dashboard projection logic for no operator gain.
- **Decision: emit `agent_session.indexed_field.stale_members` with `dimensions={"status": <status>}`** instead of dotted-suffix metrics like `agent_session.indexed_field.stale_members.waiting_for_children`. Matches the project-wide analytics convention (see `models/session_lifecycle.py:441`, `agent/pipeline_state.py:146`). Documented in the recon's "Revised" bucket.
- **Decision: pre-scan ALL `IndexedField` indexes**, not just `status`. The acceptance criterion calls out `waiting_for_children` because it's the only known offender, but the pre-scan loop is identical for any indexed field. This gives Future-Operator-You a free signal if drift ever appears for `session_type`, `project_key`, etc.
- **Decision: pre-scan reads `HGETALL` for every member** (one Redis round-trip per member). Cost is `O(total members across all IndexedField indexes)` — bounded by the number of live sessions × number of indexed fields, typically << 10,000 across all envs. At 1-hour cadence this is negligible.
- **Decision: gate removal is permanent**, not one-shot. The acceptance criterion says "runs unconditionally once instead of only when corruption/phantoms are detected." Two readings:
  - Strict: a one-shot flag, then revert to gated.
  - Permissive: gate is unconditional from now on.
  We pick **permissive** because (a) it's strictly simpler (no flag state), (b) cost is bounded and trivial, and (c) it gives durable safety against ANY future drift, not just pre-`615eab9c` residue. This deviation from the literal acceptance criterion is the only judgment call in this plan; it is called out in **Open Questions** below.
- **Decision: `repair_indexes()` signature stays stable.** Three production callers (`agent/session_health.py`, `agent/session_pickup.py:411,549`, `scripts/update/run.py:1269`) all unpack `(stale_count, rebuilt_count)`. The per-status pre-scan lives in the caller, not in `repair_indexes()` — that keeps `repair_indexes()` model-agnostic.
- **Decision: pre-scan logs at INFO when any drift is observed**, DEBUG when zero drift. INFO log includes per-status counts so operators get the operational signal the issue asks for.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] Confirm: the new pre-scan and metric emission are wrapped in `try/except Exception`, mirroring the existing `repair_indexes()` invocation pattern at `agent/session_health.py:2028-2040`. The cleanup pass must never abort because of an analytics or pre-scan failure.
- [ ] Add a unit test that monkey-patches `record_metric` to raise `RuntimeError`, then asserts `cleanup_corrupted_agent_sessions()` still returns its dict contract. Use the existing test patterns in `tests/unit/test_session_health_phantom_guard.py` as the model.
- [ ] Add a unit test that monkey-patches `POPOTO_REDIS_DB.keys` to raise on the pre-scan step, asserts the cleanup pass logs a WARNING and proceeds to call `repair_indexes()` regardless.

### Empty/Invalid Input Handling

- [ ] When `$IndexF:AgentSession:status:*` matches zero keys (fresh deployment), pre-scan returns `{}`. Cleanup logs DEBUG, no metrics emitted. Tested via the empty-Redis fixture.
- [ ] When `HGETALL` returns an empty dict for a member (phantom), the pre-scan does NOT count it as stale (phantoms are still `repair_indexes()`'s `stale_count`). Tested by pre-seeding a stale member via `POPOTO_REDIS_DB.sadd("$IndexF:AgentSession:status:waiting_for_children", "AgentSession:does-not-exist")` and asserting the pre-scan returns `{}` for that case.

### Error State Rendering

- [ ] No user-visible output for this change — purely internal observability via logs and analytics. Operator-facing rendering happens via the existing `python -m tools.analytics summary` and dashboard, both of which pick up the new metric automatically because `record_metric` writes to the shared `analytics_metrics` SQLite store and Redis live counters.

## Test Impact

- [ ] `tests/unit/test_agent_session_index_corruption.py` — UPDATE: add `TestWaitingForChildrenExitTransition` class with three tests: (1) lazy-loaded `s` enters `waiting_for_children` then `finalize_session(s, "killed")` and the `waiting_for_children` index does NOT contain `s.db_key.redis_key`; (2) `transition_status(s, "waiting_for_children")` then `transition_status(s, "completed")` produces the same outcome via the non-finalize path; (3) the pre-existing parameterized terminal-status tests at line 244 are extended to include `"waiting_for_children"` in the source-status parameter set.
- [ ] `tests/unit/test_session_health_phantom_guard.py` — UPDATE: change the existing assertion that `repair_indexes()` is invoked only when `cleaned > 0 or phantoms_filtered > 0` to assert it is invoked on every tick. Specifically, the `test_skips_repair_indexes_when_no_corruption` style test (if one exists — confirm at build time via `grep "if.*cleaned.*phantoms_filtered" tests/`) must flip to assert UNCONDITIONAL invocation. If no such test exists today, this disposition becomes "no change."
- [ ] `tests/unit/test_session_health_orphan_process_reap.py` — confirmed unaffected via grep: this test patches the cleanup function's return shape, not its `repair_indexes()` call gating.
- [ ] `tests/unit/test_session_health_sibling_phantom_safety.py` — confirmed unaffected: tests phantom filtering via `_filter_hydrated_sessions()`, untouched by this change.

New tests added:
- [ ] `tests/unit/test_session_health_unconditional_index_repair.py` (new file) — three tests:
  - `test_repair_indexes_called_when_no_corruption`: cleanup function is invoked on a clean DB; `repair_indexes()` is still called.
  - `test_per_status_metric_emitted_for_stale_members`: pre-seed a stale `waiting_for_children` member; assert `record_metric("agent_session.indexed_field.stale_members", 1, {"status": "waiting_for_children"})` is emitted.
  - `test_metric_emission_failure_does_not_abort_cleanup`: patch `record_metric` to raise; assert `cleanup_corrupted_agent_sessions()` returns its dict contract anyway.

## Rabbit Holes

- **Refactoring `repair_indexes()` to return per-status counts.** Tempting, because the per-status loop is very similar to what `repair_indexes()` already does. But `repair_indexes()` is a model classmethod (`AgentSession.repair_indexes()`) and has three production callers; widening its contract risks breakage. Keep the per-status pre-scan in the caller.
- **Adding a test for every existing IndexedField on AgentSession.** Out of scope. The issue's acceptance criterion targets `waiting_for_children` specifically. Coverage for other statuses already exists in the file. The per-status pre-scan handles them generically without needing per-field tests.
- **Investigating whether Fix B can now be deleted.** Out of scope for this chore. Deleting Fix B requires confidence that no new drift sources exist post-`615eab9c`, which is investigation territory. Fix B is cheap; it stays.
- **Migrating the existing test file to use a real Popoto-backed fixture instead of `MagicMock`.** Tempting (the issue criticizes `MagicMock` use), but the existing tests at lines 60-432 already pass and validate intentional behavior at the `_saved_field_values` level. Rewriting them to use real Redis would be a separate "test infrastructure" project. The new `waiting_for_children` test uses real Popoto, satisfying the acceptance criterion without forcing a wholesale rewrite.
- **Adding a Prometheus/Grafana panel for the new metric.** No existing analytics dashboard projection for SQLite-backed metrics. The metric is queryable via `python -m tools.analytics summary` and `... export`. Adding visualization is `[SEPARATE-SLUG]` material.

## Risks

### Risk 1: Removing the gate increases per-tick Redis traffic on healthy deployments
**Impact:** `repair_indexes()` runs `KEYS $IndexF:AgentSession:*` + per-key `SMEMBERS` + per-member `HGETALL`. On a hot worker with thousands of indexed sessions, this could add 50-500 ms per tick.
**Mitigation:** This is the **same cost** the function already pays whenever a corrupt record is deleted (which happens routinely). Pre-#1078 Bridge logs show the gate flipping multiple times per day. The cost is already in the operational envelope. INFO logging makes any actual regression visible quickly. Rollback is a one-line revert.

### Risk 2: Pre-scan double-reads members that `repair_indexes()` is about to delete
**Impact:** Wasted I/O. On Redis, the `HGETALL` calls in the pre-scan are immediately followed by `repair_indexes()` re-fetching the same indexes.
**Mitigation:** Acceptable cost. Both operations are in the same hourly tick. Combining them into one pass would require widening `repair_indexes()`'s signature (a Rabbit Hole, see above). The duplication is honest and easy to read.

### Risk 3: Per-status metric cardinality explosion if a future bug writes garbage status values into the index
**Impact:** `record_metric` accepts arbitrary `status` strings, so a bug producing 100s of bogus status values would create 100s of distinct dimension keys.
**Mitigation:** Validate the status segment against `models.AgentSession`'s known status set before emitting. If a status is unknown, emit `dimensions={"status": "unknown"}` and log WARNING with the actual value so operators can investigate without exploding cardinality.

### Risk 4: A future Popoto upgrade changes index naming or `repair_indexes()` semantics
**Impact:** Pre-scan loop could miss new index keys or count incorrectly.
**Mitigation:** The pre-scan uses the same `$IndexF:AgentSession:` prefix that `repair_indexes()` uses. They are coupled by construction — if Popoto changes the convention, both break together and the change is loud (KEYS returns an empty list). The unit tests assert presence of the test member in a specific index key, so a Popoto-internal rename would surface in CI.

## Race Conditions

### Race 1: Pre-scan reads a hash mid-transition
**Location:** `agent/session_health.py` (new pre-scan helper)
**Trigger:** A worker is in the middle of `transition_status(s, "waiting_for_children" -> "completed")` while the cleanup tick runs. Between the `SMEMBERS` and `HGETALL` calls, the hash's `status` field flips.
**Data prerequisite:** None. The pre-scan is observational; it does not mutate.
**State prerequisite:** Mid-transition is not a correctness concern because the pre-scan just emits a metric. The actual cleanup happens via `repair_indexes()`, which deletes the entire index key and rebuilds from live hashes — naturally idempotent.
**Mitigation:** Accept the false-positive metric (one tick will report "1 stale waiting_for_children" for a session that's mid-transition). Worth less than 1% noise on a typical deployment. Not a functional bug.

### Race 2: `repair_indexes()` clears an index key while a save() is mid-flight
**Location:** `models/agent_session.py:1928` (existing code; not new)
**Trigger:** Another worker calls `session.save()` between `repair_indexes()`'s `delete(index_key)` and `rebuild_indexes()`.
**Data prerequisite:** This is a pre-existing race in `repair_indexes()`, not a new one introduced by this plan.
**State prerequisite:** The newly-saved hash will be re-added to the index by `rebuild_indexes()`'s scan, so eventual consistency holds.
**Mitigation:** No change. Pre-existing behavior; #1078 has been in production since Apr 2026 with no reported issues. Documenting only for completeness.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1335] Root-cause investigation of pre-`615eab9c` drift. Investigation closed as "Fix B sufficient." If the operational metric reveals drift recurring post-deploy, reopen.
- [SEPARATE-SLUG #1335] Removing Fix B (`agent/session_health.py:1577-1591`). Reopening triggers in #1335: `[session-health] Skipping terminal parent` >5x in 24h, or new code path mutating `AgentSession.status` outside the lifecycle module. None met today.
- Nothing else deferred — the plan addresses every line of the issue's acceptance criteria.

## Update System

No update system changes required — this feature is purely internal. The `agent-session-cleanup` reflection is already wired in `config/reflections.yaml:31-37` and ships with every deployment. No new env vars, no new dependencies, no migration steps.

## Agent Integration

No agent integration required — this is a worker/reflection-internal change. The new metric is queryable via the existing `python -m tools.analytics` CLI (already in `pyproject.toml [project.scripts]`), so there is nothing new to wire for agent visibility.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/session-lifecycle.md` (or whichever existing doc covers `agent-session-cleanup`; confirm at build time via `grep -l "agent-session-cleanup" docs/features/`) with a one-paragraph note: "As of issue #1361, `repair_indexes()` runs unconditionally each tick and emits per-status drift counts via the `agent_session.indexed_field.stale_members` metric."
- [ ] Update `docs/features/README.md` if a feature doc index entry exists for session-lifecycle. If not, no change.

### External Documentation Site

- [ ] Not applicable. This repo does not host external docs.

### Inline Documentation

- [ ] Update the docstring of `cleanup_corrupted_agent_sessions()` (`agent/session_health.py:1924`) to reflect that `repair_indexes()` now runs unconditionally and that per-status metrics are emitted.
- [ ] Add a code comment at the gate-removal site explaining the rationale (link to #1361).

## Success Criteria

- [ ] `agent/session_health.py:cleanup_corrupted_agent_sessions()` calls `AgentSession.repair_indexes()` on every tick (gate removed).
- [ ] Per-status stale-member counts logged at INFO when any drift is observed, DEBUG when zero.
- [ ] Metric `agent_session.indexed_field.stale_members` emitted with `dimensions={"status": <status>}` for every stale member found.
- [ ] New test `TestWaitingForChildrenExitTransition` in `tests/unit/test_agent_session_index_corruption.py` passes against real Popoto-backed `AgentSession`.
- [ ] After `transition_status(s, "waiting_for_children")` followed by `finalize_session(s, "killed")`, `POPOTO_REDIS_DB.smembers("$IndexF:AgentSession:status:waiting_for_children")` does NOT contain `s.db_key.redis_key`.
- [ ] New file `tests/unit/test_session_health_unconditional_index_repair.py` exists with three tests (no-corruption, metric emission, metric failure resilience).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `if cleaned > 0 or phantoms_filtered > 0:` no longer appears in `agent/session_health.py`.
- [ ] No pre-existing tests fail. Affected files: `tests/unit/test_session_health_phantom_guard.py`, `tests/unit/test_agent_session_index_corruption.py`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (session-health)**
  - Name: session-health-builder
  - Role: Implement the gate removal, per-status pre-scan helper, metric emission, and inline doc updates in `agent/session_health.py`.
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: index-test-builder
  - Role: Add `TestWaitingForChildrenExitTransition` to the existing test file, create the new `test_session_health_unconditional_index_repair.py`, update the affected test in `test_session_health_phantom_guard.py` if present.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (suite)**
  - Name: suite-validator
  - Role: Run the full unit test suite and the targeted index-corruption tests; verify metric emission via Redis fixture; confirm no regressions in phantom-guard or sibling-phantom tests.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-builder
  - Role: Update `docs/features/session-lifecycle.md` (or whichever existing doc covers agent-session-cleanup) and the docstring on `cleanup_corrupted_agent_sessions()`.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard tier 1 / tier 2 list — see template.)

## Step by Step Tasks

### 1. Implement gate removal, pre-scan, metric emission
- **Task ID**: build-session-health
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_index_corruption.py, tests/unit/test_session_health_unconditional_index_repair.py (create), tests/unit/test_session_health_phantom_guard.py
- **Informed By**: prior art PR #1078 (the gate that this opens)
- **Assigned To**: session-health-builder
- **Agent Type**: builder
- **Parallel**: false
- Add private helper `_count_per_status_stale_index_members(cls=AgentSession) -> dict[str, int]` near `cleanup_corrupted_agent_sessions` in `agent/session_health.py`. The helper iterates `$IndexF:AgentSession:status:*` keys, walks `SMEMBERS`, and counts members whose `HGETALL` returns a non-empty hash but whose `status` field doesn't match the index key segment. Phantoms (empty `HGETALL`) are skipped.
- Validate emitted statuses against the known `AgentSession` status set; map unknowns to `"unknown"` and log WARNING with the actual value.
- In `cleanup_corrupted_agent_sessions`, replace the `if cleaned > 0 or phantoms_filtered > 0:` block at line 2027 with an unconditional `try/except`-wrapped call to `repair_indexes()`, preceded by the pre-scan helper and followed by `record_metric` calls per `(status, count)` pair.
- Wrap `record_metric` calls in `try/except Exception` (silent on failure) to match existing analytics call-site patterns.
- Update the docstring of `cleanup_corrupted_agent_sessions()` to reflect the new behavior (link to issue #1361).
- Add a code comment at the gate-removal site explaining the rationale.

### 2. Add `waiting_for_children` test coverage
- **Task ID**: build-waiting-for-children-tests
- **Depends On**: build-session-health
- **Validates**: tests/unit/test_agent_session_index_corruption.py
- **Assigned To**: index-test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add a new test class `TestWaitingForChildrenExitTransition` to `tests/unit/test_agent_session_index_corruption.py`. Use a real Popoto-backed `AgentSession` (NOT `MagicMock` — the existing helpers `_make_lazy_session` / `_make_fully_loaded_session` are mock-based; the new tests must construct real sessions and use the real-Redis fixture pattern from another test file in this repo if needed).
- Test 1 (`test_finalize_session_clears_waiting_for_children_index`): create a real session, call `transition_status(s, "waiting_for_children")`, call `finalize_session(s, "killed", skip_auto_tag=True, skip_checkpoint=True)`, assert `POPOTO_REDIS_DB.smembers("$IndexF:AgentSession:status:waiting_for_children")` does NOT contain `s.db_key.redis_key`.
- Test 2 (`test_transition_to_completed_clears_waiting_for_children_index`): same as Test 1 but use `transition_status(s, "completed")` for the second step instead of `finalize_session(...)`.
- Test 3: extend the parameterized terminal-status test at line 244 of the existing file (if present) to include `"waiting_for_children"` as a source status. If the test does not parameterize source status, add a new parameterized test that covers source = `"waiting_for_children"`, target ∈ `{"completed", "failed", "killed", "abandoned", "cancelled"}`.

### 3. Add unconditional-repair test file
- **Task ID**: build-unconditional-repair-tests
- **Depends On**: build-session-health
- **Validates**: tests/unit/test_session_health_unconditional_index_repair.py (create)
- **Assigned To**: index-test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_session_health_unconditional_index_repair.py`.
- Test 1 (`test_repair_indexes_called_when_no_corruption`): empty Redis (no corrupt records, no phantoms); patch `AgentSession.repair_indexes` to a Mock; call `cleanup_corrupted_agent_sessions()`; assert the patched method was called exactly once.
- Test 2 (`test_per_status_metric_emitted_for_stale_members`): pre-seed `POPOTO_REDIS_DB.sadd("$IndexF:AgentSession:status:waiting_for_children", <real-session-key-with-status="killed">)`. Patch `analytics.collector.record_metric` to a Mock. Call `cleanup_corrupted_agent_sessions()`. Assert `record_metric.call_args_list` contains a call with `("agent_session.indexed_field.stale_members", 1, {"status": "waiting_for_children"})`.
- Test 3 (`test_metric_emission_failure_does_not_abort_cleanup`): patch `record_metric` to raise `RuntimeError`; assert `cleanup_corrupted_agent_sessions()` still returns `{"corrupted": 0, "orphans": 0}` (or whatever the empty-DB shape is) without propagating the exception.
- Test 4 (`test_pre_scan_failure_logged_but_not_fatal`): patch `POPOTO_REDIS_DB.keys` to raise on the pre-scan call; assert `cleanup_corrupted_agent_sessions()` proceeds, logs WARNING, and still calls `repair_indexes()`.

### 4. Update phantom-guard test if it asserts gating
- **Task ID**: update-phantom-guard-test
- **Depends On**: build-session-health
- **Validates**: tests/unit/test_session_health_phantom_guard.py
- **Assigned To**: index-test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- `grep -n "cleaned.*phantoms_filtered\|repair_indexes" tests/unit/test_session_health_phantom_guard.py`. If any test asserts that `repair_indexes()` is invoked ONLY when corruption or phantoms are seen, flip it to assert UNCONDITIONAL invocation. If no such test exists, this task is a no-op (mark complete with a comment).

### 5. Validate suite
- **Task ID**: validate-suite
- **Depends On**: build-session-health, build-waiting-for-children-tests, build-unconditional-repair-tests, update-phantom-guard-test
- **Assigned To**: suite-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_agent_session_index_corruption.py tests/unit/test_session_health_unconditional_index_repair.py tests/unit/test_session_health_phantom_guard.py tests/unit/test_session_health_orphan_process_reap.py tests/unit/test_session_health_sibling_phantom_safety.py -v`. All pass.
- Run `pytest tests/unit/ -x -q -n auto`. No regressions.
- Run `python -m ruff check agent/session_health.py tests/unit/test_session_health_unconditional_index_repair.py tests/unit/test_agent_session_index_corruption.py`. Exit 0.
- Run `python -m ruff format --check agent/session_health.py tests/unit/test_session_health_unconditional_index_repair.py tests/unit/test_agent_session_index_corruption.py`. Exit 0.
- Run `grep -n "if cleaned > 0 or phantoms_filtered > 0" agent/session_health.py`. Exit 1 (string must be gone).

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-suite
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: false
- `grep -l "agent-session-cleanup" docs/features/` to find the relevant feature doc. Update it with a one-paragraph note about the unconditional index repair and the new metric.
- Verify the docstring update on `cleanup_corrupted_agent_sessions()` is in place.
- If `docs/features/README.md` lists session-lifecycle features, confirm no index-table change is needed.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: suite-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the test suite once more after docs are committed.
- Verify all Success Criteria checkboxes can be flipped.
- Confirm `git diff --stat` shows changes only in `agent/session_health.py`, `tests/unit/test_agent_session_index_corruption.py`, `tests/unit/test_session_health_unconditional_index_repair.py` (new), optionally `tests/unit/test_session_health_phantom_guard.py`, and the targeted feature doc.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_agent_session_index_corruption.py tests/unit/test_session_health_unconditional_index_repair.py tests/unit/test_session_health_phantom_guard.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/session_health.py tests/unit/test_session_health_unconditional_index_repair.py tests/unit/test_agent_session_index_corruption.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_health.py tests/unit/test_session_health_unconditional_index_repair.py tests/unit/test_agent_session_index_corruption.py` | exit code 0 |
| Gate removed | `grep -n "if cleaned > 0 or phantoms_filtered > 0" agent/session_health.py` | exit code 1 |
| `repair_indexes` still called | `grep -n "AgentSession.repair_indexes()" agent/session_health.py` | output contains `repair_indexes` |
| `waiting_for_children` test exists | `grep -n "waiting_for_children" tests/unit/test_agent_session_index_corruption.py` | output > 0 |
| New test file exists | `test -f tests/unit/test_session_health_unconditional_index_repair.py` | exit code 0 |
| Metric name present | `grep -n "agent_session.indexed_field.stale_members" agent/session_health.py` | output contains the literal |
| No regressions in full suite | `pytest tests/unit/ -x -q -n auto` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Resolved Decisions

The three open questions raised in planning have been resolved as follows. These decisions are binding and replace the original `## Open Questions` section.

### Q1 — Gate removal: Permissive (permanent), NOT one-shot

**Decision:** Remove the `cleaned > 0 or phantoms_filtered > 0` gate at `agent/session_health.py:2027` permanently. `repair_indexes()` runs every tick.

**Rationale:**
- Matches PR #1078's design philosophy. `AgentSession.repair_indexes()` (`models/agent_session.py:1905-1931`) is already idempotent — running on a clean DB is a no-op beyond Redis I/O.
- One-shot would require novel persistent-flag state-management (Redis key like `cleanup:initial_index_sweep_done`) with no prior art in this codebase.
- Permissive is strictly simpler and gives durable safety against any future drift source, not just the pre-`615eab9c` residue.
- Per-tick cost is bounded (Risk 1 analysis): equivalent to what `repair_indexes()` already pays whenever any corrupt record is deleted, which happens routinely.

### Q2 — Metric shape: `dimensions={"status": <status>}`, NOT dotted-suffix

**Decision:** Emit `record_metric("agent_session.indexed_field.stale_members", count, {"status": status})`.

**Rationale:**
- Canonical signature: `analytics/collector.py:115-119` — `record_metric(name, value, dimensions)`.
- All existing call sites use the dimensions form: `models/session_lifecycle.py:441-448`, `agent/pipeline_state.py:146`, `agent/memory_retrieval.py:329, 355`.
- Zero dotted-suffix examples exist in the codebase. Following project convention is mandatory.

### Q3 — Pre-scan scope: status-only pre-scan + free all-IndexedField repair

**Decision:** Pre-scan `$IndexF:AgentSession:status:*` keys specifically. Let `repair_indexes()` handle ALL indexed fields naturally (no caller-side widening needed).

**Rationale:**
- The pre-scan emits `dimensions={"status": <status>}` — that metric shape only makes sense for the `status` index, where the index-key segment IS a status value.
- For other indexed fields (`session_type`, `project_key`, etc.), the index-key segment is not a status, so a single status-keyed metric would be incoherent. Adding per-field metrics would explode scope.
- `repair_indexes()` (`models/agent_session.py:1921-1928`) already scans every `IndexedField` on its own — that's free coverage for non-status fields. The unconditional invocation in step 5 of the Data Flow already gives durable safety for `session_type`, `project_key`, etc.
- This resolves the previously-implicit inconsistency between the plan's "pre-scan ALL `IndexedField` indexes" stance (Solution → Technical Approach, Decision 3) and the `{"status": <s>}` metric shape (Solution → Technical Approach, Decision 2). The pre-scan is status-only; `repair_indexes()` covers the rest.

**Plan section updates required (carried into implementation):**
- `## Solution → Technical Approach`, Decision 3: Replace "pre-scan ALL `IndexedField` indexes, not just `status`" with "pre-scan status-only; `repair_indexes()` covers other IndexedFields generically." (Implementation reflects this directly; the plan body is left as-is for diff legibility but the implementer should follow the resolved decision.)
