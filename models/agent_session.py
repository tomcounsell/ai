"""AgentSession model - unified lifecycle tracking for agent work.

Single Popoto model with session_type discriminator ("eng" or "teammate";
"granite" persists on historical records only — see config/enums.py).

Popoto does not support model inheritance, so session types are
distinguished by the session_type field with factory methods and derived
properties providing type-specific behavior.

Session types (permission model):
  Eng session (session_type="eng"): Full-permission Agent SDK session, Engineer persona.
    Owns the Telegram conversation, orchestrates work, runs SDLC pipeline stages,
    and spawns child sessions — unified PM+Dev role.
  Teammate session (session_type="teammate"): Read-only session, Teammate persona.
    Participates in group conversations without orchestration authority.

Parent-child relationship:
  parent_agent_session_id is the canonical parent link.
  parent_session_id and parent_chat_session_id are deprecated aliases that
  delegate to parent_agent_session_id via property.
  Use create_child() to spawn child sessions.

Status lifecycle (see models/session_lifecycle.py for canonical mutation functions):
  Non-terminal: pending -> running -> active -> dormant | waiting_for_children | superseded
  Terminal: completed | failed | killed | abandoned | cancelled
"""

import json as _json
import logging
import time
from datetime import UTC, datetime

from popoto import (
    AutoKeyField,
    DatetimeField,
    DictField,
    Field,
    FloatField,
    IndexedField,
    IntField,
    KeyField,
    ListField,
    Model,
    SortedField,
)

from config.enums import ClassificationType, SessionType
from config.settings import settings
from models.session_event import SessionEvent

logger = logging.getLogger(__name__)

# Maximum number of entries retained in AgentSession.chat_message_log.
# Older entries are trimmed on every append so the field stays bounded.
# 50 × ~200 bytes/entry ≈ 10 KB upper bound — well within Redis hash comfort.
CHAT_LOG_MAX_ENTRIES = 50

# Number of chat_message_log entries included in the drafter prompt.
# Stored log is larger (CHAT_LOG_MAX_ENTRIES) to allow future read patterns;
# the drafter caps display to the most recent CHAT_LOG_DISPLAY_ENTRIES.
CHAT_LOG_DISPLAY_ENTRIES = 20

HISTORY_MAX_ENTRIES = 20

# SDLC stages in pipeline order
SDLC_STAGES = ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "REVIEW", "DOCS", "MERGE"]

# TRM task type vocabulary — used for TaskTypeProfile keying and delegation decisions.
# Pattern-based derivation in tools/session_tags.py auto_tag_session() Rule 7.
TASK_TYPE_VOCABULARY = {
    "sdlc-build",
    "sdlc-test",
    "sdlc-patch",
    "sdlc-plan",
    "bug-fix",
    "greenfield-feature",
    "rework-triggered",
}

# Backward-compatible aliases (import from config.enums for new code)
SESSION_TYPE_ENG = SessionType.ENG
SESSION_TYPE_TEAMMATE = SessionType.TEAMMATE


class AgentSession(Model):
    """Unified model for all Agent SDK sessions, discriminated by session_type.

    Single Popoto model with a session_type discriminator ("eng" or
    "teammate"; "granite" persists on historical records only).

    Session types (permission model):
        Eng session (session_type="eng"):
            Full-permission Agent SDK session, Engineer persona. Owns the
            Telegram conversation, orchestrates work, runs SDLC pipeline
            stages, and spawns child sessions — unified PM+Dev role.
        Teammate session (session_type="teammate"):
            Read-only session, Teammate persona. Participates in group
            conversations without orchestration authority.

    Parent-child hierarchy:
        parent_agent_session_id: Canonical parent link. Set
            by all session creators (create_child, enqueue_session).

    Factory methods:
        create_eng(): Create an Eng session (Engineer persona, full permissions).
        create_teammate(): Create a Teammate session (read-only).
        create_child(): Create a child Eng session.
        create_local(): Create a local CLI session.

    Status values (14 total):
        Non-terminal (use transition_status()):
            pending              - Queued, waiting for worker
            running              - Worker picked up, agent executing
            active               - Session in progress (transcript tracking)
            dormant              - Paused on open question, waiting for human reply
            waiting_for_children - Parent session waiting for child sessions to complete
            superseded           - Replaced by a newer session for the same session_id
            paused_circuit       - Paused by api-health-gate when Anthropic circuit breaker is OPEN;
                                   resumed by bridge-watchdog sustainability drip
            paused               - Paused mid-execution due to auth/API failure;
                                   resumed by bridge-watchdog session-resume-drip
            paused_budget        - Paused by the per-tool budget backstop (#1821) when a session
                                   exhausts its tool-call / cost budget; NON-drip, human-only
                                   recovery (never re-queued by session-recovery-drip)

        Terminal (use finalize_session()):
            completed  - Work finished successfully
            failed     - Work failed (error, crash, or watchdog detection)
            killed     - Terminated by user or scheduler
            abandoned  - Unfinished, auto-detected by watchdog or health check
            cancelled  - Cancelled before execution (pending -> cancelled)

    Lifecycle management:
        All status mutations go through models/session_lifecycle.py:
        - finalize_session(session, status, reason) for terminal transitions
        - transition_status(session, new_status, reason) for non-terminal transitions
        Direct .status = mutations outside the lifecycle module are prohibited.
    """

    # === Identity ===
    id = AutoKeyField()
    session_id = Field()  # Telegram-derived session identifier (e.g., tg_project_chatid_msgid)
    session_type = KeyField(null=True)  # "eng" or "teammate" — discriminator
    project_key = KeyField()
    status = IndexedField(default="pending")  # Non-key field with secondary index for .filter()

    # === Queue fields ===
    priority = Field(default="normal")  # urgent | high | normal | low
    scheduled_at = DatetimeField(null=True)  # UTC datetime; _pop_job() skips if > now()
    created_at = SortedField(type=datetime, partition_by="project_key")
    started_at = DatetimeField(null=True)  # Cannot be SortedField because it starts as None
    updated_at = DatetimeField(null=True)
    # auto_now intentionally not set here. As of popoto>=1.7.1 (#1653) auto_now
    # mints correct UTC, so re-adding it would be safe — but we keep the explicit
    # utc_now() stamp in the save() override below, which also covers save paths
    # that don't route through format_value_pre_save. Only remove the override
    # after confirming auto_now fires on every save path (see #1653 plan).
    completed_at = DatetimeField(null=True)
    response_delivered_at = DatetimeField(null=True)
    working_dir = Field()

    # === Telegram origin (consolidated) ===
    initial_telegram_message = DictField(null=True)  # {sender_name, sender_id, message_text, ...}

    chat_id = KeyField(null=True)

    # === Extra context (consolidated) ===
    extra_context = DictField(null=True)  # revival_context, classification_type/confidence, etc.

    task_list_id = Field(null=True)
    auto_continue_count = Field(type=int, default=0)

    # === Cross-reference to TelegramMessage ===
    telegram_message_key = Field(
        null=True
    )  # msg_id of the TelegramMessage that triggered this session (Popoto key)

    # === Session fields ===
    turn_count = IntField(default=0)
    tool_call_count = IntField(default=0)
    log_path = Field(null=True)
    branch_name = Field(null=True)
    tags = ListField(null=True)
    task_type = IndexedField(null=True)  # TRM task category (see TASK_TYPE_VOCABULARY)
    rework_triggered = Field(null=True)  # "true"/"false" — session retried prior output

    # === Structured event log (replaces history, summary, result_text, stage_states) ===
    session_events = ListField(null=True)  # List of SessionEvent dicts

    issue_url = Field(null=True)
    plan_url = Field(null=True)
    pr_url = Field(null=True)

    # === Issue-level SDLC ownership lock visibility mirror (issue #1954) ===
    # Read-side only: written ONCE at session creation to record which GitHub
    # issue this session's SDLC pipeline work is driving, never re-written on
    # lock renewal. Nullable additive field -- no backfill needed, and no
    # ``_INT_FIELDS_BACKCOMPAT`` entry required since Popoto's lazy-load
    # descriptor healing covers nullable fields generically. See
    # models/session_lifecycle.py::touch_issue_lock() for the Redis-backed
    # lock this field mirrors for human-readable dashboard/CLI display.
    issue_number = IntField(null=True)

    # === SDLC run identity + PR number mirror (issue #2003) ===
    # active_run_id: the uuid-hex identity of the pipeline run that currently
    # owns this session's issue lock. Minted EXCLUSIVELY by
    # tools/sdlc_session_ensure.ensure_session() when it wins the lock contest
    # (SET NX on session:issuelock:{N}); mirrored here for inspection and for
    # the two in-process renewal paths (tools/_sdlc_utils.
    # renew_issue_lock_for_session, agent/session_executor.
    # _tick_issue_lock_renewal) that read BACK the identity their own
    # ensure_session established. Never a source of adoption: a foreign
    # process must not read this field to impersonate the incumbent — the
    # lock payload, not this mirror, decides ownership.
    # pr_number: the PR opened for this session's work. Single writer is
    # /do-build at PR creation; read-only recovery rungs live in
    # tools/sdlc_stage_query. Both fields are nullable additive — no
    # backfill needed (Popoto lazy-load descriptor healing covers them).
    active_run_id = Field(null=True)
    pr_number = IntField(null=True)

    # === Claude Code identity mapping ===
    # IndexedField so the PreCompact hook's 3-per-fire lookups
    # (`AgentSession.query.filter(claude_session_uuid=...)` in
    # `agent/hooks/pre_compact.py::_check_cooldown`, `_update_session_cooldown`,
    # and `_increment_skipped_count`) use a stable secondary index rather than
    # a full-scan. Also eliminates a test-flake path where filter-by-UUID
    # could miss a just-saved row across partial `save(update_fields=...)`
    # calls in the same process (#1127 PR #1135 review tech-debt).
    claude_session_uuid = IndexedField(null=True)

    # === Session-runner resume scalars (plan #1924, spike #1928) ===
    # The four-scalar resume contract for headless sessions. The PM session
    # UUID (the sole `--resume` entry point) REUSES `claude_session_uuid`
    # above — do not add a duplicate field. The remaining three are nullable
    # adds; `_heal_descriptor_pollution` walks fields generically, so no
    # backcompat code is needed.
    #
    # dev_agent_id: the Dev subagent continuation handle — captured
    #   STRUCTURALLY from the sidechain directory scan
    #   (~/.claude/projects/{slug}/{claude_session_id}/subagents/), never
    #   parsed from PM prose. Lets a resumed session continue the SAME dev
    #   agent across worker restarts.
    dev_agent_id = Field(null=True)
    # runner_cwd: exact absolute working dir of the runner — Claude session
    #   lookup is cwd-scoped, so resume must re-invoke from this directory
    #   (validated to exist before any --resume; Race 3).
    runner_cwd = Field(null=True)
    # claude_version: CLI version the session ran under — agent-continuation
    #   behavior is version-specific (Risk 5a); deploy gates smoke the
    #   continuation contract before trusting resume across a version bump.
    claude_version = Field(null=True)

    # === Claude CLI subprocess PID (issue #1271) ===
    # The OS PID of the `claude_agent_sdk/_bundled/claude` subprocess spawned
    # for this session. Persistent across the session's lifetime (cleared on
    # terminal-state transitions in `models/session_lifecycle.py`). Written by
    # the `_on_sdk_started(pid)` callback in `agent/session_executor.py`.
    # IndexedField so the cross-process orphan reaper
    # (`agent/session_health.py::_reap_orphan_session_processes`) can resolve
    # the owning session per-PID via `find_by_claude_pid()` without a full scan.
    claude_pid = IndexedField(null=True)

    # === Tracing ===
    correlation_id = Field(null=True)  # End-to-end request tracing ID

    # === Watchdog fields ===
    # Reason string when flagged unhealthy, None when healthy. Renamed from
    # `watchdog_unhealthy` (schema diet #1927) — the old name implied a bool
    # flag; it always held a reason string. `_normalize_kwargs` back-aliases
    # the old key name for archive-restore payloads written before the
    # rename (see the "Map old field names to new ones" section below).
    unhealthy_reason = Field(null=True)

    # === Semantic routing fields ===
    context_summary = Field(null=True)  # What this session is about
    expectations = Field(null=True)  # What the agent needs from the human

    # === PM self-messaging ===
    pm_sent_message_ids = ListField(null=True)

    # === Drafter redundancy filter (issue #1205) ===
    # Last N successfully-sent draft texts + metadata, stored as a list of
    # dicts {ts: float, text: str, artifacts: dict}. Text entries are capped
    # at 500 chars per entry (full drafts can be ~4 KB). The list is capped at
    # RECENT_DRAFTS_N (default 3) by record_recent_sent_draft(). FIFO: oldest
    # entry dropped when cap is exceeded. Written by the funnel helper below;
    # read only inside TelegramRelayOutputHandler.send. See the precedent at
    # _append_event_dict (models/agent_session.py) for the partial-save pattern.
    recent_sent_drafts = ListField(null=True)

    # === Project config (full project dict from projects.json) ===
    # Carried through the pipeline so downstream code never needs to re-derive
    # project properties. Populated at enqueue time; empty dict for legacy sessions.
    project_config = DictField(null=True)

    # === Slugged session fields (null for unslugged eng or teammate sessions) ===
    # KeyField so `query.filter(slug=...)` is an indexed lookup — required for
    # worker pop to find slugged dev sessions by slug (issue #1085).
    slug = KeyField(null=True)  # Derives branch, plan path, worktree

    # === Session hierarchy fields ===
    parent_agent_session_id = KeyField(null=True)

    # === Per-session model selection ===
    # Claude model name for this session (short aliases preferred: "opus",
    # "sonnet", "haiku"; full names like "claude-opus-4-7" also accepted).
    #
    # Flows to the CLI harness subprocess as `--model <value>` via
    # `agent.session_executor._resolve_session_model()` and
    # `agent.sdk_client.get_response_via_harness(model=...)`. When None/empty,
    # the D1 precedence cascade falls through to
    # `settings.models.session_default_model` and finally the codebase
    # default "opus". See `docs/features/agent-session-model.md` for details.
    model = Field(null=True)

    # === BUILD session retention for hard-PATCH resume ===
    # When True, this session's completed record is exempt from scheduler cleanup
    # so the PM can resume it via `valor-session resume --id <id>`.
    # Set to True by the worker on BUILD session completion.
    # Cleared by `valor-session release --pr <N>` after PR merge/close.
    # Meta.ttl below serves as absolute backstop if the release hook never fires.
    retain_for_resume = Field(default=False)

    # === BYOB scheduler-layer serialization (issue #1256, Decision 2) ===
    # When True, this session expects to drive the user's real Chrome via the
    # BYOB MCP tools (byob_navigate, byob_click, etc.). Real Chrome has one DOM
    # tree, so two such sessions cannot run concurrently without corrupting
    # active-tab state. The worker session-pick loop in agent/session_pickup.py
    # checks this flag before starting a candidate: if any currently-running
    # session has requires_real_chrome=True, the new candidate is deferred
    # until the running one finishes (no file lock; pure scheduler defer).
    # Set at session creation time via `valor-session create --needs-real-chrome`.
    # Per memory feedback_field_backcompat_heal (issues #1099, #1172): nullable
    # Popoto field needs no migration code; _heal_descriptor_pollution walks
    # fields generically. Default False keeps existing sessions unaffected.
    requires_real_chrome = Field(default=False)

    # === Runner user-facing delivery tracking (issue #1647) ===
    # Set to True by SessionRunnerAdapter.publish_exit_summary when at least
    # one [/user] or non-empty [/complete] payload was confirmed delivered to
    # the user channel during the runner session. The executor's emoji branch
    # at session_executor.py reads this via getattr(..., False) to choose
    # REACTION_COMPLETE instead of the bare-emoji REACTION_SUCCESS, because the
    # runner path never calls messenger.send() so has_communicated() stays False.
    # Default False keeps existing sessions unaffected (no migration needed).
    user_facing_routed = Field(default=False)

    # === Non-executable ledger anchor marker (issue #2042) ===
    # True marks a CLI-created `sdlc-local-*` AgentSession anchor (created by
    # `sdlc-tool session-ensure`) as a non-executable ledger record: it holds
    # SDLC pipeline state for one GitHub issue but must never be picked up,
    # requeued, or run by the worker. Prevents a live worker from mistaking
    # the anchor for real work and double-driving an SDLC pipeline alongside
    # a genuine local-anchor session. Plain Field (not IndexedField) -- every
    # read site already loads the candidate object, so an attribute check is
    # sufficient; no query-by-is_ledger is needed. Default False keeps
    # existing sessions unaffected (no backfill required).
    is_ledger = Field(default=False)

    # Runner exit reason — the exit-classification vocabulary in
    # agent/session_runner/router.py (pm_complete, pm_user, pm_floor_delivered,
    # steer_abort, error, exception, turn_timeout, pm_empty_turn, pm_max_turns).
    # Historical PTY values (dev_hang, pm_hang, startup_unresolved, ...) persist
    # in old records and stay valid. Status mapping untouched; dashboard renders
    # a warning chip for non-clean values.
    exit_reason = Field(null=True, default=None)

    # === Runner PM subprocess identity ===
    # PM `claude -p` OS process ID for the CURRENT turn, persisted by the
    # runner's on-spawn callback (agent/session_runner/runner.py::
    # _on_turn_spawn) BEFORE the turn-await blocks (Race 2) — a worker crash
    # mid-turn leaves a reapable record. Nullable; None before the first
    # turn's subprocess exists.
    pm_pid = IntField(null=True)

    # === Crash-recovery reflection fields (issue #1539) ===
    # crash_signature: write-once stamp set at resume time, recording the
    # crash signature of the session this resume recovers. Used by the
    # crash-recovery reflection to attribute outcomes back to the originating
    # signature. Never overwritten after initial write.
    # Coordination surface for #1539 auto-resume policy.
    crash_signature = Field(null=True, default=None)

    # crash_outcome_attributed: idempotency key for the outcome-attribution loop.
    # Set True after the reflection has credited/debited the originating
    # CrashSignature record for this session's terminal outcome.
    # Read via _truthy() — Popoto stores bools as "True"/"False" strings.
    crash_outcome_attributed = Field(null=True, default=None)

    # auto_resume_attempts: count of times the crash-recovery reflection has
    # auto-resumed this session. Enforces per-session cap.
    auto_resume_attempts = Field(null=True, default=None)

    # === Continuation PM depth tracking ===
    # Tracks how many continuation PMs have been chained from the original PM.
    # Stored directly on the session (O(1)) rather than walking the parent chain
    # (which is fragile under TTL expiry). Defaults to 0.
    # _create_continuation_pm increments from parent's value; capped at 3.
    continuation_depth = IntField(default=0)

    # === Two-tier no-progress detector fields (issue #1036) ===
    # Queue-layer heartbeat, written by `_heartbeat_loop` inside
    # `_execute_agent_session` every HEARTBEAT_WRITE_INTERVAL (60s). Health
    # check reads this as Tier-1 liveness signal #1.
    last_heartbeat_at = DatetimeField(null=True)
    # Messenger-sourced heartbeat, written by the `on_heartbeat_tick` callback
    # invoked from `BackgroundTask._watchdog`. Health check reads this as
    # Tier-1 liveness signal #2 (dual-heartbeat OR semantics).
    last_sdk_heartbeat_at = DatetimeField(null=True)
    # Messenger-sourced stdout event timestamp, written by `on_stdout_event`
    # callback. Feeds the Tier-2 "recent stdout" reprieve gate.
    last_stdout_at = DatetimeField(null=True)
    # Health-check owned counters. Incremented ONLY on actual kills
    # (Tier 1 + Tier 2 both stuck); never on reprieves or worker restart.
    # At MAX_RECOVERY_ATTEMPTS, recovery finalizes as `failed` instead of
    # re-queueing to `pending`, preserving terminal history.
    recovery_attempts = IntField(default=0)
    # Count of Tier 2 reprieves (activity-positive saves) for post-hoc analysis.
    reprieve_count = IntField(default=0)

    # === Harness subprocess PID (issue #1269) ===
    # PID of the live `claude -p stream-json` subprocess for THIS session, when
    # one is currently running. Subprocess-scoped lifecycle (NOT session-scoped):
    # written by the worker's `_on_sdk_started(pid)` closure when a harness
    # subprocess spawns; cleared by the paired `_on_sdk_finished()` closure the
    # instant `proc.communicate()` returns for that subprocess. The session-exit
    # `finally` block in `_execute_agent_session` performs a defensive idempotent
    # clear for abnormal-termination paths (worker crash, CancelledError).
    #
    # Single-writer contract: ONLY `_on_sdk_started` / `_on_sdk_finished` (paired
    # closures owned by `_execute_agent_session`) write this field. The dashboard
    # reads it for the `os.kill(pid, 0)` liveness probe. PID may be None at any
    # time — `_check_process_alive(None)` returns None (uncertain), and the
    # modal renders gracefully.
    #
    # Multi-spawn note: a single turn can spawn up to 3 subprocesses (primary +
    # image-dimension fallback + stale-UUID fallback). Each invocation owns the
    # field exclusively for its runtime; between subprocesses the field is None.
    # See `agent/sdk_client.py::get_response_via_harness` call sites at lines
    # 2205, 2243, 2295. PID recycling on a busy worker is the principal risk
    # (gh/git/pytest/ruff subprocesses recycle freed PIDs), and the
    # subprocess-scoped lifecycle is the principal mitigation.
    harness_pid = IntField(null=True)

    # === Compaction hardening fields (issue #1127) ===
    # Unix timestamp of the most recent successful JSONL backup captured by
    # `pre_compact_hook`. Read by `agent/output_router.py::determine_delivery_action`
    # to enforce the 30-second post-compaction nudge guard. Read by the
    # PreCompact hook itself to enforce the 5-minute cooldown between backups.
    # Writer: `agent/hooks/pre_compact.py::pre_compact_hook` via partial save
    # `save(update_fields=["last_compaction_ts"])`. Also written by the
    # SDK-tick backstop in `agent/session_executor.py` when a message-count
    # drop implies the hook missed a compaction.
    last_compaction_ts = FloatField(default=None)

    # === Per-session token + cost accounting (issue #1128) ===
    # Cumulative counts aggregated from SDK ResultMessage.usage and from the
    # harness `result` event payload (both paths write here via
    # `agent/sdk_client.py::accumulate_session_tokens`). Readers: dashboard
    # (`/dashboard.json`) and the session watchdog token-threshold alert.
    # Writers: worker process only. Default 0 (never None) for forward-compat
    # with existing JSON consumers.
    total_input_tokens = IntField(default=0)
    total_output_tokens = IntField(default=0)
    total_cache_read_tokens = IntField(default=0)
    # `total_cost_usd` is taken verbatim from `msg.total_cost_usd` (SDK) or
    # `data.get("total_cost_usd")` (harness). Never recomputed from token
    # counts — this tracks upstream pricing automatically.
    total_cost_usd = FloatField(default=0.0)

    # === Per-tool budget backstop (issue #1821, Fix #6) ===
    # Hook-owned deny-surfacing fields set by the PreToolUse budget backstop
    # (agent/tool_budget.py) when a session exhausts MAX_TOOL_CALLS_PER_SESSION
    # or SESSION_COST_CAP_USD. Written ONLY by the budget hooks via a narrow
    # save(update_fields=["budget_tripped", "budget_tripped_reason", ...]) —
    # NEVER a status write (a hook-driven status write would race the runner
    # adapter's partitioned update_fields saves). No other writer touches
    # these fields, so they are always race-free; the dashboard,
    # `valor-session status`, and the adapter/worker READ them. Both default
    # falsy and Popoto is schema-on-read, so existing records need NO data
    # migration.
    budget_tripped = Field(default=False)
    budget_tripped_reason = Field(null=True, default=None)

    # Last subprocess exit code from `_run_harness_subprocess` (issue #1099).
    # Persisted best-effort by `get_response_via_harness` after the stale-UUID
    # fallback completes. Read by `agent/session_health.py` in the recovery
    # branch to distinguish OS-initiated OOM kills (`exit_returncode == -9`)
    # from health-check-initiated kills. When Mode 4 OOM-defer conditions are
    # met, the health check sets `scheduled_at = now + 120s` to throttle
    # re-queue under memory pressure.
    #
    # Default 0 = "no exit code recorded". Matches the convention used by every
    # other IntField on this model. A healthy clean exit (returncode 0) is not
    # meaningful to any reader here — the only reader checks for `== -9`, so
    # conflating "not recorded" with "healthy exit" is safe.
    exit_returncode = IntField(default=0)

    # === In-flight visibility fields (issue #1172, Pillar A) ===
    # Name of the tool currently being executed by the SDK subprocess, written
    # by `agent/hooks/pre_tool_use.py::pre_tool_use_hook` and cleared by
    # `agent/hooks/post_tool_use.py::post_tool_use_hook`. Read by the dashboard
    # `_session_to_json()` in `ui/app.py` so operators see live tool activity
    # without inferring from staleness. Nullable; sessions running across the
    # deploy boundary keep this None until their next tool boundary.
    current_tool_name = Field(null=True, default=None)
    # Timestamp of the last tool boundary (PreToolUse OR PostToolUse, whichever
    # fired most recently). Bumped by both hooks. Read by the dashboard for the
    # `last_evidence_at` derivation. Replaces stdout-staleness inference for
    # operator-facing liveness signal.
    last_tool_use_at = DatetimeField(null=True)

    # === Per-tool timeout counters (issue #1270) ===
    # Cumulative count of tool-wedge recoveries broken down by tier. Bumped by
    # `_agent_session_tool_timeout_loop` in `agent/session_health.py` when a
    # tool's `last_tool_use_at` exceeds its tier-specific budget. The tier is
    # determined by `_classify_tool_tier(current_tool_name)`:
    #   - internal: ToolSearch, Read, Glob, Grep, Edit, Write, NotebookEdit (30s)
    #   - mcp: any tool whose name starts with `mcp__` (120s)
    #   - default: everything else, e.g. Bash, Task, Skill, WebFetch (300s)
    # Each counter is bounded by `MAX_RECOVERY_ATTEMPTS` per session lifetime.
    # Default 0 (Popoto-backcompat-safe — pre-deploy sessions read 0).
    tool_timeout_count_internal = IntField(default=0)
    tool_timeout_count_mcp = IntField(default=0)
    tool_timeout_count_default = IntField(default=0)
    # Timestamp of the most recent SDK `result` event (turn boundary). Written
    # by `agent/sdk_client.py::_run_harness_subprocess` when `event_type ==
    # "result"`. Read by the dashboard for the `last_evidence_at` derivation.
    last_turn_at = DatetimeField(null=True)
    # Last 280 chars of extended-thinking content from the SDK stream. Written
    # by the SDK client's stream-event handler when accumulating thinking
    # deltas. Capped at 280 chars (tweet length) — small enough to render,
    # large enough to be informative. Throttled to one save per 5s.
    recent_thinking_excerpt = Field(null=True, default=None)

    # === Chat message log (issue #1192) ===
    # Rolling, bounded log of inbound and outbound chat traffic for this session.
    # Each entry is a dict: {direction, sender, content, message_id, ts}.
    #   direction: "in" (from user/Telegram) or "out" (sent by Valor)
    #   sender: display name of sender (e.g. "Tom", "valor", "unknown")
    #   content: message text
    #   message_id: Telegram message id (int or None)
    #   ts: Unix timestamp (float)
    # Bounded to CHAT_LOG_MAX_ENTRIES via append_chat_log(). Nullable; existing
    # sessions that have never received a chat-log write return [] via default=list.
    # The drafter reads the last CHAT_LOG_DISPLAY_ENTRIES entries for context.
    chat_message_log = ListField(default=list)

    class Meta:
        # 30 days — hard backstop for retain_for_resume BUILD sessions.
        # Sourced from settings so it's .env-overridable (issue #1968 Task 5).
        ttl = int(settings.timeouts.agent_session_retain_ttl_s)

    # === Worker routing key ===

    # Stages where slugged Eng sessions operate in an isolated worktree.
    # Uses an allowlist (not denylist) so unknown/future stages fail closed —
    # they serialize on project_key rather than accidentally parallelizing.
    # Matches the worktree-using stages in resolve_branch_for_stage().
    _ENG_WORKTREE_STAGES: frozenset[str] = frozenset({"BUILD", "TEST", "PATCH", "REVIEW", "DOCS"})

    @property
    def worker_key(self) -> str:
        """Compute the worker loop routing key based on isolation level.

        Teammate sessions run in parallel across chats, keyed by chat_id.

        Eng sessions:
        - Slugless Eng sessions always serialize per project_key (PR #828 invariant).
        - Slugged Eng sessions at worktree-compatible stages (BUILD/TEST/PATCH/REVIEW/DOCS)
          route by slug — two sibling Eng sessions with distinct slugs run concurrently.
        - Slugged Eng sessions at main-checkout stages (PLAN/ISSUE/CRITIQUE/MERGE/None)
          fall back to project_key to prevent git conflicts on main.

        Slugs are assumed unique across the active session keyspace.  If two
        sessions in different projects share a slug, they will share a worker
        loop and serialize.
        """
        if self.session_type == SessionType.TEAMMATE:
            return self.chat_id or self.project_key
        if self.session_type == SessionType.ENG:
            # Slugged Eng sessions at worktree stages route by slug (parallel-safe).
            # Slugless Eng sessions and sessions at main-checkout stages serialize by project_key.
            if self.slug and self._eng_stage_is_worktree_compatible():
                return self.slug
            return self.project_key
        # Fallback: isolated by slug (worktree) if present, serialized by project otherwise
        if self.slug:
            return self.slug
        return self.project_key

    def _eng_stage_is_worktree_compatible(self) -> bool:
        """Return True only for stages where a slugged Eng session uses an isolated worktree.

        Uses an allowlist so unknown or future stages fail closed (serialize)
        rather than accidentally parallelizing on an unaudited stage.
        """
        stage = getattr(self, "current_stage", None)
        return stage in self._ENG_WORKTREE_STAGES

    @property
    def is_project_keyed(self) -> bool:
        """Whether this session routes to a project-keyed worker loop."""
        return self.worker_key == self.project_key

    # === Backward-compatible field name mapping ===

    # DatetimeField names that should auto-convert float timestamps
    _DATETIME_FIELDS = {
        "scheduled_at",
        "started_at",
        "updated_at",
        "completed_at",
        "response_delivered_at",
        # Two-tier no-progress detector heartbeat/stdout fields (issue #1036)
        "last_heartbeat_at",
        "last_sdk_heartbeat_at",
        "last_stdout_at",
    }

    # IntField names added after initial model creation. Defensive coercion in
    # ``__setattr__`` and ``__getattribute__`` substitutes the safe default (0)
    # if a stale value sneaks through. Popoto v1.6.1 fixed the lazy-load
    # descriptor leak that originally motivated this set (issue #1099), but the
    # guards stay as belt-and-suspenders for any other path that could hand
    # back a non-int value.
    _INT_FIELDS_BACKCOMPAT = {
        "exit_returncode",
        # Per-tool timeout counters (issue #1270) — added after initial model
        # creation; rows written before the field was added must heal to 0
        # rather than expose the class-level descriptor.
        "tool_timeout_count_internal",
        "tool_timeout_count_mcp",
        "tool_timeout_count_default",
    }

    def __init__(self, **kwargs):
        """Initialize AgentSession with backward-compatible field name support."""
        kwargs = self.__class__._normalize_kwargs(kwargs)
        super().__init__(**kwargs)

    def __getattribute__(self, name):
        """Heal ``_INT_FIELDS_BACKCOMPAT`` descriptor leaks on read access.

        Issue #1099: Popoto's lazy-load path (``_create_lazy_model``) uses
        ``object.__new__`` and only registers keys present in the Redis hash.
        When a field was added after a row was written, accessing the field
        falls through to the class-level ``IntField`` descriptor object
        itself — readers like ``session_health.py``'s ``exit_returncode ==
        -9`` check then silently misbehave (descriptor != -9 is always True
        in the sense of "not equal", so the OOM detector never fires for
        legacy rows).

        This override intercepts reads of ``_INT_FIELDS_BACKCOMPAT`` names
        and, if the attribute is the descriptor object itself, substitutes
        the declared default (0) while also healing ``__dict__`` so future
        reads hit the cached scalar. Uses ``object.__getattribute__`` to
        avoid recursion. Must call ``super().__getattribute__`` to preserve
        Popoto's lazy-load semantics for all other field names.
        """
        # Fast path: Popoto's lazy-load and non-field attributes.
        value = super().__getattribute__(name)
        # Only heal fields we've explicitly registered as backcompat IntFields.
        # Guard against recursion and AttributeError during __init__ ordering
        # by reading the class attribute directly via type.__getattribute__.
        try:
            backcompat = type.__getattribute__(type(self), "_INT_FIELDS_BACKCOMPAT")
        except AttributeError:
            return value
        if name not in backcompat:
            return value
        # If the value we got back is the class-level IntField descriptor
        # itself, coerce to the declared default and cache in __dict__ so
        # future reads are scalar.
        from popoto.fields.shortcuts import IntField as _IntField  # local import

        if isinstance(value, _IntField):
            field = value
            default = field.default
            if default is None or not isinstance(default, int):
                default = 0
            # Direct __dict__ write bypasses __setattr__ to avoid any
            # further coercion cycles; the value is already a plain int.
            object.__getattribute__(self, "__dict__")[name] = default
            return default
        return value

    def __setattr__(self, name, value):
        """Auto-convert timestamps to datetime for DatetimeField fields.

        Handles all assignment paths (construction, Redis load, direct set):
        - int | float: Unix timestamp → UTC datetime
        - str: ISO 8601 string → UTC-aware datetime (None on parse failure)
        - other non-datetime, non-None types: reset to None (field is null=True)

        This guards against Popoto's is_valid() coercion failure when a
        DatetimeField holds a non-datetime value (e.g. a descriptor object
        for sessions loaded from Redis before the field existed).
        """
        if name in self._DATETIME_FIELDS:
            if isinstance(value, int | float):
                value = datetime.fromtimestamp(value, tz=UTC)
            elif isinstance(value, str):
                try:
                    dt = datetime.fromisoformat(value)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    value = dt
                except (ValueError, TypeError):
                    logger.debug(
                        f"AgentSession: coerced {name}={value!r} → None (unparseable ISO string)"
                    )
                    value = None
            elif value is not None and not isinstance(value, datetime):
                logger.debug(
                    f"AgentSession: coerced {name}={value!r} → None "
                    f"(bad type {type(value).__name__})"
                )
                value = None
        elif name in self._INT_FIELDS_BACKCOMPAT:
            # Issue #1099: guard against Popoto descriptor leaking the
            # ``IntField`` instance through when a row predates this field
            # in Redis. Any non-int, non-None value is coerced to 0 (the
            # field's safe default). bool is a subclass of int in Python,
            # so this accepts True/False intentionally.
            if value is not None and not isinstance(value, int):
                logger.debug(
                    f"AgentSession: coerced {name}={value!r} → 0 (bad type {type(value).__name__})"
                )
                value = 0
        super().__setattr__(name, value)

    @classmethod
    def _normalize_kwargs(cls, kwargs: dict) -> dict:
        """Map deprecated field names to their new consolidated equivalents.

        This allows callers to pass old field names (message_text, sender_name,
        etc.) and have them automatically mapped into initial_telegram_message,
        extra_context, etc.

        Also applies defensive coercion to ``response_delivered_at``. Beyond the
        standard ``int | float → datetime`` conversion that all datetime fields
        receive, this field gets extra handling for ``str`` (ISO 8601 → UTC
        datetime) and any other non-datetime, non-None type (→ None). This guards
        against Popoto's ``is_valid()`` silently aborting ``save()`` when a
        session loaded from Redis holds a stale or corrupt value in this field
        (issue #929).
        """
        # Extract fields that map to initial_telegram_message
        itm_fields = {}
        for key in (
            "message_text",
            "sender_name",
            "sender_id",
            "telegram_message_id",
            "chat_title",
        ):
            if key in kwargs and "initial_telegram_message" not in kwargs:
                val = kwargs.pop(key)
                if val is not None:
                    itm_fields[key] = val
        if itm_fields and "initial_telegram_message" not in kwargs:
            kwargs["initial_telegram_message"] = itm_fields

        # Extract fields that map to extra_context
        ec_fields = {}
        for key in ("revival_context", "classification_type", "classification_confidence"):
            if key in kwargs and "extra_context" not in kwargs:
                val = kwargs.pop(key)
                if val is not None:
                    ec_fields[key] = val
        if ec_fields and "extra_context" not in kwargs:
            kwargs["extra_context"] = ec_fields

        # Map deprecated field names
        if "work_item_slug" in kwargs and "slug" not in kwargs:
            kwargs["slug"] = kwargs.pop("work_item_slug")
        elif "work_item_slug" in kwargs:
            kwargs.pop("work_item_slug")

        if "last_activity" in kwargs and "updated_at" not in kwargs:
            kwargs["updated_at"] = kwargs.pop("last_activity")
        elif "last_activity" in kwargs:
            kwargs.pop("last_activity")

        if "scheduled_after" in kwargs and "scheduled_at" not in kwargs:
            val = kwargs.pop("scheduled_after")
            if isinstance(val, int | float):
                kwargs["scheduled_at"] = datetime.fromtimestamp(val, tz=UTC)
            else:
                kwargs["scheduled_at"] = val
        elif "scheduled_after" in kwargs:
            kwargs.pop("scheduled_after")

        # Map old field names to new ones  # legacy
        if "parent_job_id" in kwargs and "parent_agent_session_id" not in kwargs:  # legacy
            kwargs["parent_agent_session_id"] = kwargs.pop("parent_job_id")  # legacy
        elif "parent_job_id" in kwargs:  # legacy
            kwargs.pop("parent_job_id")  # legacy

        # Schema diet (#1927): watchdog_unhealthy -> unhealthy_reason back-alias.
        if "watchdog_unhealthy" in kwargs and "unhealthy_reason" not in kwargs:
            kwargs["unhealthy_reason"] = kwargs.pop("watchdog_unhealthy")
        elif "watchdog_unhealthy" in kwargs:
            kwargs.pop("watchdog_unhealthy")

        if "agent_session_id" in kwargs:
            kwargs.pop("agent_session_id")  # AutoKeyField, ignore

        if "job_id" in kwargs:  # legacy
            kwargs.pop("job_id")  # legacy

        # Map old history to session_events
        if "history" in kwargs and "session_events" not in kwargs:
            kwargs["session_events"] = kwargs.pop("history")
        elif "history" in kwargs:
            kwargs.pop("history")

        # Convert stage_states to a session event
        stage_states_val = kwargs.pop("stage_states", None)
        if stage_states_val is not None and "session_events" not in kwargs:
            if isinstance(stage_states_val, str):
                try:
                    stages_dict = _json.loads(stage_states_val)
                except (ValueError, TypeError):
                    stages_dict = None
            elif isinstance(stage_states_val, dict):
                stages_dict = stage_states_val
            else:
                stages_dict = None
            if stages_dict:
                event = SessionEvent.stage_change("bulk", "init", stages_dict)
                kwargs["session_events"] = [event.model_dump()]

        # Convert commit_sha to a session event
        commit_sha_val = kwargs.pop("commit_sha", None)
        if commit_sha_val is not None:
            events = kwargs.get("session_events", []) or []
            event = SessionEvent.checkpoint(commit_sha_val)
            events.append(event.model_dump())
            kwargs["session_events"] = events

        # Convert summary to a session event
        summary_val = kwargs.pop("summary", None)
        if summary_val is not None:
            events = kwargs.get("session_events", []) or []
            event = SessionEvent.summary(summary_val)
            events.append(event.model_dump())
            kwargs["session_events"] = events

        # Remove dead fields silently. Note: fields removed by the schema
        # diet (#1927 -- see scripts/migrate_schema_diet_fields.py's
        # module docstring for the exact deleted-field list) do NOT need an
        # entry here: Popoto's Model.__init__ silently drops any kwarg that
        # doesn't match a declared field (verified empirically and matching
        # the #1924 PTY-teardown precedent, which added no pop-list entries
        # either), so an archive-restore payload carrying an old dead-field
        # key never raises.
        for dead in (
            "depends_on",
            "stable_agent_session_id",
            "scheduling_depth",
            "_qa_mode_legacy",
        ):
            kwargs.pop(dead, None)

        # Ensure created_at has a default (SortedField is not nullable)
        if "created_at" not in kwargs:
            kwargs["created_at"] = datetime.now(tz=UTC)

        # Convert float timestamps to datetime
        if "created_at" in kwargs and isinstance(kwargs["created_at"], int | float):
            kwargs["created_at"] = datetime.fromtimestamp(kwargs["created_at"], tz=UTC)
        if "started_at" in kwargs and isinstance(kwargs["started_at"], int | float):
            kwargs["started_at"] = datetime.fromtimestamp(kwargs["started_at"], tz=UTC)
        if "completed_at" in kwargs and isinstance(kwargs["completed_at"], int | float):
            kwargs["completed_at"] = datetime.fromtimestamp(kwargs["completed_at"], tz=UTC)
        if "updated_at" in kwargs and isinstance(kwargs["updated_at"], int | float):
            kwargs["updated_at"] = datetime.fromtimestamp(kwargs["updated_at"], tz=UTC)
        if "response_delivered_at" in kwargs:
            val = kwargs["response_delivered_at"]
            if isinstance(val, int | float):
                kwargs["response_delivered_at"] = datetime.fromtimestamp(val, tz=UTC)
            elif isinstance(val, str):
                # Defence-in-depth: __setattr__ handles this too, but normalise early
                try:
                    dt = datetime.fromisoformat(val)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    kwargs["response_delivered_at"] = dt
                except (ValueError, TypeError):
                    logger.debug(
                        f"_normalize_kwargs: coerced response_delivered_at={val!r} → None "
                        "(unparseable ISO string)"
                    )
                    kwargs["response_delivered_at"] = None
            elif val is not None and not isinstance(val, datetime):
                logger.debug(
                    f"_normalize_kwargs: coerced response_delivered_at={val!r} → None "
                    f"(bad type {type(val).__name__})"
                )
                kwargs["response_delivered_at"] = None

        return kwargs

    @classmethod
    def create(cls, **kwargs) -> "AgentSession":
        """Create an AgentSession with backward-compatible field name support."""
        kwargs = cls._normalize_kwargs(kwargs)
        return super().create(**kwargs)

    @classmethod
    async def async_create(cls, **kwargs) -> "AgentSession":
        """Create an AgentSession asynchronously with backward-compatible field name support."""
        kwargs = cls._normalize_kwargs(kwargs)
        return await super().async_create(**kwargs)

    # Fields whose partial saves intentionally omit ``updated_at`` at high
    # frequency (liveness heartbeats and PID bookkeeping). Omitting the stamp
    # here is by-design — the dedicated heartbeat/freshness fields ARE the
    # freshness signal, so advancing ``updated_at`` would be redundant — and a
    # WARNING per write is pure log noise (several lines per heartbeat per
    # session) that buries genuine warnings. We downgrade these to DEBUG;
    # any other omission (e.g. ``status``) still warns. A combined partial save
    # is only downgraded when EVERY field in it is on this allowlist.
    _UPDATED_AT_OMISSION_OK_FIELDS = frozenset(
        {
            # SDK / worker liveness heartbeats and PID bookkeeping
            "last_heartbeat_at",
            "last_sdk_heartbeat_at",
            "last_stdout_at",
            "claude_pid",
            "harness_pid",
            # Runner PM subprocess pid (per-turn spawn bookkeeping, Race 2)
            "pm_pid",
            # Turn-boundary liveness (stream-json result handler)
            "last_turn_at",
            # Tool-boundary liveness (Pre/PostToolUse hooks, fires per tool call)
            "current_tool_name",
            "last_tool_use_at",
        }
    )

    def save(self, *args, update_fields=None, **kwargs):
        """Override to stamp updated_at with UTC wall-clock time.

        Popoto auto_now mints naive local time (bug #1645); instead we stamp
        explicitly so the stored value is always UTC wall-clock, consistent
        with how created_at/started_at are handled (see bridge/utc.py::utc_now).

        update_fields guard: if update_fields omits 'updated_at', skip the stamp
        entirely (no in-memory mutation without a matching persist, to avoid
        memory/Redis desync).
        """
        from bridge.utc import utc_now

        if update_fields is not None and "updated_at" not in update_fields:
            # Known high-frequency liveness/PID partial saves log at DEBUG;
            # everything else keeps the WARNING so real omissions stay visible.
            if set(update_fields) <= self._UPDATED_AT_OMISSION_OK_FIELDS:
                logger.debug(
                    "save() omitted 'updated_at' for liveness fields %s "
                    "(by design; freshness carried by heartbeat fields)",
                    list(update_fields),
                )
            else:
                logger.warning(
                    "save() called with update_fields missing 'updated_at'; "
                    "timestamp not persisted to avoid memory/Redis desync"
                )
            return super().save(*args, update_fields=update_fields, **kwargs)
        self.updated_at = utc_now()
        return super().save(*args, update_fields=update_fields, **kwargs)

    @classmethod
    def _heal_future_updated_at(cls) -> int:
        """One-shot DETECTION for future-dated updated_at values written before fix #1645.

        C2 (#1817): this function previously persisted a clamped
        ``updated_at`` (a re-save call) whenever it found a future-dated
        record. That re-save was itself a hazard, not a cure: persisting
        rewrites the ``created_at``-based sorted index on every call, so
        healing one future-dated straggler reshuffled the index position of
        every OTHER recently-created record too -- corrupting freshness
        ordering for every reader, not just the one record being healed.

        Detection is now read-only: it logs any future-dated records it
        still finds (for operator visibility) but never mutates or persists
        a clamped value. Health staleness no longer depends on this heal
        having run -- see ``agent/session_health.py``'s trusted-clock fix
        (Redis ``TIME`` instead of local wall-clock), which makes a
        future-dated (skew-written) ``updated_at`` harmless to read even
        without ever being clamped: age is computed from a single shared
        clock, not from comparing two different processes' local clocks.

        Returns the number of future-dated records detected (NOT healed —
        nothing is mutated or re-saved).
        """
        from bridge.utc import utc_now

        now = utc_now()
        count = 0
        try:
            all_sessions = cls.query.all()
        except Exception as e:
            logger.warning(f"_heal_future_updated_at: could not fetch sessions: {e}")
            return 0

        for record in all_sessions:
            try:
                if record.updated_at is None:
                    continue  # None is safe — save() will stamp on next write

                # Popoto strips tzinfo on load — treat naive datetimes as UTC
                # (consistent with bridge/utc.py::to_unix_ts).
                updated_at_utc = record.updated_at
                if updated_at_utc.tzinfo is None:
                    updated_at_utc = updated_at_utc.replace(tzinfo=UTC)

                if updated_at_utc <= now:
                    continue  # already sane, skip

                logger.warning(
                    f"_heal_future_updated_at: detected future-dated updated_at on "
                    f"{record.id} ({updated_at_utc.isoformat()} > now={now.isoformat()}) "
                    "-- NOT re-saving (index-reshuffle hazard, see C2 #1817); "
                    "staleness reads use a trusted-clock relative age instead"
                )
                count += 1
            except Exception as e:
                logger.warning(
                    f"_heal_future_updated_at: skipped {getattr(record, 'id', '?')}: {e}"
                )

        return count

    @classmethod
    def get_by_id(cls, agent_session_id: str | None) -> "AgentSession | None":
        """Look up an AgentSession by its raw string id.

        This is the canonical entry point for resolving a session from a raw
        ``agent_session_id`` string (e.g., from a CLI argument, parent reference,
        or Redis hash field).

        Popoto's ``query.get()`` does NOT accept a positional string -- it
        requires ``db_key=`` / ``redis_key=`` / full KeyField kwargs and will
        raise ``AttributeError: 'str' object has no attribute 'redis_key'`` if
        you pass a bare string. Use this helper instead.

        Args:
            agent_session_id: Raw string id of the session, or ``None``.

        Returns:
            The matching AgentSession, or None if not found / input is empty.
        """
        if not isinstance(agent_session_id, str) or not agent_session_id.strip():
            return None
        try:
            results = list(cls.query.filter(id=agent_session_id))
        except Exception as exc:
            logger.warning(
                "AgentSession.get_by_id lookup failed for %s: %s",
                agent_session_id,
                exc,
            )
            return None
        if not results:
            return None
        if len(results) > 1:
            logger.warning(
                "AgentSession.get_by_id found %d sessions for id=%s (expected 1)",
                len(results),
                agent_session_id,
            )
        return results[0]

    @classmethod
    def get_by_id_strict(cls, agent_session_id: str | None) -> "AgentSession | None":
        """Look up an AgentSession by its raw string id, propagating lookup errors.

        Raising sibling of :meth:`get_by_id` (issue #1868). ``get_by_id``
        swallows its own ``cls.query.filter`` exception and returns a plain
        ``None``, which makes a transient Redis lookup blip indistinguishable
        from a genuine not-found at the call site -- a caller that treats
        ``None`` as "record deleted, safe to reclaim" (e.g. the autonomous
        slot-lease reaper's Phase 2) can spuriously reclaim a live session's
        permit on a read blip. This method has the identical body minus the
        ``except Exception: return None`` swallow, so a lookup error
        propagates to the caller while a clean not-found still returns
        ``None``. Callers that need to distinguish "confirmed absent" from
        "lookup failed" must use this method, not ``get_by_id``.

        Args:
            agent_session_id: Raw string id of the session, or ``None``.

        Returns:
            The matching AgentSession, or None if not found / input is empty.

        Raises:
            Exception: whatever ``cls.query.filter`` raises on a lookup error
                (e.g. a transient Redis error). Not caught here by design.
        """
        if not isinstance(agent_session_id, str) or not agent_session_id.strip():
            return None
        results = list(cls.query.filter(id=agent_session_id))
        if not results:
            return None
        if len(results) > 1:
            logger.warning(
                "AgentSession.get_by_id_strict found %d sessions for id=%s (expected 1)",
                len(results),
                agent_session_id,
            )
        return results[0]

    @classmethod
    def find_by_claude_pid(cls, pid: int | None) -> "AgentSession | None":
        """Look up the AgentSession that owns the given Claude CLI subprocess PID.

        Issue #1271. Used by the cross-process orphan reaper
        (``agent/session_health.py::_reap_orphan_session_processes``) to gate
        kills on the owning session's heartbeat freshness — only provably stale
        or terminal sessions' subprocesses are reaped.

        Args:
            pid: The OS PID of a `claude_agent_sdk/_bundled/claude` subprocess.

        Returns:
            The matching AgentSession (first if multiple match — should never
            happen in steady state because PIDs are unique while live), or
            None if no record matches or pid is None/invalid.
        """
        if pid is None:
            return None
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            return None
        try:
            results = list(cls.query.filter(claude_pid=pid_int))
        except Exception as exc:
            logger.warning(
                "AgentSession.find_by_claude_pid lookup failed for pid=%s: %s",
                pid_int,
                exc,
            )
            return None
        if not results:
            return None
        if len(results) > 1:
            logger.warning(
                "AgentSession.find_by_claude_pid found %d sessions for pid=%s "
                "(expected 1) — returning the first hydrated record",
                len(results),
                pid_int,
            )
        return results[0]

    # === Backward-compatible property: agent_session_id -> id ===

    @property
    def agent_session_id(self) -> str | None:
        """Backward-compatible alias for id."""
        return self.id

    @agent_session_id.setter
    def agent_session_id(self, value: str) -> None:
        """Backward-compatible setter for id."""
        self.id = value

    # === Compatibility property aliases ===

    @property
    def sender_name(self) -> str | None:
        """Extract sender_name from initial_telegram_message."""
        itm = self.initial_telegram_message
        if isinstance(itm, dict):
            return itm.get("sender_name")
        return None

    @sender_name.setter
    def sender_name(self, value: str | None) -> None:
        """Set sender_name in initial_telegram_message."""
        itm = self.initial_telegram_message
        if not isinstance(itm, dict):
            itm = {}
        itm["sender_name"] = value
        self.initial_telegram_message = itm

    @property
    def sender_id(self) -> int | None:
        """Extract sender_id from initial_telegram_message."""
        itm = self.initial_telegram_message
        if isinstance(itm, dict):
            val = itm.get("sender_id")
            return int(val) if val is not None else None
        return None

    @sender_id.setter
    def sender_id(self, value: int | None) -> None:
        """Set sender_id in initial_telegram_message."""
        itm = self.initial_telegram_message
        if not isinstance(itm, dict):
            itm = {}
        if value is not None:
            itm["sender_id"] = value
        elif "sender_id" in itm:
            del itm["sender_id"]
        self.initial_telegram_message = itm

    @property
    def message_text(self) -> str | None:
        """Extract message_text from initial_telegram_message."""
        itm = self.initial_telegram_message
        if isinstance(itm, dict):
            return itm.get("message_text")
        return None

    @message_text.setter
    def message_text(self, value: str) -> None:
        """Set message_text in initial_telegram_message."""
        itm = self.initial_telegram_message
        if not isinstance(itm, dict):
            itm = {}
        itm["message_text"] = value
        self.initial_telegram_message = itm

    @property
    def telegram_message_id(self) -> int | None:
        """Extract telegram_message_id from initial_telegram_message."""
        itm = self.initial_telegram_message
        if isinstance(itm, dict):
            val = itm.get("telegram_message_id")
            return int(val) if val is not None else None
        return None

    @telegram_message_id.setter
    def telegram_message_id(self, value: int | None) -> None:
        """Set telegram_message_id in initial_telegram_message."""
        itm = self.initial_telegram_message
        if not isinstance(itm, dict):
            itm = {}
        if value is not None:
            itm["telegram_message_id"] = value
        elif "telegram_message_id" in itm:
            del itm["telegram_message_id"]
        self.initial_telegram_message = itm

    @property
    def chat_title(self) -> str | None:
        """Extract chat_title from initial_telegram_message."""
        itm = self.initial_telegram_message
        if isinstance(itm, dict):
            return itm.get("chat_title")
        return None

    @chat_title.setter
    def chat_title(self, value: str | None) -> None:
        """Set chat_title in initial_telegram_message."""
        itm = self.initial_telegram_message
        if not isinstance(itm, dict):
            itm = {}
        if value is not None:
            itm["chat_title"] = value
        elif "chat_title" in itm:
            del itm["chat_title"]
        self.initial_telegram_message = itm

    @property
    def sender(self) -> str | None:
        """Alias for sender_name (SessionLog used 'sender')."""
        return self.sender_name

    @property
    def revival_context(self) -> str | None:
        """Extract revival_context from extra_context."""
        ec = self.extra_context
        if isinstance(ec, dict):
            return ec.get("revival_context")
        return None

    @revival_context.setter
    def revival_context(self, value: str | None) -> None:
        """Set revival_context in extra_context."""
        ec = self.extra_context
        if not isinstance(ec, dict):
            ec = {}
        if value is not None:
            ec["revival_context"] = value
        elif "revival_context" in ec:
            del ec["revival_context"]
        self.extra_context = ec

    @property
    def classification_type(self) -> str | None:
        """Extract classification_type from extra_context."""
        ec = self.extra_context
        if isinstance(ec, dict):
            return ec.get("classification_type")
        return None

    @classification_type.setter
    def classification_type(self, value: str | None) -> None:
        """Set classification_type in extra_context."""
        ec = self.extra_context
        if not isinstance(ec, dict):
            ec = {}
        if value is not None:
            ec["classification_type"] = value
        elif "classification_type" in ec:
            del ec["classification_type"]
        self.extra_context = ec

    @property
    def classification_confidence(self) -> float | None:
        """Extract classification_confidence from extra_context."""
        ec = self.extra_context
        if isinstance(ec, dict):
            val = ec.get("classification_confidence")
            return float(val) if val is not None else None
        return None

    @classification_confidence.setter
    def classification_confidence(self, value: float | None) -> None:
        """Set classification_confidence in extra_context."""
        ec = self.extra_context
        if not isinstance(ec, dict):
            ec = {}
        if value is not None:
            ec["classification_confidence"] = value
        elif "classification_confidence" in ec:
            del ec["classification_confidence"]
        self.extra_context = ec

    @property
    def work_item_slug(self) -> str | None:
        """Backward-compatible alias for slug."""
        return self.slug

    @property
    def scheduling_depth(self) -> int:
        """Derive scheduling depth by walking parent_agent_session_id chain.

        Returns the depth of the parent chain, capped at 5 for safety.
        """
        depth = 0
        current = self
        max_depth = 5
        while depth < max_depth:
            pid = current.parent_agent_session_id
            if not pid:
                break
            try:
                parent = AgentSession.get_by_id(pid)
                if parent is None:
                    break
                depth += 1
                current = parent
            except Exception as exc:
                logger.warning(
                    "AgentSession lookup failed walking parent chain for %s: %s",
                    pid,
                    exc,
                )
                break
        return depth

    # === Derived properties from session_events ===

    @property
    def summary(self) -> str | None:
        """Get the most recent summary from session_events."""
        events = self.session_events
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if isinstance(event, dict) and event.get("event_type") == "summary":
                return event.get("text")
        return None

    @summary.setter
    def summary(self, value: str) -> None:
        """Set summary by appending a summary event."""
        if value:
            self.append_event("summary", value)

    @property
    def result_text(self) -> str | None:
        """Get the most recent delivery text from session_events."""
        events = self.session_events
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if isinstance(event, dict) and event.get("event_type") == "delivery":
                return event.get("text")
        return None

    @result_text.setter
    def result_text(self, value: str) -> None:
        """Set result_text by appending a delivery event."""
        if value:
            self.append_event("delivery", value)

    @property
    def stage_states(self) -> str | None:
        """Get the most recent stage_states from session_events as JSON string."""
        events = self.session_events
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if isinstance(event, dict) and event.get("event_type") == "stage":
                data = event.get("data")
                if isinstance(data, dict) and "stages" in data:
                    stages = data["stages"]
                    if isinstance(stages, str):
                        return stages
                    return _json.dumps(stages)
        return None

    @stage_states.setter
    def stage_states(self, value) -> None:
        """Set stage_states by appending a stage event."""
        if value is not None:
            if isinstance(value, str):
                try:
                    stages_dict = _json.loads(value)
                except (ValueError, TypeError):
                    stages_dict = None
            elif isinstance(value, dict):
                stages_dict = value
            else:
                stages_dict = None
            if stages_dict:
                event = SessionEvent.stage_change("bulk", "update", stages_dict)
                self._append_event_dict(event.model_dump())

    @property
    def last_commit_sha(self) -> str | None:
        """Get the most recent commit SHA from session_events."""
        events = self.session_events
        if not isinstance(events, list):
            return None
        for event in reversed(events):
            if isinstance(event, dict) and event.get("event_type") == "checkpoint":
                return event.get("text")
        return None

    @property
    def commit_sha(self) -> str | None:
        """Backward-compatible alias for last_commit_sha."""
        return self.last_commit_sha

    @commit_sha.setter
    def commit_sha(self, value: str) -> None:
        """Set commit_sha by appending a checkpoint event."""
        if value:
            event = SessionEvent.checkpoint(value)
            self._append_event_dict(event.model_dump())

    # === Session type helpers ===

    @property
    def is_eng(self) -> bool:
        """Whether this is an Eng session (Engineer persona, full permissions)."""
        return self.session_type == SESSION_TYPE_ENG

    @property
    def is_teammate(self) -> bool:
        """Whether this is a Teammate session (read-only, no orchestration)."""
        return self.session_type == SESSION_TYPE_TEAMMATE

    @property
    def current_stage(self) -> str | None:
        """Return the first SDLC stage with status 'in_progress', or None."""
        stages = self._get_stage_states_dict()
        if not stages:
            return None
        for stage in SDLC_STAGES:
            if stages.get(stage) == "in_progress":
                return stage
        return None

    @property
    def derived_branch_name(self) -> str | None:
        """Derive branch name from slug if available."""
        s = self.slug
        return f"session/{s}" if s else self.branch_name

    @property
    def plan_path(self) -> str | None:
        """Derive plan path from slug if available."""
        s = self.slug
        return f"docs/plans/{s}.md" if s else None

    def _get_stage_states_dict(self) -> dict | None:
        """Parse stage_states into a dict, or None."""
        raw = self.stage_states
        if not raw:
            return None
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except (_json.JSONDecodeError, TypeError):
                pass
        return None

    # === Factory methods ===

    @classmethod
    def _create_session_with_telegram(
        cls,
        *,
        session_type: str,
        session_id: str,
        project_key: str,
        working_dir: str,
        chat_id: str,
        telegram_message_id: int,
        message_text: str,
        sender_name: str | None = None,
        sender_id: int | None = None,
        chat_title: str | None = None,
        telegram_message_key: str | None = None,
        **kwargs,
    ) -> "AgentSession":
        """Internal helper: create a session with Telegram message context."""
        itm = {
            "message_text": message_text,
            "sender_name": sender_name,
            "telegram_message_id": telegram_message_id,
        }
        if sender_id is not None:
            itm["sender_id"] = sender_id
        if chat_title is not None:
            itm["chat_title"] = chat_title

        session = cls(
            session_id=session_id,
            session_type=session_type,
            project_key=project_key,
            working_dir=working_dir,
            chat_id=chat_id,
            initial_telegram_message=itm,
            telegram_message_key=telegram_message_key,
            created_at=datetime.now(tz=UTC),
            **kwargs,
        )
        session.save()
        return session

    @classmethod
    def create_eng(
        cls,
        *,
        session_id: str,
        project_key: str,
        working_dir: str,
        chat_id: str,
        telegram_message_id: int,
        message_text: str,
        sender_name: str | None = None,
        sender_id: int | None = None,
        chat_title: str | None = None,
        telegram_message_key: str | None = None,
        **kwargs,
    ) -> "AgentSession":
        """Create an Eng session (Engineer persona, full permissions, orchestrates work)."""
        return cls._create_session_with_telegram(
            session_type=SESSION_TYPE_ENG,
            session_id=session_id,
            project_key=project_key,
            working_dir=working_dir,
            chat_id=chat_id,
            telegram_message_id=telegram_message_id,
            message_text=message_text,
            sender_name=sender_name,
            sender_id=sender_id,
            chat_title=chat_title,
            telegram_message_key=telegram_message_key,
            **kwargs,
        )

    @classmethod
    def create_teammate(
        cls,
        *,
        session_id: str,
        project_key: str,
        working_dir: str,
        chat_id: str,
        telegram_message_id: int,
        message_text: str,
        sender_name: str | None = None,
        sender_id: int | None = None,
        chat_title: str | None = None,
        telegram_message_key: str | None = None,
        **kwargs,
    ) -> "AgentSession":
        """Create a Teammate session (read-only, no orchestration authority)."""
        return cls._create_session_with_telegram(
            session_type=SESSION_TYPE_TEAMMATE,
            session_id=session_id,
            project_key=project_key,
            working_dir=working_dir,
            chat_id=chat_id,
            telegram_message_id=telegram_message_id,
            message_text=message_text,
            sender_name=sender_name,
            sender_id=sender_id,
            chat_title=chat_title,
            telegram_message_key=telegram_message_key,
            **kwargs,
        )

    @classmethod
    def create_local(
        cls,
        *,
        session_id: str,
        project_key: str,
        working_dir: str,
        session_type: str = SESSION_TYPE_ENG,
        **kwargs,
    ) -> "AgentSession":
        """Create an AgentSession for a local Claude Code CLI session."""
        now = datetime.now(tz=UTC)
        chat_id = kwargs.pop("chat_id", None) or session_id
        session = cls(
            session_id=session_id,
            session_type=session_type,
            project_key=project_key,
            working_dir=working_dir,
            chat_id=chat_id,
            created_at=now,
            started_at=now,
            updated_at=now,
            **kwargs,
        )
        session.save()
        return session

    @classmethod
    def create_child(
        cls,
        *,
        session_id: str,
        project_key: str,
        working_dir: str,
        parent_agent_session_id: str,
        message_text: str,
        slug: str | None = None,
        stage_states: dict | None = None,
        **kwargs,
    ) -> "AgentSession":
        """Create a child Dev AgentSession.

        Args:
            session_id: Unique session identifier.
            project_key: Project this session belongs to.
            working_dir: Working directory for the session.
            parent_agent_session_id: ID of the parent session.
            message_text: Initial message text.
            slug: Optional work item slug.
            stage_states: Optional initial SDLC stage states.
            **kwargs: Additional fields passed to the constructor.
        """
        itm = {"message_text": message_text}

        # If stage_states provided, store as an initial event
        initial_events = None
        if isinstance(stage_states, dict):
            event = SessionEvent.stage_change("bulk", "init", stage_states)
            initial_events = [event.model_dump()]

        session = cls(
            session_id=session_id,
            session_type=SESSION_TYPE_ENG,
            project_key=project_key,
            working_dir=working_dir,
            parent_agent_session_id=parent_agent_session_id,
            initial_telegram_message=itm,
            slug=slug,
            session_events=initial_events,
            created_at=datetime.now(tz=UTC),
            **kwargs,
        )
        session.save()
        return session

    def get_parent_session(self) -> "AgentSession | None":
        """Return the parent session if this is a child session."""
        if not self.parent_agent_session_id:
            return None
        try:
            return AgentSession.get_by_id(self.parent_agent_session_id)
        except Exception as exc:
            logger.warning(
                "Parent session %s lookup failed for session %s: %s",
                self.parent_agent_session_id,
                self.id,
                exc,
            )
            return None

    def get_parent_chat_session(self) -> "AgentSession | None":
        """Backward-compat wrapper for get_parent_session().

        Deprecated: Use get_parent_session() instead.
        """
        return self.get_parent_session()

    def get_child_sessions(self) -> list["AgentSession"]:
        """Return all child sessions linked via parent_agent_session_id."""
        try:
            return list(AgentSession.query.filter(parent_agent_session_id=self.id))
        except Exception as e:
            logger.warning(f"Failed to query child sessions for {self.id}: {e}")
            return []

    def get_dev_sessions(self) -> list["AgentSession"]:
        """Backward-compat wrapper for get_child_sessions().

        Deprecated: Use get_child_sessions() instead.
        """
        return self.get_child_sessions()

    # === Chat message log helpers (issue #1192) ===

    def append_chat_log(
        self,
        direction: str,
        sender: str,
        content: str,
        message_id: int | None = None,
        ts: float | None = None,
    ) -> None:
        """Append one entry to chat_message_log, bounded to CHAT_LOG_MAX_ENTRIES.

        Re-fetches the latest record from Popoto immediately before the
        append-and-save to narrow the concurrent-write race window (Race 1 in the
        plan). This costs one extra Redis read per call but prevents lost-update
        when inbound and outbound writes collide.

        Silently skips empty/whitespace-only content — no junk pollutes the log.
        Substitutes "unknown" for None/empty sender so the drafter never renders
        `sender=None`.

        Wrapped in try/except so a save failure never crashes the caller — the
        chat log is enrichment, not a critical path.
        """
        content = (content or "").strip()
        if not content:
            return
        sender = (sender or "").strip() or "unknown"
        entry = {
            "direction": direction,
            "sender": sender,
            "content": content,
            "message_id": message_id,
            "ts": ts if ts is not None else time.time(),
        }
        try:
            # Re-fetch the freshest version to minimize lost-update window.
            # query.filter(session_id=...) is correct — session_id is a regular Field(),
            # not the AutoKeyField. query.get() requires the AutoKey (id field).
            rows = list(AgentSession.query.filter(session_id=self.session_id))
            fresh = rows[0] if rows else None
            if fresh is None:
                # Session vanished — fall back to self to avoid losing the entry.
                fresh = self
            log = list(fresh.chat_message_log or [])
            log.append(entry)
            log = log[-CHAT_LOG_MAX_ENTRIES:]
            fresh.chat_message_log = log
            fresh.save(update_fields=["chat_message_log", "updated_at"])
        except Exception as exc:
            logger.warning(
                f"append_chat_log failed for session {self.session_id} "
                f"(direction={direction!r}, sender={sender!r}): {exc}"
            )

    # === PM self-messaging helpers ===

    def record_pm_message(self, msg_id: int) -> None:
        """Record a Telegram message ID sent by the PM."""
        current = self.pm_sent_message_ids
        if not isinstance(current, list):
            current = []
        current.append(msg_id)
        self.pm_sent_message_ids = current
        try:
            self.save()
        except Exception as e:
            logger.warning(
                f"record_pm_message save failed for session {self.session_id} "
                f"(msg_id={msg_id}): {e}"
            )

    def has_pm_messages(self) -> bool:
        """Check whether the PM sent any self-authored messages during this session."""
        ids = self.pm_sent_message_ids
        return isinstance(ids, list) and len(ids) > 0

    # === Drafter redundancy filter helpers (issue #1205) ===

    def record_recent_sent_draft(
        self,
        text: str,
        artifacts: dict,
        *,
        max_n: int = 3,
        preview_chars: int = 500,
    ) -> None:
        """Append a successfully-sent draft to the session's recent_sent_drafts.

        Caps the list to ``max_n`` entries (FIFO — oldest dropped). Each entry
        stores a text preview of at most ``preview_chars`` characters, not the
        full draft, to bound the AgentSession Redis hash size (3 × 500 chars
        ≈ 1.5 KB upper bound, well within safe Redis write sizes).

        Modelled on ``_append_event_dict`` (just above this method) which uses
        ``self.save(update_fields=["session_events", "updated_at"])`` to defend
        against stale-object callers clobbering concurrent field writes (see
        #898). Never raises — a failed save logs a warning and continues so
        the outbox ``rpush`` that already succeeded is not rolled back.

        FIFO cap: after append, slice to last ``max_n`` entries.
        Preview cap: ``text[:preview_chars]`` (full drafts can be ~4 KB).
        Partial save: ``update_fields=["recent_sent_drafts", "updated_at"]``
        so concurrent writes to ``context_summary`` or ``session_events`` by
        the same ``send()`` flow are not clobbered. This is the right pattern;
        the adjacent ``record_pm_message`` uses an unscoped save and is an
        older helper that pre-dates the stale-object hazard documented in #898.
        """
        import time as _time

        entry = {
            "ts": _time.time(),
            "text": text[:preview_chars],
            "artifacts": artifacts or {},
        }
        current = self.recent_sent_drafts
        if not isinstance(current, list):
            current = []
        current.append(entry)
        # FIFO cap: drop oldest entry when over the limit.
        if len(current) > max_n:
            current = current[-max_n:]
        self.recent_sent_drafts = current
        try:
            self.save(update_fields=["recent_sent_drafts", "updated_at"])
        except Exception as e:
            logger.warning(
                "record_recent_sent_draft save failed for session %s: %s",
                self.session_id,
                e,
            )

    # === Event log helpers ===

    def get_history_list(self) -> list:
        """Get session_events as a list of formatted strings (backward compat)."""
        events = self.session_events
        if not isinstance(events, list):
            return []
        result = []
        for event in events:
            if isinstance(event, dict):
                etype = event.get("event_type", "system")
                text = event.get("text", "")
                result.append(f"[{etype}] {text}")
            elif isinstance(event, str):
                result.append(event)
        return result

    # Keep private alias for internal callers
    _get_history_list = get_history_list

    @property
    def history(self) -> list | None:
        """Backward-compatible alias for session_events."""
        return self.session_events

    @history.setter
    def history(self, value) -> None:
        """Backward-compatible setter for session_events."""
        self.session_events = value

    def append_event(self, event_type: str, text: str, data: dict | None = None) -> None:
        """Append a structured event to session_events, capped at HISTORY_MAX_ENTRIES.

        Args:
            event_type: Event type (lifecycle, summary, delivery, stage, checkpoint, etc.)
            text: Event description
            data: Optional structured payload

        **Stale-object hazard**: This method saves via _append_event_dict, which uses a
        partial save (update_fields=["session_events", "updated_at"]) to protect against
        callers operating on a stale in-memory snapshot. A stale caller can at worst
        append a spurious event; it cannot clobber status, auto_continue_count, or
        message_text. See #898 and docs/features/session-lifecycle.md.
        """
        event = SessionEvent(event_type=event_type, text=text, data=data)
        self._append_event_dict(event.model_dump())

    def _append_event_dict(self, event_dict: dict) -> None:
        """Append a raw event dict to session_events, capped at HISTORY_MAX_ENTRIES."""
        current = self.session_events
        if not isinstance(current, list):
            current = []
        current.append(event_dict)
        if len(current) > HISTORY_MAX_ENTRIES:
            dropped = len(current) - HISTORY_MAX_ENTRIES
            logger.warning(
                f"Session {self.session_id} session_events truncated from "
                f"{len(current)} to {HISTORY_MAX_ENTRIES}, "
                f"{dropped} oldest entries lost"
            )
            current = current[-HISTORY_MAX_ENTRIES:]
        self.session_events = current
        try:
            # Partial save: only persist session_events + updated_at. This prevents
            # stale-object callers from clobbering status/auto_continue_count/
            # message_text when they call append_event / append_history /
            # log_lifecycle_transition on a local that has already been superseded
            # by a fresh authoritative write (e.g. _enqueue_nudge). See #898.
            self.save(update_fields=["session_events", "updated_at"])
        except Exception as e:
            logger.warning(
                f"append_event save failed for session {self.session_id} "
                f"(event_type={event_dict.get('event_type')!r}): {e}"
            )

    def append_history(self, role: str, text: str) -> None:
        """Backward-compatible: append a lifecycle event using append_event."""
        self.append_event(role, text)

    def set_link(self, kind: str, url: str) -> None:
        """Set a tracked link on this session.

        Uses partial save (update_fields) to avoid clobbering status on stale
        worker references. See #950 for the root cause analysis.
        """
        field_map = {
            "issue": "issue_url",
            "plan": "plan_url",
            "pr": "pr_url",
        }
        field_name = field_map.get(kind)
        if field_name:
            existing = getattr(self, field_name, None)
            action = "update" if existing else "set"
            cid = getattr(self, "correlation_id", None) or "unknown"
            logger.info(
                f"LINK session={self.session_id} correlation={cid} "
                f"type={kind} action={action} url={url}"
            )
            setattr(self, field_name, url)
            try:
                self.save(update_fields=[field_name, "updated_at"])
            except Exception as e:
                logger.warning(
                    f"set_link save failed for session {self.session_id} "
                    f"(kind={kind!r}, field={field_name}): {e}"
                )

    def log_lifecycle_transition(self, new_status: str, context: str = "") -> None:
        """Log a structured lifecycle transition and append event.

        **Implicit save**: This method calls append_event, which triggers a partial
        Redis save (session_events + updated_at only). If called on a stale object,
        it appends a lifecycle entry but does NOT clobber status or other fields.
        See #898 and the finalized_by_execute gate in agent_session_queue._worker_loop.
        """
        old_status = self.status or "none"
        now = datetime.now(tz=UTC)

        # Calculate duration from session start
        prev_time = self.started_at or self.created_at
        if prev_time is not None:
            if isinstance(prev_time, datetime):
                pt = prev_time if prev_time.tzinfo else prev_time.replace(tzinfo=UTC)
                duration = (now - pt).total_seconds()
            elif isinstance(prev_time, int | float):
                duration = now.timestamp() - prev_time
            else:
                duration = 0
        else:
            duration = 0

        logger.info(
            f"LIFECYCLE session={self.session_id} transition={old_status}\u2192{new_status} "
            f"id={self.id} project={self.project_key} "
            f"duration_in_prev_state={duration:.1f}s" + (f' context="{context}"' if context else "")
        )

        self.append_event(
            "lifecycle",
            f"{old_status}\u2192{new_status}" + (f": {context}" if context else ""),
        )

    def get_links(self) -> dict[str, str]:
        """Return all non-None tracked links."""
        links = {}
        if self.issue_url:
            links["issue"] = self.issue_url
        if self.plan_url:
            links["plan"] = self.plan_url
        if self.pr_url:
            links["pr"] = self.pr_url
        return links

    def get_stage_progress(self) -> dict[str, str]:
        """Return SDLC stage completion status via PipelineStateMachine.

        Prefers the issue-keyed PipelineLedger when this session's per-issue
        run_id lease is live and pinned to a target_repo (issue #2012
        follow-up); falls back to the session-keyed store otherwise.
        """
        from agent.pipeline_state import resolve_pipeline_state_machine

        sm, _, _ = resolve_pipeline_state_machine(self)
        return sm.get_display_progress()

    # === Stage-aware auto-continue helpers ===

    @property
    def is_sdlc(self) -> bool:
        """Whether this session is an SDLC pipeline session."""
        stages = self._get_stage_states_dict()
        if stages and any(v not in ("pending", "ready") for v in stages.values()):
            return True
        if self.classification_type == ClassificationType.SDLC:
            return True
        return False

    def has_remaining_stages(self) -> bool:
        """Check if any SDLC stages are not yet completed.

        Prefers the issue-keyed PipelineLedger when this session's per-issue
        run_id lease is live and pinned to a target_repo (issue #2012
        follow-up); falls back to the session-keyed store otherwise.
        """
        from agent.pipeline_state import resolve_pipeline_state_machine

        sm, _, _ = resolve_pipeline_state_machine(self)
        return sm.has_remaining_stages()

    def has_failed_stage(self) -> bool:
        """Check if any SDLC stage has failed.

        Prefers the issue-keyed PipelineLedger when this session's per-issue
        run_id lease is live and pinned to a target_repo (issue #2012
        follow-up); falls back to the session-keyed store otherwise.
        """
        from agent.pipeline_state import resolve_pipeline_state_machine

        sm, _, _ = resolve_pipeline_state_machine(self)
        return sm.has_failed_stage()

    # === Session hierarchy helpers ===

    def get_parent(self) -> "AgentSession | None":
        """Return the parent AgentSession if this is a child session."""
        if not self.parent_agent_session_id:
            return None
        try:
            return AgentSession.get_by_id(self.parent_agent_session_id)
        except Exception as exc:
            logger.warning(
                "Parent agent session %s lookup failed for child %s: %s",
                self.parent_agent_session_id,
                self.id,
                exc,
            )
            return None

    def get_children(self) -> list["AgentSession"]:
        """Return all child AgentSessions linked via parent_agent_session_id."""
        try:
            return list(AgentSession.query.filter(parent_agent_session_id=self.id))
        except Exception as e:
            logger.warning(f"Failed to query children for agent session {self.id}: {e}")
            return []

    def get_completion_progress(self) -> tuple[int, int, int]:
        """Compute aggregate completion status of child sessions."""
        children = self.get_children()
        total = len(children)
        completed = sum(1 for c in children if c.status == "completed")
        failed = sum(1 for c in children if c.status == "failed")
        return completed, total, failed

    # === Cleanup ===

    @classmethod
    def repair_indexes(cls) -> tuple[int, int]:
        """Clear stale IndexedField index entries then rebuild all indexes.

        Popoto's built-in rebuild_indexes() does NOT enumerate $IndexF keys
        (those are maintained separately by this method's loop).  However,
        rebuild_indexes() DOES delete the class set ($Class:AgentSession) at
        base.py:2745, then re-adds members in batch_size=1000 pipeline batches
        (base.py:2785-2813).  This class-set delete→re-add is the layer that
        transiently breaks session_id lookups: query.filter(session_id=...) on
        a non-indexed Field reads smembers($Class:AgentSession) and filters in
        memory, so it returns empty during the window.  See issue #1720.

        Read-path defense: both caller sites that do query.filter(session_id=...)
        — tools/valor_session.py::_find_session and
        tools/sdlc_stage_query.py::_find_session_by_id — apply a bounded retry
        (cap sized to exceed the measured p99 class-set-empty interval) to cover
        this window without touching popoto internals.

        This method's own role: clear all $IndexF:ClassName:* keys that
        rebuild_indexes() does not touch (only status is an IndexedField here),
        counting stale members before deletion so the caller can report drift.
        Then call rebuild_indexes() to repopulate the class set, KeyField, and
        SortedField indexes from actual hashes.

        Returns:
            (stale_count, rebuilt_count) — stale pointers removed and sessions
            indexed during rebuild.
        """
        from popoto.models.query import POPOTO_REDIS_DB

        # Find all $IndexF indexes for this model and count stale entries before clearing.
        # Existence checks are pipelined in batches — a bloated index (hundreds
        # of thousands to millions of stale pointers) turns a one-round-trip-
        # per-member scan into a multi-hour hang, since this method runs
        # unconditionally on every worker startup and reflection tick.
        prefix = f"$IndexF:{cls.__name__}:"
        stale_count = 0
        batch_size = 5000
        for index_key in POPOTO_REDIS_DB.keys(f"{prefix}*"):
            members = list(POPOTO_REDIS_DB.smembers(index_key))
            for i in range(0, len(members), batch_size):
                batch = members[i : i + batch_size]
                pipe = POPOTO_REDIS_DB.pipeline(transaction=False)
                for member in batch:
                    pipe.exists(member)
                stale_count += sum(1 for exists in pipe.execute() if not exists)
            # Delete the whole index key — rebuild_indexes() will reconstruct it.
            POPOTO_REDIS_DB.delete(index_key)

        rebuilt_count = cls.rebuild_indexes()
        return stale_count, rebuilt_count

    @classmethod
    def cleanup_expired(cls, max_age_days: int = 90) -> int:
        """Delete AgentSession Redis metadata older than max_age_days."""
        cutoff = datetime.now(tz=UTC).timestamp() - (max_age_days * 86400)
        all_sessions = cls.query.all()
        deleted = 0
        for session in all_sessions:
            started = session.started_at or session.created_at
            if started is None:
                continue
            # Handle both datetime and float timestamps (migration period).
            # to_unix_ts treats naive datetimes as UTC (Popoto strips tzinfo).
            from bridge.utc import to_unix_ts

            ts = to_unix_ts(started)
            if ts is None:
                continue
            if ts < cutoff:
                session.delete()
                deleted += 1
        return deleted
