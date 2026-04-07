---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/778
last_comment_id:
---

# valor_session create: Immediate Worker Trigger via Redis Pub/Sub

## Problem

When `python -m tools.valor_session create --role pm --message "..."` is run, the created session sits in Redis as `pending` for up to **10 minutes** before the standalone worker processes it.

**Current behavior:**
`tools/valor_session.py:81` calls `_push_agent_session()` directly inside a transient `asyncio.run()` call. The spawned `asyncio.create_task()` in `_ensure_worker()` dies when the event loop closes. The standalone worker never receives notification and only picks up the session via `_agent_session_health_loop()` — which fires every 300s with a 300s minimum pending age gate.

**Desired outcome:**
Sessions created via CLI are picked up within ~5 seconds, matching the behavior of Telegram-triggered sessions.

## Prior Art

No prior issues found related to this work.

## Spike Results

### spike-1: Is `_push_agent_session()` the single canonical enqueue path?
- **Assumption**: "All sessions go through `_push_agent_session()`, so publishing there covers all entry points"
- **Method**: code-read
- **Finding**: `_push_agent_session()` is the canonical path for Telegram/CLI intake, but `agent_session_scheduler.py`, retry/orphan/auto-continue paths in `agent_session_queue.py`, and dev session creation in hooks all call `AgentSession.create()` directly. For this issue's scope (CLI and bridge paths), `_push_agent_session()` is the right publish point. Also discovered: `tools/valor_session.py:81` calls `_push_agent_session()` directly (bypassing `enqueue_agent_session()`), so `_ensure_worker()` is never called at all from the CLI path.
- **Confidence**: high
- **Impact on plan**: Publish inside `_push_agent_session()` itself (not in `enqueue_agent_session()`) so all callers are covered automatically.

### spike-2: Is Popoto's Subscriber safe to use in asyncio?
- **Assumption**: "Popoto pub/sub Subscriber integrates cleanly with the worker's asyncio loop"
- **Method**: code-read
- **Finding**: Popoto's `Subscriber` uses the **sync** Redis client. Calling `subscriber()` in asyncio would block the event loop. Popoto *does* expose `get_async_redis_db()` which returns a `redis.asyncio.Redis` client — and `redis.asyncio.PubSub` has `async for message in pubsub.listen():` for native async message consumption. No existing pubsub usage in the codebase to pattern-match against.
- **Confidence**: high
- **Impact on plan**: Use native `redis.asyncio` pubsub in the worker (not Popoto's `Subscriber`). Popoto's `Publisher` (sync) is still fine for the publish side.

## Data Flow

### Current (broken) CLI path:
1. `tools/valor_session.py:81` → `asyncio.run(_push_agent_session(...))`
2. `_push_agent_session()` writes `AgentSession` to Redis (status=`pending`)
3. `asyncio.run()` returns → event loop closes → any spawned tasks die
4. Standalone worker never notified → waits up to 10 minutes for health check

### Fixed path (after this plan):
1. `tools/valor_session.py:81` → `asyncio.run(_push_agent_session(...))`
2. `_push_agent_session()` writes `AgentSession` to Redis (status=`pending`)
3. `_push_agent_session()` publishes `{"chat_id": chat_id}` to `valor:sessions:new` channel (sync, fire-and-forget)
4. Standalone worker's async subscriber loop receives message immediately
5. Worker calls `_ensure_worker(chat_id)` → session picked up within ~1s

### Bridge path (unchanged, still works):
1. Telegram message → `enqueue_agent_session()` → `_push_agent_session()` (now also publishes)
2. `enqueue_agent_session()` also calls `_ensure_worker()` (existing behavior preserved)
3. Worker may get duplicate signals — idempotent by design

## Architectural Impact

- **New dependency**: `redis.asyncio` pubsub (already available via existing `redis` dependency)
- **Interface changes**: `_push_agent_session()` gains a fire-and-forget publish call at the end
- **Coupling**: Slight increase — worker now also listens on a Redis channel. Acceptable; Redis is already the primary coupling point.
- **Data ownership**: Unchanged — worker still owns session execution
- **Reversibility**: High — remove the publish call and the subscriber coroutine; health check fallback still works

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Pub/sub channel requires active Redis |
| Popoto configured | `python -c "from popoto.redis_db import get_async_redis_db; print('ok')"` | Async Redis client available |

Run all checks: `python scripts/check_prerequisites.py docs/plans/valor-session-worker-trigger.md`

## Solution

### Key Elements

- **Publisher in `_push_agent_session()`**: After writing the session to Redis, publish `{"chat_id": chat_id, "session_id": session_id}` to channel `valor:sessions:new` using Popoto's sync `Publisher` (or raw `redis.publish()`). Fire-and-forget — failure is logged but never raises.
- **Async subscriber in standalone worker**: A new `_session_notify_listener()` coroutine subscribes to `valor:sessions:new` using `redis.asyncio` native pubsub. On each message, calls `_ensure_worker(chat_id)` — which is idempotent (no-ops if a worker is already running for that chat).
- **Worker startup integration**: `_session_notify_listener()` is launched as an `asyncio.create_task()` alongside the existing `_agent_session_health_loop()`, cancelled on shutdown.

### Flow

`valor_session create` → `_push_agent_session()` writes session + publishes to `valor:sessions:new` → worker subscriber receives → `_ensure_worker(chat_id)` → session processing begins within ~1s

### Technical Approach

- `_push_agent_session()` (`agent/agent_session_queue.py`): Add sync Redis publish at the end. Use `try/except Exception` to ensure publish failure never crashes enqueue. Use `POPOTO_REDIS_DB.publish("valor:sessions:new", msgpack.packb({"chat_id": chat_id}))` or equivalent.
- `_session_notify_listener()` (new function in `agent/agent_session_queue.py`): `async def` that creates a `redis.asyncio` pubsub, subscribes to `valor:sessions:new`, loops `async for message in pubsub.listen()`, decodes msgpack, calls `_ensure_worker(chat_id)`. Handles Redis disconnects by catching `ConnectionError` and sleeping before retry.
- `worker/__main__.py:205`: After `asyncio.create_task(_agent_session_health_loop())`, add `asyncio.create_task(_session_notify_listener())`. Cancel in shutdown block.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Publish failure in `_push_agent_session()` must never propagate — wrap in `try/except Exception: logger.warning(...)`, assert session is still written to Redis even when publish fails
- [ ] Subscriber `ConnectionError` on Redis restart: subscriber loop catches, logs, sleeps 5s, retries subscribe — test with a mock that raises then succeeds

### Empty/Invalid Input Handling
- [ ] Worker receives malformed msgpack on channel: current Subscriber silently drops (`msgpack.FormatError`); replicate this pattern in the async listener
- [ ] Worker receives `chat_id` for a session that no longer exists: `_ensure_worker()` is idempotent, no session found → no task spawned → no error

### Error State Rendering
- N/A — this is a background worker path with no user-visible output

## Test Impact

- [ ] `tests/unit/test_agent_session_queue_async.py::*` — UPDATE: mock or patch `POPOTO_REDIS_DB.publish` so tests don't require a live Redis pub/sub channel; assert publish is called after enqueue
- [ ] `tests/integration/test_silent_failures.py` — UPDATE: mock publish call to avoid side effects in test environment
- [ ] `tests/integration/test_bridge_routing.py` — UPDATE: same — mock publish call
- [ ] `tests/integration/test_lifecycle_transition.py` — UPDATE: same — mock publish call

## Rabbit Holes

- **Migrating all direct `AgentSession.create()` callers to publish**: The scheduler, retry, and orphan paths bypass `_push_agent_session()`. Out of scope — they have their own recovery paths and this issue specifically targets the CLI path.
- **Replacing the health check with pub/sub entirely**: The health check is a safety net for crashes and missed notifications. Keep it.
- **Persistent pub/sub with Redis Streams**: Streams give at-least-once delivery and consumer groups. Overkill for this use case — fire-and-forget pub/sub + health check fallback is sufficient.

## Risks

### Risk 1: Redis disconnects drop pub/sub messages
**Impact:** Worker misses notification; session falls back to 10-minute health check
**Mitigation:** Health check is still the safety net. The fix reduces the *expected* case to <1s; the worst case degrades gracefully to status quo.

### Risk 2: Worker not running when notification is published
**Impact:** Notification is lost (Redis pub/sub has no persistence)
**Mitigation:** Same as above — health check on next fire picks it up. Document this explicitly in the subscriber.

## Race Conditions

### Race 1: Session written but subscriber not yet started
**Location:** `agent/agent_session_queue.py` + `worker/__main__.py:205`
**Trigger:** Worker starts up, subscriber task not yet scheduled, session enqueued during startup window
**Data prerequisite:** Session must be in Redis before subscriber loop begins
**State prerequisite:** `_session_notify_listener()` must be subscribed before any messages arrive
**Mitigation:** Startup sequence already recovers pending sessions in step 6 (`_ensure_worker` for all pending) before the subscriber launches. Any sessions created during the subscriber's startup window will be caught by the startup recovery or the first health check. Acceptable.

### Race 2: Duplicate `_ensure_worker()` calls for same chat_id
**Location:** `enqueue_agent_session()` (calls `_ensure_worker`) + `_session_notify_listener()` (also calls `_ensure_worker`)
**Trigger:** Bridge path calls `enqueue_agent_session()` which calls `_ensure_worker()` AND the subscriber fires for the same event
**State prerequisite:** `_ensure_worker` must be idempotent for same `chat_id`
**Mitigation:** `_ensure_worker()` already checks if a task exists for `chat_id` before creating a new one — confirmed idempotent.

## No-Gos (Out of Scope)

- Fixing the scheduler's direct `AgentSession.create()` bypass — separate issue
- At-least-once delivery guarantees for the notification channel
- Cross-machine pub/sub without Redis (not needed today)
- Replacing the 10-minute health check safety net

## Update System

No update system changes required — this is a purely internal change to the worker and queue module. No new config files, environment variables, or migration steps.

## Agent Integration

No agent integration required — this is an internal worker/queue change. The `valor_session create` CLI tool behavior is improved automatically. No MCP changes needed.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` to describe the pub/sub notification path alongside the health check recovery path
- [ ] Add entry to `docs/features/README.md` if a new feature doc is created

## Success Criteria

- [ ] `python -m tools.valor_session create --role pm --message "..."` triggers session pickup within ~5 seconds
- [ ] Telegram bridge path unaffected (no regression)
- [ ] Publish failure in `_push_agent_session()` is logged but never raises
- [ ] Worker subscriber handles Redis disconnect gracefully (retries without crashing)
- [ ] Integration test: enqueue session via CLI, assert transitions from `pending` to `running` within 10 seconds
- [ ] All unit tests pass with publish mocked
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (queue-and-worker)**
  - Name: `queue-worker-builder`
  - Role: Add publish call to `_push_agent_session()` and add `_session_notify_listener()` coroutine to worker
  - Agent Type: builder
  - Resume: true

- **Test Writer (pub-sub)**
  - Name: `pubsub-test-writer`
  - Role: Write unit and integration tests for the publish/subscribe notification flow
  - Agent Type: test-writer
  - Resume: true

- **Validator (final)**
  - Name: `final-validator`
  - Role: Verify all acceptance criteria and run full test suite
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `doc-writer`
  - Role: Update `docs/features/bridge-worker-architecture.md` with pub/sub notification path
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add publish call to `_push_agent_session()`
- **Task ID**: build-publisher
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_queue_async.py`
- **Informed By**: spike-1 (publish inside `_push_agent_session()` so all callers benefit), spike-2 (use sync `POPOTO_REDIS_DB.publish()`, not Popoto Subscriber)
- **Assigned To**: queue-worker-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/agent_session_queue.py`, at the end of `_push_agent_session()` (after session is written), add a fire-and-forget sync Redis publish to channel `valor:sessions:new` with payload `{"chat_id": chat_id, "session_id": session_id}` (msgpack-encoded)
- Wrap in `try/except Exception: logger.warning(...)` — publish failure must never raise

### 2. Add `_session_notify_listener()` to worker
- **Task ID**: build-subscriber
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_queue_async.py`
- **Informed By**: spike-2 (use `redis.asyncio` native pubsub with `async for message in pubsub.listen():`)
- **Assigned To**: queue-worker-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `async def _session_notify_listener()` to `agent/agent_session_queue.py` (or `worker/__main__.py`): subscribe to `valor:sessions:new` via `redis.asyncio`, decode msgpack payload, call `_ensure_worker(chat_id)`, handle `ConnectionError` with sleep+retry
- In `worker/__main__.py`, after the `_agent_session_health_loop()` task creation, add `asyncio.create_task(_session_notify_listener())` and cancel it in the shutdown block

### 3. Write tests
- **Task ID**: write-tests
- **Depends On**: build-publisher, build-subscriber
- **Validates**: `tests/unit/test_agent_session_queue_async.py`, `tests/integration/test_session_notify.py` (new)
- **Assigned To**: pubsub-test-writer
- **Agent Type**: test-writer
- **Parallel**: false
- Update `tests/unit/test_agent_session_queue_async.py`: mock `POPOTO_REDIS_DB.publish`, assert it is called after enqueue, assert session still written when publish raises
- Update `tests/integration/test_silent_failures.py`, `test_bridge_routing.py`, `test_lifecycle_transition.py`: mock publish call to prevent side effects
- Create `tests/integration/test_session_notify.py`: enqueue a session via `_push_agent_session()`, assert `valor:sessions:new` channel receives message within 1s; simulate subscriber receiving message, assert `_ensure_worker()` is called

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: write-tests
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` to describe the pub/sub notification path as the fast path and health check as the fallback safety net

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` and `pytest tests/integration/test_session_notify.py -x -q`
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Publish call present | `grep -n "valor:sessions:new" agent/agent_session_queue.py` | output > 0 |
| Subscriber present | `grep -n "_session_notify_listener" worker/__main__.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

None — approach is confirmed by spikes and recon.
