---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-08
tracking: https://github.com/tomcounsell/ai/issues/825
last_comment_id:
revision_applied: true
---

# Session Status Index Staleness — Stale Re-query & Missing health_task Callback

## Problem

A PM session (`tg_valor_-1003449100931_476`) was actively doing work — changing git branches, posting progress — yet `valor_session list` and the web dashboard showed it as `completed`. Querying Redis directly via `AgentSession.query.filter(status='running')` returned it as `running`. The divergence was invisible until manual Redis inspection.

The issue caused an incident where sessions appeared stuck in `pending` with no observable explanation. The real session was running, the index was wrong.

**Current behavior:** `valor_session list` and `AgentSession.query.filter(status=X)` can return stale/wrong statuses for the same session. A session that transitions status while `_complete_agent_session` is running can end up indexed in both `running` and `completed` simultaneously.

**Desired outcome:** Index and ground-truth Redis hash always agree. `valor_session list` reflects actual status. Sessions appear in exactly one status index at a time.

## Prior Art

- **#783** (`AgentSession status index corruption: ghost running sessions from lazy-load and delete-and-recreate bugs`) — Closed 2026-04-07. Fixed two index corruption paths: (1) lazy-loaded sessions with empty `_saved_field_values`, (2) delete-and-recreate pattern. Today's bug is a third path — stale re-query — not covered by that fix.
- **#700** (`Completed sessions revert to pending`) — Earlier index corruption from a different root cause; fixed via transition_status() discipline.
- **#738** (`Stale session cleanup kills live sessions`) — Different bug class (stale cleanup), same observability gap pattern.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #793 (closes #783) | Backfilled `_saved_field_values["status"]` in `finalize_session()` and `transition_status()` to fix lazy-load and delete-and-recreate paths | Covered the lazy-load path but not the stale re-query path in `_complete_agent_session` — a separate code location that silently falls back to the stale in-memory object when the `status="running"` filter returns empty |

**Root cause pattern:** Index corruption is a class of bug with multiple independent entry points. Each fix closes one path without auditing sibling paths. `_complete_agent_session` had its own re-query logic with a restrictive status filter — a footgun that #793 didn't reach.

## Data Flow

The bug manifests at session completion time:

1. **Claude agent exits** → `_complete_agent_session(session, failed=False)` is called with in-memory `session` object
2. **Re-query by session_id + status="running"** → `AgentSession.query.filter(session_id=sid, status="running")` — if session is not currently in `running` index (e.g., it was transitioned by another path), filter returns empty list
3. **Fallback to stale in-memory object** → `session` variable is unchanged; `session.status` may reflect the stale snapshot
4. **`finalize_session()` called with stale object** → reads `current_status = getattr(session, "status")` → backfills `_saved_field_values["status"]` with the wrong old value
5. **Popoto `on_save()`** → calls `srem(wrong_index_key)` removing from the wrong set; calls `sadd(completed_index_key)` → session is now indexed in **both** `running` and `completed` simultaneously
6. **Observable result** → `valor_session list` shows `completed`, `AgentSession.query.filter(status='running')` still returns it

**Gap 2 flow:** `health_task` runs `_agent_session_health_loop()` as an asyncio task. The internal `while True / try-except` already catches all ordinary `Exception` subclasses and continues the loop — so ordinary exceptions cannot escape. The health task only exits prematurely via `CancelledError` (expected shutdown) or `BaseException` subclasses that are not caught by `except Exception` (e.g., `SystemExit`, `KeyboardInterrupt`, asyncio internals). Without a `done_callback`, any such exit would be silent — no log, health checks permanently stopped with no alert.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Two localized changes, each ≤5 lines. No new dependencies, no schema changes.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Drop status filter in re-query**: `_complete_agent_session()` re-queries by `session_id` only, no `status="running"` constraint — guarantees a fresh Redis object regardless of current status
- **health_task done_callback**: Mirrors `_notify_task_done` pattern already present for `notify_task` — logs ERROR if task exits with an exception, ignores cancellation

### Technical Approach

**Gap 1** (`agent/agent_session_queue.py` lines 1013–1015):

Change:
```python
running_records = list(
    AgentSession.query.filter(session_id=session_id, status="running")
)
if running_records:
```

To:
```python
fresh_records = list(
    AgentSession.query.filter(session_id=session_id)
)
if fresh_records:
```

Update the variable name throughout the block (`running_records` → `fresh_records`) and update the docstring to reflect that the re-query is no longer status-filtered.

> **Implementation Note (Concern 1 — Multi-record tie-breaking):** After dropping the `status="running"` filter, the sort-by-`created_at` heuristic could select a stale `completed` record if one shares the same `session_id` with a newer timestamp. Prefer `running` records first; fall back to most-recent only if none are running:
> ```python
> running = [r for r in fresh_records if getattr(r, "status", None) == "running"]
> session = running[0] if running else sorted(fresh_records, key=lambda r: r.created_at, reverse=True)[0]
> ```
> This ensures the live running session is always selected for finalization, not a prior stale completed record.

**Gap 2** (`worker/__main__.py` after line 230):

Add immediately after `health_task = asyncio.create_task(..., name="session-health-monitor")`:
```python
def _health_task_done(t: asyncio.Task) -> None:
    if t.cancelled():
        return  # Normal shutdown path
    exc = t.exception()
    if exc is not None:
        logger.error("Health monitor task exited unexpectedly: %s", exc)

health_task.add_done_callback(_health_task_done)
```

> **Implementation Note (Gap 2 callback guards BaseException, not ordinary exceptions):** The `_agent_session_health_loop` internal `while True / try-except Exception` already prevents ordinary exceptions from escaping. The done_callback guards against `BaseException` subclasses (`SystemExit`, `KeyboardInterrupt`) and asyncio-internal exits that bypass the loop's own exception handler. The code comment added by the builder should read: "Guards against unexpected task exit — ordinary exceptions are already caught inside the loop's own try-except."

> **Implementation Note (task name and insertion point):** Add `name="session-health-monitor"` to the `asyncio.create_task()` call for `health_task` (mirrors `notify_task`'s existing `name="session-notify-listener"`). Insert the callback registration after `health_task = asyncio.create_task(...)` and *before* `notify_task = asyncio.create_task(...)`. Do not insert after the `notify_task` line.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] The existing `except Exception as exc` block in `_complete_agent_session()` (line 1030) already logs a warning and falls back. After the fix, verify the fallback still works correctly — the only change is the filter; the exception handler is unchanged.
- [x] The `_health_task_done` callback should be tested with a mock task that raises an exception to verify the ERROR log fires.

### Empty/Invalid Input Handling
- [x] `AgentSession.query.filter(session_id=session_id)` with no status filter may return sessions in any status. The existing `if fresh_records:` guard handles the empty case. Add assertion that when the filter returns empty, `finalize_session()` is called on the original in-memory object (same as before, correct fallback).

### Error State Rendering
- [x] Not applicable — this fix is internal lifecycle logic with no user-visible output paths.

## Test Impact

- [x] `tests/unit/test_agent_session_queue.py` — ADD: New test `test_complete_agent_session_requery_no_status_filter` that mocks `AgentSession.query.filter` and calls `_complete_agent_session(session, failed=False)`. Asserts the mock was called with `session_id=<id>` and NOT with `status="running"`. Regression guard for the stale re-query bug.
- [x] `tests/integration/test_session_lifecycle.py` (if it exists) — CHECK: Verify tests for session completion don't assert the old filter signature

## Rabbit Holes

- **Re-architecting the re-query pattern** — the filter-by-id approach is fine; don't refactor to use `.get()` by primary key, it adds different complexity
- **Auditing all Popoto filter call sites** — that's a separate chore; this fix is targeted
- **Fixing the recon validator regex** — the validator has a false-positive bug (`**Confirmed:**` with colon not matching `\*\*Confirmed\*\*`); out of scope here

## Risks

### Risk 1: Multi-record tie-breaking after dropping status filter
**Impact:** `AgentSession.query.filter(session_id=session_id)` may return multiple records (e.g., a `running` and a `completed` record for the same session_id if a previous corruption already occurred). The existing sort-by-`created_at` fallback handles this, but behavior should be verified.
**Mitigation:** The existing multiple-records guard (lines 1017–1029) already sorts by `created_at` desc and takes the most recent — it just needs to work for records of any status. The fix is additive — no new logic needed for this case.

### Risk 2: health_task callback adds noise if health loop is cancelled at shutdown
**Impact:** During normal `SIGTERM` shutdown, `health_task.cancel()` is called (line 273). The `if t.cancelled(): return` guard prevents false ERROR logs.
**Mitigation:** Mirrors the identical guard in `_notify_task_done` — already proven pattern.

## Race Conditions

### Race 1: Session status transitions between re-query and finalize
**Location:** `agent/agent_session_queue.py` lines 1010–1039
**Trigger:** Between `AgentSession.query.filter(session_id=session_id)` returning a fresh object and `finalize_session()` being called, another process could transition the session status again.
**Data prerequisite:** The fresh object's `_saved_field_values["status"]` must reflect the current status at re-query time for Popoto's srem() to remove the correct index entry.
**State prerequisite:** Only one process should call `_complete_agent_session()` for a given session — enforced by the worker's exclusive session execution model.
**Mitigation:** This is an inherent race in the current architecture; the fix reduces (not eliminates) the window by using a fresh re-read. The existing `finalize_session()` idempotency guard (`if current_status == status: return`) prevents double-finalization.

## No-Gos (Out of Scope)

- Rewriting the re-query pattern to use `.get()` by primary key
- Adding Redis transactions (MULTI/EXEC) around the re-query + finalize sequence
- Fixing the `validate_issue_recon.py` bucket regex false positive
- Any changes to the `finalize_session()` or `transition_status()` internals — those are correct post-#783

## Update System

No update system changes required — both fixes are internal to `agent/agent_session_queue.py` and `worker/__main__.py`. No new dependencies, config, or migration steps needed.

## Agent Integration

No agent integration required — this is a worker-internal lifecycle fix. No MCP servers, `.mcp.json`, or bridge changes needed.

## Documentation

- [x] Update docstring/inline comment on `_complete_agent_session()` in `agent/agent_session_queue.py` to document that the re-query is status-filter-free and running-first tie-breaking
- [x] Update `docs/features/session-lifecycle.md` — expanded Worker Completion Redis Re-read section for no-status-filter re-query and bug #825
- [x] Update `docs/features/agent-session-health-monitor.md` — documented `_health_task_done` callback pattern
- [x] No new feature doc needed — this is a bug fix to existing behavior

## Success Criteria

- [x] `valor_session list` and `AgentSession.query.filter(status=X)` return consistent results for the same session
- [x] `_complete_agent_session()` re-queries without a `status="running"` filter
- [x] A session that transitions status while `_complete_agent_session` is running does not end up in multiple index sets (verify via `redis-cli SMEMBERS valor:AgentSession:status:running` and `SMEMBERS valor:AgentSession:status:completed`)
- [x] `health_task` has a `done_callback` equivalent to `notify_task`'s callback
- [x] All unit tests pass
- [x] Ruff lint and format clean

## Team Orchestration

### Team Members

- **Builder (lifecycle-fix)**
  - Name: lifecycle-fix-builder
  - Role: Apply Gap 1 and Gap 2 fixes, update docstrings
  - Agent Type: builder
  - Resume: true

- **Validator (lifecycle-fix)**
  - Name: lifecycle-fix-validator
  - Role: Verify fixes are correct, run tests, check Redis index consistency
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Apply Gap 1 Fix — Drop status filter in `_complete_agent_session()`
- **Task ID**: build-gap1
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_queue.py`
- **Assigned To**: lifecycle-fix-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/agent_session_queue.py`, change `AgentSession.query.filter(session_id=session_id, status="running")` to `AgentSession.query.filter(session_id=session_id)` (line ~1014)
- Rename `running_records` → `fresh_records` throughout the block for clarity
- Apply multi-record tie-breaking: prefer running records first (`running = [r for r in fresh_records if getattr(r, "status", None) == "running"]`), fall back to most-recent by `created_at` only if none are running
- Update the block comment and function docstring to explain the intentional no-status-filter re-query and the running-first tie-breaking logic

### 2. Apply Gap 2 Fix — Add `done_callback` to `health_task`
- **Task ID**: build-gap2
- **Depends On**: none
- **Validates**: none (no existing test for this callback pattern)
- **Assigned To**: lifecycle-fix-builder
- **Agent Type**: builder
- **Parallel**: true
- In `worker/__main__.py`, add `name="session-health-monitor"` to the `asyncio.create_task()` call for `health_task` (mirrors `notify_task`'s `name="session-notify-listener"`)
- Add `_health_task_done` callback function and wire it to `health_task.add_done_callback(_health_task_done)` immediately after `health_task = asyncio.create_task(...)` and before `notify_task = asyncio.create_task(...)`
- Mirror the exact structure of `_notify_task_done` (cancelled check, exception check, ERROR log)
- Add code comment: "Guards against unexpected task exit — ordinary exceptions are already caught inside the loop's own try-except."

### 3. Write Regression Test and Validate
- **Task ID**: validate-fixes
- **Depends On**: build-gap1, build-gap2
- **Assigned To**: lifecycle-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Add `test_complete_agent_session_requery_no_status_filter` to `tests/unit/test_agent_session_queue.py`: mock `AgentSession.query.filter`, call `_complete_agent_session(session, failed=False)`, assert the mock was called with `session_id=<id>` and that `'status'` was NOT in the keyword arguments
- Run `pytest tests/unit/ -x -q` and confirm pass
- Run `python -m ruff check . && python -m ruff format --check .`
- Grep confirm: `grep -A3 'query.filter.*session_id' agent/agent_session_queue.py | grep 'status'` returns no output (no status arg in re-query block)
- Grep confirm: `grep -n 'health_task.add_done_callback' worker/__main__.py` returns a match
- Grep confirm: `grep -n 'session-health-monitor' worker/__main__.py` returns a match

### 4. Documentation
- **Task ID**: document-fix
- **Depends On**: validate-fixes
- **Assigned To**: lifecycle-fix-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update the **inline comment block at line 1006** (not the function docstring — the function already has good docstring coverage) in `_complete_agent_session()` to explain that the re-query intentionally omits the `status` filter and why (`_saved_field_values` must reflect current Redis state regardless of what status the session is currently in)
- Check `docs/features/bridge-worker-architecture.md` for session completion references; update if present

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-fix
- **Assigned To**: lifecycle-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify all success criteria met
- Generate final pass/fail report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No status filter in re-query | `grep -A3 'query.filter.*session_id' agent/agent_session_queue.py \| grep 'status'` | no output (no `status` arg in the re-query block) |
| health_task callback wired | `grep -c 'health_task.add_done_callback' worker/__main__.py` | output contains 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic/Adversary | After dropping `status="running"` filter, sort-by-`created_at` heuristic could select a stale `completed` record with a newer timestamp over the live `running` session | Embedded in Gap 1 Solution block | Prefer `running` records first; fall back to most-recent only if none running: `running = [r for r in fresh_records if getattr(r, "status", None) == "running"]` |
| CONCERN | Operator/Skeptic | Plan says "after line 230" for callback insertion — surrounding code context makes exact insertion point ambiguous | Embedded in Gap 2 Solution block | Insert after `health_task = asyncio.create_task(...)` and before `notify_task = asyncio.create_task(...)` |
| CONCERN | Skeptic/Adversary | Gap 2 problem description overstates risk — `_agent_session_health_loop` already has `except Exception` inside `while True`, so ordinary exceptions cannot escape; the callback only guards against `BaseException`/asyncio-internal exits | Corrected in Problem section and Gap 2 Solution block | Builder code comment: "Guards against unexpected task exit — ordinary exceptions are already caught inside the loop's own try-except." |
| CONCERN | Skeptic/User | Test Impact listed a non-existent test to update — `tests/unit/test_agent_session_queue.py` has zero tests for `_complete_agent_session`; the fix has no regression coverage | Corrected in Test Impact section | New test added: mock `AgentSession.query.filter`, call `_complete_agent_session`, assert `status` not in call kwargs |
| NIT | — | `health_task` missing `name=` argument (unlike `notify_task` which has `name="session-notify-listener"`) | Embedded in Gap 2 Solution block | Add `name="session-health-monitor"` to `asyncio.create_task()` call |
| NIT | — | Verification grep `grep -n 'status="running"'` has false-negative risk — matches any occurrence in file, not just re-query block | Corrected in Verification table | Use `grep -A3 'query.filter.*session_id' ... \| grep 'status'` instead |
| NIT | — | Docstring update scope under-specified — function already has good docstring coverage; target is the inline comment block at line 1006 | Corrected in Documentation section and Task 4 | Specify "inline comment block at line 1006" explicitly |

**Verdict: READY TO BUILD (with concerns addressed via revision pass)**

---

## Open Questions

None — both fixes are fully specified and localized. No human input required before build.
