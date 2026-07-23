# Session Recovery Mechanisms

Catalogue of all 10 session recovery mechanisms, their triggers, terminal status safety, and guard implementations.

## Overview

The session system has 10 mechanisms that can revive, recover, or re-enqueue sessions. After the zombie loop fix (PR #703) and lifecycle consolidation (PR #721), a systematic audit (issue #723) verified that all mechanisms respect terminal session states. PR #730 added the intake path terminal guard (8th mechanism). Issue #977 added harness startup retry (9th mechanism). Issue #1270 added the per-tool timeout sub-loop (10th mechanism) — a 30s parallel loop that recovers sessions wedged on a single tool call (PreToolUse fired but PostToolUse never returned) with tier-specific budgets and per-tier counters.

**Terminal statuses**: `completed`, `failed`, `killed`, `abandoned`, `cancelled`

## Active Mechanisms (7)

### 1. Startup Recovery (`_recover_interrupted_agent_sessions_startup`)

**Note**: As of issue #1767, a prerequisite sweep (`_sweep_dead_worker_sessions`, Step 3a) runs **before** this mechanism (Step 3b). The sweep first finalizes `running` sessions whose `claude_pid` is dead to `killed`; this mechanism then handles the remaining `running` sessions (live or no PID) by re-queuing them to `pending`.

| Property | Value |
|----------|-------|
| Location | `agent/session_health.py` (re-exported from `agent/agent_session_queue.py`) |
| Trigger | Worker process startup (`worker/__main__.py`, Step 3b — after Step 3a dead-worker sweep) |
| What it does | Resets stale `running` bridge sessions to `pending` (orphaned from previous process, with live or absent PID); for local CLI sessions, re-queues eng sessions but abandons teammate/granite sessions |
| Terminal safety | **Safe by query scope** -- only queries `status="running"`, never touches terminal sessions |
| Guard | Query filter (`status="running"`) + timing guard (`AGENT_SESSION_HEALTH_MIN_RUNNING`, 300s) + session_type-aware local session guard |
| Timing guard | Sessions with `started_at` within the last 300s are skipped -- they were likely started by a worker in the current process, not orphaned from the previous one. Sessions with `started_at=None` are always recovered. Matches the same guard used by the periodic health check (mechanism 2). Added by issue #727 to fix a race where a worker picks up a session before startup recovery fires. |
| Local session guard | Sessions with `session_id.startswith("local")` are routed by `session_type`. **Eng** local sessions are re-queued to `"pending"` like bridge sessions (issue #1092): a parent eng session spawned them via `valor-session create --role eng`, so there is no interactive CLI holding the same `claude_session_uuid` and the worker can safely resume via `claude --resume <uuid>`. When the recovered child finalizes, the parent is finalized through `finalize_session` → `_finalize_parent_sync` (no user-facing send callback on this path). **Teammate and Granite** local sessions (plus pre-migration records with `session_type=None`) are finalized as `"abandoned"` — a human CLI may be holding the UUID, and resuming would spawn a second harness competing with the interactive CLI (the issue #986 hijack rationale). The `session_type == SessionType.ENG` gate (`agent/session_health.py:546`) uses explicit equality so any other or future enum member falls through to the safer abandon path. `session_id` is the reliable prefix discriminator (`create_local()` always sets `session_id=f"local-{uuid}"`). |

### 2. Health Check (`_agent_session_health_check`)

| Property | Value |
|----------|-------|
| Location | `agent/session_health.py` (re-exported from `agent/agent_session_queue.py`) |
| Trigger | Periodic timer (every 5 min, `AGENT_SESSION_HEALTH_CHECK_INTERVAL`) |
| What it does | Recovers stuck `running` sessions on three signals: (1) dead/missing worker, (2) worker alive but no progress after the 300s startup guard (issue #944), (3) exceeded session timeout. Starts workers for stalled `pending` sessions. |
| Progress signal | `_has_progress(entry)` uses a **two-tier** detector (issue #1036, narrowed by #1226 / #1724 / #1614 / #1905 / #1935). Tier 1 sub-check A: `last_tool_use_at` or `last_turn_at` fresher than `SDK_PROGRESS_FRESHNESS_WINDOW` (1800s) counts as progress (#1226). Tier 1 sub-check B: when `sdk_ever_output=False`, `last_heartbeat_at` fresh within `HEARTBEAT_FRESHNESS_WINDOW` (90s) counts as progress, but only while the D0 never-started gate (`running_seconds <= NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS` = 150s, issue #1724, clock-consistent with sub-check B's own `running_seconds` as of #1905) has not fired; the gate is the authoritative never-started bound, superseding the #1356 grace-to-budget band and its `tier1_falloff` budget-exceeded telemetry counter (pruned in #1905 as unreachable). `sdk_ever_output` is `agent.session_runner.liveness.derive_sdk_ever_output(entry)` — as of #1935 an OR of THREE fields (`last_tool_use_at`, `last_turn_at`, and `last_stdout_at`, the last written by `SessionRunner._stamp_stdout_liveness` on the headless stream's `init`/stdout events), not just the first two, closing a toolless-but-streaming false-positive wedge. The own-progress signals (`turn_count > 0`, `log_path`, `claude_session_uuid`) are evaluated only when `sdk_ever_output=False` AND the heartbeat is fresh within `NO_OUTPUT_BUDGET_SECONDS` (#1614 — sticky fields no longer count when the heartbeat is stale). The #963 child-activity check is unconditional. Tier 2 (`no_progress` only): `_tier2_reprieve_signal()` checks `compacting` / `children` / `alive`; any one passing gate reprieves the kill for this cycle (the previous `stdout` gate was retired by #1172; this is unrelated to `last_stdout_at`, which feeds `sdk_ever_output`, not a Tier-2 reprieve gate). See [Bridge Self-Healing §Two-tier no-progress detector](bridge-self-healing.md#two-tier-no-progress-detector) for the full design. `derive_sdk_ever_output` was already this row's own shared leaf pre-#2004; issue #2004 Task 2 additionally unified the **other two** hand-forked "has this session progressed" predicates (`session_stall_classifier._has_demonstrable_progress`, `crash_signature._has_demonstrable_progress`) behind a second, narrower leaf in the same module — `has_demonstrable_activity(entry, *, freshness_window=None)`, reading only `{turn_count, last_tool_use_at}`. This row's own leaf and call sites are unchanged; see [Stall Advisory Classifier](stall-advisory-classifier.md#live-never-started-detection) and [Crash-Signature Auto-Resume](crash-signature-auto-resume.md#progress-fields-ground-truth) for the two consolidated callers. |
| Kill path | Cancels `handle.task` from `_active_sessions` registry (0.25s grace). Increments `recovery_attempts`; at `MAX_RECOVERY_ATTEMPTS=2` finalizes as `failed` (history preserved); otherwise transitions `running → pending`. `DISABLE_PROGRESS_KILL=1` suppresses kills while keeping flagging active. `worker_dead`, `timeout`, and `tool_timeout` recoveries skip Tier 2 entirely. |
| Terminal safety | **Safe by query scope** -- only queries `status="running"` and `status="pending"` |
| Guard | Query filter (only non-terminal statuses) + `transition_status()` with default `reject_from_terminal=True` |
| Observability | Each recovery increments `{project_key}:session-health:recoveries:{worker_dead\|no_progress\|tool_timeout}` in Redis (the previous `timeout` reason was retired by #1172; `tool_timeout` was added by #1270 via the shared `_apply_recovery_transition` helper). `no_progress` recoveries on the zombie profile (`claude_session_uuid` set, `sdk_ever_output=False`, stale heartbeat) also increment `recoveries:zombie_uuid_no_output` (#1614). The never-started D0 recovery path (#1724) increments `tier1_falloff:never_started_grace_exceeded` per tick where a session is detected past grace. Tier 2 reprieve increments `tier2_reprieve_total:{compacting\|alive\|children}`. Kills increment `kill_total`. The per-tool timeout sub-loop (mechanism 10) additionally increments `tool_timeouts:{internal\|mcp\|default}` per tier hit, and `tool_timeouts:default_deferred` whenever a granite PTY session's default-tier kill is deferred because the PTY screen is still painting (#1784). (all counter writes are non-fatal) |

#### Delivery guard: epoch-scoping a resumed session's stale delivery (issue #1979)

Both the health check (mechanism 2) and the shared `_apply_recovery_transition` helper (used by mechanism 2's main recovery path and mechanism 10's tool-timeout path) carry a **Delivery guard**: if a `running` session already has `response_delivered_at` set, the guard force-finalizes it as `completed` instead of recovering it back to `pending`. The guard's original purpose (issue #918) is to catch a session that delivered its response to the user but got stuck in `running` instead of finalizing — without it, that session would sit "stuck" until the next health-check tick found it again and duplicate-delivered.

**The bug:** the guard checked only whether `response_delivered_at` was set *at all*, never whether that delivery belonged to the *current* run. `response_delivered_at` is never cleared on resume (`tools/valor_session.py::resume_session` only transitions status and pushes a steering message), so a session resumed after a prior — already delivered — attempt carried the stale timestamp forward. The very next health-check tick after resume force-finalized the resumed session as `completed`, even while a live `claude -p --resume` subprocess was still legitimately working on new content. Observed 2026-07-09 on a real session: the process kept running regardless (orphaned from its own DB record), later opened and merged a real PR, then hit a `StatusConflictError` trying to finalize itself normally against the DB's already-terminal `completed` row.

**The fix:** `_delivery_belongs_to_current_run(entry)` in `agent/session_health.py` replaces the bare presence check with an epoch-scoped comparison: `response_delivered_at >= (started_at or created_at)`. `started_at` is the per-run anchor — it is re-stamped fresh on every pickup, including resumes — so a delivery timestamp from a prior run sorts *before* the current run's `started_at` and the guard correctly declines to fire. A same-run delivery (inclusive of the exact-equality boundary) still fires the guard as before, preserving the original catch-a-stuck-session behavior. Rows with no anchor at all (`started_at` and `created_at` both `None` — i.e. rows predating this repo's `started_at` field) fall back to the original always-fire behavior, since there is no epoch to compare against. Both guard sites — `_apply_recovery_transition` and `_agent_session_health_check` — now call this one shared helper rather than duplicating the presence check.

This follows the same shape as the #1614 sticky-field precedent (Progress signal row above): a field that persists across a resume boundary needs to be scoped to "did this happen *during the current run*," not merely "has this ever been set." A companion audit of `agent/session_health.py`'s other resume-persistent fields, prompted by this fix, found a second unguarded pair — `current_tool_name` / `last_tool_use_at` in the per-tool timeout sub-loop (mechanism 10) read without the same run-scoping — filed as a separate follow-up (issue #2002) and fixed the same way: `_check_tool_timeout` now applies the identical `last_tool_use_at >= (started_at or created_at)` epoch gate with the same no-anchor legacy fallback.

**Tests:** `tests/unit/test_delivery_guard_resume_epoch.py` covers `_delivery_belongs_to_current_run` directly (resumed-session stale delivery, boundary equal timestamps, same-run delivery, no-anchor rows, missing/garbage `response_delivered_at`, `created_at` fallback) plus integration coverage against `_apply_recovery_transition` confirming a stale prior-run delivery falls through to the normal requeue path while a genuine same-run delivery force-finalizes as completed.

#### Finalization gap on re-execution (issue #917)

When the health check recovers a session (`running → pending → running`), the re-executed session may complete successfully but the inner `agent_session` lookup (which filters by `status="running"`) can return `None` — because the session's status was already changed by the recovery cycle. Prior to #917, this caused finalization to be silently skipped: the session stayed in `running` state permanently, and the health check would find it "stuck" again 10–15 minutes later, causing duplicate Telegram delivery.

**Fix:** A fallback `else` branch after the `if agent_session:` finalization block in `_execute_agent_session()` calls `complete_transcript(session.session_id, status=final_status)` using the outer `session` parameter (always available). This is guarded by `if not chat_state.defer_reaction` to preserve the nudge path. `StatusConflictError` is caught at info level (CAS conflict = another process already finalized = success). Other exceptions are caught at warning level.

### 3. Hierarchy Health Check (`_agent_session_hierarchy_health_check`)

| Property | Value |
|----------|-------|
| Location | `agent/session_health.py` (re-exported from `agent/agent_session_queue.py`) |
| Trigger | Periodic timer |
| What it does | Fixes orphaned children (parent deleted) and stuck parents (all children terminal) |
| Terminal safety | **Safe** -- orphan fix preserves original status via `_extract_agent_session_fields`; stuck parent fix only finalizes (terminal transition), never revives |
| Guard | `status` field in `_AGENT_SESSION_FIELDS` preserves terminal status during delete-and-recreate |

### 4. Nudge Re-enqueue (`_enqueue_nudge`)

| Property | Value |
|----------|-------|
| Location | `agent/session_executor.py` (re-exported from `agent/agent_session_queue.py`) |
| Trigger | Agent output during execution (auto-continue) |
| What it does | Re-enqueues session with nudge message for continued execution |
| Terminal safety | **Guarded** -- three-layer defense |
| Guards | (1) Entry guard checks `session.status in TERMINAL_STATUSES`, returns early. (2) Main path re-reads session from Redis after query, returns early if terminal. (3) Fallback path (bypasses `transition_status`) has independent terminal check before `async_create`. |

### 5. Delivery Action Router (`determine_delivery_action`)

| Property | Value |
|----------|-------|
| Location | `agent/output_router.py` |
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
| What it does | Monitors for stuck loops, sets `unhealthy_reason` flag |
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

### 9. Harness Startup Retry (`_handle_harness_not_found`)

| Property | Value |
|----------|-------|
| Location | `agent/session_executor.py` (re-exported from `agent/agent_session_queue.py`) |
| Trigger | `get_response_via_harness()` returns a string starting with `"Error: CLI harness not found"` (i.e., `FileNotFoundError` on `claude` binary) |
| What it does | Silently re-queues the session up to 3 times using `transition_status()` in-place. After 3 failures, delivers one persona-aligned message instead of a raw Python exception string. |
| Terminal safety | **Guarded** -- `transition_status()` default `reject_from_terminal=True` prevents re-queuing a terminal session. B1 guard returns raw early when `agent_session is None`. |
| Guard | `transition_status()` raises `StatusConflictError` on concurrent mutation; caught and treated as exhaustion (delivers persona message instead of re-queuing). |
| Counter | `cli_retry_count` stored in `AgentSession.extra_context`; written before `transition_status()` to guarantee the incremented count is present on the re-queued record. |
| Re-queue method | `transition_status()` in-place (not delete-and-recreate) — preserves `extra_context` without a second write and leaves no orphan `running` record. |

See [Harness Startup Retry](harness-startup-retry.md) for full design details.

### 10. Per-Tool Timeout Sub-Loop (`_agent_session_tool_timeout_loop`)

| Property | Value |
|----------|-------|
| Location | `agent/session_health.py` (worker schedules `asyncio.create_task(name="session-tool-timeout-monitor")` alongside the main `health_task`) |
| Trigger | Periodic timer (every 30s, `TOOL_TIMEOUT_LOOP_INTERVAL`) |
| What it does | Recovers sessions whose `current_tool_name` is set (PreToolUse fired) but whose `last_tool_use_at` exceeds a tier-specific budget (PostToolUse never returned). Without this check, the wedge would ride out the 30-min `SDK_PROGRESS_FRESHNESS_WINDOW` — Tier 1 sub-check A treats `last_tool_use_at` as fresh progress. As of issue #1724, a **D0 block** runs before the tool-timeout check: sessions where `sdk_ever_output=False` and `_never_started_past_grace()` is True are recovered via `_apply_recovery_transition(reason_kind="no_progress")` and `continue`d (skipping the tool-timeout path entirely). As of issue #2002, `_check_tool_timeout` is **epoch-scoped**: it treats the `current_tool_name`/`last_tool_use_at` pair as describing the current run only when `last_tool_use_at >= (started_at or created_at)`, so a stale pair carried over from a prior run (cleared only on the `tool_timeout` requeue path, not the worker-startup or `no_progress`/`worker_dead` recovery paths) no longer fires a spurious tool-timeout on the first tick after a resume. No-anchor legacy rows keep the always-evaluate fallback (same choice as the #1979 delivery guard). |
| Tier classification | `_classify_tool_tier(tool_name)`: `mcp__*` → `mcp`, `{ToolSearch, Read, Glob, Grep, Edit, Write, NotebookEdit}` → `internal`, everything else → `default`. Budgets: 30s / 120s / 300s, env-tunable via `TOOL_TIMEOUT_INTERNAL_SEC`, `TOOL_TIMEOUT_MCP_SEC`, `TOOL_TIMEOUT_DEFAULT_SEC`. |
| Race mitigation | Re-reads `current_tool_name` and `last_tool_use_at` via `AgentSession.get_by_id` immediately before transitioning; aborts if PostToolUse landed between the iterator's read and the transition. The companion writer change (`agent/hooks/liveness_writers.py::record_tool_boundary` bypasses the 5s per-session cooldown when `clear=True`) prevents fast PreToolUse → PostToolUse pairs from leaving stale `current_tool_name`. |
| Recovery path | Routes through the shared `_apply_recovery_transition` helper with `reason_kind="tool_timeout"`. `MAX_RECOVERY_ATTEMPTS`, OOM-defer, the response-delivered finalize-instead-of-recover guard, and `DISABLE_PROGRESS_KILL` all apply uniformly. Tier 2 reprieve is skipped (the wedge is the evidence). On the requeue (`pending`) branch, both `current_tool_name` and `last_tool_use_at` are explicitly cleared to `None` before saving (issue #1762 — see [wedge-signal reset](#wedge-signal-reset-on-requeue-issue-1762) in `agent-session-health-monitor.md`). |
| Terminal safety | **Safe by query scope** — only iterates non-terminal sessions; the shared `_apply_recovery_transition` uses `transition_status()` with `reject_from_terminal=True`. |
| Counters | Three `IntField` counters on `AgentSession` (`tool_timeout_count_internal`, `..._mcp`, `..._default`) cumulate per-tier hits per session. Project-scoped Redis counter `{project_key}:session-health:tool_timeouts:{internal\|mcp\|default}` mirrors the existing `recoveries:{kind}` pattern for dashboards. An additional `tool_timeouts:default_deferred` counter is incremented whenever a granite PTY session's default-tier kill is deferred because the PTY screen is still painting (issue #1784). |
| Default-tier PTY-liveness gate | SDLC build sessions legitimately run default-tier tools (`Bash`, `Skill`, `Task`) for 20+ minutes. For granite PTY sessions, the flat 300s age-only kill was falsely terminating live work. Issue #1784 adds `_pty_quiescent_long_enough(entry, now)` — the kill only fires if `mid_run_quiescent_since >= MID_RUN_QUIESCENCE_SECS (180s)` OR the PTY read loop is stale (> 90s). SDK/non-granite sessions are unaffected (age-only kill preserved). Worst-case bound: ~330s (300s budget + ~30s tick cadence). Kill switch: `MID_RUN_QUIESCENCE_SECS <= 0` restores age-only kill for all sessions. |
| Kill switch | `TOOL_TIMEOUT_TIERS_DISABLED=1` short-circuits the entire sub-loop (parity with `DISABLE_PROGRESS_KILL` for the main loop). |

#### Graceful Degradation on tool_timeout (issue #1711)

Two-layer degradation ensures the user always gets a response when a tool hang triggers recovery:

**Layer 1 — Advisory steering (attempt-1 requeue):** On the `pending` requeue branch (before `MAX_RECOVERY_ATTEMPTS` is exhausted), `_apply_recovery_transition` calls `_compose_tool_timeout_steering(tool_name, original_message_text)` and prepends the result to the session's steering inbox via `push_steering_message(..., front=True)`. The message is self-contained — it names the timed-out tool, embeds the original request verbatim, and instructs the model to skip the hung tool. On re-pickup the worker pops this message first (FIFO front), so the model receives the skip instruction before any pre-existing queue entries. See [Session Steering §Automatic Steering on tool_timeout Recovery](session-steering.md#automatic-steering-on-tool_timeout-recovery) for the prepend mechanics.

**Layer 2 — Deterministic floor (terminal `failed`):** On EVERY `tool_timeout`→`failed` exit — both the `MAX_RECOVERY_ATTEMPTS` exhaustion branch and the not-confirmed-dead branch — `_deliver_tool_timeout_degraded_notice(entry, tool_name)` delivers a canned user-facing message through the session's resolved output handler. Redis `SETNX` prevents double-delivery. Routing is channel-agnostic: Telegram, email, or file output, whichever transport the session was using.

**Precedence over deferred self-draft (issues #1730, #1794):** `_deliver_deferred_self_draft_fallback` fires *before* `_deliver_tool_timeout_degraded_notice` on both `failed` branches. When `extra_context["deferred_self_draft_pending"]` is set (the session deferred a self-draft rewrite before being killed), the deferred text is recovered and delivered instead of the generic notice — **as its scrubbed/converted form, not verbatim** (issue #2211): local-path tokens are removed and, on the sync telegram/email-completed chokepoint, existing non-secret files may attach in their place; the async email helper is text-scrub-only. As of issue #1794, this async helper is **email-only** on `failed`/`abandoned`; telegram sessions are flushed on **all** terminal paths (including `completed`) by the synchronous `flush_deferred_self_draft_sync` chokepoint in `finalize_session`. The two helpers use distinct SETNX keys (`self_draft_fallback_sent:{session_id}` for the async email helper; `self_draft_completed_flush_sent:{session_id}` for the sync telegram chokepoint) so neither blocks the other and double-send is structurally impossible. See [Session Lifecycle §Deferred Self-Draft Fallback Delivery](session-lifecycle.md#deferred-self-draft-fallback-delivery-issues-1730-1794) and [Agent-Controlled Message Delivery §Validator-aware terminal flush](agent-message-delivery.md#validator-aware-terminal-flush-local-path--attachment-conversion-2211) for the full design.

See [Agent Session Health Monitor §Per-Tool Timeout Sub-Loop](agent-session-health-monitor.md#how-it-works) for the full design, including the v1 single-slot `current_tool_name` limitation and out-of-scope items (per-`tool_use_id` registries, synthetic `tool_result` injection).

## Recovery Ownership

Session recovery is split between two processes: the **worker** and the **bridge-hosted watchdog**. Each non-terminal status has exactly one owner responsible for detecting stuck sessions and recovering them.

The authoritative registry is `RECOVERY_OWNERSHIP` in `models/session_lifecycle.py`. A unit test (`tests/unit/test_recovery_ownership.py`) asserts that every non-terminal status has a registered owner, so adding a new status without declaring ownership breaks CI.

| Status | Owner | Recovery Mechanism |
|--------|-------|--------------------|
| `pending` | worker | `_agent_session_health_check` starts a worker for stalled pending sessions |
| `running` | worker | `_agent_session_health_check` + `_sweep_dead_worker_sessions` (Step 3a startup, dead-PID → killed, issue #1767) + `_recover_interrupted_agent_sessions_startup` (Step 3b startup, live/no PID → pending) |
| `waiting_for_children` | worker | `_agent_session_hierarchy_health_check` finalizes stuck parents |
| `active` | bridge-watchdog | `monitoring/session_watchdog.py` `check_all_sessions` + `check_stalled_sessions` |
| `dormant` | bridge-watchdog | `monitoring/session_watchdog.py` via `check_stalled_sessions` activity check |
| `paused` | bridge-watchdog | `agent/sustainability.py` `session_recovery_drip` (dripped after paused_circuit) |
| `paused_circuit` | bridge-watchdog | `agent/sustainability.py` `session_recovery_drip` (dripped first) |
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

Note: `_mark_superseded()` previously passed `reject_from_terminal=False` to convert `completed->superseded`. This override was removed by PR #730 as defense-in-depth — `completed` sessions now remain in their terminal state rather than being re-activated as `superseded`. `_mark_superseded()` itself was later deleted entirely and replaced by `_delete_stale_terminal_duplicates()`, which reconciles divergent duplicate records via ORM delete rather than a `transition_status()` override — see [Session Lifecycle: Divergent Duplicate Records](session-lifecycle.md#divergent-duplicate-records--pop-loop-spin-and-phantom-running-issue-2007) for the current mechanism.

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

### `agent_session` lookup returns None after health-check recovery (issue #917)

- **Window**: Health check transitions session `running → pending`. Worker picks it up, transitions `pending → running`. Inside `_execute_agent_session()`, the inner `agent_session` lookup queries `AgentSession.query.filter(status="running")` — but by this point the status may have shifted again (e.g. another health check cycle or CAS mutation), returning `None`.
- **Mitigation**: Fallback `else` branch calls `complete_transcript(session.session_id, status=final_status)` using the outer `session` parameter (always non-None). Guarded by `if not chat_state.defer_reaction` to preserve nudge path. `StatusConflictError` caught at info level (CAS conflict = success).

### Revived terminal SDLC session races a live peer already owning the same issue (issue #1954)

- **Window**: A terminal `sdlc-local-{N}` eng session (crash-recovery auto-resume from `reflections/crash_recovery.py`, or a manual `valor-session resume`) is revived at the same moment a second, independent session (local CLI or worker-driven) already owns and is actively driving the same GitHub issue. This is the exact mechanism behind incident #1915: a revived terminal session was picked back up by the worker's normal pickup loop while a second local CLI session had independently begun driving the same issue, producing a duplicate PR.
- **Mitigation**: Revival on its own does no SDLC work — the revived session's first real pipeline action still routes through `ensure_session()` (`tools/sdlc_session_ensure.py`) and, before any stage dispatch, `record_dispatch_for_session()` (`tools/sdlc_dispatch.py`), both of which check the per-issue `touch_issue_lock()` Redis lock (`session:issuelock:{issue_number}`) before proceeding. If the issue is already owned by a live peer holding a different `holder_token`, the revived session's `ensure_session()` call returns the `{"blocked": true, "reason": "ISSUE_LOCKED", ...}` shape instead of a usable session_id, and it steps aside rather than racing the live owner. No separate pre-revival gate was added inside `crash_recovery.py` itself — the plan concluded the existing checkpoints already cover the revival path, since a revived session reaches them before any real BUILD-or-later work happens. `find_session_by_issue()`'s `include_terminal=False` default (also #1954) is a companion fix: a terminal session is no longer resolvable as "the" owner of an issue by routing code in the first place. See [SDLC Issue Ownership Lock](sdlc-issue-ownership-lock.md) for the full lock design.

### A live worker mistakes a local `/do-sdlc` anchor for orphaned work (issue #2042)

- **Window**: A human runs `/do-sdlc {N}` locally while a standalone `python -m worker` process is also live (same or another machine). Local supervision creates a deterministic `sdlc-local-{N}` `AgentSession` via `sdlc-tool session-ensure` purely to hold pipeline state (stage markers, verdicts) — it was never meant to be executed. Mechanisms 1, 2, and 10 above all query broadly by `status` (`running`/`pending`), with no field distinguishing a state-only anchor from a genuinely orphaned worker session, so any of them could reset the anchor to `pending` and the worker's pickup loop (`agent/session_pickup.py::_pop_agent_session`) could then run it as a `claude -p` subprocess — a second driver racing the local supervisor on the same issue.
- **Mitigation**: `AgentSession.is_ledger` (`models/agent_session.py`) is set `True` by `sdlc_session_ensure.py` before every `sdlc-local-{N}` anchor is created. Eight worker code paths check it and skip the row: mechanism 1 (startup recovery), mechanism 2's RUNNING and PENDING loops (the RUNNING-loop check sits before the delivery-finalize exit, which would otherwise finalize the anchor as `"completed"`), mechanism 10 (`_agent_session_tool_timeout_check`), the pickup candidate loop (`_pop_agent_session`), plus (issue #2044) `_check_restart_flag()` and the operator `_cli_flush_stuck()` CLI in `agent/agent_session_queue.py`. Duplicate anchors (a rare concurrent-creation race) are accepted as harmless — both carry `is_ledger=True` and both are skipped. See [Eng Session Architecture §sdlc-local session `is_ledger` non-executable flag](eng-session-architecture.md#sdlc-local-session-is_ledger-non-executable-flag-issue-2042) for the full field and guard-site reference.

## Known Limitations

- **Redis TTL expiry**: If terminal session records expire from Redis before a revival check, the terminal-sibling filter in `check_revival()` cannot detect them. Revival may proceed for sessions whose completion record has expired. This is acceptable: if the record is gone, there is no reliable way to detect prior completion.

## Harness Failure Hardening (issue #1099)

The `claude -p stream-json` subprocess is the execution engine for every eng / teammate session. Four failure modes were historically handled by silently returning an empty string and marking the session `completed` — the user got silence with no clear path to recovery. Issue #1099 added minimal, targeted defenses for each mode.

### Mode 1 — Thinking-block corruption (detection + typed failure)

When extended-thinking interacts pathologically with compaction, both the primary harness call AND the stale-UUID fallback exit non-zero with stderr containing the substring `redacted_thinking`. The harness now:

1. Captures the first 2000 chars of stderr (`stderr_snippet`) from `_run_harness_subprocess`.
2. After the stale-UUID fallback completes, checks `stderr_snippet` against `THINKING_BLOCK_SENTINEL = "redacted_thinking"` AND `returncode != 0`. When BOTH conditions are met, raises `HarnessThinkingBlockCorruption` with the user-facing message "Session context corrupted — please start a new thread".
3. `agent/session_executor.py` catches the exception so `BackgroundTask` finalizes the session as `failed` — the user sees the specific error message, not silence.

**Escape hatch:** set `DISABLE_THINKING_SENTINEL=1` (or `true`/`yes`/`on`) in the environment to disable the check at runtime without a code rollback. The sentinel string is taken from the amux "Every way Claude Code crashes" report and is not yet confirmed against Anthropic's published error taxonomy. Every match emits `logger.warning("[harness] THINKING_BLOCK_SENTINEL matched: ...")` BEFORE raising, giving operators a grep-able audit trail (`grep "THINKING_BLOCK_SENTINEL matched" logs/worker.log`).

**Tests:** `tests/unit/test_harness_thinking_block_sentinel.py` (5 cases: sentinel fires, message is user-facing, healthy run passes through, env var disables, zero-exit bypass).

### Mode 2 — Context-usage observability

When per-turn `usage.input_tokens / context_window > 0.75`, the harness emits a single WARNING log per turn:

```
context_usage pct=0.82 session_id=<sid> model=opus input_tokens=164000
```

Pure observability — no state change, no behavior change. Dashboards and operators can grep for `context_usage` to get an early-warning signal before the session degrades.

The helper lives at `agent/sdk_client.py::_log_context_usage_if_risky` and is called from `get_response_via_harness` after the turn completes. Model context windows are looked up via `config/models.py::get_model_context_window`, which accepts both short aliases (`opus`/`sonnet`/`haiku`) and full Anthropic model IDs. Unknown models log a separate "unknown model" WARNING and skip the pct calc rather than crashing.

The entire helper body is wrapped in `try/except Exception: return` — observability must never crash the turn.

**Tests:** `tests/unit/test_harness_context_usage_log.py` (9 cases including alias/full-id parametrization and malformed-usage defensive checks).

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
| `test_startup_recovery_does_not_requeue_local_teammate_session` | startup recovery | `update_session("pending")` never called for local teammate sessions |
| `test_startup_recovery_requeues_local_eng_session` | startup recovery (#1092) | Local eng session re-queued to "pending" with CAS on running |
| `test_startup_recovery_abandons_local_non_eng_sessions` | startup recovery (local guard) | Local non-eng session finalized as "abandoned", count=0 |
| `test_startup_recovery_abandons_local_teammate_sessions` | startup recovery (local guard) | Local teammate session finalized as "abandoned", count=0 |
| `test_startup_recovery_recovers_local_eng_sessions` | startup recovery (#1092) | Local eng session re-queued and counted as recovered |
| `test_startup_recovery_local_eng_session_type_none_defaults_to_abandon` | startup recovery (#1092) | Pre-migration record with session_type=None falls through to abandon |
| `test_startup_recovery_recovers_bridge_sessions` | startup recovery (local guard) | Bridge session reset to "pending", count=1 |
| `test_startup_recovery_mixed_local_and_bridge` | startup recovery (local guard) | Mixed set: teammate→abandoned, eng→pending, bridge→pending, count=2 |
| `test_recent_session_skipped_by_timing_guard` | startup recovery | Sessions started <300s ago are skipped |
| `test_old_session_recovered_by_timing_guard` | startup recovery | Sessions started >300s ago are recovered |
| `test_none_started_at_is_recovered` | startup recovery | Sessions with no started_at are always recovered |
| `test_mixed_recent_and_stale_sessions` | startup recovery | Only stale sessions recovered, recent skipped |
| `test_watchdog_unhealthy_flag_routes_to_deliver` | session watchdog | Flag routes to deliver, not nudge |
| `test_bridge_watchdog_has_no_agent_session_import` | bridge watchdog | No AgentSession imports |
| `TestIntakePathTerminalGuard::test_guard_fires_for_each_terminal_status` | intake path (mechanism 8) | All 5 terminal statuses trigger guard |
| `TestIntakePathTerminalGuard::test_guard_does_not_fire_for_non_terminal_sessions` | intake path | Non-terminal sessions pass through unblocked |
| `TestIntakePathTerminalGuard::test_guard_skipped_for_reply_to_messages` | intake path | Reply-to bypasses guard (explicit resumption) |
| `TestIntakePathTerminalGuard::test_guard_falls_back_gracefully_on_exception` | intake path | Guard failure is non-fatal |
| `TestIntakePathTerminalGuard::test_guard_present_in_telegram_bridge` | intake path | Structural: guard code present in bridge |
| `TestMarkSupersededTerminalGuard::test_completed_to_superseded_is_now_rejected` | _mark_superseded defense-in-depth | completed→superseded now rejected |
| `TestMarkSupersededTerminalGuard::test_mark_superseded_removed_and_no_reintroduced_override` | _mark_superseded defense-in-depth | Structural: `_mark_superseded()` deleted entirely (replaced by `_delete_stale_terminal_duplicates()`, see [Session Lifecycle](session-lifecycle.md)); override never reintroduced anywhere in `agent_session_queue.py` |

Additional coverage in `tests/unit/test_health_check_recovery_finalization.py` (issue #917):

| Test | What it proves |
|------|---------------|
| `test_completed_when_no_error` | agent_session=None + no error → complete_transcript("completed") |
| `test_failed_when_error` | agent_session=None + error → complete_transcript("failed") |
| `test_nudge_path_not_finalized` | agent_session=None + defer_reaction=True → complete_transcript NOT called |
| `test_existing_path_when_agent_session_present` | agent_session non-None → existing path used (regression guard) |
| `test_status_conflict_error_is_info_not_exception` | StatusConflictError caught at info level, not propagated |
| `test_unexpected_exception_is_warning_not_propagated` | Unexpected exception caught at warning level, not propagated |
| `test_fallback_finalization_present_in_agent_session_queue` | Structural: fallback code present in source |

Additional coverage in `tests/unit/test_delivery_guard_resume_epoch.py` (issue #1979 — epoch-scoped Delivery guard, see [Delivery guard: epoch-scoping a resumed session's stale delivery](#delivery-guard-epoch-scoping-a-resumed-sessions-stale-delivery-issue-1979) above).

Additional coverage in `tests/integration/test_session_heartbeat_progress.py` (issue #1036 — 12 tests):

| Test class | What it proves |
|------------|---------------|
| `TestHeartbeatFreshness` | Tier 1: fresh queue-only / SDK-only heartbeat is progress; both stale flags stuck |
| `TestTier2ReprieveIntegration` | Tier 2: `compacting` reprieves; `stdout` gate retired by #1172 (recent stdout no longer reprieves) |
| `TestRecoveryAttemptsIntegration` | `recovery_attempts` and `reprieve_count` fields round-trip through Popoto; `MAX_RECOVERY_ATTEMPTS` constant |
| `TestDisableProgressKillIntegration` | `DISABLE_PROGRESS_KILL=1` suppresses kill; unset by default |
| `TestFreshnessWindowConstants` | `HEARTBEAT_FRESHNESS_WINDOW` is 90s (`STDOUT_FRESHNESS_WINDOW` retired by #1172). Companion constants: `SDK_PROGRESS_FRESHNESS_WINDOW=1800s` and `MAX_NO_OUTPUT_REPRIEVES=20` (#1226); `NO_OUTPUT_BUDGET_SECONDS=1800s` and `STARTUP_GRACE_SECONDS=300s` (#1356). |

## Related

- [Session Lifecycle](session-lifecycle.md) -- State machine and lifecycle module
- [Agent Session Health Monitor](agent-session-health-monitor.md) -- Health check details
- [Bridge Self-Healing](bridge-self-healing.md) -- Bridge watchdog, crash recovery, and two-tier no-progress detector
- Issue #875 / PR #885 -- CAS authority upgrade (compare-and-set conflict detection)
- Issue #723 -- Original audit issue
- Issue #727 -- Startup recovery timing guard (race condition fix)
- PR #703 -- Zombie loop fix (hierarchy health check vector)
- PR #721 -- Lifecycle consolidation
- Issue #917 -- Health-check recovery finalization gap (fallback else branch)
- Issue #986 -- Startup recovery local session guard (do not hijack interactive CLI sessions)
- Issue #1036 -- Two-tier no-progress detector (dual heartbeat + Tier 2 reprieve gates)
- Issue #1099 -- Harness failure hardening for four known modes (thinking-block corruption, context-usage observability, compacting reprieve gate, OOM backoff)
- Issue #1092 -- Session_type-aware local session recovery (worker-owned local eng sessions survive worker restart; teammate/granite still abandoned)
- Issue #1724 -- Never-started session recovery (D0 gate in the 30s sub-loop recovers `sdk_ever_output=False` sessions past `NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS`; Path-B PTY-quiescence detect-and-log; stage-2 recovery deferred) — see [Never-Started Session Recovery](never_started_session_recovery.md)
- Issue #1762 -- Wedge-signal reset on `tool_timeout` requeue: stale `current_tool_name` / `last_tool_use_at` caused the sub-loop to immediately re-fire after recovery, exhausting `MAX_RECOVERY_ATTEMPTS` before the resumed session could take a single turn
- Issue #1784 -- Default-tier PTY-liveness gate: gates the 300s default-tier kill on granite PTY screen quiescence (`_pty_quiescent_long_enough`), preventing false kills of long-running SDLC tools; worst-case bound ~330s; adds `tool_timeouts:default_deferred` counter
- Issue #1979 -- Delivery guard epoch scoping: `_delivery_belongs_to_current_run` compares `response_delivered_at` against `started_at`/`created_at` so a stale prior-run delivery no longer force-finalizes a resumed session still legitimately running; same class of bug as #1614, applied to a different field
- Issue #2002 -- Follow-up from the #1979 sticky-field audit: the tool-timeout sub-loop's `current_tool_name` / `last_tool_use_at` pair is now epoch-scoped in `_check_tool_timeout` (`last_tool_use_at >= started_at or created_at`), so a stale prior-run pair no longer fires a spurious tool-timeout on the first tick after a resume; no-anchor legacy rows preserve the always-evaluate fallback (same shape as #1979)
- Issue #2004 -- Resilience hygiene sweep: unifies the `session_stall_classifier` and `crash_signature` `_has_demonstrable_progress` forks behind one `has_demonstrable_activity` leaf in `agent/session_runner/liveness.py` (this doc's own `derive_sdk_ever_output` leaf is untouched)
- Issue #2042 -- Non-executable ledger flag (`AgentSession.is_ledger`): five worker guard sites (mechanisms 1, 2, 10, plus the pickup candidate loop) skip CLI-created `sdlc-local-{N}` anchors so a live worker never mistakes local `/do-sdlc` pipeline-state bookkeeping for orphaned work — see [Eng Session Architecture](eng-session-architecture.md#sdlc-local-session-is_ledger-non-executable-flag-issue-2042)
