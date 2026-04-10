# Session Recovery Mechanisms

Catalogue of all 8 session recovery mechanisms, their triggers, terminal status safety, and guard implementations.

## Overview

The session system has 8 mechanisms that can revive, recover, or re-enqueue sessions. After the zombie loop fix (PR #703) and lifecycle consolidation (PR #721), a systematic audit (issue #723) verified that all mechanisms respect terminal session states. PR #730 added the intake path terminal guard (8th mechanism).

**Terminal statuses**: `completed`, `failed`, `killed`, `abandoned`, `cancelled`

## Active Mechanisms (6)

### 1. Startup Recovery (`_recover_interrupted_agent_sessions_startup`)

| Property | Value |
|----------|-------|
| Location | `agent/agent_session_queue.py` |
| Trigger | Worker process startup (`worker/__main__.py`) |
| What it does | Resets stale `running` sessions to `pending` (orphaned from previous process) |
| Terminal safety | **Safe by query scope** -- only queries `status="running"`, never touches terminal sessions |
| Guard | Query filter (`status="running"`) + timing guard (`AGENT_SESSION_HEALTH_MIN_RUNNING`, 300s) |
| Timing guard | Sessions with `started_at` within the last 300s are skipped -- they were likely started by a worker in the current process, not orphaned from the previous one. Sessions with `started_at=None` are always recovered. Matches the same guard used by the periodic health check (mechanism 2). Added by issue #727 to fix a race where a worker picks up a session before startup recovery fires. |

### 2. Health Check (`_agent_session_health_check`)

| Property | Value |
|----------|-------|
| Location | `agent/agent_session_queue.py` |
| Trigger | Periodic timer (every 60s) |
| What it does | Recovers stuck `running` sessions (dead worker, timeout) back to `pending`; starts workers for stalled `pending` sessions |
| Terminal safety | **Safe by query scope** -- only queries `status="running"` and `status="pending"` |
| Guard | Query filter (only non-terminal statuses) + `transition_status()` with default `reject_from_terminal=True` |

### 3. Hierarchy Health Check (`_agent_session_hierarchy_health_check`)

| Property | Value |
|----------|-------|
| Location | `agent/agent_session_queue.py` |
| Trigger | Periodic timer |
| What it does | Fixes orphaned children (parent deleted) and stuck parents (all children terminal) |
| Terminal safety | **Safe** -- orphan fix preserves original status via `_extract_agent_session_fields`; stuck parent fix only finalizes (terminal transition), never revives |
| Guard | `status` field in `_AGENT_SESSION_FIELDS` preserves terminal status during delete-and-recreate |

### 4. Nudge Re-enqueue (`_enqueue_nudge`)

| Property | Value |
|----------|-------|
| Location | `agent/agent_session_queue.py` |
| Trigger | Agent output during execution (auto-continue) |
| What it does | Re-enqueues session with nudge message for continued execution |
| Terminal safety | **Guarded** -- three-layer defense |
| Guards | (1) Entry guard checks `session.status in TERMINAL_STATUSES`, returns early. (2) Main path re-reads session from Redis after query, returns early if terminal. (3) Fallback path (bypasses `transition_status`) has independent terminal check before `async_create`. |

### 5. Delivery Action Router (`determine_delivery_action`)

| Property | Value |
|----------|-------|
| Location | `agent/agent_session_queue.py` |
| Trigger | Every agent output (decides deliver vs nudge) |
| What it does | Returns `deliver_already_completed` for terminal sessions, preventing nudge paths |
| Terminal safety | **Guarded** -- checks `session_status in TERMINAL_STATUSES` (all 5 statuses) |
| Guard | First check in function: `if session_status in _TERMINAL_STATUSES` |

### 6. Message Intake Path (`handle_new_message` / `_push_agent_session`)

| Property | Value |
|----------|-------|
| Location | `bridge/telegram_bridge.py` |
| Trigger | New Telegram message received (non-reply-to) |
| What it does | Resolves the current session for the thread, calls `enqueue_agent_session()` to add a new work item |
| Terminal safety | **Guarded** -- intake terminal guard |
| Guard | Before calling `enqueue_agent_session()`, checks `session.status in TERMINAL_STATUSES`; if terminal, skips enqueue and creates a fresh session instead. Reply-to messages bypass the guard intentionally (explicit resumption of a prior session). |

## Confirmed Safe Mechanisms (2)

### 7. Session Watchdog

| Property | Value |
|----------|-------|
| Location | `bridge/session_watchdog.py` (PostToolUse hook) |
| Trigger | Every tool use during agent execution |
| What it does | Monitors for stuck loops, sets `watchdog_unhealthy` flag |
| Terminal safety | **Safe by design** -- only sets flags on the running session, never mutates status. The flag feeds into `determine_delivery_action()` which routes to `deliver` instead of `nudge`. |
| Guard | N/A (no status mutation) |

### 8. Bridge Watchdog

| Property | Value |
|----------|-------|
| Location | `monitoring/bridge_watchdog.py` |
| Trigger | Separate launchd service (every 60s) |
| What it does | Monitors bridge process health, restarts if unresponsive |
| Terminal safety | **Safe by design** -- has no `AgentSession` imports, operates at process level only |
| Guard | N/A (no session awareness) |

## Recovery Ownership

Session recovery is split between two processes: the **worker** and the **bridge-hosted watchdog**. Each non-terminal status has exactly one owner responsible for detecting stuck sessions and recovering them.

The authoritative registry is `RECOVERY_OWNERSHIP` in `models/session_lifecycle.py`. A unit test (`tests/unit/test_recovery_ownership.py`) asserts that every non-terminal status has a registered owner, so adding a new status without declaring ownership breaks CI.

| Status | Owner | Recovery Mechanism |
|--------|-------|--------------------|
| `pending` | worker | `_agent_session_health_check` starts a worker for stalled pending sessions |
| `running` | worker | `_agent_session_health_check` + `_recover_interrupted_agent_sessions_startup` reset to pending |
| `waiting_for_children` | worker | `_agent_session_hierarchy_health_check` finalizes stuck parents |
| `active` | bridge-watchdog | `monitoring/session_watchdog.py` `check_all_sessions` + `check_stalled_sessions` |
| `dormant` | bridge-watchdog | `monitoring/session_watchdog.py` via `check_stalled_sessions` activity check |
| `paused` | bridge-watchdog | `agent/hibernation.py` session-resume-drip |
| `paused_circuit` | bridge-watchdog | `agent/sustainability.py` circuit breaker drip |
| `superseded` | none | Transitional status; superseded sessions are finalized immediately |

**Why the split exists:** The worker process owns execution lifecycle (pending, running, hierarchy). The bridge-hosted watchdog owns monitoring of sessions that are paused or waiting outside the execution loop (active, dormant, paused variants). This split emerged naturally from the bridge/worker separation (PR #826) and is now formally documented here.

**Adding a new non-terminal status:** Add it to `NON_TERMINAL_STATUSES` in `models/session_lifecycle.py`, then add a corresponding entry to `RECOVERY_OWNERSHIP` with the process that will monitor it. The CI test enforces this.

## Guard Implementation: `transition_status()` `reject_from_terminal`

The `transition_status()` function in `models/session_lifecycle.py` now has a `reject_from_terminal` parameter (default `True`):

- **Default behavior**: Raises `ValueError` when the session's current status is terminal, preventing accidental `completed->pending` or similar transitions
- **Explicit opt-out**: Callers that need terminal-to-non-terminal transitions pass `reject_from_terminal=False`

### Callers requiring `reject_from_terminal=False`

| Caller | Transition | Reason |
|--------|-----------|--------|
| `.claude/hooks/user_prompt_submit.py` | `completed->running` | User types new prompt into a completed local session |

Note: `_mark_superseded()` previously passed `reject_from_terminal=False` to convert `completed->superseded`. This override was removed by PR #730 as defense-in-depth — `completed` sessions now remain in their terminal state rather than being re-activated as `superseded`.

All other callers use the default `reject_from_terminal=True`.

## CAS Conflict Detection

As of PR #885 (issue #875), `models/session_lifecycle.py` uses compare-and-set (CAS) semantics to detect concurrent status mutations. Before writing a new status, `update_session()` re-reads the session from Redis and compares the current status against the expected value. If another process changed the status between the caller's read and write, the function raises `StatusConflictError` instead of silently overwriting.

This is a Python-level compare (re-read + status compare before `save()`), not a Redis `WATCH`/`MULTI`/`EXEC` transaction. It closes the most common race windows — two workers finalizing the same session, or a health-check recovery firing while the session is completing — without adding Redis transaction complexity.

Key APIs introduced by the CAS authority upgrade:

| API | Purpose |
|-----|---------|
| `StatusConflictError` | Raised when CAS detects a concurrent status change |
| `get_authoritative_session(session)` | Re-reads session from Redis; returns the freshest copy |
| `update_session(session, new_status, reason, *, expected_status)` | CAS-guarded status transition: re-reads, compares `expected_status`, writes or raises `StatusConflictError` |

Callers that previously did a bare `transition_status()` or `finalize_session()` in concurrent contexts (health checks, nudge re-enqueue, worker completion) now use `update_session()` with an explicit `expected_status` to make the race window detectable rather than silent.

## Race Conditions

### Status change between `determine_delivery_action()` and `_enqueue_nudge()`

- **Window**: External process finalizes session between delivery decision and nudge enqueue
- **Mitigation**: `_enqueue_nudge()` re-reads session status from Redis at entry and after query, returns early if terminal. With CAS, the write itself would raise `StatusConflictError` if the status changed.

### Worker starts session before startup recovery fires (issue #727)

- **Window**: Worker dequeues a pending session and transitions it to `running` in the 1-2 seconds between process start and startup recovery execution
- **Mitigation**: Startup recovery now uses the same `AGENT_SESSION_HEALTH_MIN_RUNNING` (300s) timing guard as the health check. Sessions with `started_at` within the last 300 seconds are skipped. A session started 1-2 seconds ago has `started_at` well within the guard window.

### Revival check finds session about to complete

- **Window**: `check_revival()` finds pending session that completes between query and user response
- **Mitigation**: Revival only sends notification; actual respawn happens later via `queue_revival_agent_session()` -> `enqueue_agent_session()`, by which time the session is terminal and guards catch it

## Known Limitations

- **Redis TTL expiry**: If terminal session records expire from Redis before a revival check, the terminal-sibling filter in `check_revival()` cannot detect them. Revival may proceed for sessions whose completion record has expired. This is acceptable: if the record is gone, there is no reliable way to detect prior completion.

## Test Coverage

All mechanisms are covered by `tests/unit/test_recovery_respawn_safety.py`:

| Test | Mechanism | What it proves |
|------|-----------|---------------|
| `test_terminal_status_returns_already_completed` | determine_delivery_action | All 5 terminal statuses route to `deliver_already_completed` |
| `test_nudge_main_path_skips_terminal` | _enqueue_nudge entry | Terminal sessions blocked before Redis query |
| `test_nudge_fallback_path_skips_terminal` | _enqueue_nudge fallback | Terminal sessions blocked before `async_create` |
| `test_nudge_reread_guard_catches_late_terminal` | _enqueue_nudge re-read | Late terminal status (race) caught after Redis query |
| `test_revival_skips_completed_session_branch` | check_revival | Branches with terminal siblings filtered out |
| `test_rejects_from_terminal_by_default` | transition_status | Default rejects all 5 terminal->non-terminal |
| `test_allows_from_terminal_when_explicitly_permitted` | transition_status | Explicit opt-out works |
| `test_startup_recovery_only_queries_running` | startup recovery | Only queries running, not terminal |
| `test_recent_session_skipped_by_timing_guard` | startup recovery | Sessions started <300s ago are skipped |
| `test_old_session_recovered_by_timing_guard` | startup recovery | Sessions started >300s ago are recovered |
| `test_none_started_at_is_recovered` | startup recovery | Legacy sessions (no started_at) are recovered |
| `test_mixed_recent_and_stale_sessions` | startup recovery | Only stale sessions recovered, recent skipped |
| `test_watchdog_unhealthy_flag_routes_to_deliver` | session watchdog | Flag routes to deliver, not nudge |
| `test_bridge_watchdog_has_no_agent_session_import` | bridge watchdog | No AgentSession imports |
| `TestIntakePathTerminalGuard::test_guard_fires_for_each_terminal_status` | intake path (mechanism 8) | All 5 terminal statuses trigger guard |
| `TestIntakePathTerminalGuard::test_guard_does_not_fire_for_non_terminal_sessions` | intake path | Non-terminal sessions pass through unblocked |
| `TestIntakePathTerminalGuard::test_guard_skipped_for_reply_to_messages` | intake path | Reply-to bypasses guard (explicit resumption) |
| `TestIntakePathTerminalGuard::test_guard_falls_back_gracefully_on_exception` | intake path | Guard failure is non-fatal |
| `TestIntakePathTerminalGuard::test_guard_present_in_telegram_bridge` | intake path | Structural: guard code present in bridge |
| `TestMarkSupersededTerminalGuard::test_completed_to_superseded_is_now_rejected` | _mark_superseded defense-in-depth | completed→superseded now rejected |
| `TestMarkSupersededTerminalGuard::test_mark_superseded_kwarg_removed_from_source` | _mark_superseded defense-in-depth | Structural: override kwarg absent from source |

## Related

- [Session Lifecycle](session-lifecycle.md) -- State machine and lifecycle module
- [Agent Session Health Monitor](agent-session-health-monitor.md) -- Health check details
- [Bridge Self-Healing](bridge-self-healing.md) -- Bridge watchdog and crash recovery
- Issue #875 / PR #885 -- CAS authority upgrade (compare-and-set conflict detection)
- Issue #723 -- Original audit issue
- Issue #727 -- Startup recovery timing guard (race condition fix)
- PR #703 -- Zombie loop fix (hierarchy health check vector)
- PR #721 -- Lifecycle consolidation
