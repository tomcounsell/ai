---
status: Planning
type: bug
appetite: Small
owner: Dev
created: 2026-04-08
tracking: https://github.com/tomcounsell/ai/issues/831
last_comment_id: none
---

# Worker Key Computed Property — Isolation-Aware Session Routing

## Problem

Two PM sessions belonging to the same project but arriving from different Telegram threads execute concurrently. Each `chat_id` gets its own `_worker_loop`, so sessions from Thread A and Thread B both write to the same git working tree, the same plan docs, and the same `main` branch — racing each other.

**Current behavior:** `_active_workers` is keyed by `chat_id`. PM sessions from different Telegram threads for the same project run in separate worker loops and execute in parallel, causing git conflicts and corrupted working state.

**Desired outcome:** The worker key reflects actual isolation level, not communication topology. Sessions that share mutable state serialize by project; sessions in isolated worktrees stay parallel-safe.

| Session type | Slug present? | Worker key | Behavior |
|---|---|---|---|
| `pm` | N/A | `project_key` | Serialized per project |
| `dev` | yes (worktree) | `chat_id` | Parallel-safe, isolated |
| `dev` | no (main repo) | `project_key` | Serialized per project |
| `teammate` | N/A | `chat_id` | Always parallel-safe |

## Prior Art

- **Issue #814 / PR #814**: Introduced per-`chat_id` worker serialization and `_global_session_semaphore`. Serializes within a chat but not across chats sharing the same project. The routing key (`chat_id`) was correct for the original goal but became the isolation model by accident.
- **Issue #828** (closed/superseded): Proposed keying ALL pm/dev workers by `project_key`. Superseded by this issue after recognizing that dev sessions with a slug run in isolated worktrees and are parallel-safe — a blanket `project_key` rule was too coarse.

## Why Previous Fix Was Incomplete

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #814 | Keyed `_active_workers` by `chat_id`, added `_global_session_semaphore` | Fixed same-thread races but left cross-thread pm/dev races open — `chat_id` is a comms topology concept, not an isolation concept |
| Issue #828 | Proposed blanket `project_key` for all pm/dev | Correct direction but too coarse — slug-bearing dev sessions run in worktrees and don't need project-level serialization |

**Root cause pattern:** The routing key conflates two orthogonal concerns: (1) which Telegram thread a session came from, and (2) whether the session shares mutable repo state with others. `worker_key` separates these concerns.

## Data Flow

1. **Telegram message arrives** → `bridge/telegram_bridge.py` calls `enqueue_agent_session(chat_id=..., session_type=..., slug=...)`
2. **Enqueue** (line 1750 in `agent_session_queue.py`) → calls `_ensure_worker(chat_id)` [CURRENTLY] → calls `_ensure_worker(session.worker_key)` [AFTER FIX]
3. **Pub/sub notification** → `_publish_session_notification` publishes `{"chat_id": ..., "session_id": ..., "worker_key": ...}` [AFTER FIX]
4. **`_session_notify_listener`** receives notification → calls `_ensure_worker(worker_key)` using the `worker_key` from the payload [AFTER FIX]
5. **`_worker_loop(worker_key, event)`** pops next session:
   - If `worker_key == project_key` (pm or dev-no-slug): filters `AgentSession.query.filter(project_key=..., status="pending")` with session_type guards
   - If `worker_key == chat_id` (dev-with-slug, teammate): filters by `chat_id` as today
6. **Session executes** → output routed back to Telegram

## Architectural Impact

- **Interface changes**: `_ensure_worker(chat_id: str)` signature stays unchanged externally; semantics of the argument change to `worker_key` for all callers. `_worker_loop(chat_id, event)` parameter rename to `worker_key`.
- **`_pop_agent_session`**: Must branch on key type — project-key workers need a different filter predicate than chat-key workers.
- **Pub/sub payload**: Adds `worker_key` field alongside existing `chat_id`. No backward-compat concerns — only the standalone worker consumes this payload.
- **Coupling**: Reduces coupling between comms topology and execution isolation. The `worker_key` property encodes isolation as a first-class concept.
- **Reversibility**: Medium — changing `_active_workers` key semantics requires updating all callers in one shot. Test coverage makes this safe.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`AgentSession.worker_key` property**: Computed from `session_type` and `slug`. No new stored fields. Returns `project_key` for pm and unslugged-dev; returns `chat_id` for slugged-dev and teammate.
- **`_pop_agent_session` branching**: PM-keyed workers filter by `project_key + session_type in [pm, dev-no-slug]`; chat-keyed workers continue filtering by `chat_id`.
- **Pub/sub payload update**: `_publish_session_notification` includes `worker_key` so the listener can route without a Redis roundtrip.
- **All `_ensure_worker` call sites**: Updated to pass `session.worker_key` instead of `session.chat_id`.
- **`worker/__main__.py` startup**: Updated to compute `worker_key` per session instead of `chat_id`.
- **Redis pop lock**: Key changes from `worker:pop_lock:{chat_id}` to `worker:pop_lock:{worker_key}` to maintain TOCTOU protection.

### Technical Approach

**`worker_key` property on `AgentSession`:**

```python
@property
def worker_key(self) -> str:
    if self.session_type == SessionType.TEAMMATE:
        return self.chat_id
    if self.session_type == SessionType.PM:
        return self.project_key
    # dev: isolated if slug present (worktree), serialized if not (shared main tree)
    return self.chat_id if self.slug else self.project_key
```

**`_pop_agent_session` signature change:**
- Add `worker_key: str` parameter alongside or instead of `chat_id: str`
- When `worker_key == project_key`: query `AgentSession.query.filter(project_key=project_key, status="pending")` then filter in-memory to pm and dev-without-slug sessions
- When `worker_key == chat_id`: existing query `AgentSession.query.filter(chat_id=chat_id, status="pending")`
- Pop lock key: `worker:pop_lock:{worker_key}` in both branches

**Pub/sub payload addition:**
```python
payload = json.dumps({
    "chat_id": chat_id,
    "session_id": session_id,
    "worker_key": session.worker_key,
})
```

**`_session_notify_listener` update:**
- Extract `worker_key = data.get("worker_key") or data.get("chat_id")` for backward compat
- Call `_ensure_worker(worker_key)` and `_active_events.get(worker_key)`

**All call sites to update:**
- `agent_session_queue.py` lines 1276, 1328 (health check): `_ensure_worker(entry.worker_key)`
- `agent_session_queue.py` line 1510 (notify listener): `_ensure_worker(worker_key)` from payload
- `agent_session_queue.py` line 1750 (enqueue): `_ensure_worker(session.worker_key)`
- `agent_session_queue.py` lines 2288, 2323 (nudge): `_ensure_worker(session.worker_key)`
- `agent_session_queue.py` line 2408 (steering): `_ensure_worker(session.worker_key)`
- `worker/__main__.py` line 218-220 (startup): compute `worker_key = session.worker_key` per session

**`_active_events` alignment:**
- `_active_events` is keyed by the same string as `_active_workers`; must use `worker_key` consistently

## Failure Path Test Strategy

### Exception Handling Coverage
- `_ensure_worker` has a try/except around `create_task` that cleans up `_starting_workers` — no change needed; the guard remains valid with `worker_key` as key
- `_pop_agent_session` has a `finally: _release_pop_lock(...)` — both branches must release the correct key; covered by the lock key change
- Pop lock acquisition failure logs a warning and returns `None` — existing behavior preserved; no new exception paths introduced

### Empty/Invalid Input Handling
- `worker_key` property: if `session_type` is `None` (legacy/unclassified sessions), the property falls through to the `dev` branch; `self.slug` check handles `None` slug; `self.project_key` is a `KeyField` (never null) — safe
- If `chat_id` is `None` and `session_type == SessionType.TEAMMATE`: the property returns `None`, which breaks `_ensure_worker`. This is the same failure mode as today (the current code does `entry.chat_id or entry.project_key` as a fallback). The property should replicate this fallback: `return self.chat_id or self.project_key` for teammate.

### Error State Rendering
- No user-visible output changes — this is a routing change inside the worker loop
- Worker startup logs (`[chat:...] Started session queue worker`) will now show `project_key` for PM sessions — acceptable; update log prefix to `[worker:...]`

## Test Impact

- [ ] `tests/integration/test_worker_concurrency.py::TestPerChatSerialization::test_three_sessions_same_chat_id_execute_serially` — UPDATE: session creation must set `session_type` explicitly; test logic remains valid for chat-keyed sessions
- [ ] `tests/integration/test_worker_concurrency.py::TestJobHealthCheck` — UPDATE: `WORKER_KEY = "123"` comment documents chat_id assumption; after fix, health check will compute `worker_key` from session; verify test sessions have correct `session_type` so `worker_key` matches the asserted key
- [ ] `tests/integration/test_agent_session_health_monitor.py` (line 136 comment) — UPDATE: update comment "health_check uses `chat_id or project_key`" to reflect `session.worker_key`
- [ ] `tests/unit/test_worker_entry.py::test_worker_startup_sequence_order` — UPDATE: test checks `_ensure_worker(` is called; remains valid but may need to accommodate `worker_key` variable instead of `chat_id`
- [ ] `tests/unit/test_agent_session_queue.py` — REVIEW: check for any tests that mock `_ensure_worker(chat_id=...)` explicitly; update mocks to accept `worker_key`

New tests to add:
- `tests/integration/test_worker_concurrency.py::TestPMProjectKeySerialization` — two PM sessions from different `chat_id`s on same `project_key` execute serially
- `tests/integration/test_worker_concurrency.py::TestDevWorktreeParallelism` — two dev sessions with `slug` set on different `chat_id`s execute concurrently
- `tests/unit/test_agent_session.py::TestWorkerKeyProperty` — unit tests for all four cases of `worker_key` property

## Rabbit Holes

- **Per-project asyncio Lock held during execution**: rejected in #828. Do not introduce execution-time locks. The single-worker-per-key structure is the serialization mechanism.
- **Querying `session_type` from the pub/sub payload**: the payload should carry `worker_key` directly so the listener is a pure forwarder. Don't make the listener reconstruct `worker_key` from raw fields — that logic belongs in the model.
- **Changing `_active_workers` to a multi-level dict**: unnecessary complexity. The flat dict with `worker_key` as key is sufficient and maps directly to the existing code structure.
- **Granular per-slug worker keys**: Slugged dev sessions could be keyed by `slug` instead of `chat_id`, but `chat_id` is already unique per Telegram thread and provides the same isolation. Keep it simple.

## Risks

### Risk 1: PM worker starved by long-running dev-no-slug sessions
**Impact:** A dev session without a slug shares the project-keyed worker with PM sessions. A long-running dev session blocks PM sessions for that project.
**Mitigation:** This is a pre-existing property of the single-worker model. Priority (`urgent > high > normal > low`) already governs ordering. PM sessions can be enqueued at `high` priority to front-run dev sessions. No new risk introduced.

### Risk 2: `_pop_agent_session` filter misclassifies sessions
**Impact:** A session with `session_type=None` (legacy or misconfigured) might get popped by the wrong worker loop.
**Mitigation:** Worker-key property includes a `None` fallback path that matches today's `chat_id or project_key` behavior. Add an explicit guard in `_pop_agent_session` to skip sessions whose `worker_key` doesn't match the current loop's key.

### Risk 3: `_active_events` dict key mismatch after migration
**Impact:** If any code path sets `_active_events[chat_id]` while another reads `_active_events[worker_key]`, events are lost and workers stall waiting for signals.
**Mitigation:** Treat `_active_events` as an atomic sibling of `_active_workers` — update both together in `_ensure_worker`. The single function is the sole writer; grep confirms all read sites use the same key.

## Race Conditions

### Race 1: Two PM sessions from different `chat_id`s at startup
**Location:** `worker/__main__.py:215-221`
**Trigger:** At startup, two pending PM sessions with different `chat_id`s both compute `worker_key == project_key`. The `started_chats` set guards against duplicate workers within the startup loop.
**Data prerequisite:** Both sessions must compute `worker_key` before `_ensure_worker` is called for either.
**State prerequisite:** `_starting_workers` must be empty at startup.
**Mitigation:** Replace `started_chats: set[str]` with a set tracking `worker_key` values instead of `chat_id` values. The existing `_starting_workers` dedup in `_ensure_worker` handles any remaining races within the cooperative event loop.

### Race 2: Pop lock key collision between pm and dev-no-slug
**Location:** `agent_session_queue.py:507` (`_acquire_pop_lock`)
**Trigger:** A PM session and an unslugged dev session both route to `project_key`. They share the pop lock key `worker:pop_lock:{project_key}`. This is correct — exactly one of them should win and the other should retry.
**Data prerequisite:** Both sessions must be pending before either is popped.
**State prerequisite:** Only one `_worker_loop` exists for this `project_key`.
**Mitigation:** Sharing the pop lock is intentional for project-keyed sessions. The single-worker guarantee (via `_ensure_worker`) means only one `_pop_agent_session` call runs at a time for a given key — the lock is a TOCTOU guard for multi-process scenarios (watchdog + worker), not for concurrent workers.

## No-Gos (Out of Scope)

- Changing `chat_id` field semantics on `AgentSession` — it remains the Telegram thread identifier
- Per-slug worker keys for dev sessions — current `chat_id` keying for slugged dev is sufficient
- Priority lanes or separate queues for pm vs. dev sessions — single FIFO queue per key, priority within queue
- Any change to `_global_session_semaphore` — remains global and unchanged

## Update System

No update system changes required — this is a purely internal worker routing change. No new dependencies, no config changes, no migration steps needed.

## Agent Integration

No agent integration required — this is a bridge-internal change to the worker loop routing. The agent continues to be invoked via `_execute_agent_session` with the same interface.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` — add section documenting `worker_key` routing: the three-way decision table, why `chat_id` is not the isolation key, and the two worker loop archetypes (project-keyed vs. chat-keyed)
- [ ] Update inline docstrings for `_ensure_worker`, `_worker_loop`, and `_pop_agent_session` to reference `worker_key` instead of `chat_id`

## Success Criteria

- [ ] `AgentSession.worker_key` is a computed `@property`; no new stored field added
- [ ] Two PM sessions for the same `project_key` arriving on different `chat_id`s execute sequentially
- [ ] Two dev sessions with `slug` set for the same `project_key` on different `chat_id`s execute concurrently
- [ ] A dev session without `slug` serializes with PM sessions for the same `project_key`
- [ ] Teammate sessions on any `chat_id` always execute concurrently (no regression)
- [ ] Pub/sub payload includes `worker_key`; `_session_notify_listener` uses it without a Redis lookup
- [ ] Redis pop lock key updated to `worker:pop_lock:{worker_key}`
- [ ] All existing worker serialization tests pass; new tests cover PM/dev cross-chat behavior
- [ ] `docs/features/bridge-worker-architecture.md` updated to document `worker_key` routing
- [ ] Tests pass (`/do-test`)
- [ ] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (worker-key)**
  - Name: worker-key-builder
  - Role: Implement `AgentSession.worker_key` property and propagate through all call sites in `agent_session_queue.py` and `worker/__main__.py`
  - Agent Type: async-specialist
  - Resume: true

- **Test Engineer (worker-key)**
  - Name: worker-key-test-engineer
  - Role: Update existing tests and add new cross-chat PM serialization and dev worktree parallelism tests
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (worker-key)**
  - Name: worker-key-documentarian
  - Role: Update `docs/features/bridge-worker-architecture.md` with `worker_key` routing documentation
  - Agent Type: documentarian
  - Resume: true

- **Validator (worker-key)**
  - Name: worker-key-validator
  - Role: Verify all acceptance criteria, run test suite, confirm no regressions
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Add `worker_key` property to `AgentSession`
- **Task ID**: build-worker-key-property
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session.py::TestWorkerKeyProperty` (create)
- **Assigned To**: worker-key-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `worker_key` computed `@property` to `models/agent_session.py` using the three-way logic: teammate → `chat_id`, pm → `project_key`, dev-with-slug → `chat_id`, dev-no-slug → `project_key`. Fallback: `self.chat_id or self.project_key` when `session_type` is `None`.
- Add unit tests in `tests/unit/test_agent_session.py` for all four `session_type`/`slug` combinations

### 2. Update `_pop_agent_session` to handle project-keyed workers
- **Task ID**: build-pop-agent-session
- **Depends On**: build-worker-key-property
- **Validates**: `tests/integration/test_worker_concurrency.py`
- **Assigned To**: worker-key-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- Update `_pop_agent_session(chat_id: str)` to accept `worker_key: str` parameter
- When `worker_key == session.project_key`: filter `AgentSession.query.filter(project_key=project_key, status="pending")` then filter to pm + dev-no-slug in-memory; use `worker:pop_lock:{worker_key}`
- When `worker_key` is a `chat_id` (teammate, dev-with-slug): existing `chat_id` filter; use `worker:pop_lock:{worker_key}`
- Update `_pop_agent_session_with_fallback` with same branching
- Update `_acquire_pop_lock` and `_release_pop_lock` to use `worker_key` parameter

### 3. Update all `_ensure_worker` call sites
- **Task ID**: build-ensure-worker-callsites
- **Depends On**: build-worker-key-property
- **Validates**: `tests/unit/test_worker_entry.py`
- **Assigned To**: worker-key-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- `_ensure_worker` function: rename parameter from `chat_id` to `worker_key` throughout; update `_active_workers` and `_active_events` keys
- `_worker_loop`: rename `chat_id` parameter to `worker_key`; update all internal references including log prefixes to `[worker:{worker_key}]`
- Update all eight call sites: lines 1276, 1328, 1510, 1750, 2288, 2323, 2408 in `agent_session_queue.py`; line 220 in `worker/__main__.py`
- Health check locals (`worker_key = entry.chat_id or entry.project_key`) at lines 1199, 1289, 3563: replace with `entry.worker_key`
- `worker/__main__.py` startup loop: replace `chat_id = session.chat_id or session.project_key` with `wk = session.worker_key`; replace `started_chats` set with `started_workers` set keyed by `worker_key`

### 4. Update pub/sub payload and listener
- **Task ID**: build-pubsub-payload
- **Depends On**: build-worker-key-property
- **Validates**: `tests/unit/test_agent_session_queue.py` (if applicable)
- **Assigned To**: worker-key-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- `_publish_session_notification`: add `"worker_key": session.worker_key` to the JSON payload (requires the `AgentSession` object to be accessible — currently only `chat_id` and `session_id` are passed; fetch the session or pass `worker_key` as a parameter)
- `_session_notify_listener`: extract `worker_key = data.get("worker_key") or data.get("chat_id")` from payload; call `_ensure_worker(worker_key)` and look up `_active_events.get(worker_key)`

### 5. Add cross-chat integration tests
- **Task ID**: build-tests-cross-chat
- **Depends On**: build-pop-agent-session, build-ensure-worker-callsites
- **Validates**: `tests/integration/test_worker_concurrency.py` (new tests)
- **Assigned To**: worker-key-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `TestPMProjectKeySerialization`: two PM sessions with different `chat_id`s and same `project_key` — assert peak concurrent ≤ 1
- Add `TestDevWorktreeParallelism`: two dev sessions with `slug` set and different `chat_id`s, same `project_key` — assert both execute concurrently (peak concurrent == 2)
- Update `TestPerChatSerialization` and `TestJobHealthCheck` per the Test Impact section

### 6. Update documentation
- **Task ID**: document-worker-key
- **Depends On**: build-ensure-worker-callsites
- **Assigned To**: worker-key-documentarian
- **Agent Type**: documentarian
- **Parallel**: true
- Update `docs/features/bridge-worker-architecture.md`: add `worker_key` routing section with decision table (session_type × slug → worker_key), explain why `chat_id` is not the isolation model, document the two worker loop archetypes
- Update inline docstrings for `_ensure_worker`, `_worker_loop`, and `_pop_agent_session`

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests-cross-chat, document-worker-key
- **Assigned To**: worker-key-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ tests/integration/ -x -q` — all pass
- Run `python -m ruff check . && python -m ruff format --check .` — clean
- Verify `AgentSession.worker_key` property exists and has no stored field added
- Verify `docs/features/bridge-worker-architecture.md` contains `worker_key` section

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ tests/integration/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| worker_key property exists | `python -c "from models.agent_session import AgentSession; s = AgentSession.__dict__; assert 'worker_key' in {k: v for k, v in vars(AgentSession).items() if isinstance(v, property)}"` | exit code 0 |
| No new stored field | `python -c "from models.agent_session import AgentSession; assert 'worker_key' not in AgentSession._meta.fields"` | exit code 0 |
| Bridge-worker-architecture docs updated | `grep -q 'worker_key' docs/features/bridge-worker-architecture.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — recon validated all assumptions. Ready for critique.
