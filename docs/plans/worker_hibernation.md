---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/839
last_comment_id: none
---

# Worker Hibernation: Mid-Turn Session Pause + Drip Resume

## Problem

When the Anthropic API goes down mid-execution, the worker marks the in-flight session as stuck in `running` and exits the worker loop. Startup recovery re-queues it to `pending`, the worker picks it up again, and the session fails again immediately — a tight failure loop with no backoff and no quiesce.

**Current behavior:** Anthropic API goes down → `sdk_client.py` raises `CircuitOpenError` or auth error mid-execution → worker loop catches, sets `session_completed = True`, breaks loop → session left in `running` → startup recovery re-queues to `pending` → worker pops again → repeat. No quiesce, no controlled wake.

**Desired outcome:** API down → health-check reflection writes hibernation Redis flag → worker stops popping sessions → in-flight session transitions to `paused` (not left in `running`), preserving full context → next health-check tick clears flag on confirmed recovery → drip-resume reflection transitions one `paused` session to `pending` per tick → Telegram notification on enter/exit.

## Prior Art

No closed issues or merged PRs found for worker hibernation or mid-turn session pause. The circuit breaker (`CircuitBreaker`, `CircuitOpenError`) was introduced in PR #502.

Issue #773 (sustainable self-healing) covers overlapping territory — it adds `paused_circuit` status, `{project_key}:sustainability:queue_paused` Redis flag, `api-health-gate` (60s), and `recovery-drip` (30s). This plan (#839) builds alongside #773. See the Relationship to #773 subsection under Solution.

## Spike Results

### spike-1: Where does CircuitOpenError currently leave the session?

- **Assumption**: "Auth error mid-execution marks session failed via finalize_session()"
- **Method**: code-read (`agent/agent_session_queue.py:2056-2069`)
- **Finding**: The catch block sets `session_completed = True` and `break`s. The `finally` block skips `finalize_session()` when `session_completed = True`. Session is left in `running` state — not `failed`. Auth errors in `sdk_client.py` line 1205 re-raise, hit the same catch. Session stays `running` indefinitely if worker dies without restart; startup recovery re-queues it if the process restarts.
- **Confidence**: high
- **Impact on plan**: Fix intercepts at the same catch block — add `transition_status(session, "paused")` before `break`. Write hibernation flag at the same site.

### spike-2: Does transition_status() accept new statuses without other changes?

- **Assumption**: "`paused` works once added to NON_TERMINAL_STATUSES"
- **Method**: code-read (`models/session_lifecycle.py:197`)
- **Finding**: Confirmed. `ValueError` raised for any status not in `NON_TERMINAL_STATUSES`. Single frozenset addition is the only required change. `finalize_session()` is terminal-only and unaffected.
- **Confidence**: high
- **Impact on plan**: `NON_TERMINAL_STATUSES` change must ship in Task 1, before any call to `transition_status(session, "paused")`.

### spike-3: Does health-check or startup recovery touch paused sessions?

- **Assumption**: "Health check or startup recovery might re-queue paused sessions"
- **Method**: code-read (`agent/agent_session_queue.py` health check and startup recovery functions)
- **Finding**: Existing `health-check` reflection scans `running` and `pending` statuses only. Startup recovery scans `running` only. Adding `paused` to `NON_TERMINAL_STATUSES` gives it a valid status without adding it to either scan — `paused` sessions are naturally excluded from both requeue paths.
- **Confidence**: high
- **Impact on plan**: No changes to health-check or startup recovery logic required.

### spike-4: Do #773 and #839 health gate reflections conflict?

- **Assumption**: "#773 api-health-gate and #839 worker-health-gate may duplicate work"
- **Method**: code-read (`docs/plans/sustainable_self_healing.md`)
- **Finding**: #773 writes `{project_key}:sustainability:queue_paused`; #839 uses `{project_key}:worker:hibernating`. Both block `_pop_agent_session()`. They are additive — `_pop_agent_session()` can check both with OR logic. The two status names (`paused_circuit` for #773, `paused` for #839) are semantically distinct: `paused_circuit` = blocked before dequeue ever happened; `paused` = interrupted mid-execution. Keep them separate.
- **Confidence**: high
- **Impact on plan**: `_pop_agent_session()` checks both flags if both keys exist. `session-resume-drip` (this plan) only restores `paused` sessions; #773's `recovery-drip` only restores `paused_circuit` sessions. No coordination conflict.

## Data Flow

### Hibernation Entry (API failure path)

1. **`sdk_client.py` `query()`**: Raises `CircuitOpenError` (circuit OPEN before query) or auth error pattern detected mid-stream
2. **`_worker_loop()` catch (`agent_session_queue.py:2060`)**: Catches `CircuitOpenError`. **NEW**: calls `transition_status(session, "paused", "circuit open — hibernating")` then writes `{project_key}:worker:hibernating = "1"` (TTL 600s) to Redis. Sets `session_completed = True`, breaks.
3. **Telegram notification**: On first flag write (key did not previously exist), enqueues notification session: "Worker hibernating — N sessions paused"
4. **Worker loop exits**: semaphore released. Worker waits for event signal.

### Hibernation Gate (pop path)

1. **`_pop_agent_session()` top**: reads `{project_key}:worker:hibernating` from Redis before `_acquire_pop_lock()`. If set → `logger.debug("[pop] Hibernating — skipping pop")` → return `None`
2. **Worker loop**: gets `None`, releases semaphore, waits — no sessions consumed

### Recovery (health-check → drip path)

1. **`worker-health-gate` reflection (60s)**: reads `{project_key}:worker:hibernating`. If set, checks Anthropic circuit state. If `CircuitState.CLOSED`: deletes `hibernating`, writes `{project_key}:worker:recovering = "1"` (TTL 3600s). Enqueues notification: "Worker woke — beginning drip resume"
2. **`session-resume-drip` reflection (30s)**: reads `recovering` flag — if absent, no-op. If set, queries `AgentSession.query.filter(status="paused")`, pops one (oldest first), `transition_status(session, "pending", "drip resume")`
3. **When `paused` list drains**: `session-resume-drip` deletes `recovering` flag
4. **`_pop_agent_session()`**: flag absent → proceeds normally

## Relationship to #773 (Sustainable Self-Healing)

#773 adds `paused_circuit` status and `queue_paused` Redis flag. #839 adds `paused` status and `hibernating` Redis flag. The two are additive:

| Concern | #773 | #839 |
|---------|------|------|
| Status | `paused_circuit` (blocked before dequeue) | `paused` (interrupted mid-execution) |
| Redis gate key | `{pk}:sustainability:queue_paused` | `{pk}:worker:hibernating` |
| Gate written by | `api-health-gate` reflection (60s) | `_worker_loop()` catch + `worker-health-gate` (60s) |
| Drip recovery | `recovery-drip` (30s) | `session-resume-drip` (30s) |

`_pop_agent_session()` checks both flags with OR logic: return `None` if either is set. The two recovery drips are independent and do not interfere — each only transitions its own status type.

## Architectural Impact

- **Modified**: `agent/agent_session_queue.py` — `_pop_agent_session()` top (one Redis GET), `_worker_loop()` catch block (add `transition_status` call + flag write)
- **Modified**: `models/session_lifecycle.py` — `NON_TERMINAL_STATUSES` (add `"paused"`)
- **New module**: `agent/hibernation.py` — three functions. Kept separate from `sustainability.py` (#773) to prevent merge conflicts between the two plans.
- **New entries**: `config/reflections.yaml` — two new reflections
- **No new external dependencies**: Redis only
- **Coupling**: `hibernation.py` imports from `bridge.health` (already used in `sdk_client.py`) and `bridge.resilience` — minimal new coupling
- **Reversibility**: Remove two Redis reads from `_pop_agent_session()`, delete `agent/hibernation.py`, remove YAML entries, revert `NON_TERMINAL_STATUSES` change. Any `paused` sessions would need manual status reset.

## Appetite

**Size:** Medium

**Team:** Solo dev, async-specialist

**Interactions:**
- PM check-ins: 1
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Hibernation flags stored in Redis |
| Anthropic circuit registered | `python -c "from bridge.health import get_health; print(get_health().summary())"` | Circuit state readable for health gate |

## Solution

### Key Elements

- **`paused` status**: New entry in `NON_TERMINAL_STATUSES`. Semantics: session interrupted by transient external failure; full context preserved; not terminal.
- **`_pop_agent_session()` guard**: First action (before `_acquire_pop_lock()`), reads `{project_key}:worker:hibernating`. If set, returns `None`. O(1) Redis GET.
- **`_worker_loop()` rescue**: On `CircuitOpenError`, calls `transition_status(session, "paused")` and writes hibernation flag before breaking. Session stays in Redis with full context.
- **`agent/hibernation.py`**: Contains `worker_health_gate()` (60s reflection), `session_resume_drip()` (30s reflection), and `send_hibernation_notification()` (helper, not a reflection).
- **Telegram notification**: Enqueued as a session — reflections cannot call bridge directly (they run inside the worker process).

### Flow

```
Anthropic API down
  → CircuitOpenError raised in sdk_client.py
  → _worker_loop catch: transition_status(session, "paused") + write worker:hibernating (TTL 600s)
  → Telegram notification enqueued: "Worker hibernating, N sessions paused"
  → _pop_agent_session reads hibernating flag → returns None
  → worker waits (no sessions consumed)

Anthropic API recovers
  → worker-health-gate (60s): circuit CLOSED → delete hibernating, write recovering (TTL 3600s)
  → Telegram notification enqueued: "Worker woke, beginning drip resume"
  → session-resume-drip (30s): one paused → pending per tick (~2 sessions/min)
  → when paused list empty: delete recovering flag
```

### Technical Approach

- All functions in `agent/hibernation.py` are **synchronous** (run in executor via reflection scheduler) — no new async complexity
- Redis operations use `from popoto import redis as r` — project-keyed via `os.environ.get("VALOR_PROJECT_KEY", "default")`
- All functions `except Exception: logger.error(...)` and never raise — reflection tick survives any failure
- Hibernation flag TTL 600s (10min): auto-expires if reflection stops running, unblocking worker automatically
- Guard in `worker_health_gate()`: `cb = get_health().get("anthropic"); if cb is None: return` — handles cold-start / test environments
- Use `cb.state == CircuitState.CLOSED` (explicit equality, import from `bridge.resilience`) to clear hibernation flag — NOT `!= OPEN` which would incorrectly fire during `HALF_OPEN`
- On first-write detection in `_worker_loop()` catch: `was_hibernating = r.exists(key)` before `r.set(key, "1", ex=600)`. If `not was_hibernating`, enqueue notification.
- `valor-session list --status paused` works without changes — the status index is updated correctly by `transition_status()`.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `worker_health_gate()` must `except Exception: logger.error(...)` — never crash the reflection tick
- [ ] `session_resume_drip()` must `except Exception` around `transition_status()` call — session may be deleted concurrently
- [ ] Redis `GET` in `_pop_agent_session()` must catch `redis.RedisError` and **fail open** (log warning, proceed with pop) — Redis down must not block session execution
- [ ] `transition_status(session, "paused")` in `_worker_loop()` catch: if it raises, log error and fall back to current behavior (leave session in `running`); do not crash worker

### Empty/Invalid Input Handling

- [ ] `session_resume_drip()` with zero `paused` sessions: deletes `recovering` flag and returns — no crash
- [ ] `worker_health_gate()` with no registered Anthropic circuit (`cb is None`): returns immediately — no `AttributeError`
- [ ] `_pop_agent_session()` when hibernating flag has unexpected value (non-"1"): any truthy value blocks pop — treat as hibernating

### Error State Rendering

- [ ] Telegram notification on hibernation: must reach the ops channel with failure reason and session count
- [ ] If notification enqueue fails (e.g., worker is fully down), log error but do not prevent status transition or flag write

## Test Impact

- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: add test that `_pop_agent_session()` returns `None` when `worker:hibernating` flag set; add test flag absent → proceeds normally
- [ ] `tests/unit/test_session_lifecycle.py` (if exists) or `tests/unit/test_hibernation.py` — UPDATE/CREATE: assert `"paused"` in `NON_TERMINAL_STATUSES`; assert `transition_status(session, "paused")` succeeds
- [ ] `tests/unit/test_hibernation.py` — CREATE: `worker_health_gate()` circuit OPEN → writes flag; circuit CLOSED → clears flag + writes `recovering`; `cb is None` → no-op; `session_resume_drip()` one `paused` session → `pending`; empty list → clears `recovering`
- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: add test that `CircuitOpenError` in worker loop transitions session to `paused` (not left in `running`)

## Rabbit Holes

- **Merging `paused` and `paused_circuit` into one status**: They have different semantics. Keep separate.
- **Persistent (no-TTL) hibernation flag**: If the reflection stops, the queue is permanently blocked. TTL is the correct safety valve.
- **Per-session retry budgets**: Tracking how many times a session has been `paused` and abandoning after N attempts is a separate concern.
- **Dashboard widgets for hibernation state**: Dashboard can read Redis — defer to a follow-up.
- **Merging the two Redis dequeue flags** (`queue_paused` from #773 + `hibernating` from #839): Would require coordinating two independent plans. Keep them additive — two independent OR checks in `_pop_agent_session()`.
- **Auth error detection improvements**: `_AUTH_ERROR_PATTERNS` list is sufficient for current needs. Not in scope.

## Risks

### Risk 1: `paused` sessions accumulate if both reflections stop

**Impact:** If `worker-health-gate` stops running (bridge restart), the `hibernating` flag expires after 10min (worker resumes popping). But if `session-resume-drip` also stopped before draining the `paused` list, sessions are stuck until the next restart brings the reflection back.

**Mitigation:** `session-resume-drip` activates on `recovering` flag (TTL 3600s), which is set when `worker-health-gate` detects circuit recovery. After bridge restart, the next `worker-health-gate` tick will re-evaluate. Acceptable: sessions are not lost, only delayed.

### Risk 2: Race between startup recovery and `paused` transition

**Impact:** A session left in `running` (process crashed before `transition_status("paused")`) is re-queued to `pending` by startup recovery instead of staying `paused`.

**Mitigation:** The `transition_status("paused")` call is the first action in the catch block, before `break`. Process crash between the two lines means session stays `running` — startup recovery handles it as an interrupted running session (re-queues to `pending`). This is acceptable degraded behavior; the session will be retried rather than lost.

### Risk 3: Telegram notification failure blocks flag write

**Impact:** If enqueuing the notification raises an exception, it could prevent the hibernation flag from being written.

**Mitigation:** Notification enqueue is wrapped in `try/except` and called after the flag write, not before. Flag write always happens first.

### Risk 4: Two dequeue flags cause debugging confusion

**Impact:** Operators see `queue_paused` (from #773) AND `hibernating` (from #839) in Redis, unsure which is blocking.

**Mitigation:** Distinct log messages per flag: `"[pop] Hibernating (worker:hibernating)"` vs `"[pop] Queue paused (sustainability:queue_paused)"`. Document both in feature docs.

## Race Conditions

### Race 1: Two workers read `hibernating = None` before either writes it

**Location:** `agent/agent_session_queue.py` — `_pop_agent_session()` top
**Trigger:** Two workers wake simultaneously; both check the flag before the `_worker_loop()` catch block writes it
**Data prerequisite:** Flag written by catch block or health-gate before workers check it
**State prerequisite:** Flag value consistent across all Redis-sharing workers
**Mitigation:** Redis `GET` is atomic. Workers are readers of the flag; only `_worker_loop()` catch and `worker_health_gate()` write it. No race in the read path.

### Race 2: `session-resume-drip` transitions a session being deleted by cleanup

**Location:** `agent/hibernation.py` `session_resume_drip()`
**Trigger:** Concurrent cleanup deletes session while drip calls `transition_status("pending")`
**Mitigation:** `transition_status()` uses Popoto's atomic save; if session deleted, save fails silently. Wrap `transition_status()` in `try/except` in `session_resume_drip()`.

### Race 3: Worker writes `hibernating` while health gate concurrently clears it

**Location:** `agent/hibernation.py` `worker_health_gate()` + `_worker_loop()` catch
**Trigger:** Health gate runs during circuit recovery at the exact moment worker writes the flag
**Mitigation:** Both are single atomic Redis operations. Worst case: flag is re-written on next failure and cleared on next health gate tick (60s). Not harmful.

## No-Gos (Out of Scope)

- Session-count throttling (belongs to #773)
- Failure fingerprint deduplication / GitHub issue auto-creation (belongs to #773)
- Daily digest Telegram message (belongs to #773)
- Per-session retry budgets
- Dashboard widgets for hibernation state
- Merging `paused` and `paused_circuit` statuses
- Merging `hibernating` and `queue_paused` Redis flags
- Auth error pattern improvements (`_AUTH_ERROR_PATTERNS`)

## Update System

No update script changes required. New reflection entries in `config/reflections.yaml` are picked up on next bridge restart automatically. `agent/hibernation.py` ships with the code.

New env vars: none. TTL (600s) and drip interval (30s) are hardcoded to sensible defaults; no per-machine configuration needed.

## Agent Integration

No new MCP server required. Hibernation functions run inside the bridge process via `ReflectionScheduler`.

Telegram notifications are enqueued as sessions via the existing session queue — no new bridge wiring needed.

`valor-session list --status paused` works immediately once `paused` is added to `NON_TERMINAL_STATUSES`. No scheduler or list-command changes needed.

No changes to `.mcp.json`.

## Documentation

- [ ] Create `docs/features/worker-hibernation.md` describing: hibernation flow, Redis key schema (`worker:hibernating`, `worker:recovering`), `paused` status semantics, relationship to #773 (`paused_circuit` / `queue_paused`), drip resume rate
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add inline docstrings to all public functions in `agent/hibernation.py`

## Success Criteria

- [ ] `"paused"` in `NON_TERMINAL_STATUSES`: `python -c "from models.session_lifecycle import NON_TERMINAL_STATUSES; assert 'paused' in NON_TERMINAL_STATUSES"`
- [ ] `transition_status(session, "paused")` succeeds without `ValueError` (unit test)
- [ ] `_pop_agent_session()` returns `None` when `{project_key}:worker:hibernating` is set (unit test)
- [ ] `CircuitOpenError` in `_worker_loop()` transitions session to `paused`, not left in `running` (unit test)
- [ ] `worker_health_gate()` with circuit `CLOSED` clears `hibernating` and writes `recovering` (unit test)
- [ ] `session_resume_drip()` with one `paused` session transitions it to `pending`; with empty list clears `recovering` (unit test)
- [ ] Both reflections declared in `config/reflections.yaml`, enabled and valid: `python -c "from agent.reflection_scheduler import load_registry; r=load_registry(); assert any(e.name=='worker-health-gate' for e in r)"`
- [ ] `valor-session list --status paused` returns without error
- [ ] Telegram notification sent on hibernation entry and on wake
- [ ] Tests pass (`/do-test`)
- [ ] Documentation created (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (lifecycle)**
  - Name: lifecycle-builder
  - Role: Add `paused` to `NON_TERMINAL_STATUSES`; update `_worker_loop()` catch to transition to `paused` and write hibernation flag; add hibernation flag guard to `_pop_agent_session()` top
  - Agent Type: builder
  - Resume: true

- **Builder (hibernation-module)**
  - Name: hibernation-builder
  - Role: Create `agent/hibernation.py` with `worker_health_gate`, `session_resume_drip`, and `send_hibernation_notification`
  - Agent Type: async-specialist
  - Resume: true

- **Builder (reflections-yaml)**
  - Name: reflections-yaml-builder
  - Role: Add `worker-health-gate` and `session-resume-drip` entries to `config/reflections.yaml`
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: hibernation-tester
  - Role: Write unit tests covering: hibernation flag blocks pop, drip resumes one per tick, `CircuitOpenError` → `paused` transition, `paused` in `NON_TERMINAL_STATUSES`
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: hibernation-documentarian
  - Role: Write `docs/features/worker-hibernation.md` and update `docs/features/README.md`
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: hibernation-validator
  - Role: Verify all success criteria, run tests, confirm YAML entries load cleanly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add `paused` status and lifecycle changes
- **Task ID**: build-lifecycle
- **Depends On**: none
- **Validates**: `tests/unit/test_session_lifecycle.py` (create or update), `tests/unit/test_agent_session_queue.py` (update)
- **Informed By**: spike-2 (`NON_TERMINAL_STATUSES` only change needed), spike-1 (`_worker_loop()` catch at line 2060 is the fix site), spike-3 (startup recovery not affected), spike-4 (both flags checked with OR logic)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `"paused"` to `NON_TERMINAL_STATUSES` in `models/session_lifecycle.py`
- In `agent/agent_session_queue.py` `_worker_loop()` catch block (line 2060): before `session_completed = True; break`, add: `try: transition_status(session, "paused", "circuit open — hibernating"); except Exception as e: logger.error(...)`. Then write hibernation flag: `was_hibernating = r.exists(f"{pk}:worker:hibernating"); r.set(f"{pk}:worker:hibernating", "1", ex=600)`. If `not was_hibernating`, call `send_hibernation_notification()`.
- Add at top of `_pop_agent_session()` (before `_acquire_pop_lock()`): `if r.get(f"{pk}:worker:hibernating"): logger.debug("[pop] Hibernating — skipping pop"); return None`. Also add check for `{pk}:sustainability:queue_paused` (from #773, if that key exists in production) with same OR logic.
- Import `from popoto import redis as r` at top of `agent_session_queue.py` if not already present; `pk = os.environ.get("VALOR_PROJECT_KEY", "default")`
- Check whether #773 has already shipped (`queue_paused` key pattern present) and add the additive guard accordingly

### 2. Create `agent/hibernation.py`
- **Task ID**: build-hibernation-module
- **Depends On**: build-lifecycle (`paused` status must exist before `transition_status("paused")` can be called)
- **Validates**: `tests/unit/test_hibernation.py` (create)
- **Informed By**: spike-4 (flag naming, #773 coordination pattern), spike-1 (circuit check pattern from sustainable_self_healing.md)
- **Assigned To**: hibernation-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- Create `agent/hibernation.py` with:
  - `worker_health_gate()`: guard `cb = get_health().get("anthropic"); if cb is None: return`. If OPEN or HALF_OPEN: write/renew `hibernating` (ex=600). If `CircuitState.CLOSED`: delete `hibernating`, write `recovering` (ex=3600), call `send_hibernation_notification("waking", count)`. Wrap all in `except Exception: logger.error(...)`
  - `session_resume_drip()`: read `recovering` — if absent, return. Query `AgentSession.query.filter(status="paused")`, sort oldest first, pop one, `try: transition_status(session, "pending", "drip resume"); except Exception: logger.error(...)`. When list empty, delete `recovering`. Wrap all in `except Exception: logger.error(...)`
  - `send_hibernation_notification(event, count)`: enqueue a lightweight session with pre-composed message string. Wrap in `try/except` — never raises.
- Use `CircuitState.CLOSED` (imported from `bridge.resilience`) for closed-state check — not `!= OPEN`
- Use `cb.state == CircuitState.CLOSED` pattern explicitly

### 3. Register reflections in YAML
- **Task ID**: build-reflections-yaml
- **Depends On**: build-hibernation-module
- **Validates**: `python -c "from agent.reflection_scheduler import load_registry; r=load_registry(); assert any(e.name=='worker-health-gate' for e in r)"`
- **Assigned To**: reflections-yaml-builder
- **Agent Type**: builder
- **Parallel**: false
- Add to `config/reflections.yaml`:
  - `worker-health-gate`: interval 60, priority high, execution_type function, callable `agent.hibernation.worker_health_gate`, enabled true
  - `session-resume-drip`: interval 30, priority high, execution_type function, callable `agent.hibernation.session_resume_drip`, enabled true

### 4. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-lifecycle, build-hibernation-module
- **Validates**: `tests/unit/test_hibernation.py`, `tests/unit/test_agent_session_queue.py`, `tests/unit/test_session_lifecycle.py`
- **Assigned To**: hibernation-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- `tests/unit/test_session_lifecycle.py`: assert `"paused" in NON_TERMINAL_STATUSES`
- `tests/unit/test_agent_session_queue.py`: `_pop_agent_session()` returns `None` when `hibernating` set; returns session when flag absent; `CircuitOpenError` in `_worker_loop()` catch leaves session in `paused` (not `running`)
- `tests/unit/test_hibernation.py` (create): `worker_health_gate()` with circuit OPEN → writes flag; CLOSED → clears + writes `recovering`; `cb is None` → no-op; `session_resume_drip()` one `paused` → `pending`; empty list → clears `recovering`
- Use `fakeredis` or test Redis; never touch production data

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-lifecycle, build-hibernation-module, build-reflections-yaml
- **Assigned To**: hibernation-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/worker-hibernation.md` covering: hibernation flow, Redis key schema, `paused` status semantics, relationship to #773, recovery drip rate
- Add entry to `docs/features/README.md` index table

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: hibernation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_hibernation.py tests/unit/test_agent_session_queue.py tests/unit/test_session_lifecycle.py -v`
- Check `paused` in statuses: `python -c "from models.session_lifecycle import NON_TERMINAL_STATUSES; assert 'paused' in NON_TERMINAL_STATUSES"`
- Check reflections: `python -c "from agent.reflection_scheduler import load_registry; r=load_registry(); assert any(e.name=='worker-health-gate' for e in r)"`
- Check imports: `python -c "from agent.hibernation import worker_health_gate, session_resume_drip; print('ok')"`
- Run `python -m ruff check agent/hibernation.py` — exit 0
- Report pass/fail for all success criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_hibernation.py tests/unit/test_agent_session_queue.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/hibernation.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/hibernation.py` | exit code 0 |
| `paused` in statuses | `python -c "from models.session_lifecycle import NON_TERMINAL_STATUSES; assert 'paused' in NON_TERMINAL_STATUSES"` | exit code 0 |
| Reflections load | `python -c "from agent.reflection_scheduler import load_registry; r=load_registry(); assert any(e.name=='worker-health-gate' for e in r)"` | exit code 0 |
| Module imports | `python -c "from agent.hibernation import worker_health_gate, session_resume_drip"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **#773 ship order**: Should this plan wait for #773 to merge? If #773 ships first, Task 1 must check whether `api-health-gate` and `queue_paused` already exist and confirm the additive OR logic. If #839 ships first, #773's queue guard task adopts the same OR pattern. Both orderings work — but the builder must check for existing keys/reflections before adding new ones.

2. **Telegram notification delivery**: Should hibernation/wake notifications be enqueued as agent sessions (depends on worker being partially up, adds latency) or sent via a direct Redis pub/sub write that the bridge picks up independently? Direct bridge notification would be more reliable when the worker is quiescing. This is a design decision that could affect notification architecture.
