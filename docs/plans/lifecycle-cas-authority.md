---
status: Completed
type: feature
appetite: Medium
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/875
last_comment_id:
---

# Promote session_lifecycle.py to Status Authority with CAS

## Problem

`models/session_lifecycle.py` is a "status setter" -- it mutates the in-memory `AgentSession` object and calls `session.save()` without any version check, compare-and-set, or authoritative re-read. Concurrent writers race each other blindly, and whoever saves last wins. This structural gap is the root cause of a family of race-condition bugs:

- **#867**: nudge re-enqueue stomped by worker finally-block `finalize_session()`
- **#870**: `_execute_agent_session` re-fetch uses `status="running"` filter, silently degrading when another writer changed status
- **#872**: worker finally-block runs redundant finalize on happy path, creating #867's race window
- **#873**: idempotent `transition_status()` silently drops companion field edits -- 4 of 5 callers affected

Each bug can be patched individually with narrow guards, but the guards don't compose, accumulate O(N*M) complexity (N call sites x M statuses), and rely on convention that callers consistently get wrong (15+ blind `[0]` re-read sites).

**Current behavior:**
- `finalize_session()` and `transition_status()` blindly `session.save()` whatever in-memory object the caller hands them
- No version check, no CAS, no authoritative re-read
- 25 production writers of `AgentSession.status` across the codebase
- 15+ call sites use the blind `list(AgentSession.query.filter(session_id=sid))[0]` pattern with no tie-break

**Desired outcome:**
- The lifecycle module owns the full mutation: callers hand in a `session_id` (not an instance), the module re-reads, applies changes, and CAS-saves atomically
- CAS refuses to write if the on-disk status changed since the in-memory object was loaded
- A `StatusConflictError` exception surfaces conflicts instead of silent stomps
- The 15+ blind re-read sites get a clear migration target

## Freshness Check

**Baseline commit:** `4c03a851`
**Issue filed at:** 2026-04-10
**Disposition:** Minor drift

**File:line references re-verified:**
- `models/session_lifecycle.py:155` (finalize_session save) -- drifted to line 170 due to RECOVERY_OWNERSHIP addition (#877). Code unchanged.
- `models/session_lifecycle.py:244` (transition_status save) -- drifted to line 259. Code unchanged.
- `models/session_lifecycle.py:149-150` (_saved_field_values backfill comment) -- drifted to lines 164-165. Code unchanged.
- `agent/agent_session_queue.py:1096-1123` (tie-break re-read in _complete_agent_session) -- confirmed at lines 1096-1123. Unchanged.
- `agent/agent_session_queue.py:2684` (racy status="running" filter) -- confirmed at line 2688. Unchanged.
- `agent/agent_session_queue.py:2215-2234` (nudge guard) -- confirmed at lines 2219-2238. Unchanged.

**Cited sibling issues/PRs re-checked:**
- #867 -- still OPEN
- #870 -- still OPEN
- #872 -- still OPEN
- #873 -- still OPEN
- #825 -- CLOSED 2026-04-08 (re-read fix in _complete_agent_session, established tie-break pattern)
- #743 -- CLOSED 2026-04-06 (extract-nudge-to-pm shipped as docs_complete)

**Commits on main since issue was filed (touching referenced files):**
- `4c03a851` Add RECOVERY_OWNERSHIP registry (#877) -- added constant to session_lifecycle.py, no structural change to finalize/transition functions
- `27bb2c51` Fix CLI status summary miscounts (#869) -- unrelated CLI fix
- `d24dd07f` Add CLI harness abstraction (#868) -- unrelated

**Active plans in `docs/plans/` overlapping this area:**
- `extract-nudge-to-pm.md` (#743) -- status `docs_complete`, issue CLOSED. No longer an active coordination concern.

**Notes:** Line numbers in session_lifecycle.py shifted by ~15 lines due to the RECOVERY_OWNERSHIP constant added by #877. All code references in this plan use the current (post-#877) line numbers.

## Prior Art

- **#825** (closed 2026-04-08): Added tie-break re-read in `_complete_agent_session` -- prefer `status="running"` records, fall back to most-recent by `created_at`. Established the tie-break pattern this plan centralizes. Did not propagate to `_execute_agent_session` (that's #870).
- **#783**: Earlier lazy-load index leak fix -- fixed empty `_saved_field_values` dict. Different root cause but same Popoto-internal coupling area.
- **#700** (closed 2026-04-05): Completed sessions reverting to pending, causing infinite execution loop. Symptom of the same structural gap -- no version fence on status writes.
- **#727** (closed 2026-04-06): Startup recovery resetting recently-started sessions. Another manifestation of blind status overwrites.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #825 | Added tie-break re-read in `_complete_agent_session` | Fixed one call site; 15+ other blind `[0]` sites remain. Did not add CAS -- the re-read itself can race. |
| #700 | Added terminal-status guard in transition_status | Prevented one symptom (completed->pending) but didn't prevent concurrent writers from stomping each other within valid transitions. |
| user_prompt_submit.py:79-82 | Added compensating `save()` after idempotent `transition_status` | Fixed one call site with scar tissue; four other sites still silently drop companion fields (#873). |

**Root cause pattern:** Every fix addresses one call site or one transition path. The structural gap -- no version fence in the lifecycle module itself -- means each new status, writer, or code path produces a new race surface.

## Sibling Issue Disposition

This section specifies, for each sibling issue, whether this refactor subsumes, fixes, or leaves cleanup work.

### #870 -- `_execute_agent_session` racy status="running" filter

**Disposition: SUBSUMES (close after this PR merges)**

The racy `status="running"` filter at `agent/agent_session_queue.py:2688` will be replaced by a call to the new `get_authoritative_session()` helper that re-reads by `session_id` with tie-break, matching the pattern already in `_complete_agent_session`. The `None` handling and WARNING log will be added per #870's acceptance criteria. This PR fully implements #870's solution sketch.

### #872 -- worker finally-block redundant finalize on happy path

**Disposition: FIXES root cause, leaves narrow cleanup as optional follow-up**

CAS in `finalize_session()` eliminates the stomp risk that makes the dual-finalize path dangerous. After CAS lands, the finally-block's redundant `_complete_agent_session` call becomes a harmless no-op (CAS detects the already-finalized record and skips). The `finalized_by_execute` gate described in #872's solution sketch is defense-in-depth that reduces unnecessary Redis reads but is no longer a correctness requirement. That gate can be added as a follow-up optimization or left as-is.

### #873 -- idempotent transition_status() drops companion field edits

**Disposition: SUBSUMES (close after this PR merges)**

The new `update_session()` API is exactly the `update_session_and_transition` helper proposed in #873's Option B. Callers pass `fields={"priority": "high", "started_at": None}` and the module handles re-read + field application + save atomically, regardless of whether the status transition is idempotent. All four non-compensating call sites identified in #873 will be migrated in this PR.

### #867 -- nudge re-enqueue stomped by worker finally-block

**Disposition: FIXES root cause structurally**

CAS in `finalize_session()` refuses to write if on-disk status changed since the in-memory read. The stomp described in #867 becomes impossible: the finally-block's stale `session` object (still showing `status="running"`) triggers a `StatusConflictError` when it tries to finalize a record that the nudge path already moved to `pending`. The narrow nudge guard at lines 2219-2238 becomes defense-in-depth rather than the sole protection. #867 can be closed, though the guard should remain as a belt-and-suspenders measure.

## Spike Results

### spike-1: Redis WATCH/MULTI/EXEC feasibility with Popoto

- **Assumption**: "WATCH/MULTI/EXEC can be used alongside Popoto's save() without corrupting indexes"
- **Method**: code-read
- **Finding**: Popoto's `save()` calls `on_save()` in `IndexedFieldMixin` which does `srem` (old index) + `sadd` (new index). The `_saved_field_values["status"]` backfill at `session_lifecycle.py:166-167` already proves the module is willing to manage Popoto internals. However, wrapping the entire `save()` inside a MULTI/EXEC block is not feasible because Popoto's `save()` issues multiple Redis commands internally (HSET + SREM + SADD), and WATCH/MULTI/EXEC requires all commands to be queued via the pipeline -- we cannot inject Popoto's internal commands into our pipeline.
- **Confidence**: high
- **Impact on plan**: Use Python-level CAS (re-read + compare + save) instead of Redis WATCH/MULTI/EXEC. The race window is small (between re-read and save), and the status field is the CAS key. Full Redis transactions would require bypassing Popoto's save entirely and reimplementing index management -- too much coupling risk for marginal benefit.

### spike-2: Version field vs status-only CAS

- **Assumption**: "A monotonic version field would provide stronger CAS than status-only comparison"
- **Method**: code-read
- **Finding**: Status-only CAS is sufficient for the race family being fixed. The degenerate case (two writers both setting `running -> pending` on an already-pending record) is handled by #873's companion-field fix -- the `update_session()` API always saves fields regardless of status idempotency. A version field would add an `IntField` to AgentSession requiring a migration, and every `save()` call across the codebase would need to increment it -- too invasive for the marginal correctness gain.
- **Confidence**: high
- **Impact on plan**: Use status-only CAS. No version field. The `update_session()` API's explicit field dict handles the companion-field case.

## Data Flow

### Current flow (blind save)

1. **Caller** holds an in-memory `session` object (possibly stale)
2. **Caller** mutates `session.priority = "high"` etc.
3. **Caller** calls `transition_status(session, "pending", ...)`
4. **transition_status** backfills `_saved_field_values["status"]`, sets `session.status = new_status`, calls `session.save()`
5. **Popoto save()** writes HSET (all fields), SREM (old index), SADD (new index) to Redis
6. If another writer saved between steps 1 and 5, those changes are silently overwritten

### Target flow (CAS via update_session)

1. **Caller** calls `update_session(session_id="abc", new_status="pending", fields={"priority": "high"}, expected_status="running", reason="recovery")`
2. **update_session** re-reads from Redis via `get_authoritative_session(session_id)` with tie-break
3. **update_session** compares `session.status` against `expected_status` -- if mismatch, raises `StatusConflictError`
4. **update_session** applies `fields` dict to session object
5. **update_session** delegates to `transition_status()` or `finalize_session()` (which now also re-verify status before save)
6. **Popoto save()** writes to Redis

### Target flow (existing callers using transition_status/finalize_session directly)

1. **Caller** holds an in-memory `session` object
2. **Caller** calls `finalize_session(session, "completed", ...)`
3. **finalize_session** re-reads session from Redis by `session_id`
4. **finalize_session** compares on-disk status against the in-memory object's status at call time
5. If on-disk status differs from expected, raises `StatusConflictError`
6. If match, proceeds with save as before

## Architectural Impact

- **New dependencies**: None -- uses existing `POPOTO_REDIS_DB` and Popoto ORM
- **Interface changes**: New `update_session()` public API. `transition_status()` and `finalize_session()` gain CAS internally but keep their existing signatures (backward compatible). New `StatusConflictError` exception class. New `get_authoritative_session()` helper.
- **Coupling**: Decreases -- callers no longer need to implement their own re-read + tie-break logic. The lifecycle module owns the full mutation path.
- **Data ownership**: Status authority moves fully into the lifecycle module. Callers that currently do `session.status = x; session.save()` bypassing the lifecycle module are not affected by this PR but should be migrated in a follow-up.
- **Reversibility**: High -- CAS is additive. Removing it just means removing the re-read + compare step. No data model changes.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope confirmation on migration call sites)
- Review rounds: 1

## Prerequisites

No prerequisites -- all changes are internal to existing modules with no new external dependencies.

## Solution

### Key Elements

- **`StatusConflictError`**: New exception class raised when CAS detects a conflict. Includes the session_id, expected status, and actual on-disk status for diagnostics.
- **`get_authoritative_session(session_id)`**: Centralized re-read helper with tie-break logic (prefer `running`, fall back to most-recent by `created_at`). Replaces the 15+ blind `[0]` pattern. Lives in `models/session_lifecycle.py`.
- **CAS in `finalize_session()` and `transition_status()`**: Both functions re-read the session from Redis before saving and compare the on-disk status against the in-memory object's status. On mismatch, raise `StatusConflictError`.
- **`update_session()`**: New public API that takes `session_id`, optional `new_status`, optional `fields` dict, optional `expected_status`, and `reason`. Handles re-read, field application, and CAS-save in one call.

### Flow

**Caller** -> `update_session(session_id, ...)` -> `get_authoritative_session(session_id)` -> re-read from Redis with tie-break -> compare status (CAS check) -> apply fields -> `transition_status()` or `finalize_session()` -> CAS-verified `session.save()` -> done

### Technical Approach

- **Python-level CAS, not Redis WATCH/MULTI/EXEC.** Spike-1 confirmed that wrapping Popoto's `save()` in a Redis transaction is not feasible without reimplementing index management. Instead, re-read the record from Redis, compare the on-disk status against the expected status, then save. The race window between re-read and save is small (microseconds of Python execution) and acceptable for this use case.
- **Status-only CAS, no version field.** Spike-2 confirmed that a monotonic version field is too invasive for the marginal gain. Status is the CAS key. The `update_session()` API's explicit field dict handles companion-field persistence regardless of status idempotency.
- **Conflict policy: raise, don't retry.** `StatusConflictError` is raised on conflict. Callers decide whether to retry, bail, or log. No automatic retry -- automatic retry without understanding the conflict can amplify races. The exception includes enough context (expected vs actual status, session_id) for the caller to make an informed decision.
- **`transition_status()` idempotent path now saves companion fields.** When `current_status == new_status`, the function still calls `session.save()` to persist any companion field edits. This directly fixes #873.
- **Backward compatibility.** `finalize_session()` and `transition_status()` keep their current signatures. The CAS check is internal. Existing callers that pass a session instance continue to work -- they just get conflict detection they didn't have before. Callers that want the full atomic API use `update_session()`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `StatusConflictError` is raised (not swallowed) when CAS detects a conflict in `finalize_session()`
- [ ] `StatusConflictError` is raised (not swallowed) when CAS detects a conflict in `transition_status()`
- [ ] `get_authoritative_session()` returns `None` with a WARNING log when no session found (not silent `None`)
- [ ] `update_session()` raises `ValueError` for invalid `session_id` (None, empty string)

### Empty/Invalid Input Handling
- [ ] `update_session()` with no `new_status` and no `fields` -- raises `ValueError` (nothing to do)
- [ ] `update_session()` with `expected_status` that doesn't match -- raises `StatusConflictError` before any mutation
- [ ] `get_authoritative_session()` with a `session_id` that has zero records -- returns `None`, logs WARNING

### Error State Rendering
- [ ] `StatusConflictError` message includes session_id, expected status, actual status, and reason -- sufficient for log diagnosis without needing to inspect Redis

## Test Impact

- [ ] `tests/unit/test_session_lifecycle_consolidation.py` -- UPDATE: add CAS conflict test cases; update `finalize_session` and `transition_status` tests to account for re-read behavior
- [ ] `tests/unit/test_agent_session_index_corruption.py` -- UPDATE: verify CAS does not interfere with index backfill logic
- [ ] `tests/unit/test_complete_agent_session_redis_reread.py` -- UPDATE: tie-break logic moves to `get_authoritative_session()`, update imports
- [ ] `tests/integration/test_agent_session_queue_race.py` -- UPDATE: race scenarios should now produce `StatusConflictError` instead of silent stomp
- [ ] `tests/integration/test_lifecycle_transition.py` -- UPDATE: add CAS verification to existing transition tests
- [ ] `tests/unit/test_stop_hook.py` -- review: uses `finalize_session` -- verify no breakage from CAS re-read
- [ ] `tests/unit/test_agent_session_hierarchy.py` -- review: uses `finalize_session` via parent finalization -- verify no breakage

## Rabbit Holes

- **Full Redis WATCH/MULTI/EXEC transactions.** Spike-1 ruled this out -- Popoto's `save()` issues multiple internal Redis commands that cannot be injected into a pipeline. Reimplementing index management is not worth the coupling risk.
- **Monotonic version field on AgentSession.** Spike-2 ruled this out -- requires a migration, every `save()` must increment it, and status-only CAS is sufficient for the races being fixed.
- **Migrating all 15+ blind `[0]` re-read sites in this PR.** Only migrate the sites directly implicated in #867, #870, #872, #873. File a follow-up sweep issue for the remaining sites.
- **Refactoring `_execute_agent_session()` or `_worker_loop()` beyond the specific call sites.** These functions have many concerns; only change the lifecycle-touching parts.
- **Making `transition_status()` / `finalize_session()` private.** The long-term endgame is for `update_session()` to become the only public API. But deprecating the existing functions is a follow-up concern after all callers migrate.

## Risks

### Risk 1: CAS false positives in high-concurrency scenarios
**Impact:** Legitimate transitions get rejected because a benign concurrent writer (e.g., `updated_at` refresh) changed the status between re-read and save.
**Mitigation:** CAS only checks the `status` field, not all fields. Benign writers that don't change status (e.g., hooks updating `task_list_id`) will not trigger conflicts. Only status-changing writers compete.

### Risk 2: Popoto internal coupling deepened
**Impact:** The `_saved_field_values["status"]` backfill is already a Popoto internal coupling. CAS adds a re-read that depends on `AgentSession.query.filter()` returning fresh data.
**Mitigation:** `query.filter()` is a public Popoto API, not an internal. The `_saved_field_values` backfill is already present and documented with an upgrade warning. No new Popoto-internal coupling is added.

### Risk 3: Performance overhead from re-reads
**Impact:** Every `finalize_session()` and `transition_status()` call now does an extra Redis read.
**Mitigation:** Session transitions are infrequent (once per turn, not per tool call). The overhead is one Redis `SMEMBERS` + `HGETALL` per transition -- sub-millisecond. Benchmark threshold: CAS overhead must be under 5ms per transition.

## Race Conditions

### Race 1: CAS re-read vs concurrent save
**Location:** `models/session_lifecycle.py` -- between `get_authoritative_session()` re-read and `session.save()`
**Trigger:** Two writers call `transition_status()` or `finalize_session()` on the same session within microseconds
**Data prerequisite:** Session must exist in Redis with a status that both writers expect
**State prerequisite:** Both writers hold in-memory objects with the same expected status
**Mitigation:** Python-level CAS has a small race window (~microseconds between re-read and save). This is acceptable: the window is orders of magnitude smaller than the current race (seconds between pop and finally-block). For the remaining window, the first writer wins and the second gets `StatusConflictError`. True elimination would require Redis transactions, which spike-1 ruled out.

### Race 2: update_session re-read vs deletion
**Location:** `models/session_lifecycle.py` -- `get_authoritative_session()` returns a session, then it's deleted before save
**Trigger:** A concurrent process deletes/recreates the session (nudge fallback path)
**Data prerequisite:** Session existed at re-read time
**State prerequisite:** Concurrent deletion in progress
**Mitigation:** Popoto's `save()` on a deleted record creates a new record with the same key. This is the existing behavior and is not made worse by CAS. The nudge fallback deletion path is rare and already handled by the nudge guard.

## No-Gos (Out of Scope)

- Migrating all 15+ blind `[0]` re-read sites -- only the 4-5 sites implicated in the sibling issues migrate in this PR. A follow-up sweep issue will be filed.
- Adding a monotonic `version` field to `AgentSession` -- ruled out by spike-2.
- Redis WATCH/MULTI/EXEC transactions -- ruled out by spike-1.
- Refactoring `_worker_loop()` or extracting `SessionRunner` -- separate architectural concern.
- Making `transition_status()` / `finalize_session()` private -- follow-up after migration converges.
- Removing the nudge guard at `agent_session_queue.py:2219-2238` -- keep as defense-in-depth.
- Touching `bridge/session_transcript.py`'s blind `sessions[0]` -- defer to the follow-up sweep.

## Rollback Plan

CAS is purely additive -- no data model changes, no new fields, no migrations.

**To roll back:**
1. Revert the CAS re-read logic in `finalize_session()` and `transition_status()` -- restore blind `session.save()`.
2. Revert the idempotent-path save in `transition_status()` -- restore early return.
3. Remove `update_session()`, `get_authoritative_session()`, `StatusConflictError`.
4. Restore the original call sites that were migrated to `update_session()`.

**Data safety:** No data is at risk. CAS only adds a read-before-write check. Rolling back removes the check and returns to the current behavior. No Redis data needs cleanup.

**When to roll back:** If CAS overhead exceeds 5ms per transition in production (measured via lifecycle transition logs), or if `StatusConflictError` fires on legitimate transitions that should succeed (false positive rate > 1%).

## Update System

No update system changes required -- this is purely internal to the lifecycle module. No new dependencies, no new config files, no new CLI tools.

## Agent Integration

No agent integration required -- this is an internal refactor of the session lifecycle module. No new MCP servers, no changes to `.mcp.json`, no bridge changes. The agent interacts with sessions via existing tools (`valor-session`, hooks) which will transparently benefit from CAS without any interface changes.

## Documentation

- [ ] Update `models/session_lifecycle.py` module docstring to reflect "status authority" role and CAS behavior
- [ ] Add inline docstrings for `update_session()`, `get_authoritative_session()`, `StatusConflictError`
- [ ] Update `docs/features/session-recovery-mechanisms.md` to reference CAS as the concurrency safety mechanism
- [ ] Add entry to `docs/features/README.md` index table for lifecycle CAS authority

## Success Criteria

- [ ] `models/session_lifecycle.py` exposes `update_session()` that takes `session_id`, optional `new_status`, optional `fields` dict, optional `expected_status`, and `reason`
- [ ] `finalize_session()` and `transition_status()` have CAS (re-read + status compare) that raises `StatusConflictError` on conflict
- [ ] `StatusConflictError` exception class exists with session_id, expected_status, actual_status attributes
- [ ] `get_authoritative_session(session_id)` centralizes the tie-break re-read pattern
- [ ] `transition_status()` idempotent path saves companion fields (fixes #873)
- [ ] `_execute_agent_session` re-fetch uses `get_authoritative_session()` instead of `status="running"` filter (fixes #870)
- [ ] The four #873 call sites (`_recover_interrupted_agent_sessions_startup`, `_agent_session_health_check`, `_cli_recover_single_agent_session`, `_enqueue_nudge`) use `update_session()` or are verified to work with the idempotent-path save fix
- [ ] Unit test: two concurrent writers targeting the same session -- exactly one succeeds, the other raises `StatusConflictError`
- [ ] Unit test: companion field edits before idempotent `transition_status()` are persisted
- [ ] Integration test: #867 scenario (nudge + finally finalize) -- nudge status preserved
- [ ] Existing lifecycle tests pass unchanged (except for expected updates noted in Test Impact)
- [ ] CAS overhead per transition under 5ms (measured in test)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (lifecycle-cas)**
  - Name: lifecycle-builder
  - Role: Implement CAS in session_lifecycle.py, add update_session/get_authoritative_session/StatusConflictError, fix idempotent companion-field drop
  - Agent Type: builder
  - Resume: true

- **Builder (call-site-migration)**
  - Name: callsite-builder
  - Role: Migrate the 4-5 call sites implicated in #867/#870/#872/#873 to use new APIs
  - Agent Type: builder
  - Resume: true

- **Validator (lifecycle)**
  - Name: lifecycle-validator
  - Role: Verify CAS behavior, race detection, backward compatibility, performance
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update module docstrings, feature docs, README index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add StatusConflictError and get_authoritative_session
- **Task ID**: build-cas-primitives
- **Depends On**: none
- **Validates**: tests/unit/test_session_lifecycle_consolidation.py (update)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `StatusConflictError` exception class to `models/session_lifecycle.py` with `session_id`, `expected_status`, `actual_status`, `reason` attributes
- Add `get_authoritative_session(session_id, project_key=None)` that queries by `session_id`, applies tie-break (prefer `running`, fall back to most-recent by `created_at`), returns single session or `None` with WARNING log
- Add unit tests for `get_authoritative_session` (single record, multiple records with tie-break, no records)

### 2. Add CAS to finalize_session and transition_status
- **Task ID**: build-cas-lifecycle
- **Depends On**: build-cas-primitives
- **Validates**: tests/unit/test_session_lifecycle_consolidation.py (update), tests/unit/test_agent_session_index_corruption.py (update)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- In `finalize_session()`: after validation, re-read session from Redis via `get_authoritative_session(session.session_id)`. Compare on-disk status against caller's in-memory status. If mismatch and on-disk status is terminal, raise `StatusConflictError`. If mismatch and on-disk status is non-terminal, raise `StatusConflictError`. Use fresh session for the save.
- In `transition_status()`: same CAS pattern. On idempotent path (`current_status == new_status`), still call `session.save()` to persist companion fields (fixes #873).
- Add unit test: concurrent writers -- mock Redis to return different status on re-read, verify `StatusConflictError` raised
- Add unit test: companion fields persisted on idempotent transition

### 3. Add update_session API
- **Task ID**: build-update-session
- **Depends On**: build-cas-lifecycle
- **Validates**: tests/unit/test_session_lifecycle_consolidation.py (update)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `update_session(session_id, new_status=None, fields=None, expected_status=None, reason="")` to `models/session_lifecycle.py`
- Implementation: call `get_authoritative_session(session_id)`, validate `expected_status` if provided, apply `fields` dict, delegate to `transition_status()` or `finalize_session()` based on whether `new_status` is terminal
- If `new_status` is None and `fields` is provided, just apply fields and `session.save()`
- Add unit tests for update_session (happy path, CAS conflict, field-only update, terminal transition)

### 4. Migrate call sites for #870 and #873
- **Task ID**: build-migrate-callsites
- **Depends On**: build-update-session
- **Validates**: tests/unit/test_complete_agent_session_redis_reread.py (update), tests/integration/test_agent_session_queue_race.py (update)
- **Assigned To**: callsite-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `_execute_agent_session` racy `status="running"` filter (line 2688) with `get_authoritative_session(session.session_id)` -- fixes #870
- Migrate `_recover_interrupted_agent_sessions_startup` to use `update_session()` -- fixes #873 call site 1
- Migrate `_agent_session_health_check` to use `update_session()` -- fixes #873 call site 2
- Migrate `_cli_recover_single_agent_session` to use `update_session()` -- fixes #873 call site 3
- Migrate `_enqueue_nudge` to use `update_session()` -- fixes #873 call site 4
- Update existing tests that assert on the old call patterns

### 5. Validate CAS behavior end-to-end
- **Task ID**: validate-cas
- **Depends On**: build-migrate-callsites
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all lifecycle tests: `pytest tests/unit/test_session_lifecycle_consolidation.py tests/unit/test_agent_session_index_corruption.py tests/unit/test_complete_agent_session_redis_reread.py tests/integration/test_lifecycle_transition.py -v`
- Verify #867 scenario: create session, simulate nudge (transition to pending), then attempt finalize -- verify `StatusConflictError` raised
- Verify #873 scenario: set companion fields, call idempotent transition, re-read from Redis, verify fields persisted
- Verify backward compatibility: existing callers that pass session instances still work
- Benchmark: measure CAS overhead per transition, verify under 5ms

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-cas
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `models/session_lifecycle.py` module docstring
- Add docstrings for new public APIs
- Update `docs/features/session-recovery-mechanisms.md`
- Add entry to `docs/features/README.md`

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Run lint: `python -m ruff check .`
- Run format: `python -m ruff format --check .`
- Verify `StatusConflictError` is importable: `python -c "from models.session_lifecycle import StatusConflictError, update_session, get_authoritative_session"`
- Verify no remaining `status=\"running\"` filter in `_execute_agent_session` re-read

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| CAS APIs exist | `python -c "from models.session_lifecycle import StatusConflictError, update_session, get_authoritative_session"` | exit code 0 |
| No racy status filter | `grep -n 'status="running"' agent/agent_session_queue.py \| grep -v '#\|complete_agent_session\|comment'` | exit code 1 |
| Lifecycle tests pass | `pytest tests/unit/test_session_lifecycle_consolidation.py -v` | exit code 0 |
| CAS conflict test | `pytest tests/unit/test_session_lifecycle_consolidation.py -k "conflict" -v` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-10 -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Adversary | `_enqueue_nudge` blind `sessions[0]` at line 2536 not migrated to `get_authoritative_session()` — same blind re-read pattern the plan centralizes | Task 4 (build-migrate-callsites) should add this site | `_enqueue_nudge` line 2536: replace `session = sessions[0]` with `session = get_authoritative_session(session.session_id)` and handle `None` return. The function already re-reads but uses the blind `[0]` pattern without tie-break. |
| CONCERN | Adversary, Skeptic | `transition_status()` idempotent path currently returns early (line 240) — plan says "still call `session.save()`" but doesn't specify where the companion fields get applied when caller sets them *before* calling `transition_status()` and on-disk status already matches | Task 2 (build-cas-lifecycle) | The CAS re-read replaces the caller's in-memory object with a fresh one. Companion fields set by the caller on the *old* object (e.g., `entry.priority = "high"` at line 1302) will be lost unless the new `transition_status()` explicitly copies them from the caller's object or the caller switches to `update_session(fields={...})`. Plan must clarify: does the idempotent save use the *re-read* object or the *caller's* object? If re-read, companion fields are silently dropped — exactly the #873 bug. |
| CONCERN | Operator | Plan specifies "CAS overhead must be under 5ms" but no logging or metric emission is described for measuring this in production — only "measured in test" | Task 5 (validate-cas) | Add a `time.monotonic()` pair around the CAS re-read+compare in `finalize_session()`/`transition_status()` and emit `logger.debug("[lifecycle-cas] CAS overhead: %.1fms", elapsed_ms)`. The 5ms threshold can then be monitored from existing log aggregation. |
| NIT | Simplifier | Task 1 marks `Parallel: true` but has no sibling task to run alongside — all downstream tasks depend on it sequentially | Task 1 | Cosmetic only; remove `Parallel: true` from Task 1 to avoid confusion in orchestration tooling |
| NIT | Archaeologist | Plan references line 2688 for the racy `status="running"` filter but actual code is at line 2760 (post-drift) — the freshness check section acknowledges line drift in session_lifecycle.py but not in agent_session_queue.py | Freshness Check section | Update the `agent_session_queue.py:2684` and `2688` references to `2760` in the Freshness Check section to prevent builder confusion |

---

## Open Questions

No open questions -- all design decisions resolved by spikes and issue analysis.
