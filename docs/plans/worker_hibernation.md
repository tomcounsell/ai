---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/839
last_comment_id: none
---

# Worker Hibernation: Pause Sessions and Quiesce Queue on Transient API/Auth Failures

## Problem

When external dependencies fail temporarily — Anthropic API downtime, auth token expiration, credit exhaustion — the system has no graceful pause mechanism. Sessions hit API errors mid-execution and are marked `failed`. The health check requeues them. The worker pops them again. They fail again. This is a tight failure loop with no circuit gate at the dequeue level and no safe suspend-and-resume path for in-flight sessions.

**Current behavior:** API down → worker pops session → mid-turn failure → session marked `failed` → health check requeues → repeat. No quiesce, no pause, no controlled wake.

**Desired outcome:** API down → health-check reflection detects failure → worker enters hibernation (queue pop blocked by Redis flag) → in-flight sessions transition to `paused` (not `failed`) → next health-check tick confirms recovery → sessions resume one-at-a-time via drip reflection.

## Prior Art

No prior issues or PRs found for worker hibernation or mid-execution session pause.

Related: `docs/plans/sustainable_self_healing.md` (tracking issue #773) — plans `paused_circuit` status for circuit-based queue pause at dequeue time. This issue (#839) is complementary: it handles mid-execution session-level pause and worker hibernation. The two statuses coexist without conflict. #839 can ship independently; #773 can reference it as a completed sub-item.

## Data Flow

**Failure path:**
1. **Entry**: `sdk_client.py` raises `AuthenticationError` or detects API unavailability mid-execution
2. **Queue exception handler** (`agent_session_queue.py`): catches the error, calls `transition_status(session, "paused")` instead of `finalize_session(session, "failed")`
3. **Session state**: session moves to `paused` in Redis; context (branch, plan URL, steering queue) preserved
4. **Worker loop**: session is not requeued; worker continues to next session

**Hibernation path:**
1. **Entry**: `worker_health_gate` reflection fires on 60s tick
2. **Detection**: checks circuit state (OPEN) or auth error in recent logs
3. **Flag write**: writes `{project_key}:worker:hibernating = "1"` (TTL 10min) to POPOTO_REDIS_DB
4. **Guard**: `_pop_agent_session()` reads flag first; returns `None` if set — no sessions dequeued
5. **Notification**: Telegram message sent on first hibernation entry

**Recovery path:**
1. **Entry**: `worker_health_gate` reflection fires; circuit now CLOSED, auth succeeds
2. **Flag swap**: clears `hibernating` flag, writes `{project_key}:worker:recovering = "1"` (TTL 30min)
3. **Notification**: Telegram wake message sent
4. **Drip**: `session_resume_drip` reflection (30s tick) pops one `paused` session → `pending` per tick
5. **Drain**: when `paused` list is empty, `recovering` flag is cleared

## Architectural Impact

- **New status value**: `paused` added to `NON_TERMINAL_STATUSES` in `models/session_lifecycle.py` — enables `transition_status(session, "paused")` calls without ValueError
- **New Redis flags**: `{project_key}:worker:hibernating` and `{project_key}:worker:recovering` — plain Redis keys (not Popoto models), scoped by project key, with TTL
- **New reflections**: two new entries in `config/reflections.yaml` — `worker-health-gate` (60s) and `session-resume-drip` (30s)
- **New callables**: two new functions in a new module `agent/worker_hibernation.py` (or inline in `agent/agent_session_queue.py`)
- **Modified**: `_pop_agent_session()` gets a one-line guard at the top; exception handler in worker loop catches auth errors and transitions to `paused`
- **Coupling**: low — hibernation flag is a Redis key readable by any component; no new cross-module dependencies

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on `paused` vs `paused_circuit` boundary with #773)
- Review rounds: 1

## Prerequisites

No prerequisites — this work uses existing infrastructure (POPOTO_REDIS_DB, ReflectionScheduler, transition_status, CircuitBreaker).

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Redis must be available for flag reads/writes |

## Solution

### Key Elements

- **`paused` status**: New non-terminal status in `models/session_lifecycle.py` — semantics: session interrupted by transient external failure, waiting for conditions to improve. Distinct from `dormant` (awaits human) and `paused_circuit` (circuit-level queue pause from #773).
- **Auth error catch**: In `agent_session_queue.py` worker exception handler, catch auth errors (`_is_auth_error`) and `CircuitOpenError`; call `transition_status(session, "paused")` instead of `finalize_session(session, "failed")`.
- **Hibernation flag**: Redis key `{project_key}:worker:hibernating` (TTL 10min, renewed each tick while unhealthy). Checked at the top of `_pop_agent_session()` — returns `None` when set.
- **`worker_health_gate` reflection**: 60s tick function. Detects failure (circuit OPEN or recent auth error), writes/renews hibernation flag. On recovery: clears flag, writes `recovering` flag. Sends Telegram on state change.
- **`session_resume_drip` reflection**: 30s tick function. Active only when `recovering` flag set. Pops one `paused` session per tick, transitions to `pending`. Clears `recovering` when list drains.

### Flow

API error detected → session transitions to `paused` → worker_health_gate writes hibernation flag → `_pop_agent_session()` returns None (queue quiesced) → Telegram: "hibernating" → health-gate confirms recovery → recovering flag written → Telegram: "awake" → session_resume_drip ticks every 30s: one paused session → pending → queue resumes normal operation

### Technical Approach

- Add `"paused"` to `NON_TERMINAL_STATUSES` frozenset in `models/session_lifecycle.py`
- In `_pop_agent_session()`: add guard at line 555 (before `_acquire_pop_lock`): read `{project_key}:worker:hibernating` from POPOTO_REDIS_DB; return `None` if set
- Worker exception handler (around line 2056): extend the existing `CircuitOpenError` catch block to also catch auth-error exceptions; call `transition_status(session, "paused", reason="transient API failure")` instead of setting `session_failed = True`
- New module `agent/worker_hibernation.py` with two functions: `worker_health_gate()` and `session_resume_drip()` — keeps queue module size manageable
- Project key: read from `os.environ.get("VALOR_PROJECT_KEY", "valor")` — same pattern as `agent/memory_extraction.py`
- Telegram notification: use existing `send_telegram_message` or equivalent already used in the bridge/worker
- Two new YAML entries in `config/reflections.yaml` with `execution_type: function` and intervals 60 and 30 respectively

## Failure Path Test Strategy

### Exception Handling Coverage

- `worker_health_gate()` and `session_resume_drip()` must catch all exceptions internally and log (never crash the scheduler tick). Add test asserting that a Redis failure inside `worker_health_gate()` is caught and logged, not propagated.
- `_pop_agent_session()` hibernation flag read: wrap in try/except — Redis unavailability must not block the pop path (fail open, not closed).

### Empty/Invalid Input Handling

- `session_resume_drip()`: when no `paused` sessions exist, must clear `recovering` flag and return cleanly — no error, no loop.
- `worker_health_gate()`: when circuit state is indeterminate (e.g., circuit not yet initialized), must treat as healthy (fail open).

### Error State Rendering

- Telegram notifications on hibernation start/wake must not crash if the Telegram send fails — wrap in try/except.
- `valor-session list --status paused` must render without error when status index contains `paused` sessions.

## Test Impact

- [ ] `tests/unit/test_session_lifecycle_consolidation.py::TestStatusSets::test_non_terminal_statuses` — UPDATE: add `"paused"` to expected set
- [ ] `tests/unit/test_session_lifecycle_consolidation.py::TestStatusSets::test_eleven_total_statuses` — UPDATE: assert `len(ALL_STATUSES) == 12` (one new status added)
- [ ] `tests/unit/test_session_lifecycle_consolidation.py::TestStatusSets::test_all_statuses_is_union` — passes as-is (derived from union, no hardcoded count)
- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: add test for hibernation flag blocking `_pop_agent_session()` (new unit test case)

New tests to create:
- `tests/unit/test_worker_hibernation.py`: hibernation flag blocks pop, drip resumes one per tick, auth error → `paused` transition (not `failed`), recovery flag cleared when paused list drains

## Rabbit Holes

- **Persisting reason/context on the paused session**: tempting to add a `pause_reason` field to AgentSession — not worth it for this slice; the lifecycle log already captures the reason.
- **Backoff with jitter on drip rate**: starting with one-per-tick (30s) is sufficient; don't build a configurable drip rate governor yet.
- **Merging with #773's `paused_circuit` flow**: they are separate statuses with different triggers and resume paths. Keep them separate to avoid entangled state machines.
- **Detecting API recovery without a live call**: circuit state is sufficient; don't build a synthetic health-probe to Anthropic — it would consume quota during a quota issue.

## Risks

### Risk 1: `paused` sessions never resume if `recovering` flag expires before drip completes
**Impact:** Sessions stuck in `paused` indefinitely after a long outage with many paused sessions
**Mitigation:** `session_resume_drip` renews the `recovering` flag on each tick while paused sessions remain. If the flag expires (worker crash), the next `worker_health_gate` tick that detects a healthy circuit will re-enter the recovery path and re-set `recovering`.

### Risk 2: Auth error catch is too broad — non-transient auth errors (e.g., permanently invalid key) put sessions in `paused` forever
**Impact:** Sessions never resume; drip keeps transitioning them to pending where they fail again
**Mitigation:** Add a `paused_count` ceiling: if a session has been paused more than N times (tracked via lifecycle log or a counter field), fall through to `failed` on the next auth error. Start with N=3.

### Risk 3: Hibernation flag read in `_pop_agent_session()` adds Redis round-trip to every dequeue
**Impact:** Marginal latency on dequeue path
**Mitigation:** Flag read is a single `GET` call — sub-millisecond in practice. Acceptable trade-off. Can cache in-process with 5s TTL if profiling reveals an issue.

## Race Conditions

### Race 1: Two workers both check hibernation flag before either sets it
**Location:** `agent/worker_hibernation.py::worker_health_gate()`
**Trigger:** Two concurrent health-gate ticks check the flag simultaneously; neither sees it set; both attempt to write
**Data prerequisite:** Flag must be written atomically before first dequeue check
**State prerequisite:** Redis SET with NX semantics
**Mitigation:** Use `POPOTO_REDIS_DB.set(key, "1", nx=True, ex=TTL)` — only one writer succeeds; the other's write is a no-op. Idempotent outcome.

### Race 2: Session transitions to `paused` while drip concurrently pops it back to `pending`
**Location:** `agent/agent_session_queue.py` exception handler + `agent/worker_hibernation.py::session_resume_drip()`
**Trigger:** Worker pauses a session at the same moment drip is transitioning it to `pending`
**Data prerequisite:** Drip only touches sessions currently in `paused` status index
**State prerequisite:** `transition_status()` is idempotent for same-state transitions; Popoto index update is atomic per-session
**Mitigation:** `transition_status()` checks current status before writing; worst case session ends up in `pending` and gets picked up cleanly by the next pop.

## No-Gos (Out of Scope)

- Session-count throttle (belongs in #773)
- Failure fingerprinting / dedup GitHub issues (belongs in #773)
- Daily digest / operator summary (belongs in #773)
- Configurable drip rate or backoff algorithm
- `paused_count` circuit for permanently-invalid auth (can be added in a follow-up)
- Hibernation across multiple projects simultaneously (each project key is independent — this is already true by design)

## Update System

No update system changes required. The new reflection callables are internal Python functions registered in `config/reflections.yaml`. The YAML file is already tracked in git and deployed via the standard `git pull` update path. No new environment variables, dependencies, or config migration needed.

## Agent Integration

No agent integration required. Worker hibernation is a bridge-internal/worker-internal change. The `valor-session list --status paused` command works automatically once `paused` is a valid status — the CLI reads Popoto status indexes directly.

No MCP server changes needed. No `.mcp.json` changes needed.

## Documentation

- [ ] Create `docs/features/worker-hibernation.md` describing the hibernation lifecycle, Redis flags, status transitions, and recovery flow
- [ ] Add entry to `docs/features/README.md` index table for worker-hibernation
- [ ] Update `docs/features/reflections.md` to document the two new reflection entries (`worker-health-gate`, `session-resume-drip`)

## Success Criteria

- [ ] `paused` status added to `NON_TERMINAL_STATUSES`; `transition_status(session, "paused")` succeeds without ValueError
- [ ] Auth error or API-down condition mid-execution → session transitions to `paused` (not `failed`); worker moves on without requeuing
- [ ] `_pop_agent_session()` returns `None` when hibernation flag set in Redis
- [ ] `worker_health_gate` reflection (60s) writes/renews hibernation flag on failure; clears on confirmed recovery
- [ ] `session_resume_drip` reflection (30s) transitions one `paused` session to `pending` per tick when `recovering` flag is active; clears `recovering` when list drains
- [ ] Telegram notification sent on hibernation start and on wake
- [ ] `valor-session list --status paused` works
- [ ] Unit tests pass: hibernation flag blocks pop, drip resumes one per tick, auth error → `paused` transition
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hibernation-core)**
  - Name: hibernation-builder
  - Role: Implement `paused` status, hibernation flag guard, auth-error catch, and two reflection callables
  - Agent Type: builder
  - Resume: true

- **Validator (hibernation-core)**
  - Name: hibernation-validator
  - Role: Verify implementation meets all acceptance criteria; run unit tests
  - Agent Type: validator
  - Resume: true

- **Test Engineer (hibernation)**
  - Name: hibernation-test-engineer
  - Role: Write `tests/unit/test_worker_hibernation.py` and update affected existing tests
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: hibernation-documentarian
  - Role: Create feature docs and update reflections doc
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add `paused` status and update lifecycle module
- **Task ID**: build-status
- **Depends On**: none
- **Validates**: `tests/unit/test_session_lifecycle_consolidation.py`
- **Assigned To**: hibernation-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `"paused"` to `NON_TERMINAL_STATUSES` frozenset in `models/session_lifecycle.py`
- Verify `transition_status(session, "paused")` no longer raises ValueError

### 2. Add hibernation flag guard to `_pop_agent_session()`
- **Task ID**: build-pop-guard
- **Depends On**: build-status
- **Validates**: new unit test in `tests/unit/test_worker_hibernation.py`
- **Assigned To**: hibernation-builder
- **Agent Type**: builder
- **Parallel**: false
- At top of `_pop_agent_session()` (before `_acquire_pop_lock`): read `{project_key}:worker:hibernating` from POPOTO_REDIS_DB; return `None` if set
- Project key sourced from `os.environ.get("VALOR_PROJECT_KEY", "valor")`

### 3. Catch auth errors mid-execution and transition to `paused`
- **Task ID**: build-auth-catch
- **Depends On**: build-status
- **Validates**: new unit test asserting `paused` (not `failed`) on auth error
- **Assigned To**: hibernation-builder
- **Agent Type**: builder
- **Parallel**: true
- In worker loop exception handler (around line 2056 in `agent_session_queue.py`): extend `CircuitOpenError` block to also handle auth errors detected via `_is_auth_error(str(e))`
- Call `transition_status(session, "paused", reason="transient API failure — auth error")` and set `session_completed = True` (do not set `session_failed`)

### 4. Implement `worker_health_gate` and `session_resume_drip` callables
- **Task ID**: build-reflections
- **Depends On**: build-pop-guard, build-auth-catch
- **Validates**: new unit tests for both callables
- **Assigned To**: hibernation-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/worker_hibernation.py` with `worker_health_gate()` and `session_resume_drip()`
- `worker_health_gate()`: check circuit state; write/renew `hibernating` flag (TTL 600s) on failure; clear and write `recovering` on recovery; send Telegram on state change; all exceptions caught and logged
- `session_resume_drip()`: if `recovering` flag set, pop one `paused` session → `pending`; clear `recovering` when list is empty; all exceptions caught and logged
- Add two entries to `config/reflections.yaml`: `worker-health-gate` (interval: 60) and `session-resume-drip` (interval: 30)

### 5. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-reflections
- **Validates**: `tests/unit/test_worker_hibernation.py`, updated `test_session_lifecycle_consolidation.py`
- **Assigned To**: hibernation-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_worker_hibernation.py`: hibernation flag blocks pop, drip resumes one per tick, auth error → `paused`, recovering flag cleared on drain
- Update `tests/unit/test_session_lifecycle_consolidation.py`: add `"paused"` to expected NON_TERMINAL set; update count to 12

### 6. Validate implementation
- **Task ID**: validate-core
- **Depends On**: build-tests
- **Assigned To**: hibernation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_worker_hibernation.py tests/unit/test_session_lifecycle_consolidation.py -v`
- Verify all success criteria are met
- Verify no regressions in `test_agent_session_queue.py` and `test_circuit_breaker.py`

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-core
- **Assigned To**: hibernation-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/worker-hibernation.md`
- Add entry to `docs/features/README.md`
- Update `docs/features/reflections.md` with new reflection entries

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hibernation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q`
- Run `python -m ruff check . && python -m ruff format --check .`
- Verify all documentation files exist on disk
- Generate final pass/fail report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `paused` in NON_TERMINAL | `python -c "from models.session_lifecycle import NON_TERMINAL_STATUSES; assert 'paused' in NON_TERMINAL_STATUSES"` | exit code 0 |
| Hibernation module exists | `python -c "import agent.worker_hibernation"` | exit code 0 |
| Reflection entries present | `python -c "import yaml; r=yaml.safe_load(open('config/reflections.yaml')); names=[x['name'] for x in r['reflections']]; assert 'worker-health-gate' in names and 'session-resume-drip' in names"` | exit code 0 |
| Feature doc exists | `test -f docs/features/worker-hibernation.md` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Open Questions

None — recon and issue analysis resolved all unknowns before planning.
