# Harness Startup Retry

When the `claude` CLI binary is missing from PATH, the worker silently re-queues the session up to three times before sending a persona-aligned message to Telegram. This prevents raw Python exception strings from reaching the user and recovers automatically when the PATH issue is transient (e.g., a shell spawned without the correct environment after a bridge restart).

## Problem

The `claude` binary may be absent from PATH in the shell that spawns the harness subprocess. This happens most often when:

- The bridge or worker is restarted in a shell that didn't inherit the right `$PATH` (e.g., a launchd service running without the user's shell profile)
- A deploy temporarily displaces the binary until the next `npm install` or `brew upgrade` completes

**Before this fix:** `_run_harness_subprocess()` caught `FileNotFoundError` and returned a raw error string (`"Error: CLI harness not found — [Errno 2] No such file or directory: 'claude'"`). This string propagated through `get_response_via_harness()` → `do_work()` → `BackgroundTask._run_work()` → `messenger.send()` → Telegram. The user received a technical error string and the original request was silently lost.

## Solution

`do_work()` in `agent/session_executor.py` intercepts the error string before it is delivered. The interception lives in the execution module, not inside `sdk_client.py`, so that the `AgentSession` record is accessible.

### Retry Flow

```
Worker pops AgentSession
  -> get_response_via_harness() returns "Error: CLI harness not found ..."
  -> do_work() detects the prefix
  -> agent_session is None? → return raw (B1 guard, preserve existing behavior)
  -> Read cli_retry_count from agent_session.extra_context (default 0)
  -> count < 3?
       -> Increment cli_retry_count in extra_context
       -> transition_status(agent_session, "pending", "harness-retry")
          (updates existing record in-place — no orphan running record)
       -> _ensure_worker() so the worker picks it up again
       -> logger.warning("[session_id] Harness not found — retry N/3")
       -> return "" (BackgroundTask skips send on empty string)
       -> _harness_requeued = True → finalization block skipped
  -> count >= 3?
       -> return persona-aligned message
       -> BackgroundTask sends it normally to Telegram
```

### Exhaustion Message

After three failed attempts the user receives exactly one message:

> Tried a few times but couldn't get Claude to start — looks like the CLI may not be on PATH. You can resend once that's sorted.

## Key Design Decisions

### Why `transition_status()` instead of delete-and-recreate

`transition_status()` updates the existing `AgentSession` record in-place, which:

- Preserves `extra_context` (including `cli_retry_count`) without a second write
- Keeps a single canonical record — no ghost `status="running"` record left behind for the health monitor to find
- Matches the established contract for non-terminal status moves throughout the codebase

The [stall-retry](stall-retry.md) path uses delete-and-recreate because it needs a fresh `AutoKeyField`-generated ID and a new priority; harness startup retry needs neither.

### Why no backoff delay

A missing binary is typically resolved within seconds (the PATH fix propagates the next time the worker loops). Adding an artificial delay adds latency without benefit. The session goes back to the normal pending queue and is picked up immediately.

### Why the interception is in `session_executor.py`, not `sdk_client.py`

`_run_harness_subprocess()` does not have access to the `AgentSession` model. Keeping the retry logic in the execution module maintains proper separation of concerns and gives full access to `transition_status()`, `_ensure_worker()`, and the session's `extra_context`.

## Retry Counter

`cli_retry_count` is stored as an integer in `AgentSession.extra_context` (the existing `DictField`). No model schema change is required. The counter is incremented and written to `agent_session.extra_context` **before** `transition_status()` is called, guaranteeing the updated count is present on the re-queued record.

## Finalization Guard

When `do_work()` returns `""` after a silent re-queue, `_harness_requeued` is set to `True`. The finalization block at the end of `_execute_agent_session()` checks this flag and returns early — skipping `complete_transcript()` — because `transition_status()` has already moved the session to `"pending"`. Without this guard, the session would be immediately finalized to `"completed"` and become invisible to the worker.

## Constants

Defined at module level in `agent/session_executor.py`:

```python
_HARNESS_NOT_FOUND_PREFIX = "Error: CLI harness not found"
_HARNESS_NOT_FOUND_MAX_RETRIES = 3
```

## What Is NOT Retried

- **Non-FileNotFoundError harness results**: Any other error string (parsing failures, API errors, permission denied) is treated as deterministic and delivered as-is on first occurrence without retry.
- **SDK execution failures**: The SDK (non-harness) path has its own separate error handling.
- **Sessions that hang mid-execution**: Covered by [stall-retry](stall-retry.md) via the session watchdog.

## Distinction from Stall-Retry

| | Harness Startup Retry | Stall-Retry |
|---|---|---|
| **Trigger** | `FileNotFoundError` before any output | Session hangs mid-execution |
| **Detection** | Immediate (error string prefix check) | After timeout threshold (600s) |
| **Re-queue method** | `transition_status()` in-place | Delete-and-recreate |
| **Backoff** | None | Exponential (10s, 20s, 40s, …300s cap) |
| **Max retries** | 3 | 3 (`STALL_MAX_RETRIES`) |
| **Where implemented** | `agent/session_executor.py` | `monitoring/session_watchdog.py` |

## Related Features

- [Stall-Retry](stall-retry.md): Retry for sessions that hang mid-execution
- [Agent Session Health Monitor](agent-session-health-monitor.md): Liveness monitor for running sessions with dead workers
- [Session Watchdog](session-watchdog.md): The monitoring loop that runs stall checks
