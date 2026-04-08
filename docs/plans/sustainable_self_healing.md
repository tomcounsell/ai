---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-04-08
tracking: https://github.com/tomcounsell/ai/issues/773
last_comment_id: none
---

# Sustainable Self-Healing: CircuitBreaker + Queue Governance

## Problem

The system runs 24/7 but has no mechanism to pause or govern session execution when external dependencies fail. When the Anthropic API goes down, the worker keeps dequeuing sessions; each one fails, gets marked `failed` or `abandoned`, and the health check eventually requeues them — creating an unbounded failure loop with no token budget protection. There is no way to know how much was spent in a single day, no deduplication of "same root cause" GitHub issues, and no automated health summary for the human operator.

**Current behavior:** Anthropic API down → worker pops session → session fails mid-execution → health check requeues → repeat. Dozens of failures from the same root cause. No cap on session starts. No daily summary. Same error pattern files multiple GitHub issues.

**Desired outcome:** The system governs itself. When Anthropic circuit is OPEN, `_pop_agent_session()` returns `None` immediately. When it recovers, sessions resume at a controlled drip rate. A session-count throttle prevents runaway execution. Repeated same-fingerprint failures produce exactly one GitHub issue. A daily Telegram message keeps the human informed.

## Prior Art

- **PR #502** — Introduced `CircuitBreaker` and `DependencyHealth` for bridge resilience. Successfully ships. This plan builds on those primitives, extending them to govern the session queue — which PR #502 did not touch.
- No closed issues found for queue pause, token budget, or failure loop detection.

## Spike Results

Recon was conducted via direct code read (no prototypes needed).

### spike-1: Does AgentSession or session snapshots carry token counts?
- **Assumption**: "Token usage could be read from session logs or AgentSession fields"
- **Method**: code-read
- **Finding**: Confirmed absent. `logs/sessions/*/complete.json` contains only `session_id`, `event`, `timestamp`, `project_key`, `branch_name`, `messages`, `task_summary`, `git_status`, `extra_context`. No token fields on AgentSession model either. The Claude Agent SDK does not surface per-session token usage in the current integration.
- **Confidence**: high
- **Impact on plan**: Token budget monitoring scoped to session-count throttling (sessions per hour) instead of actual token spend. No new SDK instrumentation required.

### spike-2: Where is the correct pause insertion point in the dequeue path?
- **Assumption**: "The pause check belongs inside `_pop_agent_session()`"
- **Method**: code-read
- **Finding**: Confirmed. `_pop_agent_session()` is the sole dequeue path called by `_worker_loop()`. The pause check must be the very first thing inside `_pop_agent_session()` — before the `_acquire_pop_lock()` call — returning `None` to allow the caller to release the semaphore safely without ever popping a session.
- **Confidence**: high
- **Impact on plan**: Pause check is a one-liner at the top of `_pop_agent_session()`. No changes to `_worker_loop()` needed.

### spike-3: Does ExistenceFilter support standalone/namespaced use?
- **Assumption**: "ExistenceFilter from Popoto could be used as a keyed dedup store"
- **Method**: code-read
- **Finding**: ExistenceFilter is a model-level field (`popoto.fields.existence_filter.ExistenceFilter`), not a standalone class. It can only be attached to a Popoto Model as a field. It does not support namespaced standalone usage.
- **Confidence**: high
- **Impact on plan**: Failure-loop dedup uses a plain Redis set keyed by `{project_key}:failure_loop:seen_fingerprints` with a TTL of 7 days. This is simpler and does not require a new Model.

### spike-4: How does recovery-drip self-disable without file writes?
- **Assumption**: "A reflection can write to reflections.yaml to set enabled: false"
- **Method**: code-read
- **Finding**: File writes to `config/reflections.yaml` would be racy and affect all instances. The scheduler reloads on every tick from in-memory entries only (no dynamic reload of YAML at runtime). Correct approach: use a Redis flag `{project_key}:recovery:active`. `recovery-drip` no-ops when flag is absent — functionally equivalent to being disabled.
- **Confidence**: high
- **Impact on plan**: `recovery-drip` checks for presence of `{project_key}:recovery:active` at the start of each tick and returns early if absent. The `api-health-gate` sets this flag on circuit close; `recovery-drip` deletes it when the paused list drains.

### spike-5: What Redis key prefix to use for queue flags?
- **Assumption**: "Project-scoped keys already have a standard prefix pattern"
- **Method**: code-read
- **Finding**: `VALOR_PROJECT_KEY` env var (default `"default"`) is the standard project scope prefix used throughout the codebase (memory, session keys). All new sustainability flags should use `{project_key}:sustainability:*` as prefix.
- **Confidence**: high
- **Impact on plan**: All Redis keys in `agent/sustainability.py` prefixed with `{project_key}:sustainability:*`.

## Data Flow

### Queue Pause (api-health-gate → _pop_agent_session)

1. **ReflectionScheduler tick (60s)**: calls `api_health_gate()` in `agent/sustainability.py`
2. **`api_health_gate()`**: reads `DependencyHealth.get("anthropic").state` from `bridge/health.py`
3. **If OPEN**: writes `{project_key}:sustainability:queue_paused = "1"` to Redis (TTL 3600s)
4. **If CLOSED (was previously OPEN)**: deletes `queue_paused`, sets `{project_key}:recovery:active = "1"` (TTL 3600s)
5. **`_pop_agent_session()`**: reads `queue_paused` flag as first action; if set, returns `None`
6. **`_worker_loop()`**: gets `None`, releases semaphore, waits for event — no session wasted

### Recovery Drip (recovery-drip → pending queue)

1. **ReflectionScheduler tick (30s)**: calls `recovery_drip()` in `agent/sustainability.py`
2. **`recovery_drip()`**: checks `{project_key}:recovery:active` — if absent, returns immediately
3. **If active**: calls `AgentSession.query.filter(status="paused_circuit")` — sessions paused by `api-health-gate` are marked `paused_circuit`
4. **Pops one session**: sets `status = "pending"` via `transition_status()`
5. **When list empty**: deletes `{project_key}:recovery:active`

### Session-Count Throttle (session-count-throttle → _pop_agent_session)

1. **ReflectionScheduler tick (1hr)**: calls `session_count_throttle()` in `agent/sustainability.py`
2. Scans `AgentSession` records with `started_at` in the last hour for this `project_key`
3. Writes throttle level to `{project_key}:sustainability:throttle_level` (none/moderate/suspended)
4. **`_pop_agent_session()`**: reads throttle level; if `suspended`, low-priority sessions (normal/low) return `None`

### Failure-Loop Detection (failure-loop-detector → GitHub)

1. **ReflectionScheduler tick (1hr)**: calls `failure_loop_detector()` in `agent/sustainability.py`
2. Loads recently-failed `AgentSession` records (last 4 hours, `project_key`)
3. Clusters by error fingerprint (HTTP status + first 80 chars of error message)
4. For each cluster with ≥ 3 failures:
   - Checks `{project_key}:sustainability:seen_fingerprints` Redis set — skip if fingerprint present
   - Calls `gh issue create` with fingerprint + session IDs
   - Adds fingerprint to set (SADD), sets TTL 7 days if new
   - Marks affected sessions with `loop_detected=True` on `extra_context`

### Daily Digest (sustainability-digest → Telegram)

1. **ReflectionScheduler tick (86400s)**: enqueues a Claude agent session with command prompt
2. Agent session reads: circuit breaker states, throttle level, session counts, failure clusters
3. Agent composes a Telegram summary and sends via existing bridge path

## Architectural Impact

- **New module**: `agent/sustainability.py` — five standalone functions registered as reflections
- **Modified**: `agent/agent_session_queue.py` — two new Redis reads at the top of `_pop_agent_session()` (pause flag + throttle level)
- **New status value**: `paused_circuit` on `AgentSession.status` — sessions paused due to open circuit
- **New Redis keys**: all under `{project_key}:sustainability:*` namespace
- **No new external dependencies**: Redis only, no new Python packages
- **Coupling**: `sustainability.py` imports from `bridge/health.py` (already used in `agent/sdk_client.py`) — minimal new coupling
- **Reversibility**: Removing the two Redis reads from `_pop_agent_session()` and deleting `agent/sustainability.py` fully reverts to today's behavior

## Appetite

**Size:** Large

**Team:** Solo dev, async-specialist

**Interactions:**
- PM check-ins: 1-2
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `GH_TOKEN` or `gh` auth | `gh auth status` | filing GitHub issues from failure-loop-detector |
| Redis running | `redis-cli ping` | sustainability flags stored in Redis |
| `bridge/health.py` registered circuit | `python -c "from bridge.health import get_health; print(get_health().summary())"` | circuit state readable |

## Solution

### Key Elements

- **`agent/sustainability.py`**: Five functions, each registered in `config/reflections.yaml`. No scheduler wiring needed beyond YAML entries.
- **`api-health-gate` (60s)**: Reads Anthropic circuit state. Writes/clears `queue_paused` flag. Sets `recovery:active` on circuit close.
- **`session-count-throttle` (1hr)**: Counts sessions started in last hour. Writes throttle level. Shed strategy: `suspended` blocks normal+low; `moderate` blocks low only.
- **`failure-loop-detector` (1hr)**: Scans failed sessions by error fingerprint. Files one GitHub issue per novel fingerprint cluster (≥3 failures). Uses Redis set for dedup.
- **`recovery-drip` (30s)**: Only active when `recovery:active` flag set. Drips one `paused_circuit` session back to `pending` per tick.
- **`sustainability-digest` (86400s, agent)**: Daily Claude session that reads system state and posts Telegram summary.
- **`_pop_agent_session()` guard**: Two Redis reads at the top — pause flag check and throttle check — returning `None` before any session query if blocked.

### Flow

```
Anthropic API down
  → CircuitBreaker OPEN
  → api-health-gate tick: writes queue_paused flag
  → _pop_agent_session: reads flag, returns None
  → worker waits for event (no sessions consumed)

Anthropic API recovers
  → CircuitBreaker CLOSED
  → api-health-gate tick: clears queue_paused, sets recovery:active
  → recovery-drip ticks every 30s: one paused_circuit session → pending per tick
  → sessions resume at ≤1 per 30s

Session runaway (>N sessions/hr)
  → session-count-throttle writes throttle_level=suspended
  → _pop_agent_session: reads throttle, skips normal/low sessions
  → urgent/high sessions continue; others wait for next throttle check

Same error 3+ times
  → failure-loop-detector: fingerprint cluster found
  → checks Redis set: fingerprint not seen
  → gh issue create → fingerprint added to set
  → affected sessions marked loop_detected=True
```

### Technical Approach

- All five functions in `agent/sustainability.py` are **synchronous** (run in executor via `asyncio.get_running_loop().run_in_executor(None, func)` inside the scheduler) — no new async complexity
- Redis operations use the existing `popoto` Redis connection (`from popoto import redis`) — project-keyed via `os.environ.get("VALOR_PROJECT_KEY", "default")`
- `_pop_agent_session()` guard uses `r.get(f"{project_key}:sustainability:queue_paused")` — one read, no lock needed
- `paused_circuit` status: added to `TERMINAL_STATUSES`? No — it must be recoverable. Add to a new `PAUSABLE_STATUSES` set that the health check skips (so it doesn't requeue paused sessions)
- Error fingerprint: `f"{http_status}:{error_message[:80]}"` hashed with `hashlib.sha256().hexdigest()[:16]`
- Throttle thresholds: configurable via env vars `SUSTAINABILITY_THROTTLE_MODERATE` (default 20 sessions/hr) and `SUSTAINABILITY_THROTTLE_SUSPENDED` (default 40 sessions/hr)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] All five sustainability functions must `except Exception` and log — never crash the reflection tick
- [ ] Redis connection errors in `_pop_agent_session()` guard must log and allow session to proceed (fail open, not fail closed)
- [ ] `gh issue create` failure in failure-loop-detector must log and continue — does not re-raise

### Empty/Invalid Input Handling
- [ ] `failure_loop_detector()` with zero failed sessions: no-op, no crash
- [ ] `recovery_drip()` with empty `paused_circuit` list: clears `recovery:active` flag and returns
- [ ] `session_count_throttle()` with zero recent sessions: writes `none` throttle level

### Error State Rendering
- [ ] When `queue_paused` is set, `_pop_agent_session()` logs a debug message at every skip
- [ ] When throttle level is `suspended`, `_pop_agent_session()` logs which session was skipped and why

## Test Impact

- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: add test that `_pop_agent_session()` returns `None` when `queue_paused` flag is set in Redis; add test that throttle `suspended` blocks normal/low sessions
- [ ] `tests/integration/test_session_queue_integration.py` — UPDATE if exists, else create: end-to-end test that a paused queue correctly drips sessions back on recovery
- [ ] `tests/unit/test_agent_session_health_check.py` — UPDATE: verify health check skips `paused_circuit` sessions (does not requeue them)
- No existing reflection tests exist — new `tests/unit/test_sustainability.py` must be created

## Rabbit Holes

- **Actual token tracking via Anthropic API**: Requires separate billing API key and HTTP call per session. Not worth the complexity — session-count throttle achieves the same protective outcome.
- **Making `recovery-drip` dynamically unregister from the scheduler**: The scheduler has no dynamic disable mechanism. Redis flag is the correct minimal solution; do not add a dynamic registration API to the scheduler.
- **Cross-instance coordination**: If two machines share Redis, both will read the same `queue_paused` flag. This is correct behavior (both should stop) and not a problem to solve here.
- **Per-session-type throttle differentiation**: Treating pm/dev/teammate sessions differently under throttle is a separate concern. This plan applies throttle only based on priority level.
- **Dashboard integration for new flags**: The dashboard can read from Redis — defer this to a follow-up.

## Risks

### Risk 1: `paused_circuit` sessions accumulate without recovery
**Impact:** If `api-health-gate` sets `queue_paused` but the circuit never closes (e.g., extended outage), sessions accumulate in `paused_circuit` state indefinitely.
**Mitigation:** `api-health-gate` sets a TTL of 3600s on `queue_paused`. If the reflection stops running (bridge down), the flag auto-expires and the queue unpauses. `paused_circuit` sessions are re-examined by the health check every 5 minutes — after 2x the pause TTL, they are returned to `pending`.

### Risk 2: False positive circuit OPEN during transient blip
**Impact:** A brief Anthropic API hiccup opens the circuit and pauses the queue for up to 60s after recovery.
**Mitigation:** `api-health-gate` runs every 60s; `recovery-drip` re-enables sessions 30s after `recovery:active` is set. Total delay: ~90s max from circuit close to first session resumed. This is acceptable for a 24/7 system.

### Risk 3: Redis read in hot path (`_pop_agent_session`) adds latency
**Impact:** Two additional Redis reads on every pop attempt, even when healthy.
**Mitigation:** Both reads are `GET` (O(1)). On a local Redis instance this is <1ms. In healthy state (no flags set), `r.get()` returns `None` immediately — no branch taken.

### Risk 4: `failure-loop-detector` files GitHub issues for legitimate API errors during outages
**Impact:** An Anthropic outage causes many failures with the same fingerprint → files one issue. But this is a false-positive — it's expected failure, not a bug.
**Mitigation:** `failure-loop-detector` checks if `api_health_gate` flag is set (`queue_paused` present) before scanning for failure clusters. If the queue is paused due to API outage, skip failure analysis entirely — failures during an acknowledged outage are not loops.

## Race Conditions

### Race 1: Two worker coroutines both read `queue_paused=None`, then both pop
**Location:** `agent/agent_session_queue.py` — `_pop_agent_session()` top
**Trigger:** Two workers wake simultaneously; both check `queue_paused` before either writes it
**Data prerequisite:** `queue_paused` flag must be written before workers check it
**State prerequisite:** Flag must be consistent across all workers sharing Redis
**Mitigation:** Redis `GET` is atomic. The flag is written by the scheduler, not by workers. Workers are readers only. No race — multiple workers reading the same Redis key always get the same value.

### Race 2: `recovery-drip` transitions a session to `pending` while it's being deleted by cleanup
**Location:** `agent/sustainability.py` `recovery_drip()` + `agent/agent_session_queue.py` cleanup
**Trigger:** `recovery-drip` calls `transition_status("pending")` on a session being cleaned up concurrently
**Mitigation:** `transition_status()` uses Popoto's atomic save; if the session is already deleted, the save will fail silently. Add `try/except` around the transition in `recovery_drip()`.

### Race 3: `failure-loop-detector` creates duplicate GitHub issues on concurrent runs
**Location:** `agent/sustainability.py` `failure_loop_detector()`
**Trigger:** Two runs (unlikely but possible on restart) both check the Redis set before either writes
**Mitigation:** Redis `SADD` is atomic. Use `SADD` and check return value — if `SADD` returns 0 (already existed), skip `gh issue create`. This is the Redis pattern for "check-and-set" without a lock.

## No-Gos (Out of Scope)

- Actual token-count monitoring via Anthropic billing API
- Dashboard widgets for sustainability flags (separate issue)
- Per-project-key throttle differentiation (one global throttle per project_key is sufficient)
- Retroactive requeuing of old `failed` sessions based on fingerprint matches
- Alerting integrations beyond Telegram (PagerDuty, Slack, etc.)
- Making circuit breaker thresholds configurable in YAML (existing defaults are sufficient)

## Update System

The update script (`scripts/remote-update.sh`) pulls latest code and restarts the bridge. No changes to the update script are needed. The new reflections entries in `config/reflections.yaml` will be picked up on next bridge restart automatically.

New env vars that should be added to `.env` on each machine:
- `SUSTAINABILITY_THROTTLE_MODERATE` (default: `20`) — sessions/hour threshold for moderate throttle
- `SUSTAINABILITY_THROTTLE_SUSPENDED` (default: `40`) — sessions/hour threshold for suspension

These are optional (defaults are baked in) so the update process does not block on their absence.

## Agent Integration

No new MCP server required. The sustainability functions run inside the bridge process via the `ReflectionScheduler`, not as agent-accessible tools. The `sustainability-digest` reflection enqueues a Claude agent session — this uses the existing session queue and requires no new wiring.

No changes to `.mcp.json` needed.

## Documentation

- [ ] Create `docs/features/sustainable-self-healing.md` describing the five reflections, Redis key schema, throttle levels, and failure-loop dedup
- [ ] Update `docs/features/bridge-self-healing.md` to reference this feature as the queue-layer complement to the process-layer watchdog
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] When the Anthropic circuit is OPEN, `_pop_agent_session()` returns `None` and logs a skip message
- [ ] When circuit closes, sessions resume at ≤1 per 30s until `paused_circuit` list drains
- [ ] With > `SUSTAINABILITY_THROTTLE_SUSPENDED` sessions started in the last hour, `normal`/`low` sessions are not dequeued
- [ ] ≥ 3 failures with the same error fingerprint produce exactly one GitHub issue (subsequent runs are no-ops)
- [ ] All five reflections declared in `config/reflections.yaml`, enabled and valid
- [ ] `paused_circuit` sessions are NOT requeued by the health check
- [ ] Unit tests: pause flag blocks dequeue, throttle level blocks low-priority, bloom dedup, drip rate
- [ ] Tests pass (`/do-test`)
- [ ] Documentation created (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (sustainability-core)**
  - Name: sustainability-builder
  - Role: Implement `agent/sustainability.py` and all five reflection functions
  - Agent Type: async-specialist
  - Resume: true

- **Builder (queue-guard)**
  - Name: queue-guard-builder
  - Role: Add pause + throttle guard to `_pop_agent_session()` and add `paused_circuit` status handling
  - Agent Type: builder
  - Resume: true

- **Builder (reflections-yaml)**
  - Name: reflections-yaml-builder
  - Role: Add five entries to `config/reflections.yaml`
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: sustainability-tester
  - Role: Write unit tests for sustainability functions and queue guard
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: sustainability-validator
  - Role: Verify all success criteria, run tests, check Redis key patterns
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: sustainability-documentarian
  - Role: Write `docs/features/sustainable-self-healing.md` and update related docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build sustainability.py
- **Task ID**: build-sustainability-core
- **Depends On**: none
- **Validates**: `tests/unit/test_sustainability.py` (create)
- **Informed By**: spike-1 (no token data in sessions), spike-3 (use Redis set for dedup), spike-4 (Redis flag for drip self-disable), spike-5 (project_key prefix pattern)
- **Assigned To**: sustainability-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Create `agent/sustainability.py` with five functions: `api_health_gate`, `session_count_throttle`, `failure_loop_detector`, `recovery_drip`, `sustainability_digest`
- Import `from bridge.health import get_health` for circuit state
- Use `os.environ.get("VALOR_PROJECT_KEY", "default")` for all Redis key prefixes
- All functions must `except Exception: logger.error(...)` and never raise
- `failure_loop_detector()`: check `queue_paused` flag before scanning — skip if API outage in progress
- `recovery_drip()`: read `{project_key}:recovery:active`; if absent, return immediately; otherwise pop one `paused_circuit` session to `pending` via `transition_status()`
- Add `paused_circuit` as a non-terminal, non-health-check-requeue-able status to `models/session_lifecycle.py`

### 2. Add queue guard to _pop_agent_session
- **Task ID**: build-queue-guard
- **Depends On**: build-sustainability-core (Redis key schema must be established first)
- **Validates**: `tests/unit/test_agent_session_queue.py`
- **Informed By**: spike-2 (insertion point confirmed: top of `_pop_agent_session()`)
- **Assigned To**: queue-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add pause flag check at top of `_pop_agent_session()`: `if r.get(f"{project_key}:sustainability:queue_paused"): return None`
- Add throttle check: if throttle_level is `suspended`, skip sessions with `priority in ("normal", "low")`; if `moderate`, skip `priority == "low"` only
- Update health check (`_agent_session_health_check`) to skip `paused_circuit` sessions — do not requeue them
- Add `paused_circuit` to session status transitions in `models/session_lifecycle.py`

### 3. Register reflections in YAML
- **Task ID**: build-reflections-yaml
- **Depends On**: build-sustainability-core
- **Validates**: `python -c "from agent.reflection_scheduler import load_registry; r = load_registry(); assert len(r) >= 8"`
- **Informed By**: existing `config/reflections.yaml` structure
- **Assigned To**: reflections-yaml-builder
- **Agent Type**: builder
- **Parallel**: false
- Add five entries to `config/reflections.yaml`:
  - `api-health-gate`: interval 60s, high priority, function `agent.sustainability.api_health_gate`
  - `session-count-throttle`: interval 3600s, normal priority, function `agent.sustainability.session_count_throttle`
  - `failure-loop-detector`: interval 3600s, normal priority, function `agent.sustainability.failure_loop_detector`
  - `recovery-drip`: interval 30s, high priority, function `agent.sustainability.recovery_drip`
  - `sustainability-digest`: interval 86400s, low priority, agent type with Telegram command

### 4. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-sustainability-core, build-queue-guard
- **Validates**: `tests/unit/test_sustainability.py`, `tests/unit/test_agent_session_queue.py`
- **Assigned To**: sustainability-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Test `api_health_gate()`: circuit OPEN writes `queue_paused`; circuit CLOSED deletes it and sets `recovery:active`
- Test `_pop_agent_session()` with `queue_paused` set: returns `None` without touching pending sessions
- Test throttle level `suspended`: normal/low sessions skipped; urgent/high sessions returned
- Test `failure_loop_detector()`: ≥3 same-fingerprint failures → issues Redis set add; second run with same fingerprint → no-op
- Test `recovery_drip()`: with `recovery:active` set and one `paused_circuit` session → transitions to `pending`; with empty list → clears flag
- Test health check: `paused_circuit` sessions are not requeued

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-sustainability-core, build-queue-guard, build-reflections-yaml
- **Assigned To**: sustainability-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sustainable-self-healing.md`
- Update `docs/features/bridge-self-healing.md`
- Add entry to `docs/features/README.md`

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: sustainability-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sustainability.py tests/unit/test_agent_session_queue.py -v`
- Verify all five reflections load cleanly: `python -c "from agent.reflection_scheduler import load_registry; print(len(load_registry()))"`
- Verify `api-health-gate` callable resolves: `python -c "from agent.sustainability import api_health_gate; print('ok')"`
- Verify `paused_circuit` is in session lifecycle: `python -c "from models.session_lifecycle import PAUSABLE_STATUSES; print(PAUSABLE_STATUSES)"`
- Run `python -m ruff check agent/sustainability.py` — exit 0
- Report pass/fail for all success criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sustainability.py tests/unit/test_agent_session_queue.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/sustainability.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/sustainability.py` | exit code 0 |
| Reflections load | `python -c "from agent.reflection_scheduler import load_registry; r=load_registry(); assert any(e.name=='api-health-gate' for e in r)"` | exit code 0 |
| Sustainability module imports | `python -c "from agent.sustainability import api_health_gate, session_count_throttle, failure_loop_detector, recovery_drip"` | exit code 0 |
| paused_circuit status exists | `python -c "from models.session_lifecycle import PAUSABLE_STATUSES; assert 'paused_circuit' in PAUSABLE_STATUSES"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Should `paused_circuit` sessions still appear in the Telegram dashboard, or should they be hidden from the "pending" count? (UX decision — not a blocker for implementation)
2. Should the `sustainability-digest` agent session use the existing Teammate persona or a dedicated "system monitor" prompt? (Default to Teammate persona with a focused system-health prompt)
