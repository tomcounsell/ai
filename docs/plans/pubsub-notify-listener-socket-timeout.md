---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-08
tracking: https://github.com/tomcounsell/ai/issues/824
last_comment_id:
revision_applied: true
---

# Fix pub/sub notify listener socket timeout regression

## Problem

The standalone worker picks up CLI-created sessions intermittently late — up to 5+ minutes — despite the pub/sub fix shipped in PR #784. Sessions created via `valor_session create` sit in `pending` state, silently missed, until the 5-minute health check fires.

**Current behavior:**
`_session_notify_listener` spawns a background thread that creates a `pubsub` object from `POPOTO_REDIS_DB`. That global connection pool has `socket_timeout=5` (set for request-response commands). The blocking `pubsub.listen()` iterator inherits this timeout. When no session notifications arrive within 5 seconds, Redis raises a socket timeout exception. The thread catches it, logs "Timeout reading from socket", exits, and sends a `None` sentinel that causes the outer coroutine to sleep 5 seconds before resubscribing. Any notification published during that 5-second dead window is permanently lost — Redis pub/sub is fire-and-forget with zero buffering.

Observed in production: sessions `0_1775578955` and `0_1775578966` were stuck pending for 8+ minutes. The listener cycled every 10 seconds (5s timeout + 5s sleep) and missed both notifications. Manual republish immediately unblocked them.

**Desired outcome:**
`_session_notify_listener` blocks indefinitely between messages. "Timeout reading from socket" never appears in logs during normal idle. Sessions created via `valor_session create` are consistently picked up within 2 seconds. The reconnect path still fires on genuine Redis failures (network drop, Redis restart). The health check task also gets a `done_callback` so unexpected exits are detected.

## Prior Art

- **Issue #778**: `valor_session create does not trigger the worker — sessions sit pending up to 10 minutes` — root cause was asyncio loop closing before worker subscribed; closed by PR #784.
- **PR #781**: `Fix: valor_session create triggers worker immediately via Redis pub/sub` — first attempt; added pub/sub publish in `_push_agent_session()`. Partial fix; listener socket_timeout not addressed.
- **PR #784**: `fix(worker): trigger session pickup immediately via Redis pub/sub (#778)` — added `_session_notify_listener` coroutine with the thread/queue bridge pattern; merged 2026-04-07. Also incomplete: inherited `socket_timeout=5` from `POPOTO_REDIS_DB` was not addressed, leaving a 50% message-loss window.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #781 | Added pub/sub publish in `_push_agent_session()` | No persistent listener on the worker side — messages were published but nothing was subscribed to receive them reliably |
| PR #784 | Added `_session_notify_listener` with thread/queue bridge | Fixed the missing listener but `pubsub = POPOTO_REDIS_DB.pubsub()` inherits the global `socket_timeout=5`. Idle periods > 5s cause exception → reconnect → dead window |

**Root cause pattern:** Both fixes applied the pub/sub mechanism but neither addressed connection-level configuration. The global `POPOTO_REDIS_DB` is tuned for request-response (short `socket_timeout` is correct there) but pub/sub requires an indefinitely blocking connection.

## Data Flow

1. **Session creation** (`valor_session create` or bridge): calls `_push_agent_session()` → publishes JSON payload to `valor:sessions:new` via `POPOTO_REDIS_DB.publish()`
2. **Notify listener thread** (`_listen_in_thread`): calls `POPOTO_REDIS_DB.pubsub()` → subscribes → iterates `pubsub.listen()` → on message: puts `chat_id` onto `notify_queue`
3. **Bug location**: `pubsub.listen()` inherits `socket_timeout=5` from `POPOTO_REDIS_DB`'s connection pool → 5s idle raises exception → thread exits → `None` sentinel → 5s sleep → resubscribe
4. **Fix**: create a fresh `redis.Redis` instance with explicit `socket_timeout=None` inside `_listen_in_thread`, reading host/port/db from `POPOTO_REDIS_DB.connection_pool.connection_kwargs` but passing timeout params explicitly
5. **Worker pickup**: `notify_queue.get()` returns `chat_id` → `_ensure_worker(chat_id)` → session processing starts within ~1s

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Redis is already running locally.

## Solution

### Key Elements

- **Fresh Redis connection for pub/sub**: Inside `_listen_in_thread`, create a new `redis.Redis` instance with `socket_timeout=None` (and `socket_connect_timeout=None`) derived from `POPOTO_REDIS_DB.connection_pool.connection_kwargs`. This leaves the global pool untouched.
- **Health task `done_callback`**: In `worker/__main__.py`, add a `_health_task_done` callback to `health_task` mirroring the existing `_notify_task_done` pattern, so unexpected health loop exits are logged as errors.
- **Reconnect loop stays**: The outer `while True` + `asyncio.sleep(5)` in `_session_notify_listener` remains. It handles genuine Redis failures (Redis restart, network drop). The fix ensures it is no longer triggered by routine idle timeouts.

### Technical Approach

- In `_listen_in_thread` (inside `_session_notify_listener` in `agent/agent_session_queue.py`):
  - Import `redis` directly
  - Instantiate a dedicated connection: `conn = redis.Redis(host=..., port=..., db=..., socket_timeout=None, socket_connect_timeout=None)` — read host/port/db from `POPOTO_REDIS_DB.connection_pool.connection_kwargs` but pass `socket_timeout=None` and `socket_connect_timeout=None` explicitly. **Do NOT copy-and-modify the full kwargs dict** (avoids Risk 2 — unexpected keys causing `TypeError`). The explicit parameter approach is the canonical pattern.
  - Call `conn.pubsub()` instead of `POPOTO_REDIS_DB.pubsub()`
  - In the `finally` block, teardown in this exact order to avoid dangling Redis subscribers:
    1. `pubsub.unsubscribe()` — remove subscription before closing
    2. `pubsub.close()` — release pubsub resources
    3. `conn.close()` — close the dedicated connection
  - Then proceed with the reconnect sleep as before
- In `worker/__main__.py`:
  - After `health_task = asyncio.create_task(...)`, define `_health_task_done` callback identical in structure to `_notify_task_done`
  - Call `health_task.add_done_callback(_health_task_done)`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_listen_in_thread`'s `except Exception` block already logs and exits cleanly; verify it now only fires on genuine errors (not timeouts)
- [ ] New `redis.Redis()` instantiation failure (bad host) must be caught by the existing outer `except Exception` in `_session_notify_listener` — confirmed: it is

### Empty/Invalid Input Handling
- [ ] `notify_queue.put_nowait(None)` sentinel path unchanged — thread-exit still signals the coroutine side correctly
- [ ] If `connection_kwargs` is missing expected fields (unusual Popoto version), `redis.Redis()` falls back to redis-py defaults — acceptable

### Error State Rendering
- [ ] Worker logs must NOT contain "Timeout reading from socket" during normal idle (verified by running the worker and observing logs)
- [ ] Genuine reconnect on Redis restart must still log the reconnect warning ("Session notify listener thread error: ...") — verified by `redis-cli DEBUG SLEEP 10`

## Test Impact

- [ ] `tests/integration/test_session_notify.py` — existing **publish** tests (`test_push_agent_session_*`) mock `POPOTO_REDIS_DB.publish()` and test `_push_agent_session`. These tests target `_push_agent_session`, not `_listen_in_thread`, and **do not need mock changes**. No update required for publish-side tests.
- [ ] Add new test `test_notify_listener_uses_no_socket_timeout` — verifies that the `redis.Redis` instance created inside `_listen_in_thread` has `socket_timeout=None`. This new test patches `redis.Redis` directly (not `POPOTO_REDIS_DB`) as the mock target, since the fix introduces a new `redis.Redis(...)` call site in `_listen_in_thread`.

## Rabbit Holes

- **Parameterizing `socket_timeout` in config**: The fix is `None` (block forever). Don't add a config knob — it adds complexity without benefit for a pub/sub connection.
- **Switching to `aioredis` or an async pub/sub client**: The thread/queue bridge pattern already works; replacing it with a fully-async pub/sub implementation is a rewrite, not a bugfix.
- **Lowering the health check interval**: The 5-minute health check is a safety net, not the primary pickup mechanism. Tuning it doesn't fix the lost-notification bug.
- **Adding a message buffer or replay mechanism**: Redis pub/sub is intentionally fire-and-forget. The correct fix is eliminating the dead window, not adding a replay layer.

## Risks

### Risk 1: Connection pool leak from the fresh redis.Redis instance
**Impact:** Each reconnect cycle (genuine failures) creates a new Redis connection that may not be closed properly, leaking TCP connections over time.
**Mitigation:** Add `_pubsub_redis.close()` (or `.connection_pool.disconnect()`) in the `finally` block of `_listen_in_thread`. Verified pattern: redis-py 7.x supports `.close()`.

### Risk 2: `connection_kwargs` structure differs across Popoto/redis-py versions
**Impact:** If `connection_pool.connection_kwargs` contains unexpected keys, `redis.Redis(**kwargs)` may raise a `TypeError`.
**Mitigation:** Whitelist only known safe keys (`host`, `port`, `db`, `username`, `password`, `decode_responses`, `encoding`) instead of spreading all kwargs. Alternatively, read `.connection_pool.connection_kwargs` and selectively override only the timeout keys. Prefer the selective-override approach to minimize coupling to Popoto internals.

## Race Conditions

### Race 1: Notification published during reconnect window
**Location:** `agent/agent_session_queue.py:_listen_in_thread`, reconnect path
**Trigger:** Redis restart causes thread error → `None` sentinel → 5s sleep → resubscribe. Any `valor:sessions:new` publish during those 5 seconds is lost.
**Data prerequisite:** Session must exist in Redis before the notification is published (already guaranteed by `async_create` completing before `publish` in `_push_agent_session`)
**Mitigation:** This race is inherent to pub/sub without replay. The 5-minute health check is the backstop. The fix eliminates the *routine* 10-second cycle that makes this race happen constantly during idle — it now only occurs on genuine Redis restarts, which are rare events.

### Race 2: `notify_queue.put_nowait` called after loop is closed
**Location:** `_listen_in_thread`, `finally` block calling `loop.call_soon_threadsafe`
**Trigger:** Worker shuts down (asyncio loop closes) while the background thread is still running
**Mitigation:** Already handled — `asyncio.CancelledError` on `task.cancel()` in the outer coroutine propagates; the thread's `finally` block calling `loop.call_soon_threadsafe` on a closed loop raises `RuntimeError` which is caught by the outer `except Exception`. No change needed here.

## No-Gos (Out of Scope)

- Replacing the thread/queue bridge pattern with fully-async pub/sub
- Adding message replay or buffering for missed notifications
- Changing the global `POPOTO_REDIS_DB` connection pool configuration
- Reducing the health check polling interval
- Monitoring or alerting on missed notification count

## Update System

No update system changes required — this is a purely internal worker fix. No new dependencies, no config changes, no migration steps needed.

## Agent Integration

No agent integration required — this is a worker-internal change. The fix is contained to `agent/agent_session_queue.py` and `worker/__main__.py`. No MCP server changes, no `.mcp.json` changes, no bridge changes.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` to note that `_session_notify_listener` uses a dedicated Redis connection with `socket_timeout=None` (brief inline note, not a full rewrite)
- [ ] No new feature doc needed — this is a bug fix to existing documented behavior

## Success Criteria

- [ ] `_session_notify_listener` does not log "Timeout reading from socket" during normal idle periods (verified by running worker and observing logs for 30 seconds with no activity)
- [ ] Sessions created via `valor_session create` are picked up within 2 seconds consistently (verified by creating a session and checking worker logs)
- [ ] Reconnect still works after a genuine Redis disconnect (`redis-cli DEBUG SLEEP 10` triggers reconnect warning log within 15 seconds)
- [ ] Worker logs include a `done_callback` warning if the health check task exits unexpectedly (manual test: cancel `health_task` directly)
- [ ] `tests/integration/test_session_notify.py` updated mocks pass
- [ ] New test `test_notify_listener_uses_no_socket_timeout` passes
- [ ] `pytest tests/ -x -q` exits 0
- [ ] `python -m ruff check .` exits 0

## Team Orchestration

### Team Members

- **Builder (listener-fix)**
  - Name: listener-fix-builder
  - Role: Fix `_listen_in_thread` to use a fresh Redis connection with `socket_timeout=None`; add health task `done_callback`
  - Agent Type: builder
  - Resume: true

- **Validator (listener-fix)**
  - Name: listener-fix-validator
  - Role: Verify fix, run tests, check logs for no timeout noise
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Fix `_listen_in_thread` and add health task `done_callback`
- **Task ID**: build-listener-fix
- **Depends On**: none
- **Validates**: `tests/integration/test_session_notify.py` (update), new `test_notify_listener_uses_no_socket_timeout` (create)
- **Assigned To**: listener-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/agent_session_queue.py` inside `_listen_in_thread`: create a fresh `redis.Redis` instance using explicit `socket_timeout=None` (read host/port/db from `POPOTO_REDIS_DB.connection_pool.connection_kwargs` but pass timeout params explicitly — do NOT spread full kwargs); use this instance for `pubsub`; in `finally` call `pubsub.unsubscribe()`, then `pubsub.close()`, then `conn.close()` in that order
- In `worker/__main__.py`: add `_health_task_done` callback to `health_task` mirroring existing `_notify_task_done` pattern
- Do NOT change existing publish-side tests in `tests/integration/test_session_notify.py` — they test `_push_agent_session` and are unaffected by this change
- Add new test `test_notify_listener_uses_no_socket_timeout` that patches `redis.Redis` directly and asserts the instance passed to `pubsub()` has `socket_timeout=None`
- Run `python -m ruff format . && python -m ruff check .`
- Run `pytest tests/integration/test_session_notify.py -v`

### 2. Validate fix
- **Task ID**: validate-listener-fix
- **Depends On**: build-listener-fix
- **Assigned To**: listener-fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` and confirm exit 0
- Confirm "Timeout reading from socket" does not appear in logs during 30s idle observation
- Confirm `docs/features/bridge-worker-architecture.md` updated with inline note about dedicated pub/sub connection
- Report pass/fail on all Success Criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No idle timeouts | `grep -r "Timeout reading from socket" logs/ 2>/dev/null` | exit code 1 |
| New test present | `grep -r "test_notify_listener_uses_no_socket_timeout" tests/` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Adversary | Technical Approach says "copy and remove keys" but Risk 2 recommends "selective-override" — builder receives contradictory instructions | Technical Approach revised: use explicit `socket_timeout=None` param, do NOT spread full kwargs | `conn = redis.Redis(host=..., port=..., db=..., socket_timeout=None, socket_connect_timeout=None)` |
| CONCERN | Archaeologist | Test Impact says existing publish tests need mock changes — they test `_push_agent_session`, not `_listen_in_thread`, so they are unaffected | Test Impact section corrected: publish tests unchanged; only new listener test patches `redis.Redis` | New test patches `redis.Redis` directly as mock target |
| CONCERN | Operator | `finally` block closes `conn` but does not unsubscribe/close pubsub first — leaves dangling Redis subscriber on reconnect | Technical Approach updated with explicit teardown sequence | `pubsub.unsubscribe()` → `pubsub.close()` → `conn.close()` in `finally`, in that order |

---

## Open Questions

None — solution is fully defined by code inspection. The fix is confined to two files with no external dependencies or ambiguous tradeoffs.
