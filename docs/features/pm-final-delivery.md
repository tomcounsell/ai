# PM Final-Delivery Protocol

## Status

- **Introduced:** issue #1058 (2026-04-21)
- **Replaces:** `[PIPELINE_COMPLETE]` content marker protocol (commit `0e4d41e1`)
- **Touched files:**
  - `agent/pipeline_complete.py` (new)
  - `agent/session_completion.py` (runner + scheduler + drain)
  - `agent/messenger.py` (CancelledError handler)
  - `agent/session_health.py` (fan-out path)
  - `agent/output_router.py` (marker removal)
  - `agent/session_executor.py` (marker branch removal)
  - `models/session_lifecycle.py` (`_finalize_parent_sync` defers to runner)
  - `config/personas/project-manager.md` (Rule 5 rewrite, marker references removed)

## Problem

PM sessions running the SDLC pipeline previously relied on a string marker
(`[PIPELINE_COMPLETE]`) to signal that the pipeline had finished and the next
output should be delivered to Telegram rather than nudged. The router inspected
every PM output for the marker; if missing, the output was re-enqueued as a nudge.

Three observed production failure modes:

1. **Marker omission → 50-nudge loop → garbage forced delivery.** The PM
   forgot or could not emit the marker — context overflow, stale Claude Code
   UUID triggering a first-turn fallback, or persona drift. The session
   looped until `auto_continue_count >= MAX_NUDGE_COUNT (50)` forced a
   mid-pipeline output to Telegram.
2. **Empty harness output → ghost session.** Partially fixed by `3a0346b3`:
   empty results now invoke the router with `""` so `nudge_empty` /
   `deliver_fallback` applies. Still produced a delayed fallback line.
3. **Worker shutdown (`CancelledError`) → ~5-minute silence.** `_run_work`
   only caught `Exception`; `CancelledError` propagated uncaught and the
   session stayed `"running"` until startup-recovery kicked in.

## Design

### Predicate + Runner split

Two distinct pieces replace the marker:

- **`is_pipeline_complete(psm_states, outcome, pr_open=None)`** — pure
  function in `agent/pipeline_complete.py`. Returns `(True, reason)` when
  the pipeline has reached a terminal state per the persisted
  `PipelineStateMachine.states` dict, never based on message content.

  Logic:
  - `(True, "merge_success")` when `psm_states["MERGE"] == "completed"` and
    outcome is success.
  - `(True, "docs_success_no_pr")` when `psm_states["DOCS"] == "completed"`,
    MERGE is not completed, outcome is success, and `pr_open is False`.
  - `(False, "pr_state_unavailable")` when the DOCS-no-MERGE path applies
    but `pr_open is None` — conservative. Never treat unknown state as
    "complete."
  - `(False, <reason>)` otherwise.

  Key subtlety: the predicate reads `psm.states.get("MERGE")` rather than
  calling `psm.current_stage()`. After `complete_stage(MERGE)` fires,
  `current_stage()` returns `None` (no stage is `in_progress`), so a
  predicate keyed on `current_stage` would return `False` precisely when
  the pipeline just finished.

- **`_deliver_pipeline_completion(parent, summary_context, send_cb, chat_id, telegram_message_id)`**
  — async coroutine in `agent/session_completion.py`. Owns the final
  delivery end-to-end:
  1. **Terminal-status guard** (kill-is-terminal, #1208): re-read the
     parent's authoritative hash status. If it is terminal-and-not-`completed`
     (e.g., `killed`, `failed`, `abandoned`, `cancelled`), log at INFO and
     return without acquiring the CAS lock or sending anything. `completed`
     parents pass through so the success-path runner can deliver. The same
     guard fires at the top of `schedule_pipeline_completion(...)` so the
     short-circuit happens before the asyncio task is even created. See
     [Session Lifecycle: Kill-is-Terminal Invariant](session-lifecycle.md#kill-is-terminal-invariant).
  2. Acquire the Redis CAS lock
     `pipeline_complete_pending:{parent_id}` via SETNX (60s TTL). If already
     held, log at INFO and return.
  3. Resolve the PM's prior Claude Code UUID via
     `agent.sdk_client._get_prior_session_uuid`. `None` is tolerated — the
     harness falls back to `full_context_message` for a no-UUID first turn.
  4. Invoke `get_response_via_harness` with a dedicated "compose final
     summary" prompt. On harness failure or empty/whitespace result, fall
     back to the caller-supplied `summary_context`.
  5. Call `send_cb(chat_id, text, telegram_message_id, parent)` to deliver.
  6. Stamp `response_delivered_at` on the parent and finalize to
     `"completed"` via `finalize_session`. The runner is the **sole** caller
     that transitions the parent to `"completed"` on the success path.
  7. On `asyncio.CancelledError`, best-effort deliver
     `"I was interrupted and will resume automatically. No action needed."`
     (dedup'd by `interrupted-sent:{session_id}` — 120s TTL) and re-raise to
     preserve asyncio shutdown semantics.

`schedule_pipeline_completion(...)` wraps the runner in a tracked
`asyncio.Task` registered in `_pending_completion_tasks` so the worker
shutdown sequence can drain it via `drain_pending_completions(timeout=15)`.

### Entry points

Two paths invoke `schedule_pipeline_completion`:

1. **`_handle_dev_session_completion`** — after the existing stage-comment
   and `psm.complete_stage(...)` logic, call the predicate and, if
   `is_complete` is True, spawn the runner and **return** before the
   continuation-steer logic fires.
2. **`_agent_session_hierarchy_health_check`** — when all children of a
   fan-out parent are terminal and none failed, invoke the runner with a
   fan-out-specific summary (listing per-child outcomes) instead of pushing
   a steering message with marker instructions.

Both entry points share the same CAS lock — exactly one runner ever spawns
per parent, regardless of which entry fires first.

### Finalization deferral

`models/session_lifecycle.py::_finalize_parent_sync` checks
`pipeline_complete_pending:{parent_id}` before transitioning the parent to
`"completed"` on the success path. If the lock is held, it returns without
transitioning — the runner owns the terminal transition.

### Call-site gating for `_check_pr_open`

Callers only invoke `_check_pr_open(issue_number)` when
`psm_states["DOCS"] == "completed"` AND `psm_states["MERGE"] != "completed"`.
For the primary MERGE-success path, `pr_open` is not consulted. For
non-terminal stages, the predicate is not called. Net effect: at most one
`gh pr list` subprocess invocation per pipeline, not per dev-session
completion.

### CancelledError handler in `_run_work`

`agent/messenger.py::_run_work` adds an explicit `except
asyncio.CancelledError` branch before the generic `except Exception`.
Behavior mirrors the runner's CancelledError handler: dedup on
`interrupted-sent:{session_id}` (120s TTL), best-effort
`asyncio.wait_for(send_callback(...), timeout=2.0)`, then re-raise. The
redundancy is intentional — both layers may trip during shutdown; the
Redis dedup ensures the user sees exactly one interrupted message per
real interruption window, not N.

## Race conditions (mitigations)

| # | Scenario | Mitigation |
|---|---------|------------|
| 1 | Runner vs `_finalize_parent_sync` | Runner is sole `"completed"` transition. `_finalize_parent_sync` checks `pipeline_complete_pending` lock and defers. |
| 2 | Concurrent runner invocations (`_handle_dev_session_completion` + `_agent_session_hierarchy_health_check`) | Both paths call `schedule_pipeline_completion`, which acquires the same Redis CAS lock. Only one proceeds. |
| 3 | Harness call while worker shutting down | Runner task tracked in `_pending_completion_tasks`; `drain_pending_completions(15s)` cancels past budget; CancelledError handler delivers interrupted message. |
| 4 | `response_delivered_at` double-stamped | Runner stamps synchronously before finalizing. Subsequent output hits `deliver_already_completed`. |

## Flap protection (Risk 6)

A flapping worker (deploy loop, OOM-kill cycling) can otherwise fire the
CancelledError handler on every cycle, producing N identical "interrupted"
messages. Both the messenger handler and the runner's CancelledError branch
SETNX `interrupted-sent:{session_id}` with a 120s TTL. Only the caller that
acquires the lock sends. Genuine distinct interruptions more than 2
minutes apart still surface; rapid-fire duplicates are suppressed.

## Deprecation

The `[PIPELINE_COMPLETE]` marker is fully removed:

- `agent/output_router.py::PIPELINE_COMPLETE_MARKER` constant — deleted.
- `deliver_pipeline_complete` action and branch in
  `agent/session_executor.py` — deleted.
- `config/personas/project-manager.md` marker references at L44, L49, L384,
  L487 — deleted. Rule 5 rewritten to name the worker as the delivery actor.
- Worker-constructed steering messages at
  `agent/session_completion.py:271, 450` — rewritten to say "signaling
  pipeline completion" instead of "emitting [PIPELINE_COMPLETE]".

Contributors encountering the string in older git history, prior PRs, or
chat logs should treat it as dead code. The router no longer routes on
message content; the predicate + runner is the canonical path.

## See also

- `docs/features/pipeline-state-machine.md` — stage states and persistence.
- `docs/features/agent-message-delivery.md` — overall delivery architecture.
- `docs/features/session-steering.md` — fan-out path uses the runner, not
  steering messages, for final delivery.
- Tests: `tests/unit/test_pipeline_complete_predicate.py`,
  `tests/unit/test_deliver_pipeline_completion.py`,
  `tests/unit/test_messenger_cancelled_error.py`,
  `tests/integration/test_pm_final_delivery.py`.
