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
  - `config/personas/engineer.md` (Rule 5 rewrite, marker references removed)

## Problem

The parent eng session running the SDLC pipeline previously relied on a string
marker (`[PIPELINE_COMPLETE]`) to signal that the pipeline had finished and the
next output should be delivered to Telegram rather than nudged. The router
inspected every output for the marker; if missing, the output was re-enqueued as
a nudge.

Three observed production failure modes:

1. **Marker omission → 50-nudge loop → garbage forced delivery.** The session
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
  3. Resolve the parent eng session's prior Claude Code UUID via
     `agent.sdk_client._get_prior_session_uuid`. `None` is tolerated — the
     harness falls back to `full_context_message` for a no-UUID first turn.
  4. Invoke `get_response_via_harness` with a dedicated "compose final
     summary" prompt. On harness failure or empty/whitespace result, fall
     back to the caller-supplied `summary_context`.
  5. Call `send_cb(chat_id, text, telegram_message_id, parent)` to deliver.
  6. Stamp `response_delivered_at` on the parent and finalize to
     `"completed"` via `finalize_session`. The runner is the **sole** caller
     that transitions the parent to `"completed"` on the success path.
  7. On `asyncio.CancelledError`, stay silent unless
     `agent/cancel_reason.py::get_cancel_reason` reports the terminal
     `"no_resume"` reason, in which case best-effort deliver
     `INTERRUPT_NO_RESUME` ("won't resume automatically"), dedup'd by
     `interrupted-sent:{session_id}` — 120s TTL, then re-raise to preserve
     asyncio shutdown semantics. An auto-resuming interruption (the common
     case) sends nothing — the session re-queues and its real answer arrives
     later. See
     [Reason-Aware Interrupt Messaging and Failure Notification](#reason-aware-interrupt-messaging-and-failure-notification-issue-1877-silent-resume-inversion)
     below.

`schedule_pipeline_completion(...)` wraps the runner in a tracked
`asyncio.Task` registered in `_pending_completion_tasks` so the worker
shutdown sequence can drain it via `drain_pending_completions(timeout=15)`.

### Trigger

`schedule_pipeline_completion` has a single trigger:

- **`_agent_session_hierarchy_health_check`** (`agent/session_health.py`) —
  when a `waiting_for_children` parent has all children terminal and none
  failed (aggregate success), it builds a fan-out summary (listing per-child
  outcomes) and calls
  `schedule_pipeline_completion(parent, summary, send_cb, chat_id, telegram_message_id)`.
  This replaces the deprecated path that pushed a steering message with marker
  instructions.

The old per-child entry point `_handle_dev_session_completion` was deleted
when the bridge PM and Dev roles merged into the unified `eng` session. Parent
and child completion is now a status transition only — there is no
continuation-session creation and no parent-steering nudge. Final delivery
fires once, from the hierarchy-health check, when the whole fan-out resolves.

### Finalization deferral

`models/session_lifecycle.py::_finalize_parent_sync` is the second half of the
completion coordination. On the success path (`new_status == "completed"`) it
checks `pipeline_complete_pending:{parent_id}` before transitioning the parent.
If the lock is held — i.e., the runner spawned by the hierarchy-health trigger
is in flight — it returns without transitioning, and the runner owns the
terminal `"completed"` transition. Failed parents finalize immediately and
never defer. If Redis is unavailable, the check fails open and finalization
proceeds (pre-runner behavior).

This is the dedup mechanism: the hierarchy-health trigger spawns the runner
(which holds the CAS lock); any concurrent `_finalize_parent_sync` call defers
to it. Exactly one path transitions the parent to `"completed"`.

### Call-site gating for `_check_pr_open`

Callers only invoke `_check_pr_open(issue_number)` when
`psm_states["DOCS"] == "completed"` AND `psm_states["MERGE"] != "completed"`.
For the primary MERGE-success path, `pr_open` is not consulted. For
non-terminal stages, the predicate is not called. Net effect: at most one
`gh pr list` subprocess invocation per pipeline, not per child-session
completion.

### CancelledError handler in `_run_work`

`agent/messenger.py::_run_work` adds an explicit `except
asyncio.CancelledError` branch before the generic `except Exception`.
Behavior mirrors the runner's CancelledError handler: read the cancel-reason
first and stay silent unless it is the terminal `"no_resume"`; only then
dedup on `interrupted-sent:{session_id}` (120s TTL) and best-effort
`asyncio.wait_for(send_callback(...), timeout=2.0)`, then re-raise. The
redundancy is intentional — both layers may trip during shutdown; the
Redis dedup ensures the user sees at most one interrupted message per
real terminal interruption, and zero messages for an auto-resuming one.

## Reason-Aware Interrupt Messaging and Failure Notification (issue #1877; silent-resume inversion)

`agent/notification_copy.py` is the single source of truth for the
user-facing lifecycle copy that both this doc and `agent/messenger.py` /
`agent/session_completion.py` reference: `INTERRUPT_NO_RESUME` and
`FAILURE_NOTICE`. Send sites import these constants rather than inlining
literal strings, so a copy change is a single-file edit. There is no longer
a "will resume automatically" copy constant — auto-resuming interruptions
are silent by design (issue #1937), not narrated with a mid-flight promise.
A session finishes or fails; nothing in between speaks.

### Defect #1 — reason-aware interrupt copy (superseded by the silent-resume inversion)

Before issue #1877, both interrupt send sites hardcoded "I was interrupted
and will resume automatically" on every `CancelledError`, even when the
killer had finalized the session to a terminal, non-resumable status. The
#1877 fix threaded a transient reason signal from the killer to the send
site so it could choose between a resume copy and a no-resume copy without
giving the ORM-free messenger a database read. Issue #1937 went further and
retired the resume copy entirely — the signal is now binary (terminal or
silent), not a choice between two announcements:

- **Key:** `cancel-reason:{session_id}` in `POPOTO_REDIS_DB` (raw Redis, the
  same access pattern as the `interrupted-sent:{session_id}` dedup key).
- **Values:** `"no_resume"` is the only value any caller writes — the killer
  finalized the session terminal and nothing will resume automatically.
  Every non-terminal path (re-queue to `pending`, genuine worker shutdown,
  or an escalation branch whose pre-cancel prediction was non-terminal)
  writes nothing, leaving the key absent. Absent key now means **silence**
  — not a resume-copy fallback (that constant was retired entirely by
  issue #1937).
- **TTL:** 180 seconds for the reason key; 120 seconds for the
  `interrupted-sent:{session_id}` send-dedup key both send sites and the
  escalation-branch terminal notice share. 120s is comfortably above the
  worst-case SIGTERM→SIGKILL escalation wall-clock ceiling
  (`SUBPROCESS_KILL_TIMEOUT = 3.0s` in `agent/session_health.py`), so the
  dedup key cannot expire mid-escalation and reopen a double-send window.
- **Read discipline:** `get_cancel_reason()` is read **only inside the
  branch that won the `interrupted-sent` SET-NX dedup** (i.e. only by the
  site that is actually about to send). This is load-bearing: both
  `agent/messenger.py` and `agent/session_completion.py` race that
  single-winner dedup, and a destructive read by the *losing* (non-sending)
  site could otherwise starve the *winning* site into reading `None`.
  Non-destructive reads plus read-inside-winner placement close that race.
- **Writers:** only a killer that finalizes a session to a terminal,
  non-resumable status writes `"no_resume"` before it cancels the running
  task — the deadline kill in `agent/agent_session_queue.py`'s worker loop,
  and the terminal-escalation branch inside
  `agent/session_health.py::_apply_recovery_transition` (health-check kill
  and the post-cancel subprocess-survived escalation to `failed`). A
  predicted-resume path (re-queue to `pending`) writes nothing at all now,
  where it previously wrote `"resume"`.
- **Last-resort terminal voice for the subprocess-survived escalation
  branch:** when the pre-cancel prediction was non-terminal (nothing
  written), the two `CancelledError` send sites stay silent and never
  acquire the `interrupted-sent` dedup key. If the subprocess then survives
  cancel + SIGTERM + SIGKILL, `_apply_recovery_transition` re-stamps
  `"no_resume"` and calls `_deliver_terminal_interrupt_notice(entry)` — a
  dedicated helper in `agent/session_health.py` that sends
  `INTERRUPT_NO_RESUME` against the shared `interrupted-sent` key, gated by
  `not _has_deferred and not _degraded_sent` so it never double-messages the
  sibling `_deliver_deferred_self_draft_fallback` /
  `_deliver_tool_timeout_degraded_notice` deliveries. Without this last
  resort, that branch would finalize to `failed` in complete silence — the
  exact regression class issue #1937 exists to prevent.
- **Shared delivery mechanics:** `_deliver_terminal_interrupt_notice` and
  `_deliver_tool_timeout_degraded_notice` both delegate their SETNX-dedup +
  transport-resolve + `FileOutputHandler`-fallback + send logic to a common
  `_deliver_oneshot_dedup_notice(entry, *, dedup_key, ttl, message) -> bool`
  helper — the terminal notice calls it with
  `dedup_key=f"interrupted-sent:{session_id}"` / `ttl=120`, the degraded
  notice with its own `tool_timeout:degraded_sent` key / longer TTL.
  `_deliver_tool_timeout_degraded_notice` returns that `bool` (previously
  `None`), and the escalation branch gates `_degraded_sent` on the actual
  return value rather than on merely having called it — a swallowed
  send-callback exception inside the degraded notice no longer silently
  suppresses the terminal notice too (issue #1937 build-stage fix; regression
  covered by
  `test_escalation_branch_speaks_when_degraded_notice_silently_fails` in
  `tests/unit/test_session_health_subprocess_kill.py`).
- **Deliberately not wired:** supersede and PM-cancel finalize sessions that
  are not currently `running`, so no `CancelledError` interrupt send ever
  fires on those paths — there is nothing for a cancel-reason to influence,
  so those call sites do not write one.

See `agent/cancel_reason.py` for the full docstring covering the
non-destructive-read contract and safe-default semantics.

### Defect #2 — running→failed notification

Before issue #1877, a session that crashed from an uncaught exception
(`running` → `failed`) produced no Telegram message at all — the three
finalize-on-failure paths in `agent/session_executor.py` persisted the
terminal status but never called back to the user. The fix adds
`_maybe_send_failure_notice(messenger, session_id)`, invoked on the
failure-finalize branch when `task.error` is set and
`not chat_state.defer_reaction`:

- Sends the shared `FAILURE_NOTICE` copy via
  `messenger._send_callback(...)`, bounded by a 2s `asyncio.wait_for` and
  wrapped so a send failure (including the timeout) is swallowed and never
  blocks finalization — mirroring the CancelledError best-effort pattern.
- Deduped via a `failed-sent:{session_id}` SET-NX key (120s TTL) so the
  three finalize-on-failure paths in the executor never double-send.
- **Cross-class skip-guard:** before sending, the helper checks
  `get_cancel_reason(session_id)`. If a cancel-reason is present, a killer
  already owns this session's exit narrative (it cancelled the task and
  sent its own reason-aware interrupt message), so the failure send is
  skipped entirely rather than sending a second, competing "something went
  wrong" message for the same exit.

## Race conditions (mitigations)

| # | Scenario | Mitigation |
|---|---------|------------|
| 1 | Runner (from hierarchy-health trigger) vs `_finalize_parent_sync` | Runner is sole `"completed"` transition. `_finalize_parent_sync` checks `pipeline_complete_pending` lock and defers when it is held. |
| 2 | Concurrent runner invocations for the same parent | The runner SETNX-acquires `pipeline_complete_pending:{parent_id}`; a second invocation finds the lock held and returns. Only one runner proceeds per parent. |
| 3 | Harness call while worker shutting down | Runner task tracked in `_pending_completion_tasks`; `drain_pending_completions(15s)` cancels past budget; CancelledError handler delivers interrupted message. |
| 4 | `response_delivered_at` double-stamped | Runner stamps synchronously before finalizing. Subsequent output hits `deliver_already_completed`. |

## Flap protection (Risk 6)

A flapping worker (deploy loop, OOM-kill cycling) can otherwise fire the
CancelledError handler on every cycle, producing N identical "interrupted"
messages. Both the messenger handler and the runner's CancelledError branch
SETNX `interrupted-sent:{session_id}` with a 120s TTL. Only the caller that
acquires the lock sends. Genuine distinct interruptions more than 2
minutes apart still surface; rapid-fire duplicates are suppressed.

## Mid-session-send-aware completion suppression

Issue #1262 / plan `docs/plans/dedupe-completion-emit.md`.

### Why it exists

A parent eng bridge session can finish in two visible steps:

1. A sub-skill (`/do-docs`, `/sdlc`, etc.) calls `valor-telegram send` from
   inside the session and posts an answer to the user (Path B).
2. Seconds later, the completion runner fires its auto-emit and posts a
   reformatted version of the same answer.

Without a dedupe check the user sees two consecutive messages saying
substantively the same thing. The runner now reads
`parent.chat_message_log` to detect this and suppresses the auto-emit
when the new draft is substantively a restatement of a recent Path B send.

### What the user sees

| Scenario | User-visible result |
|----------|---------------------|
| Mid-session send + materially-different completion summary | Two messages (the mid-session send + the new summary). |
| Mid-session send + completion summary that restates it (high-confidence dedupe) | One message (the mid-session send) + 👀 reaction on the user's anchor message. |
| Mid-session send + completion summary in the borderline band, judge says "new" | Two messages (the mid-session send + the summary). |
| Mid-session send + completion summary in the borderline band, judge says "restate" | One message + 👀 reaction. |
| No mid-session send | One message (existing behavior — unchanged). |

### Implementation

The runner does two new things between Pass 2's `final_text` and the
existing `send_cb` call:

1. **Pass 1 prompt injection** — a "you already sent these messages in
   this thread" block is appended to the harness prompt, drawn from
   `parent.chat_message_log` outbound entries within the redundancy
   window. (`_build_draft_prompt` was removed from `bridge/message_drafter.py`
   in the drafter_passthrough_validation refactor; this prompt injection in
   `agent/session_completion.py` is now the sole chat-log read path.)
2. **Post-draft suppression check** — calls
   `bridge/redundancy_filter.should_suppress(...)` against an adapter-mapped
   view of the same `chat_message_log` outbound entries
   (`_build_completion_baseline`). The call passes:
   - `threshold=0.55` (LOW band edge — forces `verdict.jaccard` to be
     populated for any meaningful match; the high-confidence cutoff is
     enforced in the caller)
   - `session_status=None` (intentionally bypasses the
     `_TERMINAL_STATUSES` exemption — that exemption is correct for the
     in-session drafter path but the completion runner is a different
     surface where dedupe IS desired)
   - `expectations=None` (Pass 2 returns plain text, not a `MessageDraft`)

   The caller then enforces the high-confidence cutoff
   (`DRAFTER_COMPLETION_REDUNDANCY_THRESHOLD`, default `0.75`):
   - `J >= 0.75` → suppress without LLM cost (high confidence).
   - `0.55 <= J < 0.75` → escalate to a Haiku judge
     (`_judge_completion_novelty`) with the prior text and timestamp;
     judge returns `restate` (suppress) or `new` (deliver).
   - `verdict.action == "send"` → deliver (legitimate non-duplicate).

### Suppress fallback: 👀 reaction on the anchor message

When the runner suppresses, it queues a 👀 reaction on
`telegram_message_id` (the user's anchor message) via the canonical
outbox path — the same payload schema as
`TelegramRelayOutputHandler._build_reaction_payload`
(`agent/output_handler.py:789-820`). If `telegram_message_id` is `None`
(rare: the runner was invoked without an anchor), the runner falls
silent and logs a warning rather than emitting a "Done." text (which
would violate the persona convention of emoji-over-acks).

### Race mitigation: outbox-drain wait + parent re-fetch

The Path B publisher (`tools/valor_telegram.py::cmd_send`) returns
immediately after `r.rpush`; the relay drain loop appends to
`chat_message_log` asynchronously in a separate process. To bound the
read-after-write race, the runner:

1. Calls `_await_outbox_drained(parent, timeout_seconds=2.0)` before
   reading the baseline. Polls `LLEN telegram:outbox:{session_id}` every
   100ms until empty or 2s timeout. Fail-open: returns `True` on any
   exception so a Redis outage cannot block delivery.
2. Re-fetches the parent from Popoto immediately before the suppression
   check via `AgentSession.get_by_id(parent.agent_session_id)` so a
   stale in-memory copy doesn't shadow a fresh chat_log append.

If the wait times out and the suppression baseline misses the most
recent send, the duplicate ships — degraded behavior == today's
behavior, not worse.

### `response_delivered_at = None` is intentional after suppression

When the runner suppresses, `delivery_attempted` stays `False` and
`response_delivered_at` is NOT stamped. Dashboards and analytics that
treat `None` as a failure should be updated to recognize this as
"intentional silent suppression". The session still finalizes cleanly
to `"completed"` (the `finally` block's `finalize_session` call runs
unconditionally).

### Fail-open contract

Every layer of the suppression block is fail-open:

- `_build_completion_baseline` exception → returns `[]` → no suppression.
- `_await_outbox_drained` exception → returns `True` → proceed
  immediately.
- `_judge_completion_novelty` exception or timeout → returns `False` →
  deliver the draft.
- `should_suppress` exception → existing contract returns
  `SuppressionVerdict(action="send", reason="filter_error")`.
- The whole suppression block is wrapped in `try/except` that logs and
  falls through to the existing delivery path.

A buggy suppression check MUST NEVER block a legitimate completion
delivery.

### Scope: SDLC eng sessions only

The completion runner is only invoked by the pipeline-completion path,
which fires for SDLC eng sessions with a fan-out of child sessions. Non-SDLC
eng/teammate sessions take a different path and are unaffected by this fix.
No new gating is needed.

### Tests

- Unit: `tests/unit/test_deliver_pipeline_completion.py::TestCompletionSuppression`
  (14 cases — high-confidence suppress, borderline-band restate vs new,
  inbound-filter, stale-filter, defensive matched_index fallthrough,
  send-with-new-artifact, malformed-entry fail-open, silent-fallback-no-
  anchor, sentinel-bypass, drain-wait-timeout, refetch-before-suppress).
- Unit: `tests/unit/test_redundancy_filter.py::TestThresholdParameter`
  (per-call threshold override).
- Integration: `tests/integration/test_chat_message_log_e2e.py::TestCompletionRunnerSuppressionE2E`
  (3 cases — adapter shape, inbound-exclusion, baseline-source-is-
  chat_message_log-not-recent_sent_drafts).

## Deprecation

The `[PIPELINE_COMPLETE]` marker is fully removed:

- `agent/output_router.py::PIPELINE_COMPLETE_MARKER` constant — deleted.
- `deliver_pipeline_complete` action and branch in
  `agent/session_executor.py` — deleted.
- `config/personas/engineer.md` marker references — deleted. Rule 5 ("MERGE is
  Mandatory Before Sign-Off") names the worker as the delivery actor: the
  session does not self-signal pipeline completion; the worker's predicate +
  runner own delivery once MERGE succeeds.
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
  `tests/integration/test_pm_final_delivery.py`,
  `tests/unit/test_cancel_reason.py`,
  `tests/unit/test_session_executor_failure_notification.py` (issue #1877).
