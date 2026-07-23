# Agent Session Queue

**Status**: Complete

## Module Structure (Post-Refactor)

`agent/agent_session_queue.py` was split from 5545 lines into six focused modules in PR #1051
(issue #1023). The file now sits at ~1709 lines and owns only the queue dispatch surface.

### Extracted Modules

| Module | LOC | One-sentence purpose |
|--------|-----|----------------------|
| `agent/session_state.py` | ~84 | Shared mutable session-tracking state for the worker — prevents circular imports between executor and health modules. |
| `agent/session_revival.py` | ~276 | Revival detection, cooldown tracking, and stale branch cleanup for AgentSession. |
| `agent/session_pickup.py` | ~462 | Session selection, pop locking, startup steering drain, dependency readiness checks, and real-Chrome slot gate (`_real_chrome_slot_busy()` defers new `requires_real_chrome` sessions while one is already running). |
| `agent/session_health.py` | ~1231 | Periodic health monitoring, no-progress detection, orphan cleanup, and startup recovery. |
| `agent/session_completion.py` | ~535 | Post-execution lifecycle: session finalization, parent transitions, dev completion handling, and continuation-PM creation. |
| `agent/session_executor.py` | ~1398 | Core session execution: CLI harness subprocess lifecycle, turn-boundary steering, nudge/re-enqueue paths, and calendar heartbeat. |
| `agent/agent_session_queue.py` | ~1709 | Queue dispatch surface — the entry points that bridge and worker import. |

### Module Relationship

```
worker/__main__.py
    └── agent_session_queue.py  (entry points: enqueue, worker loop, callbacks)
            ├── session_state.py        (shared globals: SessionHandle, _active_sessions, etc.)
            ├── session_pickup.py       (pop + lock + steering drain; imports session_state)
            ├── session_revival.py      (revival + cooldown; standalone, no agent/ deps)
            ├── session_health.py       (health monitor + recovery; imports session_state)
            ├── session_executor.py     (execute loop; imports session_state, session_pickup)
            └── session_completion.py   (finalization + continuation-PM; imports session_state)
```

All six modules re-export their public symbols from `agent_session_queue.py` for backward
compatibility — existing callers (bridge, tools, tests) continue to import from
`agent.agent_session_queue` without change.

### Import Rules

- `session_state.py` imports ONLY from stdlib and `models/` — never from other `agent/` modules
- `session_revival.py` has no `agent/` imports (it uses `subprocess` + `models/`)
- `session_health.py` and `session_completion.py` use deferred imports (`# noqa: PLC0415`)
  to break circular dependencies with `session_executor.py`
- Bridge hooks (`bridge.telegram_bridge`) always use inline deferred imports in all modules

## KeyField Index Corruption Fix

Popoto's `KeyField.on_save()` only adds the object key to the new status index set -- it never removes from the old one. This means in-place status mutations like `job.status = "running"; await job.async_save()` leave stale entries in the previous index set, causing ghost sessions and double-processing.

### Delete-and-Recreate Pattern (Historical)

> **Note**: This section describes the original design. Status transitions now use in-place `IndexedField` mutation via `transition_status()` in `models/session_lifecycle.py` for all non-KeyField status changes. Delete-and-recreate is only required when a KeyField (e.g., `parent_agent_session_id`) must change, since Popoto cannot mutate KeyFields in-place. See `models/session_lifecycle.py` and issue #783 for the migration history.

The original delete-and-recreate pattern was:

```python
fields = _extract_agent_session_fields(job)
await job.async_delete()        # removes from old index via on_delete
fields["status"] = "running"
new_job = await AgentSession.async_create(**fields)  # adds to new index via on_save
```

The `_extract_agent_session_fields()` helper reads all non-auto fields (56+) from an AgentSession instance for recreation. The `status` field is included for defense-in-depth: any remaining delete-and-recreate path (e.g., health check orphan-fixing that reparents sessions) preserves the original status instead of defaulting to `"pending"`. Callers that intentionally override status (retry, nudge fallback) set `fields["status"]` explicitly after extraction.

## Worker Drain Guard (Event-Based)

The worker loop uses an Event-based drain strategy to reliably pick up pending sessions after completing each job. This replaces the original 100ms sleep-and-retry approach which was insufficient to handle the thread-pool race between Popoto's `async_create` index writes and `async_filter` reads.

### How It Works

1. **asyncio.Event notification**: `enqueue_agent_session()` signals a per-chat `asyncio.Event` after pushing a session. The event is level-triggered (stays set until cleared), so signals during job execution are not lost.

2. **Event-based wait**: After completing a session, `_worker_loop()` clears the event and waits for it with a `DRAIN_TIMEOUT` (configurable constant, default 1.5s). If the event fires, the worker pops the next session via `_pop_agent_session()`.

3. **Sync Popoto fallback**: If the timeout expires without an event, `_pop_agent_session_with_fallback()` runs a synchronous `AgentSession.query.filter()` call that bypasses `to_thread()` scheduling. This eliminates the thread-pool race that caused the original index visibility bug.

4. **Exit-time safety check**: Before exiting with "Queue empty", the worker runs one final `_pop_agent_session_with_fallback()` and logs a WARNING if orphaned pending jobs are found.

### Key Functions

| Function | Purpose |
|----------|---------|
| `_pop_agent_session_with_fallback(worker_key, is_project_keyed)` | Tries `_pop_agent_session()` first, then sync Popoto query as fallback |
| `DRAIN_TIMEOUT` | Module constant (1.5s) controlling the Event wait timeout |
| `_active_events` | Dict mapping worker_key to asyncio.Event for worker notification |

### Why the Original Drain Guard Failed

The original 100ms sleep relied on `_pop_agent_session()` (which uses `async_filter` via `to_thread()`) finding the session on retry. But the root cause is a thread-pool scheduling race: `async_create` writes the hash and index entries via multiple Redis commands in a thread, and `async_filter` reads the index intersection in a separate thread. The 100ms window was too short, and both calls suffered from the same `to_thread()` race. The sync fallback bypasses this entirely.

## Pop-Loop Exception Resilience

No exception raised while popping/transitioning a **single** session may terminate the whole `_worker_loop` task — if it did, every other pending session for that `worker_key` would be stranded until the next restart or the ~5-min session-health sweep. The primary pop site (`_pop_agent_session()`, which drives `pending → running`) has two typed skip-and-continue handlers before the final `except BaseException: raise`:

| Exception | Issue | Trigger | Handling |
|-----------|-------|---------|----------|
| `StatusConflictError` | #1803 | A session is killed/transitioned out from under the pop (race between reading `status=pending` and `transition_status(→running)` finding it terminal). | Log, bounded per-`session_id` escalation (delete stale terminal duplicate, then last-resort cancel), release slot, `continue`. |
| `ModelException` (Popoto) | #2088 | A **corrupted** record (all fields `None` except `status="pending"`) fails the `pending → running` `save()` — Popoto raises `"Model instance parameters invalid. Failed to save."` from `pre_save()` when `is_valid()` is `False`. | Log, best-effort route to `cleanup_corrupted_agent_sessions()` (return value ignored), release slot **before** the backoff `await`, bounded per-`worker_key` spin guard, `await asyncio.sleep(CORRUPTED_POP_BACKOFF_SECONDS)`, `continue`. |

`ModelException` is the **base** of Popoto's save/transition family (`KeyMutationError`, `SkipSaveException` subclass it), so the single clause covers the whole class of Popoto save/transition failures without swallowing fatal signals (`KeyboardInterrupt`/`CancelledError`) or broad logic bugs — those still crash loudly. The catch is deliberately **not** `except Exception`.

Key design points of the #2088 handler:

- **Reaper return value is ignored.** `cleanup_corrupted_agent_sessions()` is called opportunistically as best-effort head-of-queue cleanup; on the common path it deletes the corrupted record so the next pop returns a healthy co-tenant session. The periodic session-health sweep remains the **authoritative** backstop for anything the reaper cannot delete this tick — the same mechanism that self-heals these records in production today. Interpreting the reaper's `{"corrupted", ...}` return value was the source of four failed critique rounds and was removed at the root. The reaper call is wrapped in `try/except Exception` so a reaper failure degrades to a no-op instead of escaping the clause (a sibling `except` does not catch it).
- **Reaper is cooldown-gated (issue #2101).** `cleanup_corrupted_agent_sessions()` runs an **unconditional** `AgentSession.repair_indexes()` rebuild (issue #1361), and that rebuild re-adds every identity-less `AgentSession:*` hash to the `$IndexF:AgentSession:status:pending` index (`status` defaults to `"pending"`). A record stuck at the queue head raises `ModelException` on every ~2s pop, so calling the reaper on **every** corrupted pop re-drove a full index rebuild every 2s and re-inflated the pending index into a runaway leak + worker crash-loop. The handler now gates the reaper behind a per-`worker_key` cooldown (`CORRUPTED_POP_REAP_COOLDOWN_S`, default 300s, env-overridable): the **first** corrupted pop reaps immediately, but subsequent pops within the cooldown skip the rebuild and rely on the periodic sweep. The cooldown timestamp (`_corrupted_pop_last_reap`) is loop-local and reset on any successful pop. This is the accelerant fix — the deeper `repair_indexes` re-inflation and the popoto delete-ordering `srem` asymmetry that manufactures the phantoms are tracked separately under #2101.
- **Core re-inflation fix — A1 (issue #2101, generalized in #2207).** The `repair_indexes()` rebuild installs a transient guard, for the duration of the `rebuild_indexes()` call, on the `on_save` of **every** `IndexedField` (`status`, `task_type`, `claude_session_uuid`, `claude_pid` today — enumerated at runtime from `cls._meta`, never hardcoded): identity-less (`session_id`-less) hashes are refused re-add to any of those fields' `$IndexF:AgentSession:*` sets (counted via `AgentSession._last_quarantined_identityless`, summed across fields, + WARNING log), while healthy records delegate to unmodified popoto. The guard is scoped to the rebuild path only — live `AgentSession(...).save()` still indexes a legitimate new pending session (inverse-bug guard) — and install is wrapped in a non-reentrant `_repair_lock` so overlapping `repair_indexes()` calls can't race the shim install/restore. Gone-hash orphans are cleared by the whole-`$IndexF`-key delete-and-rebuild, not by A1. Solution B (popoto delete-ordering `srem` fix) is deferred: A1 converges the index in one pass. See [AgentSession Pending-Index Phantom Leak](agentsession-pending-index-leak.md) and [Popoto Index Hygiene](popoto-index-hygiene.md#a1-rebuild-guard-identity-less-phantom-re-inflation).
- **Slot released before the backoff.** `release_unbound()` runs after the reaper call and before the `asyncio.sleep`, honoring the release-before-`await` invariant so the backoff never holds the global concurrency slot.
- **Spin guard is keyed by `worker_key`, coarser than #1803's `session_id` keying by necessity** — a `ModelException` carries no `session_id`, and a fully-corrupted record may have none. It is a plain consecutive-corrupted-pop counter (loop-local `_corrupted_pop_count` / `_corrupted_pop_escalated`, reset on any successful pop) that emits a one-shot `logger.error` naming the stuck `worker_key` after `CORRUPTED_POP_ESCALATE_N` consecutive corrupted pops. The handler never dereferences `session_id`.

`CORRUPTED_POP_ESCALATE_N`, `CORRUPTED_POP_BACKOFF_SECONDS`, and `CORRUPTED_POP_REAP_COOLDOWN_S` are provisional/tunable module constants. Regression coverage lives in `tests/unit/test_worker_persistent.py` (`test_model_exception_during_pop_does_not_crash_loop`, `test_repeated_corrupted_pop_escalates_without_crash`, `test_corrupted_pop_reaper_throttled_by_cooldown`, and the reaper-failure / guard-reset cases).

## Startup Session Cleanup and Recovery

At startup, three cleanup passes run before session processing begins. These are called exclusively from `worker/__main__.py` — the bridge does not call them.

0. **Future-dated timestamp heal** (`AgentSession._heal_future_updated_at()`): Scans all sessions for `updated_at` values in the future (written by the pre-#1645 `auto_now=True` producer on non-UTC hosts) and clamps them to `max(created_at, now)`. Idempotent — a re-run clamps only still-future records. Fail-soft: wrapped in `try/except` so a Redis hiccup cannot abort the startup sequence. The heal count is logged at INFO level even when zero (confirms the heal ran cleanly). See [Session Lifecycle → Timestamp Convention](session-lifecycle.md#timestamp-convention--updated_at-is-explicit-utc) for details.

1. **Corrupted session cleanup** (`cleanup_corrupted_agent_sessions()`): Detects sessions with invalid IDs (e.g., length 60 instead of expected 32 for uuid4) or sessions whose `.save()` raises a validation-type exception. Before iteration, `query.all()` results pass through `_filter_hydrated_sessions()` to drop phantom records — instances whose fields are still Popoto `Field` descriptors (orphan `$IndexF` members pointing to deleted hashes). Real corrupt records are deleted via the ORM (no raw-Redis fallback), then `AgentSession.repair_indexes()` clears orphan `$IndexF:AgentSession:*` members at the source before rebuilding indexes. Also runs hourly as the `agent-session-cleanup` reflection and during `/update`. As of issue #1271 the same call also performs the cross-process orphan reap pass before returning, so the function's signature is `() -> dict[str, int]` (`{"corrupted": int, "orphans": int}`); `worker/__main__.py` and `scripts/update/run.py` keep an `isinstance(result, dict)` defensive fallback for the older `int` return shape. **As of issue #1361, `repair_indexes()` runs unconditionally on every tick** (the previous `cleaned > 0 or phantoms_filtered > 0` gate from PR #1078 has been removed) and a per-status drift pre-scan emits `agent_session.indexed_field.stale_members` analytics with `dimensions={"status": <status>}` so operators can observe drift accumulation in indexed fields like `waiting_for_children`. The pre-scan covers the `status` index only; other `$IndexF:AgentSession:*` indexes are still cleaned by `repair_indexes()` itself but do not get a per-field drift metric. See [Bridge Self-Healing §7](bridge-self-healing.md#7-agent-session-cleanup-agentsession_healthpy) for the phantom-filter rationale (issue #1069) and the cross-process reap details.

2. **Interrupted session recovery** (`_recover_interrupted_agent_sessions_startup()`): Resets stale running sessions to pending with high priority. Sessions started within the last `AGENT_SESSION_HEALTH_MIN_RUNNING` seconds (300s) are skipped — they may have been picked up by a worker in the current process before startup recovery fired. Sessions with `started_at=None` (missing or corrupt) are always recovered. Uses the same timing guard as the periodic health check to prevent orphaning SDK subprocesses (issue #727).

3. **Orphaned process cleanup** (`_cleanup_orphaned_claude_processes()`): One-line shim retained for backward compatibility with the worker startup wiring; delegates entirely to `_reap_orphan_session_processes()` (issue #1271). Defined in `agent/session_health.py` (re-exported from `agent_session_queue.py`). The reaper scans the OS process table for `claude_agent_sdk/_bundled/claude` and `mcp_servers/*.py` processes whose `PPID == 1`, gates each candidate on a 30-min `last_heartbeat_at` freshness check via `AgentSession.find_by_claude_pid()`, and self-protects every live worker via the `worker:registered_pid:*` Redis skip-set. The same reaper also fires hourly inside the `agent-session-cleanup` reflection (item 1 above) — startup is no longer the only call site. See [Agent Session Health Monitor → Cross-Process Orphan Reap](agent-session-health-monitor.md) for the full design.

### Caller: Worker Only

The following execution functions are called exclusively from `worker/__main__.py`:
- `_ensure_worker(worker_key, is_project_keyed)` — spawns per-worker-key worker loops
- `_recover_interrupted_agent_sessions_startup()` — startup session recovery
- `_agent_session_health_loop()` — background health monitor (safety net: every 5 min)
- `_session_notify_listener()` — pub/sub subscriber for immediate session pickup (~1s latency); creates a dedicated `redis.Redis` connection with `socket_timeout=None` to avoid the global pool's `socket_timeout=settings.timeouts.redis_socket_s` (default 5s, `.env`-overridable via `TIMEOUTS__REDIS_SOCKET_S` — see [Config Timeout Catalog](config-timeout-catalog.md)) (issue #824); verifies `PUBSUB NUMSUB >= 1` after subscribe (up to 3 retries, ~300 ms) and returns early on persistent failure so the outer loop re-subscribes after its 5 s backoff (issue #1804)
- `_cleanup_orphaned_claude_processes()` — startup shim that delegates to `_reap_orphan_session_processes()` (issue #1271)
- `register_worker_pid()` — write `worker:registered_pid:{hostname}:{pid}` (TTL 24h) so the cross-process reaper never SIGKILLs a live worker
- `AgentSession.rebuild_indexes()` — repair Redis index entries

The bridge only calls `enqueue_agent_session()` and `register_callbacks()`. See [Bridge/Worker Architecture](bridge-worker-architecture.md).

## Revival Chat Scoping Fix

`check_revival()` was rewritten to scope revival detection strictly to the originating Telegram chat. The previous implementation listed all `session/*` git branches across the entire repository (via `git branch --list "session/*"`), which caused revival notifications to bleed across unrelated Telegram chats whenever any project had an open session branch.

### New Approach

Instead of scanning git branches globally, `check_revival()` queries Redis (Popoto) for jobs filtered by `project_key` + `status` (pending or running), then filters by `chat_id` in Python:

```python
for status in ("pending", "running"):
    jobs = AgentSession.query.filter(project_key=project_key, status=status)
    for session in sessions:
        if str(session.chat_id) == chat_id_str:
            branch = _session_branch_name(session.session_id)
            branches.append(branch)
```

Branch existence is then verified individually in git (`git branch --list <specific-branch>`), rather than enumerating all branches. Redis is the sole source of truth for session state.

### Behavioral Change

| Before | After |
|--------|-------|
| All `session/*` branches visible to any chat | Only branches belonging to the calling `chat_id` |
| Revival could notify wrong chat | Revival only notifies the chat that owns the session |
| File-based state checked as fallback | Redis is the sole source of truth |

## Deferred Execution (`scheduled_at`)

The `AgentSession` model has a `scheduled_at` field (UTC datetime). `_pop_agent_session()` skips jobs where `scheduled_at > now()`, enabling deferred execution. Jobs with `scheduled_at` in the past or `None` are eligible immediately.

## Priority Model

Four-tier priority system: `urgent > high > normal > low`. Default priority is `normal` for all new jobs. Within the same tier, FIFO ordering (oldest first). Recovery jobs use `high`; catchup/revival use `low`.

Priority ranking constant: `PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}`

## Self-Scheduling

The agent can enqueue jobs mid-conversation via `tools/agent_session_scheduler.py`. See [Agent Session Scheduling](agent-session-scheduling.md) for details.

## Parent-Child Job Hierarchy

Jobs support parent-child decomposition via the `parent_agent_session_id` field on `AgentSession`.

### Hierarchy Fields

| Field | Type | Description |
|-------|------|-------------|
| `parent_agent_session_id` | `KeyField(null=True)` | Links child to parent session. Indexed for efficient queries. |
| `stable_agent_session_id` | `KeyField(null=True)` | UUID set once at creation, never changes on delete-and-recreate. Used as dependency reference key. |
| ~~`depends_on`~~ | ~~`ListField(null=True)`~~ | Removed. Dependency tracking was removed from the model. |
| `commit_sha` | `@property` | HEAD commit SHA for checkpoint/restore. Derived from the latest `checkpoint` event in `session_events` — not a stored Popoto field. |

### Status Values

| Status | Description |
|--------|-------------|
| `waiting_for_children` | Parent has spawned children and is waiting for them to complete |
| `cancelled` | Explicitly cancelled by PM; blocks dependents (same as `failed`) |

### Completion Propagation

When a child session completes, `_complete_agent_session()` calls `_finalize_parent_sync()` **before** deleting the child from Redis. The completing child's intended terminal status is passed as a parameter (since its Redis status is still "running" at that point). `_finalize_parent_sync()` queries all siblings and uses the override status for the completing child. When all siblings are terminal (`completed` or `failed`), the parent transitions to `completed` (all succeeded) or `failed` (any failed). Only after finalization does `_complete_agent_session()` delete the child.

Parent finalization mutates the parent's status in place via `transition_status()` (since `status` is an IndexedField, not a KeyField). `_pop_agent_session()` likewise uses in-place mutation -- delete-and-recreate is only required for callers that change a KeyField (e.g., `parent_agent_session_id` reparenting in retry/orphan-fix paths).

### Health Monitor Extensions

The periodic health check (`_agent_session_hierarchy_health_check()`, called from `_agent_session_health_loop()` every 5 minutes) detects and self-heals:

- **Orphaned children**: `parent_agent_session_id` points to non-existent session -- cleared (status preserved via `_extract_agent_session_fields()` to prevent zombie loops)
- **Stuck parents**: `waiting_for_children` with all children terminal -- auto-finalized

See [Agent Session Scheduling](agent-session-scheduling.md) for usage details and CLI commands.

## See Also

- `docs/features/scale-agent-session-queue-with-popoto-and-worktrees.md` -- Original agent session queue architecture
- `docs/features/agent-session-scheduling.md` -- Agent-initiated scheduling tool
- `docs/features/agent-session-model.md` -- AgentSession model fields and lifecycle
- `docs/features/session-lifecycle.md` -- Session state machine, zombie loop prevention
- `agent/agent_session_queue.py` -- Queue dispatch surface (entry points)
- `agent/session_executor.py` -- Core execute loop
- `agent/session_health.py` -- Health monitor and recovery
- `agent/session_completion.py` -- Finalization and continuation-PM
- `agent/session_pickup.py` -- Pop locking and steering drain
- `agent/session_revival.py` -- Revival detection
- `agent/session_state.py` -- Shared mutable globals
