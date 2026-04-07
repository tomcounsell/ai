---
status: docs_complete
type: bug
appetite: Small
owner: valor
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/783
last_comment_id: IC_kwDOEYGa0876K2z8
---

# AgentSession Status Index Corruption: Ghost Running Sessions

## Problem

The dashboard shows duplicate "running" sessions that, when clicked, open as "abandoned". Calling `kill --session-id` on these ghosts creates additional "killed" records but leaves the ghost running entries intact — running kill repeatedly makes the list *longer*, not shorter. A manual Redis inspection found **166 phantom/stale index entries** and **49 corrupted hash keys** that had to be cleaned via direct `redis-cli` commands.

**Current behavior:**
- `AgentSession.query.filter(status="running")` returns sessions that are actually abandoned
- `kill --session-id <id>` on a ghost creates a new corrupted hash key and adds it to the running index instead of removing the ghost
- Dashboard running count never matches actual active worker processes

**Desired outcome:**
- `transition_status()` correctly removes the old index entry even when called on lazy-loaded sessions
- `kill --session-id` marks sessions as killed without producing new ghost records
- Dashboard running count accurately reflects active worker processes

## Prior Art

- No prior closed issues or merged PRs specifically addressing this corruption pattern.

## Data Flow

### Bug 1 — Lazy-load index leak

1. **`AgentSession.get_by_id(id)`** → calls `_create_lazy_model()` in Popoto's `encoding.py`
2. **`_create_lazy_model`** (encoding.py:416–430) initializes `_saved_field_values = {}` then populates **only KeyFields** — `status` (an IndexedField) is not seeded
3. **`transition_status(session, new_status)`** in `models/session_lifecycle.py:198–200` calls `session.status = new_status; session.save()`
4. **`IndexedFieldMixin.on_save()`** reads `old_value = saved_values.get("status")` → returns `None` (missing key)
5. Guard `if old_value is not None and old_value != field_value` is **never satisfied** → `pipeline.srem(old_set_key, member_key)` is skipped
6. Session accumulates in **both** old and new status index sets simultaneously

### Bug 2 — Delete-and-recreate corrupted key

1. **`_kill_agent_session(target)`** in `tools/agent_session_scheduler.py:824–829` calls `_extract_agent_session_fields(target)`
2. `target.delete()` removes the current Redis hash
3. `fields["status"] = "killed"` and `AgentSession.create(**fields)` are called
4. If any nullable KeyField value (`session_type`, `chat_id`, `parent_agent_session_id`) was never set on the source object, the Python field **descriptor object** is passed instead of `None`
5. Popoto stringifies the descriptor into the key: `AgentSession:local9091:69daf81b:...<KeyField object at 0x10c1850d0>:valor:dev`
6. This corrupted hash key is added to the running/killed index sets but can **never be resolved, loaded, or deleted** via normal ORM calls
7. Next kill attempt finds the ghost in the running index, tries `delete()`, fails silently, creates another corrupted record

## Architectural Impact

- **No new dependencies**: Both fixes are single-file changes using existing functions
- **Interface changes**: `_kill_agent_session` return dict loses `new_agent_session_id` key (no callers inspect this field except logging)
- **Coupling**: Reduces coupling — `_kill_agent_session` no longer imports `_extract_agent_session_fields` from `agent.agent_session_queue`
- **Reversibility**: Both changes are minimal and easily reverted

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

- **Bug 1 fix**: In `transition_status()` (`models/session_lifecycle.py`), before setting `session.status = new_status`, backfill `session._saved_field_values["status"] = current_status`. This ensures Popoto's `on_save()` guard has the old value and calls `srem` to remove the old index entry. One line.
- **Bug 2 fix**: In `_kill_agent_session()` (`tools/agent_session_scheduler.py`), replace the delete-and-recreate block (lines 824–829) with `finalize_session(session, "killed", reason="CLI kill")`. This uses in-place mutation + `save()`, avoiding key recomputation entirely. Remove the `_extract_agent_session_fields` import that is no longer needed in this function.

### Flow

Ghost session in running index → `kill --session-id <id>` → `_kill_agent_session` → `finalize_session(session, "killed")` → `transition_status` backfills `_saved_field_values["status"]` → `session.status = "killed"; session.save()` → `on_save()` calls `srem` on running index → `sadd` on killed index → ghost removed

### Technical Approach

- Backfill `_saved_field_values["status"]` in `transition_status()` at line 198, before the assignment — use the `current_status` variable already computed at line 175
- `finalize_session()` already calls `transition_status()` indirectly through `session.status = status; session.save()` — but it also needs the same backfill since it uses the same `save()` path. Add the backfill to **both** `transition_status()` and `finalize_session()` before their respective `session.status = ...` assignments.
- Remove the `_extract_agent_session_fields` import from `_kill_agent_session` (it was only used for the deleted block)
- The `datetime.now(tz=UTC)` assignment for `completed_at` is already handled by `finalize_session()` via `session.completed_at = time.time()`

## Failure Path Test Strategy

### Exception Handling Coverage

- `transition_status()` and `finalize_session()` both have try/except blocks around side effects (lifecycle log, auto-tag, checkpoint) — these are already tested in `test_session_lifecycle_consolidation.py`. The `_saved_field_values` backfill is before any exception-prone side effect; no new handlers needed.
- `_kill_agent_session` has no catch-all; exceptions propagate to `cmd_kill`. Existing behavior preserved.

### Empty/Invalid Input Handling

- `transition_status()` already raises `ValueError` for None session and invalid statuses — no change needed
- The `_saved_field_values` backfill only runs when `current_status` is already computed; if it's None (very first save of a new object that was never persisted), backfilling None is still correct behavior (the guard `if old_value is not None` stays false, which is appropriate for an unpersisted object)

### Error State Rendering

- Kill CLI errors propagate as JSON `{"status": "error", "message": "..."}` via `_output()` — no change to this path

## Test Impact

- [ ] `tests/unit/test_agent_session_scheduler_kill.py` — UPDATE: the `new_agent_session_id` key in the kill result dict is removed (no recreate); update assertions that check this key
- [ ] `tests/unit/test_session_lifecycle_consolidation.py` — UPDATE: may need new test covering lazy-load backfill; review existing tests for `transition_status` to verify they still pass
- [ ] `tests/unit/test_agent_session_queue_revival_helper.py` — UPDATE if it tests `_kill_agent_session` indirectly

New test to add:
- [ ] `tests/unit/test_agent_session_index_corruption.py` — CREATE: verifies that status transition on a lazy-loaded `AgentSession` removes the old index entry (unit test with real Redis or mocked `_saved_field_values`)

## Rabbit Holes

- **Fixing Popoto upstream (Option A)**: Modifying `_create_lazy_model` in `.venv/lib/python3.12/site-packages/popoto/models/encoding.py` would be the clean library fix but requires patching a vendored dependency and maintaining the patch across upgrades. The call-site fix (Option B) is simpler and self-contained.
- **Ghost cleanup migration script**: Tempting to write a one-time script to clean up existing corrupted keys. This is a separate concern — the plan fixes the root cause going forward. Manual `redis-cli` cleanup has already been done.
- **Generalizing to all IndexedFields**: Only `status` is the problem field in practice. Generalizing `_saved_field_values` backfill to all IndexedFields would require iterating `model_class._meta` — not worth the scope increase.

## Risks

### Risk 1: `finalize_session` side effects in CLI context
**Impact:** `finalize_session()` triggers auto-tag, branch checkpoint, and parent finalization. In the `kill` CLI context, these may fail if the environment isn't fully set up (no git repo, no session tags config).
**Mitigation:** All side effects in `finalize_session()` are already wrapped in try/except with `logger.debug` — they fail silently. The `skip_auto_tag` and `skip_checkpoint` flags are available if needed, but silent failure is already the contract.

### Risk 2: `_saved_field_values` is a private Popoto internal
**Impact:** A Popoto upgrade could rename or restructure `_saved_field_values`, breaking the backfill.
**Mitigation:** We already depend on this dict in `on_save()` hooks (it's not truly private in practice). Add a comment documenting the coupling so future maintainers know to check this on Popoto upgrades.

## Race Conditions

No race conditions introduced. The `_saved_field_values` backfill happens synchronously before `save()` within the same thread. The existing Popoto save-and-index operations are already non-atomic (no Redis transactions for the backfill itself), but this is unchanged from the current behavior.

## No-Gos (Out of Scope)

- One-time migration to clean up existing corrupted hash keys in production Redis (done manually already)
- Patching Popoto library source in `.venv/`
- Fixing any other `delete-and-recreate` patterns in `_enqueue_nudge` (separate concern)
- Adding Redis transactions (MULTI/EXEC) around index updates in Popoto

## Update System

No update system changes required — this is a purely internal bug fix with no new dependencies, config files, or deployment changes.

## Agent Integration

No agent integration required — `_kill_agent_session` and `transition_status` are internal functions not exposed via MCP. The `agent_session_scheduler` CLI is used by the worker and humans directly.

## Documentation

- [x] Update docstring on `transition_status()` in `models/session_lifecycle.py` to note the `_saved_field_values` backfill and why it exists (lazy-load Popoto behavior)
- [x] Update docstring on `_kill_agent_session()` to note that `finalize_session()` is used instead of delete-and-recreate

No new feature docs needed — this is a bug fix with no user-visible API changes.

## Success Criteria

- [ ] `transition_status()` correctly removes the old `status` index entry even when called on a lazy-loaded `AgentSession`
- [ ] `kill --session-id <id>` marks all matching running sessions as killed without creating additional ghost records
- [ ] Running kill on a ghost session does not increase the number of running sessions in the index
- [ ] Dashboard running count matches actual active worker processes
- [ ] Unit test in `tests/unit/test_agent_session_index_corruption.py` verifies that status transition on a lazy-loaded `AgentSession` removes the old index entry
- [ ] Tests pass (`/do-test`)
- [ ] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (lifecycle-fix)**
  - Name: lifecycle-builder
  - Role: Apply Bug 1 fix to `transition_status()` and `finalize_session()` in `models/session_lifecycle.py`; apply Bug 2 fix to `_kill_agent_session()` in `tools/agent_session_scheduler.py`; write new unit test
  - Agent Type: builder
  - Resume: true

- **Validator (lifecycle-fix)**
  - Name: lifecycle-validator
  - Role: Verify fixes are correct, run test suite, confirm no regression
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Apply Bug 1 fix — `transition_status` backfill
- **Task ID**: build-lifecycle-fix
- **Depends On**: none
- **Validates**: `tests/unit/test_session_lifecycle_consolidation.py`, `tests/unit/test_agent_session_index_corruption.py` (create)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- In `models/session_lifecycle.py`, inside `transition_status()`, after computing `current_status = getattr(session, "status", None)` (line 175) and before `session.status = new_status` (line 199), insert: `if hasattr(session, "_saved_field_values"): session._saved_field_values["status"] = current_status`
- In `models/session_lifecycle.py`, inside `finalize_session()`, after computing `current_status = getattr(session, "status", None)` (line 77) and before `session.status = status` (line 126), insert the same backfill line
- Add docstring note to both functions explaining the Popoto lazy-load behavior
- Create `tests/unit/test_agent_session_index_corruption.py` with a test that: creates an `AgentSession`, simulates a lazy-load by clearing `_saved_field_values` (leaving only key fields), calls `transition_status()`, and asserts the old status index set no longer contains the session's member key

### 2. Apply Bug 2 fix — `_kill_agent_session` replace delete-and-recreate
- **Task ID**: build-kill-fix
- **Depends On**: build-lifecycle-fix
- **Validates**: `tests/unit/test_agent_session_scheduler_kill.py`
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/agent_session_scheduler.py`, in `_kill_agent_session()`, replace lines 824–830 (the delete-and-recreate block) with `finalize_session(session, "killed", reason="CLI kill", skip_auto_tag=True, skip_checkpoint=True)`
- Add `from models.session_lifecycle import finalize_session` import at the top of the function (or at module level if not already present)
- Remove the `_extract_agent_session_fields` import from this function (check if it's used elsewhere in the file before removing the module-level import)
- Update the `result` dict to remove `new_agent_session_id` key (no longer applicable)
- Update `result["status"] = "killed"` — keep this for the return value
- Update `tests/unit/test_agent_session_scheduler_kill.py` to remove assertions on `new_agent_session_id`

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-kill-fix
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_session_lifecycle_consolidation.py tests/unit/test_agent_session_scheduler_kill.py tests/unit/test_agent_session_index_corruption.py -v`
- Run `python -m ruff check models/session_lifecycle.py tools/agent_session_scheduler.py`
- Verify all success criteria are met
- Report pass/fail

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_session_lifecycle_consolidation.py tests/unit/test_agent_session_scheduler_kill.py tests/unit/test_agent_session_index_corruption.py -v` | exit code 0 |
| Lint clean | `python -m ruff check models/session_lifecycle.py tools/agent_session_scheduler.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/session_lifecycle.py tools/agent_session_scheduler.py` | exit code 0 |
| No delete-and-recreate in kill | `grep -n "target.delete" tools/agent_session_scheduler.py` | exit code 1 |
| Backfill present in transition_status | `grep -n "_saved_field_values" models/session_lifecycle.py` | output contains _saved_field_values |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — root causes verified, fixes are well-scoped, no human decisions required.
