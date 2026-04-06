# Session Recovery Mechanisms

Catalogue of all 7 session recovery mechanisms, their triggers, terminal status safety, and guard implementations.

## Overview

The session system has 7 mechanisms that can revive, recover, or re-enqueue sessions. After the zombie loop fix (PR #703) and lifecycle consolidation (PR #721), a systematic audit (issue #723) verified that all mechanisms respect terminal session states.

**Terminal statuses**: `completed`, `failed`, `killed`, `abandoned`, `cancelled`

## Active Mechanisms (5)

### 1. Startup Recovery (`_recover_interrupted_agent_sessions_startup`)

| Property | Value |
|----------|-------|
| Location | `agent/agent_session_queue.py` |
| Trigger | Bridge process startup |
| What it does | Resets all `running` sessions to `pending` (orphaned from previous process) |
| Terminal safety | **Safe by query scope** -- only queries `status="running"`, never touches terminal sessions |
| Guard | Query filter (`status="running"`) |

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

## Confirmed Safe Mechanisms (2)

### 6. Session Watchdog

| Property | Value |
|----------|-------|
| Location | `bridge/session_watchdog.py` (PostToolUse hook) |
| Trigger | Every tool use during agent execution |
| What it does | Monitors for stuck loops, sets `watchdog_unhealthy` flag |
| Terminal safety | **Safe by design** -- only sets flags on the running session, never mutates status. The flag feeds into `determine_delivery_action()` which routes to `deliver` instead of `nudge`. |
| Guard | N/A (no status mutation) |

### 7. Bridge Watchdog

| Property | Value |
|----------|-------|
| Location | `monitoring/bridge_watchdog.py` |
| Trigger | Separate launchd service (every 60s) |
| What it does | Monitors bridge process health, restarts if unresponsive |
| Terminal safety | **Safe by design** -- has no `AgentSession` imports, operates at process level only |
| Guard | N/A (no session awareness) |

## Guard Implementation: `transition_status()` `reject_from_terminal`

The `transition_status()` function in `models/session_lifecycle.py` now has a `reject_from_terminal` parameter (default `True`):

- **Default behavior**: Raises `ValueError` when the session's current status is terminal, preventing accidental `completed->pending` or similar transitions
- **Explicit opt-out**: Callers that need terminal-to-non-terminal transitions pass `reject_from_terminal=False`

### Callers requiring `reject_from_terminal=False`

| Caller | Transition | Reason |
|--------|-----------|--------|
| `_mark_superseded()` in `enqueue_agent_session()` | `completed->superseded` | Intentional bookkeeping when a new session replaces an old one |
| `.claude/hooks/user_prompt_submit.py` | `completed->running` | User types new prompt into a completed local session |

All other callers use the default `reject_from_terminal=True`.

## Race Conditions

### Status change between `determine_delivery_action()` and `_enqueue_nudge()`

- **Window**: External process finalizes session between delivery decision and nudge enqueue
- **Mitigation**: `_enqueue_nudge()` re-reads session status from Redis at entry and after query, returns early if terminal

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
| `test_watchdog_unhealthy_flag_routes_to_deliver` | session watchdog | Flag routes to deliver, not nudge |
| `test_bridge_watchdog_has_no_agent_session_import` | bridge watchdog | No AgentSession imports |

## Related

- [Session Lifecycle](session-lifecycle.md) -- State machine and lifecycle module
- [Agent Session Health Monitor](agent-session-health-monitor.md) -- Health check details
- [Bridge Self-Healing](bridge-self-healing.md) -- Bridge watchdog and crash recovery
- Issue #723 -- Original audit issue
- PR #703 -- Zombie loop fix (hierarchy health check vector)
- PR #721 -- Lifecycle consolidation
