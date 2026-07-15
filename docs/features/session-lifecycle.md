# Session Lifecycle

How sessions transition between states via the consolidated lifecycle module (`models/session_lifecycle.py`).

## Session States (14 total)

### Non-terminal (use `transition_status()`)

| State | Description |
|-------|-------------|
| `pending` | Queued, waiting to be picked up by `_pop_agent_session()` |
| `running` | Worker picked up, agent executing |
| `active` | Session in progress (transcript tracking) |
| `dormant` | Paused on open question, waiting for human reply |
| `waiting_for_children` | Parent session waiting for child sessions to complete |
| `superseded` | A newer session for the same session_id has taken over |
| `paused_circuit` | Paused by api-health-gate when Anthropic circuit breaker is OPEN; resumed by bridge-watchdog sustainability drip |
| `paused` | Paused mid-execution due to auth/API failure; resumed by bridge-watchdog session-resume-drip |
| `paused_budget` | Paused by the per-tool budget backstop (#1821) when a session exhausts its tool-call/cost budget, only under `TOOL_BUDGET_AUTO_PAUSE` (default off); NON-drip, human-only recovery — never re-queued by `session_recovery_drip`. See [Out-of-Domain Recovery + Per-Tool Budget Backstop](out-of-domain-recovery.md). |

### Terminal (use `finalize_session()`)

| State | Description |
|-------|-------------|
| `completed` | Work finished successfully |
| `failed` | Work failed (error, crash, or watchdog detection). Operator-resumable via `valor-session resume` when `claude_session_uuid` is stored (#1061). |
| `killed` | Terminated by user or scheduler. Operator-resumable via `valor-session resume` when `claude_session_uuid` is stored (#1061). |
| `abandoned` | Unfinished, auto-detected by watchdog or health check |
| `cancelled` | Cancelled before execution (pending -> cancelled) |

### Dashboard Iconography

Of the 9 non-terminal states, most render with distinct glyphs in the dashboard row template (`ui/templates/_partials/sessions_table.html`) — including specific glyphs for `paused` (⏸), `paused_circuit` (⛌), `superseded` (→), `waiting_for_children`, `running`, `pending`, `dormant`, and `active`. `paused_budget` has no dedicated glyph yet — it falls through to the template's default branch and renders as plain status text. Non-terminal rows additionally surface a row-level freshness chip (age since `last_evidence_at`) and a ghost badge when the harness PID probe reports a dead process. See [Dashboard — Liveness Signals](dashboard.md#liveness-signals).

### Resume Handle (`claude_session_uuid` generalization, #2000)

`AgentSession.claude_session_uuid` is the field the `failed`/`killed` resumability note above and [Simple Resume](#simple-resume-d3-four-scalars) below both depend on. Its *meaning* generalized from "the Claude Agent SDK's session id" to "the harness's opaque resume handle" as part of #2000's HarnessAdapter seam — the field name itself was deliberately kept unchanged (no migration; every existing reader/writer, dashboard query, and `valor-session resume` call site continues to work against the same key). Concretely: before #2000 the value was captured by the now-deleted `ValorAgent`/`get_agent_response_sdk` SDK path; today it is captured by `ClaudeHarnessAdapter`'s `session.started{handle}` normalized `TurnEvent` (see [HarnessAdapter Seam § Resume-Handle Contract](harness-adapter.md#resume-handle-contract-race-1)) and persisted on first sight by the runner, preserving the persist-at-init contract (Race 1) through the seam. A same-PR empirical finding (Task 2.1) also established that plain `--resume` reuses the session id on claude 2.1.207 rather than forking it, which simplified the `_claude_session_id` reassignment in `role_driver.py` to assert-and-alarm — see [HarnessAdapter Seam § Resume-Id Stability](harness-adapter.md#resume-id-stability-task-21-empirical-finding).

## Lifecycle Module

All session status mutations go through `models/session_lifecycle.py`. Direct `.status =` mutations outside this module are prohibited.

### `finalize_session(session, status, reason, *, skip_auto_tag=False, skip_checkpoint=False, skip_parent=False, reject_from_terminal=True)`

For terminal transitions. Executes all completion side effects in order:

1. **Lifecycle log** -- `session.log_lifecycle_transition(status, reason)` (always)
2. **Auto-tag** -- `auto_tag_session(session_id)` (unless `skip_auto_tag=True`)
3. **Branch checkpoint** -- `checkpoint_branch_state(session)` (unless `skip_checkpoint=True`)
4. **Parent finalization** -- `_finalize_parent_sync(parent_id, ...)` (unless `skip_parent=True` or no parent)
5. **Status + timestamp + save** -- sets `session.status`, `session.completed_at`, calls `session.save()`

**Telemetry**: emits a `status_transition` event to the session's JSONL telemetry file (see [Session Telemetry](session-telemetry.md)), then calls `agent.session_telemetry.finalize_session()` to flush the file handle and evict in-memory state. Pass `emit_telemetry=False` to suppress the event (used by `session_health._apply_recovery_transition`, which emits its own kill-enriched `status_transition` first to avoid duplicate events).

**Idempotent**: if the session is already in the target terminal state, logs and returns without re-executing side effects.

**Kill-is-terminal guard** (`reject_from_terminal`, default `True`): When the session is already in a terminal state and the caller is trying to transition it to a *different* terminal status (e.g., `killed -> completed`), raises `StatusConflictError` instead of overwriting. This mirrors the symmetric guard on `transition_status()` and enforces the rule that **once terminal, always terminal — unless the caller has explicitly documented why they need to re-classify**. Callers with legitimate re-classification needs (rare; e.g., escalating `abandoned -> failed` on timeout) must pass `reject_from_terminal=False` explicitly. See the *Kill-is-Terminal Invariant* section below for the full rationale and the audit of catch-and-log call sites.

**Lazy-load safety**: Before saving, `finalize_session()` backfills `session._saved_field_values["status"]` with the current status. Popoto's `_create_lazy_model()` only seeds `_saved_field_values` with KeyFields, so lazy-loaded sessions have no `"status"` entry. Without this backfill, `IndexedFieldMixin.on_save()` skips `srem()` and the session accumulates in both the old and new status index sets simultaneously (ghost sessions).

**Skip flags**: The Claude Code hook subprocess path (`.claude/hooks/stop.py`) uses `skip_auto_tag=True, skip_checkpoint=True` to avoid importing heavy dependencies that may not be available in the subprocess context.

### `transition_status(session, new_status, reason, *, reject_from_terminal=True)`

For non-terminal transitions. Logs the lifecycle transition and updates the status.

1. **Terminal guard** -- if `reject_from_terminal=True` (default) and current status is terminal, raises `ValueError`
2. **Lifecycle log** -- `session.log_lifecycle_transition(new_status, reason)` (always)
3. **Status + save** -- sets `session.status`, calls `session.save()`

**Idempotent**: if the session is already in the target state, logs and returns.

**Telemetry**: emits a `status_transition` event to the session's JSONL telemetry file (see [Session Telemetry](session-telemetry.md)). Pass `emit_telemetry=False` to suppress the event when the caller is already emitting a richer telemetry record.

**Lazy-load safety**: Before saving, `transition_status()` backfills `session._saved_field_values["status"]` with the current status. This mirrors the same backfill in `finalize_session()` — both functions share the same Popoto lazy-load coupling. See `finalize_session()` above for the full explanation.

**Terminal respawn protection**: By default, `transition_status()` rejects transitions from terminal statuses (`completed`, `failed`, `killed`, `abandoned`, `cancelled`). This prevents accidental respawning of finished sessions. Callers that legitimately need terminal-to-non-terminal transitions must pass `reject_from_terminal=False` explicitly. Currently two callers use this opt-out:
- `_mark_superseded()`: `completed->superseded` (intentional bookkeeping)
- `user_prompt_submit.py` hook: `completed->running` (user reactivates local session)

See [Session Recovery Mechanisms](session-recovery-mechanisms.md) for the full audit of all recovery paths.

### Recovery Confirms Subprocess Death (issue #1537)

When the liveness check recovers a no-progress `running` session, `_apply_recovery_transition()` (`agent/session_health.py`) calls `handle.task.cancel()` with a `TASK_CANCEL_TIMEOUT` of 0.25s. Cancellation alone does **not** guarantee the underlying `claude -p` subprocess exited — a true hang ignores `CancelledError` and orphans the PID. Once the DB record leaves `running`, that orphan is invisible to every detector (the forward scan only queries `status="running"`; the in-process and PPID==1 reapers only act on terminal-status / launchd-reparented processes). At the time of the 2026-05-31 incident (a 25.5h hang) this also wedged the worker's execution slot until a human ran `worker-restart` — as of issue #1820, the slot itself no longer requires a restart to recover: `_apply_recovery_transition` reclaims the owner-keyed lease immediately after the row flips terminal, and the health check's top-of-tick reap pass catches any lease that slips through. See [Slot-Lease Ownership](slot-lease-ownership.md).

Recovery now confirms subprocess termination before deciding how to transition:

1. **`_confirm_subprocess_dead(pid, *, timeout)`** runs after the `task.cancel()` await. It probes liveness with `os.kill(pid, 0)`, then escalates **SIGTERM → SIGKILL** against the recorded `entry.claude_pid`, polling for exit within a short single-digit-second grace (`SUBPROCESS_KILL_TIMEOUT = 3.0`). SIGKILL is sent **only** when SIGTERM fails to terminate the PID. It returns `True` only when the PID is confirmed gone (`ProcessLookupError`); a `None`/non-positive PID short-circuits to `True` (nothing to kill); `PermissionError` or a PID that survives SIGKILL returns `False`.
2. **The requeue `else` branch** (below `MAX_RECOVERY_ATTEMPTS`) branches on that boolean:
   - **Confirmed dead** → the existing requeue-to-`pending` path runs (nulls `started_at`, bumps priority to `high`, `transition_status(..., "pending")`).
   - **Not confirmed dead** → `finalize_session(entry, "failed", ...)` escalates the session to the `failed` terminal status so the in-process orphan reaper (which acts on `TERMINAL_STATUSES`) owns cleanup. `started_at` is **not** nulled into a `pending` record. This is the exact fix for #1537: a hung subprocess can never be silently parked at `pending` as an untracked orphan.
3. **Observability:** best-effort Redis counters (failure never propagates out of recovery) — `{project_key}:session-health:subprocess_kill_escalated` when a recorded PID was confirmed dead via the kill path, and `:subprocess_kill_failed` when the subprocess could not be confirmed dead.

**PID-reuse caveat:** a recorded `claude_pid` could in principle be recycled by an unrelated process before recovery runs. The window is the sub-second recovery path and this matches the existing PPID==1 reaper's assumptions; the residual risk is accepted rather than tracking PID generations.

**User-facing notification (issue #1937 — silent-resume inversion):** the requeue-to-`pending` branch above is an auto-resuming interruption and is **silent** — no "I was interrupted" message is sent; the user next hears from the session when it actually finishes or fails. Only the "not confirmed dead" escalation to `failed` is terminal, and that branch delivers a last-resort `INTERRUPT_NO_RESUME` notice (`_deliver_terminal_interrupt_notice` in `agent/session_health.py`) when neither the deferred self-draft fallback nor the tool-timeout degraded notice already spoke for this exit. See [Reason-Aware Interrupt Messaging and Failure Notification](pm-final-delivery.md#reason-aware-interrupt-messaging-and-failure-notification-issue-1877-silent-resume-inversion) for the full send-site design.

## Kill-is-Terminal Invariant

`valor-session kill` is a hard guarantee. Once a session is killed (or in any terminal state), no routine pipeline-progression code path may transition it to a different terminal status. This invariant is enforced symmetrically by both lifecycle entry points:

- `transition_status(reject_from_terminal=True)` — blocks `terminal -> non-terminal` (e.g., `killed -> running`). Raises `ValueError`.
- `finalize_session(reject_from_terminal=True)` — blocks `terminal -> different-terminal` (e.g., `killed -> completed`). Raises `StatusConflictError`.

**Layered defense** (introduced in #1208):

1. **Lifecycle layer** — `finalize_session()` raises `StatusConflictError` on the `terminal -> different-terminal` flip. This is the load-bearing check.
2. **Hierarchy health-check layer** — `_agent_session_hierarchy_health_check()` in `agent/session_health.py` re-reads each parent's authoritative hash status before acting. If the hash says terminal, the parent is skipped at INFO and the loop continues. This handles the case where a `waiting_for_children` index entry is stale (the index was not srem'd at kill time).
3. **Runner-entry layer** — `_deliver_pipeline_completion()` and `schedule_pipeline_completion()` in `agent/session_completion.py` short-circuit before any drafting, locking, or send-callback invocation when the parent is terminal-and-not-`completed`. `completed` is explicitly allowed through so the success-path runner can deliver its summary.

**Catch-and-log call sites** — the guard's `StatusConflictError` is the *expected, correct, defense-in-depth outcome* of a kill racing routine pipeline progression, not an alarm condition. The following call sites wrap `finalize_session()` in `try/except StatusConflictError: logger.info(...)` so this expected event stays at INFO and does not pollute WARNING/ERROR signal:

- `agent/session_completion.py` — always-finalize at runner exit
- `agent/session_executor.py` — executor-guard `missing_working_dir -> failed`
- `agent/session_health.py` — already-delivered → completed; recovery dispatch (downgraded from existing WARNING handler); orphan local pending → abandoned
- `bridge/session_transcript.py` — transcript end → terminal status
- `bridge/telegram_bridge.py` — intake-classifier acknowledgment → completed
- `models/session_lifecycle.py` — `_transition_parent` helper itself, so every caller of `_transition_parent` gets the catch for free

The `agent/agent_session_queue.py:cancel_agent_session` site does **not** need a wrapper — its pre-condition (`session.status != "pending"` early-return) guarantees a non-terminal status, and there is no race window before the `finalize_session()` call.

**Operator semantics**:

- `valor-session kill <id>` — writes `status=killed` via `finalize_session(reject_from_terminal=True)` (its own pre-condition guarantees no terminal-flip).
- `./scripts/valor-service.sh worker-disable` — pairs `launchctl disable` with `bootout`. Use this when killing all sessions and you do **not** want the worker to come back via launchd's `KeepAlive=true` respawn. Re-enable with `worker-enable` (or `worker-start`, which calls `launchctl enable` idempotently before `bootstrap`). See `./scripts/valor-service.sh` help and CLAUDE.md "Quick Commands" for the full table.

## Completion Flow

When a session finishes execution, all paths converge on `finalize_session()`:

| Path | Caller | Skip Flags |
|------|--------|------------|
| Worker completion | `_complete_agent_session()` in `agent_session_queue.py` | None (all side effects run) |
| Transcript completion | `complete_transcript()` in `session_transcript.py` | None (all side effects run) |
| Claude Code hook stop | `.claude/hooks/stop.py` | `skip_auto_tag=True, skip_checkpoint=True` |
| Bridge acknowledgment | `telegram_bridge.py` dormant->completed | None |
| PM cancel | `agent_session_queue.py` | None |
| Watchdog abandon/fail | `session_watchdog.py` | None |
| Deploy stale cleanup | `_cleanup_stale_sessions()` in `scripts/update/run.py` | `skip_checkpoint=True` |

### Worker Completion — Redis Re-read

`_complete_agent_session()` re-reads the session record from Redis before calling `finalize_session()`. This ensures that any `stage_states` accumulated during execution (e.g., SDLC pipeline transitions written while the worker was running) are captured rather than overwritten by the stale in-memory snapshot.

The re-query is intentionally **status-filter-free** — it queries by `session_id` only, with no `status="running"` constraint. Filtering by status would return an empty list if the session had already transitioned away from `running` (via a concurrent path) before `_complete_agent_session()` fired, causing `finalize_session()` to operate on the stale in-memory object and corrupt the status index (the session would end up indexed under both the old and new status simultaneously). See issue #825.

**Tie-breaking** when multiple records share the same `session_id`: prefer any record currently in `running` status (ensures the live session is finalized), then fall back to most-recent by `created_at` only if no running records exist. If no records are found at all, `finalize_session()` is called on the original in-memory object.

## Side Effect Consolidation

Before consolidation, completion side effects were scattered across 4 paths, each performing different subsets:

| Side Effect | Before (which paths) | After |
|-------------|---------------------|-------|
| Lifecycle log | Path A only | All paths via `finalize_session()` |
| Auto-tag | Path A only | All paths (unless `skip_auto_tag`) |
| Branch checkpoint | Path B only | All paths (unless `skip_checkpoint`) |
| Parent finalization | Path B only | All paths (unless `skip_parent`) |

## Child Session Creation Temporarily Disabled (#1633)

Creation of NEW parent-attached child sessions is refused as a stopgap until the #1633 refactor lands. The granite PTY cutover (PR #1612) runs every session in a container that owns its own PM+Dev claude TUI pair from a bounded pool, so parent-spawned child AgentSessions (the old PM→Dev pattern) double-consume scarce pool slots and risk starvation/deadlock when a parent in `waiting_for_children` holds a slot its child needs. Dependent work should run as subagents inside the current session instead.

Gated creation paths (all share `models/child_session_gate.py`):

- `valor-session create --parent <id>` — exit 2 with a stderr error; `--json` emits `{"error": "child_sessions_disabled", "issue": 1633, ...}`. Nothing is written to Redis on the refused path.
- `agent/agent_session_queue.py::_push_agent_session` — raises `ChildSessionsDisabledError` before any persistence when `parent_agent_session_id` is set (covers every enqueue caller).
- `.claude/hooks/user_prompt_submit.py` — when `VALOR_PARENT_SESSION_ID` is set, the hook silently skips creating the parent-linked tracking record (the subprocess itself still runs).
- `python -m tools.agent_session_scheduler schedule --parent-session <id>` — exit 2 with the structured error.

**Escape hatch (genuine emergencies only):** `VALOR_ALLOW_CHILD_SESSIONS=1` bypasses the block with a loud warning at each creation site.

**Unaffected:** existing child sessions keep working end to end — resume, steer, kill, the `children` subcommands, and `waiting_for_children` parent finalization (below) for already-linked sessions. PM continuation chains (`session_completion.py` `create_pm`, issue #1195) are deliberately exempt: their parents are terminal and hold no pool slot. The child-session pattern itself survives untouched per #1633; only NEW parent-attached creation is refused.

## Parent Finalization

When a child session completes, `finalize_session()` checks if the parent should also be finalized:

1. Look up parent by `parent_agent_session_id`
2. If parent is already terminal, skip
3. Set parent to `waiting_for_children` if not already
4. Check all children's statuses
5. If all children terminal: finalize parent as `completed` (all succeeded) or `failed` (any failed)
6. Uses `skip_parent=True` internally to prevent infinite recursion

### Transcript-Boundary Skip (issue #1156)

A PM session's own Claude transcript can end **before** its children terminate. Prior to this skip, the worker's end-of-task path called `complete_transcript(...)` unconditionally, which delegated to `finalize_session` and force-finalized the PM while children were still running. This bypassed the child-liveness gate inside `_finalize_parent_sync`.

To close that gap, `complete_transcript` and the Claude Code Stop hook (`_complete_agent_session`) **skip the terminal transition** when the session is in `waiting_for_children` and the target is `completed` or `failed`:

- `bridge/session_transcript.py:complete_transcript` — after the session re-read, if `s.status == "waiting_for_children"` and the target is terminal, it logs an INFO line citing issue #1156 and returns. The `SESSION_END` transcript marker is still written earlier in the function.
- `.claude/hooks/stop.py:_complete_agent_session` — a silent early return under the same condition (hook-local silent-failure policy).

The two sanctioned channels that **do** finalize `waiting_for_children` PMs after all children terminate:

1. `_finalize_parent_sync` → `_transition_parent` with reason `"all children terminal"`.
2. The completion runner (`agent/session_completion.py:_deliver_pipeline_completion`) with reason `"pipeline complete: final summary delivered"`, serialized against `_finalize_parent_sync` via the `pipeline_complete_pending:{parent_id}` Redis lock (#1058).

The skip deliberately does **not** block legitimate recovery paths — `_complete_agent_session` crash finalizer, `session_health` recovery, and `session_watchdog` stale-session reaper may still finalize wedged `waiting_for_children` PMs with their respective reasons.

## Field Extraction (`_extract_agent_session_fields`)

The `_AGENT_SESSION_FIELDS` list defines which fields are preserved during delete-and-recreate operations. The `status` field is included for defense-in-depth: any delete-and-recreate path preserves the original status instead of defaulting to `"pending"`.

## Zombie Loop Prevention

### Health Check Orphan-Fixing

The `_agent_session_hierarchy_health_check()` function detects orphaned children. Because `status` is in the field extraction list, a completed orphaned session stays `completed` after recreation. Without this, the recreated session would default to `pending` and be re-executed indefinitely.

### Nudge Overwrite Guard

When a nudge (auto-continue) is enqueued during session execution, the session status is set to `pending` via `transition_status()`. The worker finally block re-reads the session from Redis before completing:
- If `status = "pending"`: a nudge was enqueued, skip completion
- If session no longer exists: nudge fallback recreated it, skip completion
- Otherwise: proceed with normal completion via `finalize_session()`

### Consecutive-Failure Circuit Breaker (issue #1413)

`agent/health_check.py::watchdog_hook` runs a cheap deterministic check on **every** tool call (complementing the Haiku watchdog, which judges holistic progress every `CHECK_INTERVAL` calls). It counts back-to-back failed tool calls per session — a failure being a `tool_response` dict with `is_error == True` (or, on rare SDK paths, a string starting with `"Error: "`); all other shapes are treated as success. When `CONSECUTIVE_FAILURE_THRESHOLD` (default 5) failures occur in a row, the session is flagged via the shared `AgentSession.unhealthy_reason` field with a reason naming the last failing tools, e.g. `"5 consecutive tool failures (Bash, Bash, Edit, Read, Bash) — strategy reassessment required"`.

Any successful tool call resets the counter and clears the recent-failure ring (`deque(maxlen=5)`). After the breaker fires, the counter resets so it re-fires every 5 *additional* consecutive failures. Because `unhealthy_reason` is read by the output router before auto-continuing, a tripped breaker pauses the nudge loop and surfaces the session to the operator/PM for reassessment. The counter and ring are process-local in-memory state (reset on worker restart) — no schema change, no Redis persistence.

## Stale Object Hazard and the `finalized_by_execute` Gate

### Three-Object Pattern

The worker loop creates multiple Python instances that all refer to the same Redis record:

1. **Outer session** — created by `_pop_agent_session()` in `_worker_loop`. This object is held across the entire session execution lifecycle.
2. **Inner `agent_session`** — fetched inside `_execute_agent_session()` after the outer `await`. This is the object used for most execution-time state mutations.
3. **Nudge fresh re-read** — created by `get_authoritative_session()` inside `_enqueue_nudge()`. This is the authoritative object that writes `status=pending, auto_continue_count` when a nudge is triggered.

When a nudge fires, objects #1 and #2 become stale snapshots: their in-memory fields no longer match Redis. Any `.save()` call on either of them would clobber the authoritative nudge state.

### The `finalized_by_execute` Gate (#898)

The `finalized_by_execute` flag (in `_worker_loop`, `agent/agent_session_queue.py`) prevents the outer finally block from firing on the happy path:

```python
finalized_by_execute = False
try:
    await _execute_agent_session(session)
    finalized_by_execute = True  # only reached on clean return
except asyncio.CancelledError:
    ...  # finalized_by_execute stays False
except Exception:
    ...  # finalized_by_execute stays False
finally:
    if not session_completed and not finalized_by_execute:
        # Crash/cancel path only. On the happy path, _execute_agent_session
        # has already finalized (via complete_transcript or _enqueue_nudge).
        session.log_lifecycle_transition(target, "worker finally block")
        ...
```

**Happy path (nudge)**: `_execute_agent_session` returns cleanly. `finalized_by_execute=True`. The finally block is a complete no-op. The nudge state (`status=pending, auto_continue_count=N`) written by object #3 is preserved exactly as `_enqueue_nudge` left it.

**Happy path (completion)**: `_execute_agent_session` returns cleanly. `finalized_by_execute=True`. Same result — the finally block is a no-op. `complete_transcript` already ran inside `_execute_agent_session` on a fresh re-read.

**Crash path**: `_execute_agent_session` raises. `finalized_by_execute=False`. The finally block runs as designed: `log_lifecycle_transition`, snapshot, nudge guard, `_complete_agent_session`. Since the SDK aborted before `end_turn`, `_enqueue_nudge` could not have fired, so the outer session's stale state is still the only authoritative state.

### Layer 1b: Partial Saves on Companion-Field Methods (#950)

Any `AgentSession` method that saves companion fields (non-status fields) **must** use `save(update_fields=[...])` to avoid clobbering `status` on stale worker references. The following methods were converted from full saves to partial saves:

| Method | Fields Written | File |
|--------|---------------|------|
| `set_link()` | `[field_name, "updated_at"]` | `models/agent_session.py` |
| Heartbeat in `_heartbeat_loop` | `["updated_at"]` | `agent/agent_session_queue.py` |
| Steering drain (async) | `["initial_telegram_message", "updated_at"]` | `agent/agent_session_queue.py` |
| Steering drain (sync fallback) | `["initial_telegram_message", "updated_at"]` | `agent/agent_session_queue.py` |
| `retain_for_resume` save | `["retain_for_resume", "updated_at"]` | `agent/agent_session_queue.py` |
| Session metadata save | `["updated_at", "branch_name", "task_list_id"]` | `agent/agent_session_queue.py` |
| `response_delivered_at` save | `["response_delivered_at", "updated_at"]` | `agent/agent_session_queue.py` |
| Branch/commit checkpoint | `["branch_name", "session_events", "updated_at"]` | `agent/agent_session_queue.py` |
| Resume hydration | `["initial_telegram_message", "updated_at"]` | `agent/agent_session_queue.py` |
| Priority reorder | `["priority", "updated_at"]` | `agent/agent_session_queue.py` |
| Continuation project_config | `["project_config", "updated_at"]` | `agent/agent_session_queue.py` |
| Tool call tracking | `["updated_at", "tool_call_count"]` | `.claude/hooks/post_tool_use.py` |
| Idempotent reactivation | `["updated_at", "completed_at"]` | `.claude/hooks/user_prompt_submit.py` |

**Rule**: When adding a new save site on `AgentSession` that modifies non-lifecycle fields, always use `save(update_fields=[...])` listing only the fields you modified plus `"updated_at"`. Never use a bare `save()` on a session object that might be stale.

Steering is not in this table because it no longer touches the `AgentSession` model at all — `push_steering_message()` and `pop_all_steering_messages()` (`agent/steering.py`) operate on a dedicated Redis list (`steering:{session_id}`), so there is no stale-object partial-save risk to guard against. See [Session Steering](session-steering.md).

### Layer 1c: Defensive `srem` in `finalize_session` (#950)

After `session.save()`, `finalize_session()` performs a defensive `srem` that removes the session's hash key from ALL status index sets except the target terminal status. This catches orphan index entries that were created by prior stale-object saves clobbering the status to an intermediate value. The defensive `srem` is wrapped in try/except and is non-fatal.

### Layer 2: Partial Save in `_append_event_dict`

Even when `finalized_by_execute` gates off the finally block, `log_lifecycle_transition` (called from other paths) triggers `append_event → _append_event_dict`. Without protection, this would do a full `self.save()` on the stale object, clobbering `status`, `auto_continue_count`, and `message_text`.

`_append_event_dict` uses `save(update_fields=["session_events", "updated_at"])` — a Popoto partial save that:
- Writes only the listed fields to Redis HSET
- Calls `on_save` hooks only for listed fields (the `status` IndexedField hook is NOT called)
- Cannot clobber any field not in the list

A stale caller can at worst append a spurious `session_events` entry. It cannot clobber `status`, `auto_continue_count`, or `message_text`. This makes stale-object saves non-destructive by construction.

### Regression Detection

`scripts/reflections.py` scans bridge logs daily for `"Stale index entry"` warnings. A non-zero count triggers a finding tagged `(regression marker for #898)`. The `finalized_by_execute` fix should eliminate all such warnings; a reappearance indicates a regression.

## Timestamp Convention — `updated_at` is Explicit UTC

`AgentSession.updated_at` is always an explicit UTC wall-clock timestamp. It is stamped inside the `save()` override using `bridge.utc.utc_now()`, not by a Popoto `auto_now` field.

**Why:** Popoto's `auto_now` calls `datetime.now()` (no `tz` argument), which mints a naive datetime in the host's local timezone. On non-UTC hosts the stored value is naive-local, but every downstream reader (watchdog, dashboard, stale-cleanup) interprets it as UTC. The result is a future-dated `updated_at` for sessions created on hosts running ahead of UTC, causing the watchdog/dashboard to report sessions as perpetually "fresh" and stale-cleanup to skip them forever.

**The fix (issue #1645):** `auto_now` was removed from the field declaration. The `save()` override stamps `self.updated_at = utc_now()` unconditionally unless `update_fields` is provided *without* `"updated_at"` — in which case the stamp is skipped entirely (no in-memory mutation without a matching persist, to avoid memory/Redis desync).

**Rule for new fields:** `auto_now=True` must not be added to any `DatetimeField`. Always stamp explicitly with `utc_now()` at the appropriate call site. This constraint is documented on the field declaration at `models/agent_session.py` line 153–155 (#1645).

### First-deploy callout

`_heal_future_updated_at()` (a classmethod on `AgentSession`) runs once at worker startup after the fix is deployed. It clamps any `updated_at` that is in the future down to `max(created_at, now)`. After the clamp, those previously-future-dated records appear newly-updated to the watchdog and dashboard staleness checks for exactly one staleness window — operators should **not** interpret this momentary freshness as real session activity. No threshold change is required; the clamp only moves timestamps from future to now.

The heal is idempotent: a re-run clamps only still-future records, so a mid-run restart is safe.

## Stale Session Cleanup

`_cleanup_stale_sessions()` in `scripts/update/run.py` runs during every `/update` deploy and terminates `running` or `pending` sessions that have no live process. It is a safety net for sessions that were never finalized due to a crash or abrupt restart.

**Primary liveness check — `updated_at` recency (30-minute window):** The function first checks each session's `updated_at` timestamp. If `updated_at` is within the last 30 minutes, the session is considered live and unconditionally skipped. The worker writes a periodic `updated_at` heartbeat every 25 minutes via `_heartbeat_loop` in `agent/agent_session_queue.py`, so even sessions blocked on a long Claude API call stay fresh in Redis. Sessions skipped for recent activity are counted and reported in the `/update` log as "Skipped N live session(s) (recent heartbeat)".

**Fallback liveness check — `created_at` age (120-minute threshold):** When `updated_at` is `None` (sessions created before the heartbeat feature was added), the function falls back to checking `created_at` age. Sessions younger than 120 minutes are skipped. This preserves the original safety margin for pre-heartbeat sessions.

**Secondary defense — `_active_workers` registry:** Before either timestamp check, any session whose `worker_key` maps to a not-done asyncio Task in `_active_workers` is unconditionally skipped. Workers are keyed by `worker_key` (`project_key` for slugless PM/dev sessions and PM sessions at PLAN/ISSUE/CRITIQUE/MERGE stages; `slug` for slugged-dev sessions and PM sessions at BUILD/TEST/PATCH/REVIEW/DOCS stages; or `chat_id` for teammate sessions). This registry is only populated during in-process invocations and is always empty when the update script runs as a CLI subprocess.

**Return value:** The function returns `(killed_count, skipped_live)` — both the number of sessions killed and the number skipped due to recent heartbeat activity.

**Lifecycle routing:** All terminal transitions go through `finalize_session(session, "killed", reason="stale cleanup (no live process)", skip_checkpoint=True)`. This fires all lifecycle hooks (lifecycle log, auto-tag, parent finalization) while skipping the branch checkpoint, which is unavailable outside the normal worker context.

**In-process vs. standalone:** When the update script runs inside the same process as the queue (bridge in-process update), `_active_workers` is populated and fully authoritative. When it runs as a CLI subprocess, `_active_workers` will always be empty and the function logs a warning before relying on the `updated_at` recency check.

## Duplicate-Session Dedup — Only `completed` Counts as Handled (issue #1877)

`_cleanup_duplicate_sessions()` in `scripts/update/run.py` runs during every
`/update` deploy and kills `pending` sessions that re-process a
`(chat_id, telegram_message_id)` pair already covered by another session.
Of the five terminal statuses, only **`completed`** means the message was
actually handled — `killed`, `abandoned`, and `failed` all mean the prior
attempt did *not* produce a delivered response.

Before issue #1877, the terminal-status scan included all four of
`("completed", "killed", "abandoned", "failed")`, so a legitimate `pending`
retry after a killed/abandoned/failed attempt was silently suppressed —
the user's message was never actually answered. The scan now checks only
`("completed",)`: a `pending` session survives unless another session for
the same `(chat_id, telegram_message_id)` reached `completed`.

This is a narrower rule than the general terminal-status vocabulary above —
it applies specifically to the duplicate-message dedup scan, not to the
[Kill-is-Terminal Invariant](#kill-is-terminal-invariant) or any other
terminal-status check in this document.

## Divergent Duplicate Records — Pop-Loop Spin and Phantom Running (issue #2007)

Distinct from the message-level dedup above, this is about two `AgentSession`
records sharing one `session_id` in divergent statuses: one non-terminal
(`pending` or `running`) carrying the real work, alongside a stale terminal
record (`failed`, `killed`, `abandoned`, `completed`, or `cancelled`) left
over from an earlier attempt. Two consumers assumed a single record per
`session_id` and mishandled the pair.

### The failure mode

**Pop-loop spin.** `_pop_agent_session()` transitions the `pending` record
toward `running` and CAS-re-reads on disk. Index/tie-break ambiguity between
the two records can resolve to the stale terminal one, so the transition
fails with `StatusConflictError`. The pop loop's handler logged a WARNING,
released the slot, and `continue`d — no counter, no escalation — so the same
`session_id` re-popped and re-conflicted every tick, forever.

**Phantom running.** `complete_transcript()` (`bridge/session_transcript.py`)
selected the record to finalize with a blind `list(...)[0]`. With a
divergent pair present, `[0]` could land on the stale terminal record.
`finalize_session()` correctly refused to re-transition it (the
[Kill-is-Terminal Invariant](#kill-is-terminal-invariant) guard-swallowed it
or idempotency-skipped it), and the real `running` record — the one that had
actually delivered its reply and run its cleanup — was never finalized. It
stayed `running` forever with a stale heartbeat, invisible to every
live-process check, until a human killed it manually.

### Root cause

Divergent duplicates were created because `enqueue_agent_session`'s
duplicate-record guard (formerly `_mark_superseded`) was a documented no-op.
It only ever attempted a `completed -> superseded` transition, and #730 had
already forbidden re-activating or re-transitioning a terminal record (see
the Kill-is-Terminal Invariant above), so that transition was guard-rejected
every time. Every divergent record — including stale `failed`/`killed`/
`abandoned` ones entirely outside its narrow scope — survived indefinitely.

### The fix

**Reconcile by deletion, not by re-activation.**
`_delete_stale_terminal_duplicates(session_id)` in
`agent/agent_session_queue.py` replaces `_mark_superseded`. It deletes every
terminal-status duplicate for a `session_id` via ORM `instance.delete()`,
skipping any duplicate that has child sessions (`get_child_sessions()`) so a
parent link is never orphaned, and never touching `running`/`pending`
records. It runs at enqueue time, before the new `pending` record is
created, so a divergent pair is never born in the first place. The pop
loop's escalation (below) reuses this exact helper, so both consumers apply
identical semantics.

**Bounded, self-terminating pop-loop escalation.** `_worker_loop` tracks a
loop-local conflict count per `session_id`, keyed off
`StatusConflictError.session_id` (the exception already carries this
attribute). At a primary threshold it calls
`_delete_stale_terminal_duplicates()` unconditionally, every tick,
idempotent and ungated — a transient failure just retries — while the ERROR
log for this escalation fires once, not per tick. Deleting the stale
terminal duplicate resolves the ambiguity, so the `pending` record pops
cleanly on the next tick with its undelivered work intact. Only as a bounded
last resort, at a higher threshold reached solely when the terminal
duplicate has children and can't be deleted, does the loop cancel the stuck
`pending` record itself, writing `cancel_reason="conflict_escalation"`
(`agent/cancel_reason.py`) for operator visibility. That marker is
short-lived and best-effort; the durable record of the cancellation is the
`finalize_session(..., reason=...)` call, which lands in the `LIFECYCLE`
transition log.

**Guaranteed terminal finalize on the completion exit.**
`complete_transcript()` now selects the record to finalize via
`get_authoritative_session()` — the same running-preferring tie-break
pattern described in [Worker Completion — Redis
Re-read](#worker-completion--redis-re-read) above — instead of the blind
`[0]`. The load-bearing fix for phantom running, though, is an unconditional
completion-exit guard in `agent/session_executor.py`, placed after the
entire `if agent_session: / else:` completion block closes so it covers
both exits, including the case where the `agent_session` lookup returned
`None`. It re-reads the authoritative session and, if still `running`,
calls `finalize_session()`, treating a `StatusConflictError` from a racing
concurrent finalizer as success. Every non-deferred completion path now
reaches a terminal status regardless of what `complete_transcript()` did
upstream.

## Stall Reaction Dedup Reset (issue #1313)

When `monitoring/session_watchdog.py::check_stalled_sessions` queues a user-visible ⏳ reaction for a stalled session (see [Bridge Self-Healing § 4a](bridge-self-healing.md#4a-user-visible-stall-alerts-monitoringsession_watchdogpy-issue-1313)), it claims the dedup key `watchdog:stall_reaction_applied:{session_id}` so the reaction is queued at most once per stall period.

**Reset on healthy observation:** the same iteration loop in `check_stalled_sessions` calls `_clear_stall_reaction_dedup(session_id)` whenever the session's duration is within its threshold. This is the only point where the dedup key is deleted — there is no lifecycle hook in `models/agent_session.py` for this, by design. The watchdog-side placement keeps the diff inside `monitoring/session_watchdog.py` only and avoids threading reaction state through the broader transition machinery in this module.

**Implication:** there is a ≤5-minute window (one watchdog tick interval) where ⏳ can briefly persist on the user's message after the session recovers, before the next tick clears the dedup. The recovery message itself lands first, so the user sees the recovery before the reaction is reset for a future stall.

## Deferred Self-Draft Fallback Delivery (issues #1730, #1794, #1797)

When the message drafter flags an output as an "empty promise" (`needs_self_draft=True`),
`TelegramRelayOutputHandler.send()` injects a `sender="drafter-fallback"` steering message asking
the agent to rewrite and resend, then skips the outbox write.  This is called a *deferred delivery*:
the user's message will be delivered on the agent's next SDK turn after it consumes the steering.

**The failure mode (issue #1730):** if the session is killed by the health checker (`tool_timeout`
or `no_progress`) before the self-draft completes, the deferred answer is silently lost.  The
steering queue is empty by finalization time — the agent drains it at turn start — so it cannot be
used as a detection signal.

**A second failure mode (issue #1794):** a session that deferred a reply for self-draft and then
reached a *clean* `completed` state also silently lost its reply.  The original async helper only
ran on the health-checker's `failed`/`abandoned` branches, never on the `completed` path.

**The fix (issue #1730):**

1. **Persist at defer time**: at the point where `steering_deferred=True` in
   `agent/output_handler.py`, the handler persists two keys into `AgentSession.extra_context`
   (safe read-modify-write via `get_authoritative_session` re-read):
   - `"deferred_self_draft_pending"`: `True`
   - `"deferred_self_draft_text"`: the original output text

2. **Fallback at finalization — EMAIL on failed/abandoned**: the async helper
   `_deliver_deferred_self_draft_fallback(entry)` in `agent/session_health.py` remains on the
   three health-checker terminal recovery branches (`failed` × 2, `abandoned` × 1), but is now
   **EMAIL-ONLY**.  It early-returns for telegram via
   `if transport in (None, "telegram"): return` (telegram is owned by the synchronous chokepoint
   flush described below).  On an email-transport truthy flag it delivers the recovered
   `deferred_self_draft_text` — narration-gated via `is_narration_only` and
   `NARRATION_FALLBACK_MESSAGE` from `bridge.message_quality`, or an explicit "couldn't finish
   responding" notice when text is absent.  Idempotent via Redis SETNX on its own key
   `self_draft_fallback_sent:{session_id}` (1 h TTL).

3. **Synchronous chokepoint flush — TELEGRAM on all paths, EMAIL on completed path (issues #1794, #1797)**: a new
   fully-synchronous helper `flush_deferred_self_draft_sync(session)` in `agent/session_health.py`
   delivers the held text on qualifying terminal statuses.  It is invoked once from `finalize_session`
   in `models/session_lifecycle.py` — the single centralised terminal-transition chokepoint — with
   the following placement invariants:
   - Runs **after** the idempotency early-return (already-terminal sessions exit before reaching it).
   - Runs **after** the `reject_from_terminal` guard (illegal re-transitions raise before reaching it).
   - Runs **before** `session.save()`, inside the CAS region.
   - **Exception-isolated**: a flush failure never blocks the status write.
   - Reads the deferral flag from a **fresh authoritative session** via
     `get_authoritative_session(session_id)` — not the caller's possibly-stale object.
   - Deduplicates on its **own** SETNX key `self_draft_completed_flush_sent:{session_id}` (1 h TTL).
   - **Transport/status gate** (evaluated before the dedup SETNX):
     - **telegram** (or `None`): proceeds for **all** terminal statuses (`completed`, `failed`,
       `abandoned`), writing directly to `telegram:outbox:{session_id}` via `rpush`.
     - **email** + `completed`: proceeds and writes the payload (built via the shared
       `build_email_outbox_payload` function in `agent/output_handler.py`) to
       `email:outbox:{session_id}` via `rpush`.  The `build_email_outbox_payload` helper is pure
       and synchronous — no I/O, no event loop — making it safe to call on the completed path.
     - **email** + `failed`/`abandoned`: early-returns; the async helper owns those paths.

4. **Disjoint-transport design**: the two helpers partition all paths without overlap — the sync
   chokepoint owns **telegram** on all terminal statuses, and **email** on the `completed` path; the
   async helper owns **email** on `failed`/`abandoned`.  The two distinct SETNX keys
   (`self_draft_completed_flush_sent` vs. `self_draft_fallback_sent`) make double-send structurally
   impossible regardless of execution order.

5. **Precedence**: the self-draft fallback fires *before* the generic degraded notice
   (`_deliver_tool_timeout_degraded_notice`).  When `deferred_self_draft_pending` is set, the
   generic notice is suppressed.

6. **Counter cleanup (AC4 dual-seat)**: `reset_self_draft_attempts(session_id)` is called at
   every terminal finalize to clean up the `steering:attempts:{session_id}` Redis counter instead
   of relying on its 1-hour TTL:
   - **Seat A** (`models/session_lifecycle.py::finalize_session`): outside the `emit_telemetry`
     guard so it fires unconditionally (covers `completed` + any telemetry-on caller).
   - **Seat B** (`agent/session_health.py`, next to `finalize_telemetry`): covers the
     `emit_telemetry=False` health-checker terminal finalizes that Seat A alone would miss.

**Cross-reference:** `_deliver_tool_timeout_degraded_notice` (added in PR #1738, issue #1711) is
the delivery primitive this fallback is modelled after.  New precedence: self-draft fallback
supersedes the generic degraded notice when a deferred self-draft was pending.

## Design Constraints

- **Import safety**: The module uses lazy imports for `tools.session_tags` and `agent.agent_session_queue` so it can be imported from `.claude/hooks/stop.py` subprocess context where those modules may not be on `sys.path`.
- **Fail-safe side effects**: Each side effect (auto-tag, checkpoint, parent finalization) is wrapped in a try/except that logs and continues. A failure in any side effect never blocks the status save.
- **Synchronous only**: The module provides sync functions. Callers in async contexts use `asyncio.to_thread()` as needed (matching existing patterns).

## Index-Rebuild Race and Read-Path Retry (issue #1720)

### Root cause

`AgentSession.repair_indexes()` calls popoto's `rebuild_indexes()`, which
**deletes the class set (`$Class:AgentSession`) at `base.py:2745`** and then
re-adds all members in `batch_size=1000` pipeline batches (`base.py:2785-2813`).

`session_id` is a plain `Field()` (not an `IndexedField`), so there is no
`$IndexF:AgentSession:session_id:*` key.  A `query.filter(session_id=...)` on a
non-indexed field reads `smembers($Class:AgentSession)` and filters in memory
(`query.py:1341/1758/1790`).  During the class-set delete→re-add window, any
concurrent reader returns an empty result for a live session — `Session not found`.

`repair_indexes()` runs **hourly** (the `agent-session-cleanup` reflection at
`agent/session_health.py:2626`) and at **worker startup**
(`agent/session_pickup.py:411`), so every hour there is a window where live
sessions transiently disappear from class-set reads.

### Read-path retry defense

Both reader sites apply a bounded retry that re-reads on empty, returns
immediately on found, and falls through to the existing absent-session fallback
after the cap:

- `tools/valor_session.py::_find_session` (operator CLI: `valor-session status`)
- `tools/sdlc_stage_query.py::_find_session_by_id` (SDLC stage dispatch)

**Measured parameters (spike-1, 150 sessions):**
- `rebuild_indexes()` wall-clock duration: ~600ms
- p99 class-set-empty window: **651ms**
- Retry cap: 5 attempts × 200ms = **1000ms total** (covers p99 with ~35% margin)

When the retry fires, both sites emit a `logger.debug` message:
`"query.filter(session_id=...) returned empty on attempt N/5 — class-set may be mid-rebuild, retrying in 200ms"`

**Hot-path exclusion:** the retry is applied only at operator/dispatch reader
sites, not at internal worker paths (recovery, steering delivery) where latency
matters and the caller already handles `None` gracefully.

### Spike-2 finding (informational)

Stale class-set members (phantoms) occur primarily via TTL expiry: each
`AgentSession` hash has `Meta.ttl = int(settings.timeouts.agent_session_retain_ttl_s)`
(default 30 days / `2592000`s, `.env`-overridable via `TIMEOUTS__AGENT_SESSION_RETAIN_TTL_S`
— see [Config Timeout Catalog](config-timeout-catalog.md)), but index keys
(including the class set) do not carry a coordinated TTL.  When a hash TTL
expires, the class-set entry remains until the next `rebuild_indexes()` clears
and reconstructs it.  Delete paths (`session.delete()`, status transitions) do
maintain the class set via `srem()`, so manually-deleted sessions are not a
significant phantom source.  TTL coordination is recorded here as a documented
finding but is out of scope for issue #1720; the read-path retry already
neutralizes the `Session not found` symptom regardless of phantom source.

See also: [AgentSession Index-Drift Detection](agentsession-index-drift-detection.md)
for the loud-surfacing guard that detects the broader class of this desync
(hashes present but invisible to `query.all()`) in production, and explains
why it deliberately does not call `repair_indexes()` itself.

## Related

- [HarnessAdapter Seam](harness-adapter.md) -- the resume-handle contract `claude_session_uuid` generalized to, and the claude adapter that emits it
- [Agent Session Queue Reliability](agent-session-queue.md) -- KeyField index fixes and delete-and-recreate pattern
- [Agent Session Health Monitor](agent-session-health-monitor.md) -- Stuck session detection
- [Session Lifecycle Diagnostics](session-lifecycle-diagnostics.md) -- Structured LIFECYCLE logging at every state transition
- [Agent Session Hierarchy](agent-session-scheduling.md#parent-child-session-hierarchy) -- Parent-child relationships and orphan handling
- [Popoto Index Hygiene](popoto-index-hygiene.md) -- Full index-cleanup pipeline and model exclusion list
