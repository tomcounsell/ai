---
status: Draft
type: bug
appetite: Small
owner: Valor
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/772
last_comment_id:
---

# Fix: Re-enable Stale Session Cleanup with Heartbeat-Based Liveness Check

## Problem

`_cleanup_stale_sessions` in `scripts/update/run.py` is entirely disabled (lines 663–671, commented out as a hotfix). It was disabled because the `_active_workers` liveness check is an in-process dict that is always empty when the update script runs as a subprocess — so any running session older than 120 minutes would get killed unconditionally, including healthy long-running SDLC sessions.

The root issue is that `_active_workers` is process-local state and is not visible to a standalone `/update` subprocess invocation. The existing implementation uses `created_at` age as a proxy for staleness, but a session can be legitimately running for 2+ hours during a multi-stage SDLC pipeline.

**Desired outcome:** Stale session cleanup is re-enabled. Sessions with recent `updated_at` activity are never killed. The worker writes a periodic heartbeat to `updated_at` during long Claude API calls so that even a blocked-on-SDK session stays alive in Redis.

## Prior Art

`docs/plans/session_lifecycle_stale_cleanup.md` — the earlier #738 fix that raised the age threshold to 120 min, routed cleanup through `finalize_session`, and disabled the call pending a proper liveness fix. This plan completes that work.

## Spike Results

No spikes needed. The fix approach is clear:
- `AgentSession.updated_at` is `DatetimeField(auto_now=True)` — it updates on every `.save()`.
- The existing `_heartbeat_loop` at lines 2676–2684 of `agent/agent_session_queue.py` already fires calendar heartbeats every `CALENDAR_HEARTBEAT_INTERVAL` seconds but does NOT write `updated_at` to Redis.
- The existing `_cleanup_stale_sessions` function body (lines 143–234) already calls `finalize_session` and checks `_active_workers`. It just needs the `created_at` age check replaced with an `updated_at` recency check.

## Data Flow

**Current (broken) flow:**

1. Long-running SDLC session starts → `created_at` set, `updated_at` set
2. Session blocks on Claude SDK API call for >120 min
3. `/update` runs as subprocess → `_active_workers` is empty (different process) → age check fires on `created_at` → session killed
4. OR: stale cleanup is disabled entirely → stale orphan sessions accumulate forever

**Fixed flow:**

1. Long-running SDLC session starts → `_heartbeat_loop` fires every 5 min → writes `updated_at` to Redis
2. `/update` runs → `_cleanup_stale_sessions` checks `updated_at` recency → session was active within 30 min → skip
3. Truly orphaned session (no heartbeat for 30 min) → killed via `finalize_session`

## Architectural Impact

- **Interface changes:** No new public API. `_cleanup_stale_sessions` signature unchanged. `_heartbeat_loop` is a private async function — adding a Redis write is internal.
- **New dependencies:** None. `updated_at` field already exists on `AgentSession`.
- **Coupling:** No new coupling introduced — both files already interact with `AgentSession`.
- **Reversibility:** All changes are surgical. Revert individual lines to restore previous state.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — modifies existing Python files; no new dependencies or environment changes.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | AgentSession reads/writes |
| Pytest available | `pytest --version` | Running unit tests |

## Solution

### Key Elements

1. **`updated_at` recency check in `_cleanup_stale_sessions`** — Replace the `created_at` age check (lines 193–210 of `scripts/update/run.py`) with an `updated_at` recency check. If `updated_at` is within the last N minutes (default 30), the session is live — skip it. Only kill sessions where `updated_at` is stale. Keep `_active_workers` check as a secondary defense for in-process invocations.

2. **Periodic `updated_at` heartbeat in worker** — In `_heartbeat_loop` (line 2676, `agent/agent_session_queue.py`), after firing the calendar heartbeat, also write `agent_session.updated_at = datetime.now(tz=UTC); agent_session.save()`. This keeps the Redis record fresh during long Claude SDK calls. Interval: every 5 minutes (same as `CALENDAR_HEARTBEAT_INTERVAL` or a new constant).

3. **Re-enable the cleanup call** — Uncomment lines 663–671 in `scripts/update/run.py`. Update the log message to report both skipped-live and killed counts.

4. **Report skipped-live sessions** — Return a tuple `(killed, skipped_live)` from `_cleanup_stale_sessions` (or add a second return value) and log the skipped count alongside the killed count in `run_update`.

### Flow

Session running >30 min with recent heartbeat → `_cleanup_stale_sessions` fires → `updated_at` within 30 min → **skip** → session continues running

Session orphaned (no heartbeat for 30+ min) → `_cleanup_stale_sessions` fires → `updated_at` stale → `finalize_session(s, "killed", ...)` → lifecycle hooks fire → clean `killed` record in Redis

### Technical Approach

**Change 1 — `scripts/update/run.py:_cleanup_stale_sessions`:**
```python
# Replace created_at age check with updated_at recency check
LIVE_RECENCY_MINUTES = 30  # session is live if updated_at within this window

updated = getattr(s, "updated_at", None) or getattr(s, "created_at", None)
if not updated:
    continue
if isinstance(updated, str):
    try:
        updated = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        continue
if isinstance(updated, datetime):
    recency = now - updated.timestamp()
else:
    try:
        recency = now - float(updated)
    except (TypeError, ValueError):
        continue

if recency < LIVE_RECENCY_MINUTES * 60:
    skipped_live += 1
    continue  # recently active — do not kill
```

Also update the return value to `return killed_count, skipped_live`.

**Change 2 — `agent/agent_session_queue.py:_heartbeat_loop`:**
```python
async def _heartbeat_loop():
    while not task._task.done():
        await asyncio.sleep(CALENDAR_HEARTBEAT_INTERVAL)
        if not task._task.done():
            asyncio.create_task(
                _calendar_heartbeat(session.project_key, project=session.project_key)
            )
            # Keep updated_at fresh so stale cleanup doesn't kill this session
            if agent_session:
                try:
                    agent_session.updated_at = datetime.now(tz=UTC)
                    agent_session.save()
                except Exception as exc:
                    logger.warning("[%s] Heartbeat save failed: %s", session.session_id, exc)
```

**Change 3 — Re-enable call in `run_update`:**
```python
try:
    stale_killed, stale_skipped = _cleanup_stale_sessions(project_dir)
    if stale_killed > 0:
        log(f"Cleaned up {stale_killed} stale session(s)", v)
    if stale_skipped > 0:
        log(f"Skipped {stale_skipped} live session(s) (recent heartbeat)", v)
except Exception as e:
    log(f"WARN: Session cleanup failed: {e}", v)
```

## Failure Path Test Strategy

### Exception Handling Coverage
- `_cleanup_stale_sessions` already wraps per-session finalization in try/except. The `updated_at` recency check is a simple datetime comparison — failure falls through to `continue` (skip the session).
- Heartbeat save in `_heartbeat_loop` is wrapped in try/except with `logger.warning` — a failed save does not crash the session.
- If `_cleanup_stale_sessions` returns a non-tuple (old callers), the unpacking in `run_update` will raise a `ValueError`. Guard with a compatibility shim or update all callers.

### Empty/Invalid Input Handling
- `updated_at` may be None on very old sessions — fall back to `created_at` (same field already checked in existing code).
- `agent_session` may be None in `_heartbeat_loop` — guard with `if agent_session:` before the save.

### Error State Rendering
- Errors surface in `/update` log output via `log(f"WARN: Session cleanup failed: {e}", v)`. No user-visible output beyond update logs.

## Test Impact

- [ ] `tests/unit/test_stale_cleanup.py` — UPDATE: existing tests use `created_at` age to determine kill/skip; update to use `updated_at` recency. Verify that a session with recent `updated_at` is skipped even if `created_at` is >120 min ago.
- [ ] `tests/unit/test_stale_cleanup.py::test_stale_session_is_killed` — UPDATE: assert session with `updated_at` >30 min ago AND no `_active_workers` entry is killed.
- [ ] `tests/unit/test_stale_cleanup.py::test_live_session_skipped_by_active_workers` — KEEP: `_active_workers` check is still present as secondary defense; test remains valid.

If `tests/unit/test_stale_cleanup.py` does not yet exist, this plan creates it (see Step by Step Tasks).

## Rabbit Holes

- **Adding a separate `heartbeat_at` field to `AgentSession`:** Not needed — `updated_at` already serves this purpose and is auto-set on every `.save()`.
- **Making heartbeat interval configurable via env var:** Nice to have but out of scope. Use the existing `CALENDAR_HEARTBEAT_INTERVAL` constant or a hardcoded 5-minute value.
- **Distributed locking around cleanup:** Not needed — the function runs at most once per `/update` invocation; concurrent runs are not a concern.
- **Fixing the `CALENDAR_HEARTBEAT_INTERVAL` constant value:** If it's already 5 minutes, piggyback on it. If it's longer, introduce a separate `SESSION_HEARTBEAT_INTERVAL = 5 * 60` constant. Do not change the calendar heartbeat interval as a side effect.

## Risks

### Risk 1: `_heartbeat_loop` fires for sessions where `agent_session` is None
**Impact:** The save is skipped silently; those sessions will not get heartbeats and could be killed by stale cleanup.
**Mitigation:** Log a warning when `agent_session` is None and the heartbeat is skipped, so it's visible in logs. The 30-minute window provides ample buffer for sessions that do have agent_session set.

### Risk 2: Return value change in `_cleanup_stale_sessions` breaks callers
**Impact:** Any caller that does `killed = _cleanup_stale_sessions(...)` will fail with `TypeError: cannot unpack non-iterable int`.
**Mitigation:** Search for all callers before changing the return value. Only one known caller exists (`run_update`). Update it atomically with the function change.

### Risk 3: `updated_at` not reliably set on old orphaned sessions
**Impact:** Sessions that predate the heartbeat feature may have a stale `updated_at` from before the session entered a long-running state.
**Mitigation:** The 30-minute window is conservative. Any session that was active within 30 minutes is considered live. Sessions older than 30 minutes without a heartbeat are genuinely stale.

## Race Conditions

### Race 1: Heartbeat write races with `finalize_session` in cleanup
**Trigger:** Cleanup reads `updated_at` as stale, then the heartbeat fires and updates `updated_at` before cleanup calls `finalize_session`.
**Mitigation:** `finalize_session` is idempotent for sessions already in a terminal state. If the race occurs, the session will be killed despite a fresh heartbeat — acceptable given the 30-minute window makes this race extremely unlikely in practice.

## No-Gos (Out of Scope)

- Adding a `heartbeat_at` field to `AgentSession` — `updated_at` already serves this purpose
- Changing the calendar heartbeat interval as a side effect
- Monitoring or alerting on stale session kill events — separate observability concern
- Fixing unrelated issues in `_cleanup_stale_sessions` (e.g., the `_active_workers` import warning path)

## Update System

`_cleanup_stale_sessions` is called by `scripts/update/run.py` which is the update pipeline itself. Re-enabling the call is the primary change. No new config files, dependencies, or migration steps required. The update script does not need structural changes beyond uncommenting 8 lines and updating the log call.

## Agent Integration

No agent integration required — all changes are in bridge-internal session management code and the update script. No MCP servers, `.mcp.json`, or bridge routing changes needed.

## Documentation

- [ ] Update docstring for `_cleanup_stale_sessions` in `scripts/update/run.py` to describe the new `updated_at` recency check and the `(killed, skipped_live)` return value
- [ ] Update docstring for `_heartbeat_loop` in `agent/agent_session_queue.py` to note that it now writes `updated_at` to Redis every interval
- [ ] Update `docs/features/session-lifecycle.md` to document the heartbeat-based liveness mechanism (add a short paragraph under stale cleanup section)

## Success Criteria

- [ ] `_cleanup_stale_sessions` is re-enabled (uncommented in `scripts/update/run.py`)
- [ ] A session with `updated_at` within the last 30 minutes is never killed by stale cleanup, even if `created_at` is >120 minutes ago
- [ ] The worker writes a periodic heartbeat to `updated_at` every 5 minutes so long-running Claude API calls keep the session alive
- [ ] `/update` output reports skipped-live sessions alongside killed ones
- [ ] Unit test: session with recent `updated_at` is skipped by cleanup
- [ ] Unit test: session with stale `updated_at` AND no `_active_workers` entry is killed
- [ ] Tests pass (`pytest tests/unit/ -x -q`)
- [ ] Black formatting clean (`python -m black --check .`)

## Team Orchestration

### Team Members

- **Builder (heartbeat-cleanup)**
  - Name: heartbeat-cleanup-builder
  - Role: Implement all three changes — `updated_at` recency check, worker heartbeat write, re-enable cleanup call
  - Agent Type: builder
  - Resume: true

- **Test Engineer (heartbeat-cleanup)**
  - Name: heartbeat-cleanup-test-engineer
  - Role: Write unit tests for recency check (skipped-live and stale-killed cases)
  - Agent Type: test-engineer
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Build: `updated_at` recency check + re-enable cleanup
- **Task ID**: build-recency-check
- **Depends On**: none
- **Assigned To**: heartbeat-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- In `_cleanup_stale_sessions`: replace `created_at` age check with `updated_at` recency check (fall back to `created_at` if `updated_at` is None)
- Track `skipped_live` counter alongside `killed_count`; return `(killed_count, skipped_live)`
- Uncomment the disabled call in `run_update`; update to unpack tuple and log both counts

### 2. Build: Worker heartbeat
- **Task ID**: build-worker-heartbeat
- **Depends On**: none
- **Assigned To**: heartbeat-cleanup-builder
- **Agent Type**: builder
- **Parallel**: true (can run alongside task 1)
- In `_heartbeat_loop` in `agent/agent_session_queue.py`: add `agent_session.updated_at = datetime.now(tz=UTC); agent_session.save()` after calendar heartbeat, guarded by `if agent_session:` and wrapped in try/except

### 3. Write Tests
- **Task ID**: write-tests
- **Depends On**: build-recency-check, build-worker-heartbeat
- **Assigned To**: heartbeat-cleanup-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create or update `tests/unit/test_stale_cleanup.py`:
  - `test_live_session_skipped_by_recent_updated_at`: mock session with `updated_at = now - 5min`; assert cleanup returns `(0, 1)` and `finalize_session` is not called
  - `test_stale_session_killed_when_updated_at_old`: mock session with `updated_at = now - 60min`, `created_at = now - 150min`, not in `_active_workers`; assert cleanup returns `(1, 0)` and `finalize_session` called with `"killed"`
  - `test_updated_at_none_falls_back_to_created_at`: mock session with `updated_at = None`, `created_at = now - 10min`; assert cleanup skips (live via created_at fallback)

### 4. Documentation
- **Task ID**: document-heartbeat-cleanup
- **Depends On**: write-tests
- **Assigned To**: heartbeat-cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Update docstrings for `_cleanup_stale_sessions` and `_heartbeat_loop`
- Update `docs/features/session-lifecycle.md` heartbeat section

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Format clean | `python -m black --check .` | exit code 0 |
| Cleanup re-enabled | `grep -n "stale_killed\|stale_killed" scripts/update/run.py` | shows uncommented call |
| Recency check present | `grep -n "updated_at" scripts/update/run.py` | contains recency check |
| Heartbeat write present | `grep -n "updated_at.*heartbeat\|heartbeat.*updated_at\|Heartbeat save" agent/agent_session_queue.py` | contains heartbeat save |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique is run. -->

---

## Open Questions

None — root cause confirmed by code inspection. The fix approach is clear. Ready to build.
