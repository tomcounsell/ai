---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/950
last_comment_id:
---

# Stale Index Regression: Pending Index Entry Survives Pending-to-Killed Transitions

## Problem

After PR #905 (issue #898) shipped on 2026-04-11, the reflections report on 2026-04-13 (issue #942) flagged 540 `"Stale index entry"` warnings in `logs/bridge.log`. All warnings show sessions whose actual status is `killed` but whose Redis key remains in the `$IdxF:AgentSession:status:pending` index set. The warnings fire every worker wake-up cycle from both `_pop_agent_session` (line 746) and `_pop_agent_session_with_fallback` (line 884), producing hundreds of log lines per day.

**Current behavior:**
Sessions killed via `valor-session kill --all` or other terminal-transition paths leave orphan entries in the `pending` status index. The worker logs a stale-index warning for each orphan on every pop cycle. The reflections regression detector (installed by PR #905 as acceptance criterion #8) fires.

**Desired outcome:**
- Zero new stale-index warnings per 24-hour period (or single-digit from genuine concurrent races)
- Every terminal transition path correctly removes the old index entry
- The reflections regression check at `scripts/reflections.py:964` remains installed and silent

## Freshness Check

**Baseline commit:** `58b0ea041740e4a7e1846f05e3b78f9493c023ce`
**Issue filed at:** 2026-04-14T05:17:27Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/agent_session_queue.py:746` — stale-index warning in `_pop_agent_session` — still present
- `agent/agent_session_queue.py:884` — stale-index warning in `_pop_agent_session_with_fallback` — still present
- `tools/valor_session.py:657` — `finalize_session(s, "killed", reason="valor-session kill --all")` — still present
- `tools/valor_session.py:680` — `finalize_session(session, "killed", reason="valor-session kill")` — still present
- `tools/agent_session_scheduler.py:833` — CLI kill via scheduler — still present
- `scripts/update/run.py:153` — `_cleanup_stale_sessions` — still present; now only iterates `("running",)` per comment at line 199-200
- `models/session_lifecycle.py:357-358` — `_saved_field_values["status"]` backfill in `finalize_session` — still present
- `models/session_lifecycle.py:508-509` — `_saved_field_values["status"]` backfill in `transition_status` — still present

**Cited sibling issues/PRs re-checked:**
- #898 — CLOSED 2026-04-11, fixed `completed`-stomp path via Layer 1/2/3
- PR #905 — MERGED 2026-04-11, shipped #898's fix
- #942 — CLOSED 2026-04-14, reflections report that flagged this regression

**Commits on main since issue was filed (touching referenced files):**
- `82186dcc` fix(bridge): hydrate reply-thread context in resume-completed branch (#953) — touches `agent/agent_session_queue.py` only in the resume-hydration path, not the kill/pop paths. Irrelevant.

**Active plans in `docs/plans/` overlapping this area:**
- `nudge-stomp-append-event-bypass.md` — predecessor plan for #898; status "In Review"; does not address the `killed` transition path

**Notes:** `_cleanup_stale_sessions` no longer iterates `pending` sessions (line 199-201), which eliminates hypothesis 3 from the issue body.

## Prior Art

- **#898** (CLOSED 2026-04-11): Nudge stomp regression — fixed the `completed`-stomp path. Applied Layer 1 (`finalized_by_execute` gate), Layer 2 (`update_fields` narrowing on `_append_event_dict`), Layer 3 (reflections regression check). Did NOT audit the `killed` transition path.
- **#825** (CLOSED 2026-04-08): Session status index staleness — stale re-query in `_complete_agent_session` causing wrong status display. Fixed with status-filter-free re-query and tie-breaking.
- **#783** (CLOSED, shipped): Ghost running sessions from lazy-load index leak — `_saved_field_values` not populated for IndexedFields on lazy-loaded models. Fixed by backfilling `_saved_field_values["status"]` in `transition_status()` and `finalize_session()`.
- **#867** (CLOSED 2026-04-10): Race — nudge re-enqueue stomped by worker finally-block finalize_session(). Fixed by PR #885's CAS in finalize_session.
- **#738** (CLOSED 2026-04-06): Stale session cleanup kills live sessions. Fixed by restricting `_cleanup_stale_sessions` to `running` only.

## Spike Results

### spike-1: Verify whether full-save call sites on AgentSession bypass the `_saved_field_values` backfill
- **Assumption**: "Some code path calls `self.save()` (full save) on a stale object, clobbering the status back to an old value and corrupting the index"
- **Method**: code-read
- **Finding**: Confirmed risk. Three methods on AgentSession do full `self.save()` without backfilling `_saved_field_values["status"]`: `set_link()` (line 1338), `push_steering_message()` (line 1441), `pop_steering_messages()` (line 1453). On lazy-loaded instances from `filter()`, `_saved_field_values["status"]` is NOT set (only KeyFields are eagerly decoded — see `_create_lazy_model()` in Popoto's `encoding.py:416`). If any of these methods are called on a stale object whose in-memory status differs from on-disk status, the full save writes the stale status to Redis AND the `on_save` hook for the status field would EITHER (a) not fire the `srem` because `_saved_field_values["status"]` is None (guard at `indexed_field_mixin.py:139`: `if old_value is not None`), or (b) fire `srem` against the wrong old value. In case (a), the session gets ADDED to the in-memory status's index set without being REMOVED from the on-disk status's index set — creating an orphan.
- **Confidence**: high — the code path is confirmed; the exact runtime trigger needs Phase 1 instrumentation
- **Impact on plan**: The fix must extend the partial-save pattern (#898 Layer 2) to ALL save() call sites on AgentSession that are not part of a lifecycle transition, OR add a defensive `_saved_field_values["status"]` backfill to the save() method itself.

### spike-2: Verify `_cleanup_stale_sessions` no longer targets pending sessions
- **Assumption**: "Hypothesis 3 (cleanup races with workers on pending sessions) is still viable"
- **Method**: code-read
- **Finding**: Eliminated. `_cleanup_stale_sessions` at `scripts/update/run.py:201` now iterates only `("running",)`. Comment at line 199-200: "pending sessions are never stale — they were never started; 'pending' was added in PR #739 by mistake".
- **Confidence**: high
- **Impact on plan**: Hypothesis 3 is dead. Focus on the stale-object full-save mechanism.

### spike-3: Verify that `rebuild_indexes()` would clean up the orphan if called correctly
- **Assumption**: "The rebuild_indexes() call in the stale-index warning handler should fix the orphan"
- **Method**: code-read
- **Finding**: Confirmed. `rebuild_indexes()` (Popoto `base.py:2707`) deletes ALL index sets (step 1: lines 2742-2777), then scans all instance hashes and re-runs `on_save()` for each (step 2: lines 2779-2819). After a complete rebuild, any session with `status=killed` on disk would only appear in the `killed` index set. The orphan would be cleaned up. But the warning persists across cycles, meaning either (a) rebuild is failing silently (caught at line 753-754), or (b) the orphan is being RE-CREATED after each rebuild by a concurrent stale-object save.
- **Confidence**: high
- **Impact on plan**: The persistent nature of the warnings strongly supports the "stale-object full-save re-creating the orphan" hypothesis. Each rebuild cleans up, but the next stale save re-pollutes.

## Data Flow

### Orphan-creation sequence (confirmed mechanism)

1. **Session created**: `enqueue_agent_session()` creates session with `status="pending"`. Popoto adds hash key `HK` to `$IdxF:AgentSession:status:pending`.
2. **Worker pops**: `_pop_agent_session` transitions via `transition_status(chosen, "running")`. Backfills `_saved_field_values["status"] = "pending"`, sets `status = "running"`, saves. `on_save` does `srem(pending, HK)`, `sadd(running, HK)`.
3. **Worker holds stale reference**: The worker loop variable `session` (at `agent_session_queue.py:2316`) still points to the object from step 2 with `status = "running"` in memory.
4. **Session gets nudged**: `_enqueue_nudge` re-reads via `get_authoritative_session`, transitions fresh copy to `pending`. On-disk status is now `pending`. Index: `srem(running, HK)`, `sadd(pending, HK)`.
5. **Kill fires**: `valor-session kill --all` queries `filter(status="pending")`, gets session with `status = "pending"` in memory. `finalize_session` backfills, transitions to `killed`. `on_save`: `srem(pending, HK)`, `sadd(killed, HK)`. On-disk status: `killed`.
6. **Stale reference fires**: Back in the worker, the session's stale object from step 3 still has `status = "running"`. If something on this stale object calls `set_link()`, `push_steering_message()`, or any other full `self.save()`, it writes `status = "running"` back to Redis (clobbering `killed`) AND `on_save` runs: `_saved_field_values["status"]` is `"running"` (from the transition_status save at step 2 which set it to `"running"` via line 1337-1340 in base.py). So `on_save` sees `old_value = "running"`, `field_value = "running"` — same value, no `srem` fires. But the `sadd(running, HK)` DOES fire (line 174 of `indexed_field_mixin.py`). Now `HK` is in BOTH the `running` index and has `status = "running"` on disk.
7. **Worker eventually completes**: Via `_complete_agent_session` which re-reads fresh, finds status `running`, finalizes to `completed`. `srem(running, HK)`, `sadd(completed, HK)`.
8. **But**: If step 6 wrote `status = "running"` to disk AFTER step 5 wrote `killed`, the index now has `HK` in `running`. Then step 7 cleans `running` and moves to `completed`. The `pending` index entry from step 4's `sadd` was cleaned in step 5's `srem`. So this specific sequence doesn't create a pending orphan.

### Alternative sequence (more likely for pending orphan specifically)

1-4: Same as above. Session is now `pending` on disk and in the `pending` index.
5. **Worker stale save**: Before the kill fires, the stale worker object (status=`running` in memory) calls `self.save()` (full save). This writes `status = "running"` to Redis. `on_save`: `_saved_field_values["status"] = "running"` (from last save), `field_value = "running"` — no change, no `srem`, just `sadd(running, HK)`. But the session was `pending` on disk, so `pending` index still has `HK`. Now `HK` is in BOTH `pending` and `running` index sets.
6. **Kill fires**: `valor-session kill --all` queries `filter(status="pending")`. Since `HK` is in the `pending` index, it finds it. But `HK` on disk now shows `status = "running"` (from step 5's clobber). The kill loads the object — `current_status = "running"`. CAS re-reads, gets `"running"`. CAS passes. Backfills `_saved_field_values["status"] = "running"`. Sets `status = "killed"`. Saves. `on_save`: `srem(running, HK)`, `sadd(killed, HK)`. The `pending` index entry is NOT removed because the `srem` targeted `running`, not `pending`.
7. **Result**: `HK` is in both `pending` and `killed` index sets. The on-disk status is `killed`. This is the exact orphan described in the issue.

**Root cause**: A full `self.save()` on a stale session object clobbers the on-disk status to an intermediate value. When the kill subsequently fires, its `srem` targets the clobbered value, not the original value that's in the pending index.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #905 (#898) | Added `finalized_by_execute` gate, `update_fields` narrowing on `_append_event_dict` | Only addressed the `completed` stomp path. `_append_event_dict` now does partial save, but other AgentSession methods (`set_link`, `push_steering_message`, `pop_steering_messages`) still do full saves that can clobber status. |
| PR #885 (#783) | Backfilled `_saved_field_values["status"]` in `finalize_session()` and `transition_status()` | Correct for transitions that go through the lifecycle module. Doesn't help when a stale object calls `self.save()` directly — the stale save bypasses the lifecycle module entirely. |

**Root cause pattern:** Each prior fix narrowed one save pathway but left others open. The fundamental issue is that `AgentSession.save()` (full save) always writes ALL fields including `status` from memory to Redis. Any code path that holds a stale reference and calls `save()` — even for an unrelated field update — silently clobbers `status`. The fix must either (a) make ALL save paths go through the lifecycle module, or (b) prevent `save()` from writing `status` unless explicitly intended.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: `AgentSession.save()` will gain a defensive mechanism; existing callers are unaffected
- **Coupling**: Reduces coupling — companion-field saves no longer implicitly clobber status
- **Data ownership**: Reinforces that `session_lifecycle.py` is the exclusive owner of status mutations
- **Reversibility**: Easily reverted — the fix is additive defensive code

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Session model requires Redis |

## Solution

### Key Elements

- **Status-exclusion in full saves**: Override `save()` on `AgentSession` to exclude `status` from the hash write when the save is NOT a lifecycle transition. This prevents stale objects from clobbering the authoritative status.
- **Partial-save conversion**: Convert `set_link()`, `push_steering_message()`, and `pop_steering_messages()` from full saves to partial saves (`update_fields=[...]`), writing only the fields they actually modify. Same pattern as #898's Layer 2 fix for `_append_event_dict`.
- **Defensive srem on finalize**: Add a defensive `srem` in `finalize_session` that removes the session's hash key from ALL non-target status index sets, not just the one identified by `_saved_field_values["status"]`. This acts as a cleanup net for any prior index corruption.

### Flow

Session object calls `set_link("issue", url)` → `save(update_fields=["issue_url"])` (partial) → only `issue_url` written to Redis → status field untouched → no index corruption

Kill path: `finalize_session(session, "killed")` → CAS check → backfill `_saved_field_values` → set status → `save()` → `on_save` srem/sadd → **plus** defensive srem from all other status index sets → orphan cleaned

### Technical Approach

**Layer 1: Convert companion-field saves to partial saves (primary fix)**

Convert the three identified full-save methods to use `save(update_fields=[...])`:

1. `set_link()` at `models/agent_session.py:1338`:
   ```python
   self.save(update_fields=[field_name])
   ```

2. `push_steering_message()` at `models/agent_session.py:1441`:
   ```python
   self.save(update_fields=["queued_steering_messages"])
   ```

3. `pop_steering_messages()` at `models/agent_session.py:1453`:
   ```python
   self.save(update_fields=["queued_steering_messages"])
   ```

**Layer 2: Defensive srem in finalize_session (cleanup net)**

After the main `session.save()` in `finalize_session` (line 361), add a defensive cleanup that removes the session's hash key from ALL status index sets EXCEPT the target:

```python
# In finalize_session, after session.save():
try:
    from popoto.redis_db import POPOTO_REDIS_DB
    from popoto.models.db_key import DB_key
    member_key = session.db_key.redis_key
    status_field = session._meta.fields["status"]
    for other_status in ALL_STATUSES:
        if other_status == status:
            continue
        idx_key = DB_key(
            status_field.get_special_use_field_db_key(session, "status"),
            other_status,
        )
        POPOTO_REDIS_DB.srem(idx_key.redis_key, member_key)
except Exception as e:
    logger.debug(f"[lifecycle] Defensive srem failed (non-fatal): {e}")
```

**Layer 3: Audit all remaining full-save sites in agent_session_queue.py**

Scan all `session.save()` and `agent_session.save()` calls in `agent_session_queue.py` for sites that operate on the worker's stale session reference. Convert any that modify only companion fields to partial saves. The key sites are:

- `agent_session_queue.py:792` — `await chosen.async_save()` after steering message drain — should use `update_fields=["message_text"]`
- `agent_session_queue.py:923` — same for sync fallback path — should use `update_fields=["message_text"]`
- `agent_session_queue.py:3043` — `agent_session.save()` inside `_execute_agent_session` — audit what fields are modified
- `agent_session_queue.py:3291` — `agent_session.save()` — audit context
- `agent_session_queue.py:3500` — `agent_session.save()` — audit context
- `agent_session_queue.py:3702` — `agent_session.save()` — audit context

## Failure Path Test Strategy

### Exception Handling Coverage
- `set_link`, `push_steering_message`, `pop_steering_messages` already have try/except around their save calls — these continue to handle failures gracefully with the partial-save change
- The defensive srem in `finalize_session` uses try/except with debug logging — non-fatal

### Empty/Invalid Input Handling
- Partial saves with `update_fields` containing fields that are None/empty work correctly in Popoto — no new edge cases
- If `update_fields` contains a field name that doesn't exist, Popoto raises `ValueError` — existing behavior, no change needed

### Error State Rendering
- No user-visible output affected — this is a backend index-correctness fix

## Test Impact

- [ ] `tests/integration/test_nudge_stomp_regression.py::TestLayer2PartialSavePreservesFields` — UPDATE: extend to verify `set_link()` partial save also preserves status
- [ ] `tests/unit/test_agent_session_index_corruption.py` — UPDATE: add test cases for the `killed` transition path (currently only tests `completed` path indirectly)
- [ ] `tests/unit/test_session_lifecycle_consolidation.py` — UPDATE: add assertion that defensive srem fires during finalize_session

## Rabbit Holes

- **Overriding `AgentSession.save()` globally**: Tempting to make `save()` always exclude `status`, but this would break `transition_status` and `finalize_session` which rely on `save()` writing status. The partial-save conversion on specific methods is more targeted and safer.
- **Adding CAS to every save path**: Would add complexity and latency. The stale-save problem is better solved by simply not writing status in non-lifecycle saves.
- **Upstream Popoto fix for lazy-loaded `_saved_field_values`**: Making `_create_lazy_model` eagerly decode IndexedFields would fix the symptom, but the real bug is that non-lifecycle code paths are writing status at all. Fix the callers, not the ORM.

## Risks

### Risk 1: Partial saves may miss updated_at refresh
**Impact:** Dashboard "last updated" time stops advancing for sessions whose only save is via set_link or push_steering_message
**Mitigation:** Include `updated_at` in the `update_fields` list for all converted methods. The heartbeat system also independently updates `updated_at`, so this is defense-in-depth.

### Risk 2: Defensive srem has latency cost
**Impact:** Each finalize_session call does N srem calls (one per status value, ~10 total)
**Mitigation:** These are O(1) Redis operations and only fire on terminal transitions (~1-5/minute). Latency impact is negligible (<1ms total).

## Race Conditions

### Race 1: Stale worker save concurrent with kill
**Location:** `agent_session_queue.py:3043,3291,3500,3702` (any full save in worker) vs. `tools/valor_session.py:657,680` (kill)
**Trigger:** Worker holds a stale session object with status="running" in memory. Kill fires on the same session. Worker calls `save()` after or concurrently with the kill.
**Data prerequisite:** Session must have transitioned away from the worker's in-memory status via a different path (nudge, external kill) before the stale save fires.
**State prerequisite:** The stale object's `status` field must differ from the on-disk status.
**Mitigation:** Layer 1 (partial saves) prevents the stale save from writing `status` at all. Layer 2 (defensive srem) cleans up any residual orphan during the next finalize.

## No-Gos (Out of Scope)

- Cleaning up existing orphaned index entries manually — `rebuild_indexes()` already handles this on each worker cycle
- Modifying Popoto's `_create_lazy_model` to eagerly decode IndexedFields — upstream change, different issue
- Weakening PR #885's CAS or #898's Layer 1/2 fixes — they remain correct for the `completed` path
- Removing or modifying the reflections regression check at `scripts/reflections.py:964`

## Update System

No update system changes required — this is a bridge/worker-internal fix with no new dependencies or config files.

## Agent Integration

No agent integration required — this is a session-lifecycle-internal change. No new tools, MCP servers, or bridge imports needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` with the rule: "companion-field saves on AgentSession must use `save(update_fields=[...])` to avoid clobbering status"
- [ ] Add "Known follow-up" note to `docs/plans/nudge-stomp-append-event-bypass.md` pointing to this issue

### Inline Documentation
- [ ] Add docstring warnings on `set_link`, `push_steering_message`, `pop_steering_messages` noting the partial-save requirement

## Success Criteria

- [ ] `set_link()`, `push_steering_message()`, `pop_steering_messages()` use partial saves
- [ ] All identified full-save sites in `agent_session_queue.py` that operate on stale session references are converted to partial saves
- [ ] Defensive srem in `finalize_session` removes hash key from all non-target status index sets
- [ ] New regression test: create a pending session, simulate a stale-object full save with wrong status, then kill — assert no orphan in pending index
- [ ] Parametrized test across all five terminal statuses (`completed`, `failed`, `killed`, `abandoned`, `cancelled`) asserting zero orphan index entries post-transition
- [ ] Existing tests in `test_nudge_stomp_regression.py` continue to pass
- [ ] `"Stale index entry"` warning count on a 24-hour `bridge.log` drops to zero post-deploy
- [ ] Reflections regression check at `scripts/reflections.py:964` remains installed and untouched
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (lifecycle-fix)**
  - Name: lifecycle-builder
  - Role: Convert full saves to partial saves; add defensive srem to finalize_session
  - Agent Type: builder
  - Resume: true

- **Validator (index-integrity)**
  - Name: index-validator
  - Role: Verify no orphan index entries after all terminal transitions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Convert companion-field saves to partial saves
- **Task ID**: build-partial-saves
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_index_corruption.py (update), tests/integration/test_nudge_stomp_regression.py (update)
- **Informed By**: spike-1 (confirmed: set_link, push_steering_message, pop_steering_messages do full saves)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: true
- Convert `set_link()` at `models/agent_session.py:1338` to `self.save(update_fields=[field_name, "updated_at"])`
- Convert `push_steering_message()` at `models/agent_session.py:1441` to `self.save(update_fields=["queued_steering_messages", "updated_at"])`
- Convert `pop_steering_messages()` at `models/agent_session.py:1453` to `self.save(update_fields=["queued_steering_messages", "updated_at"])`
- Audit all `session.save()` / `agent_session.save()` calls in `agent_session_queue.py` that operate on the worker's session reference; convert companion-field saves to partial saves with `update_fields`
- Ensure `updated_at` is included in all partial-save `update_fields` lists

### 2. Add defensive srem to finalize_session
- **Task ID**: build-defensive-srem
- **Depends On**: none
- **Validates**: tests/unit/test_session_lifecycle_consolidation.py (update)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: true
- After `session.save()` in `finalize_session` at `models/session_lifecycle.py:361`, add defensive srem that removes the session's hash key from all status index sets except the target terminal status
- Wrap in try/except with debug logging (non-fatal)

### 3. Write regression tests
- **Task ID**: build-tests
- **Depends On**: build-partial-saves, build-defensive-srem
- **Validates**: all new tests pass
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- Create test: pending session, stale-object full save with status clobber, then kill — assert no orphan in pending index
- Parametrize across all five terminal statuses asserting zero orphan index entries
- Extend `test_nudge_stomp_regression.py` to cover `set_link()` partial save preserving status
- Verify existing `TestFinalizedByExecuteGatesHappyPath` and `TestLayer2PartialSavePreservesFields` still pass

### 4. Validate index integrity
- **Task ID**: validate-index
- **Depends On**: build-tests
- **Assigned To**: index-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify no stale-index warnings in test output
- Confirm reflections check at `scripts/reflections.py:964` is untouched

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-index
- **Assigned To**: lifecycle-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-lifecycle.md` with partial-save rule
- Add "Known follow-up" note to `docs/plans/nudge-stomp-append-event-bypass.md`
- Add docstring warnings to converted methods

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: index-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Partial save in set_link | `grep -n "update_fields" models/agent_session.py \| grep -c "set_link\|issue_url\|plan_url\|pr_url"` | output > 0 |
| Partial save in push_steering | `grep -n "update_fields.*queued_steering" models/agent_session.py` | output > 0 |
| Defensive srem in finalize | `grep -n "srem" models/session_lifecycle.py` | output > 0 |
| Reflections check untouched | `git diff main -- scripts/reflections.py \| grep -c "Stale index entry"` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-14. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic, Operator | Layer 3 audit lists 6 full-save sites in agent_session_queue.py but plan says "audit" without committing to convert all; lines 792/923 use `async_save()` not `save()` — plan should explicitly confirm `async_save` supports `update_fields` | Task 1 (build-partial-saves) | `async_save()` at `popoto/models/base.py:2290` accepts `update_fields` identically to `save()`. Plan should commit to converting lines 792, 923, 3043, 3291, 3500, 3702 to partial saves — not just "audit". Builder must convert `await chosen.async_save()` to `await chosen.async_save(update_fields=["message_text", "updated_at"])` at lines 792 and 923. |
| CONCERN | Adversary | Defensive srem (Layer 2) constructs index keys using `DB_key(status_field.get_special_use_field_db_key(...), other_status)` and `session.db_key.redis_key` as the member — plan should verify these match the actual Popoto index key format to avoid silent no-ops | Task 2 (build-defensive-srem) | Verified: `indexed_field_mixin.py:134` uses `model_instance.db_key.redis_key` as member_key and `DB_key(cls.get_special_use_field_db_key(model_instance, field_name), field_value)` as the set key. The plan's pseudocode at Layer 2 uses `session._meta.fields["status"]` which is the field class, not the field name string — call must be `type(status_field).get_special_use_field_db_key(session, "status")` since it's a classmethod on the field class. |
| CONCERN | Operator | Heartbeat save at line 3702 fires every ~60s for every running session; converting to partial save with `update_fields=["updated_at"]` is correct but plan doesn't mention this is the highest-frequency stale-save vector — it should be the first conversion, not an "audit" item | Task 1 (build-partial-saves) | Line 3702 runs inside `_heartbeat_loop` which ticks every 60 seconds. On a stale worker reference (status diverged), each tick writes stale status to Redis. This is the primary re-pollution vector that causes rebuild_indexes to fail to stick. Builder should prioritize this conversion. |
| NIT | Simplifier | Risk 1 mitigation says "Include `updated_at` in all partial-save `update_fields` lists" — but Popoto's `save()` has `skip_auto_now` defaulting to False, which already updates `auto_now` fields. Including `updated_at` explicitly may double-write. | Task 1 | Check if AgentSession.updated_at uses `auto_now=True`. If so, partial saves already refresh it when it's in `update_fields`. Not harmful to include explicitly, but the Risk 1 mitigation rationale is weaker than stated. |
| NIT | Archaeologist | Plan references `indexed_field_mixin.py:139` guard and `indexed_field_mixin.py:174` sadd — these are Popoto internals that could change on upgrade. Plan already notes this coupling at line 355-356 but the defensive srem (Layer 2) adds a second coupling point. Consider a comment in the defensive srem code noting both coupling sites. | Task 2 | Add inline comment listing both Popoto coupling points: (1) `_saved_field_values` backfill in finalize_session/transition_status, (2) defensive srem index key construction. Both must be re-verified on Popoto upgrade. |

---

## Open Questions

No open questions — the root cause mechanism is well-understood from the code analysis and prior art, and the fix follows the same proven pattern as #898's Layer 2.
